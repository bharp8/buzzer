"""FastAPI app: routes, WebSocket, broadcast.

Broadcasts the full game snapshot to every connected client on every change
(see spec 2.4) -- state is tiny, so there's no reason to implement deltas
and every reason not to (a client that reconnects mid-game must get a
complete picture immediately).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from .game import Game, IllegalTransitionError, build_categories
from .inputs.base import BuzzerInput
from .inputs.mock import MockBackend

logger = logging.getLogger("buzzer")
# Configured independently of the root logger and of uvicorn's own logging
# setup: buzz events are the only record when a call is disputed (spec 9),
# so they must not silently vanish because nothing else in the process
# happened to configure logging (the root logger defaults to WARNING).
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

STATIC_DIR = Path(__file__).parent / "static"


class SelectClueBody(BaseModel):
    category: int
    row: int


class AdjustScoreBody(BaseModel):
    team: int
    delta: int


class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self.active.append(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            if ws in self.active:
                self.active.remove(ws)

    async def broadcast(self, message: str) -> None:
        async with self._lock:
            targets = list(self.active)
        dead = []
        for ws in targets:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    if ws in self.active:
                        self.active.remove(ws)


def load_content(path: str):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    try:
        teams, categories = build_categories(data)
    except ValueError as e:
        raise SystemExit(f"invalid game content at {path}: {e}") from e
    return data.get("title", "Jeopardy"), teams, categories


def build_backend(num_teams: int) -> BuzzerInput:
    if config.BUZZER_BACKEND == "gpio":
        from .inputs.gpio import GpioBackend

        return GpioBackend({0: config.TEAM_A_GPIO, 1: config.TEAM_B_GPIO})
    return MockBackend(num_teams=num_teams)


def create_app(content_path: Optional[str] = None, backend: Optional[BuzzerInput] = None) -> FastAPI:
    content_path = content_path or config.GAME_CONTENT_PATH
    title, teams, categories = load_content(content_path)

    game = Game(
        teams,
        categories,
        debounce_ms=config.DEBOUNCE_MS,
        false_start_lockout_ms=config.FALSE_START_LOCKOUT_MS,
    )
    manager = ConnectionManager()
    test_manager = ConnectionManager()
    backend = backend or build_backend(len(teams))
    is_mock = isinstance(backend, MockBackend)

    loop_holder: dict = {"loop": None}

    async def broadcast_state() -> None:
        snapshot = game.snapshot(now_tick=time.monotonic_ns())
        await manager.broadcast(json.dumps(snapshot))

    async def broadcast_test_event(entry: dict, source: str) -> None:
        # Test mode (static/test.html) cares about raw edges reaching each
        # pin, independent of game phase -- it needs to see a press even
        # while IDLE, where the main game would just ignore it. `entry` must
        # be the exact dict Game.buzz() returned for this call, captured
        # synchronously under its lock -- never re-read buzz_log[-1] here,
        # since concurrent GPIO callbacks on other pins can append a newer
        # entry before this coroutine actually runs.
        await test_manager.broadcast(
            json.dumps(
                {
                    "type": "buzz",
                    "source": source,
                    "team": entry["team"],
                    "tick": entry["tick"],
                    "result": entry["result"],
                    "phase": entry["phase"],
                }
            )
        )

    def log_buzz(entry: dict, source: str) -> None:
        # Every accepted or rejected edge gets logged, regardless of which
        # input path produced it -- this is the only record when someone
        # disputes a call (spec 9).
        logger.info(
            "buzz source=%s team=%s tick=%s result=%s phase=%s",
            source,
            entry["team"],
            entry["tick"],
            entry["result"],
            entry["phase"],
        )

    async def broadcast_all(entry: dict, source: str) -> None:
        await broadcast_state()
        await broadcast_test_event(entry, source)

    def on_buzz(team: int, tick_ns: int) -> None:
        # Called from the GPIO callback thread (or synchronously from the
        # mock's /dev/buzz route, which runs on the event loop thread --
        # run_coroutine_threadsafe works correctly from either).
        entry = game.buzz(team, tick_ns)
        if entry is None:
            return  # debounced or invalid team -- nothing to log/broadcast
        source = "mock" if is_mock else "gpio"
        log_buzz(entry, source)
        loop = loop_holder["loop"]
        if loop is not None:
            asyncio.run_coroutine_threadsafe(broadcast_all(entry, source), loop)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loop_holder["loop"] = asyncio.get_running_loop()
        backend.start(on_buzz)
        try:
            health = backend.self_test()
        except Exception:
            logger.exception("buzzer input self-test failed")
            health = {}
        warnings = []
        for idx, healthy in health.items():
            if not healthy and idx < len(teams):
                msg = f"{teams[idx].name} input reads low at rest — check for a stuck button"
                warnings.append(msg)
                logger.warning(msg)
        game.set_warnings(warnings)
        yield
        backend.stop()

    app = FastAPI(title=title, lifespan=lifespan)

    @app.get("/")
    async def root():
        return RedirectResponse(url="/board")

    @app.get("/board")
    async def board_page():
        return FileResponse(STATIC_DIR / "board.html")

    @app.get("/host")
    async def host_page():
        return FileResponse(STATIC_DIR / "host.html")

    @app.get("/test")
    async def test_page():
        return FileResponse(STATIC_DIR / "test.html")

    @app.get("/dual")
    async def dual_page():
        # Board and host side by side in one tab (two iframes), for running
        # everything off a single device instead of a separate display and
        # host device.
        return FileResponse(STATIC_DIR / "dual.html")

    @app.get("/buzz/{team}")
    async def phone_buzz_page(team: int):
        # Per-team fallback buzzer, for when the physical buttons aren't
        # working at all. Server-timestamped on receipt over Wi-Fi, not a
        # kernel edge -- best-effort fairness only, see static/buzz.js.
        if not (0 <= team < len(teams)):
            raise HTTPException(status_code=404, detail="unknown team")
        return FileResponse(STATIC_DIR / "buzz.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket):
        await manager.connect(websocket)
        await websocket.send_text(json.dumps(game.snapshot(now_tick=time.monotonic_ns())))
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await manager.disconnect(websocket)

    @app.websocket("/ws/test")
    async def ws_test_endpoint(websocket: WebSocket):
        # Raw buzz-event feed for /test, independent of game phase -- see
        # broadcast_test_event. Unrelated to the main /ws snapshot contract.
        await test_manager.connect(websocket)
        await websocket.send_text(
            json.dumps({"type": "hello", "teams": [t.name for t in teams]})
        )
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await test_manager.disconnect(websocket)

    def _apply(action_fn) -> None:
        try:
            action_fn()
        except IllegalTransitionError as e:
            raise HTTPException(status_code=409, detail=str(e))

    @app.post("/api/select_clue")
    async def api_select_clue(body: SelectClueBody):
        _apply(lambda: game.select_clue(body.category, body.row, tick_ns=time.monotonic_ns()))
        await broadcast_state()
        return game.snapshot(now_tick=time.monotonic_ns())

    @app.post("/api/arm")
    async def api_arm():
        _apply(lambda: game.arm(tick_ns=time.monotonic_ns()))
        await broadcast_state()
        return game.snapshot(now_tick=time.monotonic_ns())

    @app.post("/api/mark_correct")
    async def api_mark_correct():
        _apply(lambda: game.mark_correct(tick_ns=time.monotonic_ns()))
        await broadcast_state()
        return game.snapshot(now_tick=time.monotonic_ns())

    @app.post("/api/mark_incorrect")
    async def api_mark_incorrect():
        _apply(lambda: game.mark_incorrect(tick_ns=time.monotonic_ns()))
        await broadcast_state()
        return game.snapshot(now_tick=time.monotonic_ns())

    @app.post("/api/reveal")
    async def api_reveal():
        _apply(lambda: game.reveal(tick_ns=time.monotonic_ns()))
        await broadcast_state()
        return game.snapshot(now_tick=time.monotonic_ns())

    @app.post("/api/return_to_board")
    async def api_return_to_board():
        _apply(lambda: game.return_to_board(tick_ns=time.monotonic_ns()))
        await broadcast_state()
        return game.snapshot(now_tick=time.monotonic_ns())

    @app.post("/api/adjust_score")
    async def api_adjust_score(body: AdjustScoreBody):
        _apply(lambda: game.adjust_score(body.team, body.delta))
        await broadcast_state()
        return game.snapshot(now_tick=time.monotonic_ns())

    @app.post("/api/reset_game")
    async def api_reset_game():
        game.reset_game()
        await broadcast_state()
        return game.snapshot(now_tick=time.monotonic_ns())

    @app.post("/api/manual_buzz/{team}")
    async def api_manual_buzz(team: int, source: str = "host"):
        # Manual override: if a button fails mid-game the host can still
        # register a buzz for that team by hand (spec 5, keyboard 1/2). Also
        # used by the /buzz/{team} phone fallback page (source=phone), which
        # passes no kernel edge, just this server-side receipt timestamp.
        entry = game.buzz(team, tick_ns=time.monotonic_ns())
        if entry is not None:
            log_buzz(entry, source)
            await broadcast_test_event(entry, source)
        await broadcast_state()
        return game.snapshot(now_tick=time.monotonic_ns())

    if is_mock:

        @app.post("/dev/buzz/{team}")
        async def dev_buzz(team: int):
            backend.trigger(team)
            return {"ok": True}

    app.state.game = game
    app.state.backend = backend
    return app


app = create_app()
