"""Pure Jeopardy buzzer state machine.

No FastAPI imports, no GPIO imports, no file or network I/O, no calls to the
clock. Every method that needs "now" takes it as an explicit tick (ns)
argument from the caller, so behaviour is fully deterministic and testable.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Phase(str, Enum):
    IDLE = "IDLE"
    READING = "READING"
    ARMED = "ARMED"
    LOCKED = "LOCKED"
    REVEALED = "REVEALED"


class IllegalTransitionError(Exception):
    """Raised when a host action is invalid for the current phase.

    Raised before any state is mutated -- callers can rely on the game
    being untouched when this is raised.
    """


@dataclass
class Clue:
    value: int
    text: str
    answer: str
    used: bool = False


@dataclass
class Category:
    name: str
    clues: list


@dataclass
class Team:
    name: str
    score: int = 0


@dataclass
class ActiveClue:
    category: int
    row: int
    value: int
    text: str
    answer: str


def build_categories(data: dict):
    """Validate already-parsed game content and build (teams, categories).

    Pure: takes a dict in, does no file I/O itself. Raises ValueError with
    a descriptive message on any structural problem so a malformed content
    file fails loudly at load time rather than mid-game.
    """
    if not isinstance(data, dict):
        raise ValueError("game content must be a JSON object")

    teams_raw = data.get("teams")
    if not isinstance(teams_raw, list) or len(teams_raw) < 2:
        raise ValueError("game content must have a 'teams' list of at least 2 names")
    teams = [Team(name=str(n)) for n in teams_raw]

    categories_raw = data.get("categories")
    if not isinstance(categories_raw, list) or not (5 <= len(categories_raw) <= 6):
        raise ValueError("game content must have 5 or 6 'categories'")

    categories = []
    for ci, cat in enumerate(categories_raw):
        if not isinstance(cat, dict) or "name" not in cat or "clues" not in cat:
            raise ValueError(f"category {ci} must have a 'name' and 'clues'")
        clues_raw = cat["clues"]
        if not isinstance(clues_raw, list) or len(clues_raw) != 5:
            raise ValueError(f"category {ci} ('{cat.get('name')}') must have exactly 5 clues")
        clues = []
        for ri, clue in enumerate(clues_raw):
            if not isinstance(clue, dict):
                raise ValueError(f"category {ci} clue {ri} must be an object")
            for k in ("value", "clue", "answer"):
                if k not in clue:
                    raise ValueError(f"category {ci} clue {ri} missing '{k}'")
            clues.append(
                Clue(value=int(clue["value"]), text=str(clue["clue"]), answer=str(clue["answer"]))
            )
        categories.append(Category(name=str(cat["name"]), clues=clues))

    return teams, categories


class Game:
    """Holds all game state and enforces the buzzer state machine.

    Thread-safe: `buzz()` is called from a GPIO callback thread while host
    actions arrive from HTTP handler threads. Every state read/mutation
    happens under `self._lock`.
    """

    def __init__(self, teams, categories, debounce_ms: int = 20, false_start_lockout_ms: int = 250):
        self._lock = threading.Lock()
        self._teams = list(teams)
        self._categories = list(categories)
        self._debounce_ns = debounce_ms * 1_000_000
        self._false_start_lockout_ns = false_start_lockout_ms * 1_000_000

        self.phase: Phase = Phase.IDLE
        self.active_clue: Optional[ActiveClue] = None
        self.winner: Optional[int] = None
        self.winner_tick: Optional[int] = None
        self.reveal_text: Optional[str] = None
        self.warnings: list = []

        self._already_answered: set = set()
        self._false_started: set = set()
        self._locked_out_until: dict = {}
        self._last_edge_tick: dict = {}

        self.buzz_log: list = []

    # ---- host actions ----

    def select_clue(self, category: int, row: int, tick_ns: int) -> None:
        with self._lock:
            if self.phase not in (Phase.IDLE, Phase.REVEALED):
                raise IllegalTransitionError(f"cannot select a clue from {self.phase}")
            if not (0 <= category < len(self._categories)):
                raise IllegalTransitionError("category index out of range")
            cat = self._categories[category]
            if not (0 <= row < len(cat.clues)):
                raise IllegalTransitionError("row index out of range")
            clue = cat.clues[row]
            if clue.used:
                raise IllegalTransitionError("clue already used")

            clue.used = True
            self.active_clue = ActiveClue(
                category=category, row=row, value=clue.value, text=clue.text, answer=clue.answer
            )
            self.winner = None
            self.winner_tick = None
            self.reveal_text = None
            self._already_answered = set()
            self._false_started = set()
            self._locked_out_until = {}
            self.phase = Phase.READING

    def arm(self, tick_ns: int) -> None:
        with self._lock:
            if self.phase is not Phase.READING:
                raise IllegalTransitionError(f"cannot arm from {self.phase}")
            for team in self._false_started:
                self._locked_out_until[team] = tick_ns + self._false_start_lockout_ns
            self._false_started = set()
            self.phase = Phase.ARMED

    def mark_correct(self, tick_ns: int) -> None:
        with self._lock:
            if self.phase is not Phase.LOCKED:
                raise IllegalTransitionError(f"cannot mark correct from {self.phase}")
            self._teams[self.winner].score += self.active_clue.value
            self.reveal_text = self.active_clue.answer
            self.phase = Phase.REVEALED

    def mark_incorrect(self, tick_ns: int) -> None:
        with self._lock:
            if self.phase is not Phase.LOCKED:
                raise IllegalTransitionError(f"cannot mark incorrect from {self.phase}")
            self._teams[self.winner].score -= self.active_clue.value
            self._already_answered.add(self.winner)
            self.winner = None
            self.winner_tick = None
            if len(self._already_answered) >= len(self._teams):
                self.reveal_text = self.active_clue.answer
                self.phase = Phase.REVEALED
            else:
                self.phase = Phase.ARMED

    def reveal(self, tick_ns: int) -> None:
        with self._lock:
            if self.phase not in (Phase.READING, Phase.ARMED, Phase.LOCKED):
                raise IllegalTransitionError(f"cannot reveal from {self.phase}")
            self.reveal_text = self.active_clue.answer
            self.phase = Phase.REVEALED

    def return_to_board(self, tick_ns: int) -> None:
        with self._lock:
            if self.phase is not Phase.REVEALED:
                raise IllegalTransitionError(f"cannot return to board from {self.phase}")
            self.active_clue = None
            self.winner = None
            self.winner_tick = None
            self.reveal_text = None
            self.phase = Phase.IDLE

    def adjust_score(self, team: int, delta: int) -> None:
        with self._lock:
            if not (0 <= team < len(self._teams)):
                raise IllegalTransitionError("team index out of range")
            self._teams[team].score += delta

    def reset_game(self) -> None:
        with self._lock:
            for cat in self._categories:
                for clue in cat.clues:
                    clue.used = False
            for team in self._teams:
                team.score = 0
            self.phase = Phase.IDLE
            self.active_clue = None
            self.winner = None
            self.winner_tick = None
            self.reveal_text = None
            self._already_answered = set()
            self._false_started = set()
            self._locked_out_until = {}
            self._last_edge_tick = {}
            self.buzz_log = []

    def set_warnings(self, warnings) -> None:
        with self._lock:
            self.warnings = list(warnings)

    # ---- buzz handling ----
    # Called from the GPIO callback thread (real presses) or from a host
    # manual-override endpoint (button failure fallback). Latch the winner
    # under the lock before doing anything else -- see spec 2.3.

    def buzz(self, team: int, tick_ns: int):
        """Returns the log entry this edge produced, or None if it was
        filtered entirely (out-of-range team, or debounced). Callers that
        need to know exactly what this specific call did (logging,
        broadcasting to /ws/test) must use this return value rather than
        re-reading buzz_log[-1] afterwards -- another thread's edge can be
        appended in between, on the real GPIO path where multiple pins'
        callbacks run concurrently.
        """
        with self._lock:
            if not (0 <= team < len(self._teams)):
                return None

            # Per-pin debounce: only ever filters a *repeat* edge on the same
            # pin. It never delays the first edge, so it can't add latency
            # ahead of the winner comparison below.
            last = self._last_edge_tick.get(team)
            if last is not None and tick_ns - last < self._debounce_ns:
                return None
            self._last_edge_tick[team] = tick_ns

            if self.phase is Phase.READING:
                self._false_started.add(team)
                entry = {"team": team, "tick": tick_ns, "phase": self.phase.value, "result": "false_start"}
                self.buzz_log.append(entry)
                return entry

            if self.phase is Phase.ARMED:
                if self._locked_out_until.get(team, 0) > tick_ns:
                    entry = {"team": team, "tick": tick_ns, "phase": self.phase.value, "result": "locked_out"}
                    self.buzz_log.append(entry)
                    return entry
                if team in self._already_answered:
                    entry = {
                        "team": team,
                        "tick": tick_ns,
                        "phase": self.phase.value,
                        "result": "already_answered",
                    }
                    self.buzz_log.append(entry)
                    return entry
                self.phase = Phase.LOCKED
                self.winner = team
                self.winner_tick = tick_ns
                entry = {"team": team, "tick": tick_ns, "phase": Phase.LOCKED.value, "result": "latched"}
                self.buzz_log.append(entry)
                return entry

            if self.phase is Phase.LOCKED:
                # A different team's edge that actually happened earlier
                # (by kernel tick) always wins, no matter which callback
                # was dispatched first. This is the fairness guarantee the
                # whole design exists to protect -- see spec 2.1/2.3.
                if team != self.winner and self.winner_tick is not None and tick_ns < self.winner_tick:
                    self.winner = team
                    self.winner_tick = tick_ns
                    entry = {"team": team, "tick": tick_ns, "phase": self.phase.value, "result": "correction"}
                else:
                    entry = {"team": team, "tick": tick_ns, "phase": self.phase.value, "result": "ignored"}
                self.buzz_log.append(entry)
                return entry

            # IDLE, REVEALED
            entry = {"team": team, "tick": tick_ns, "phase": self.phase.value, "result": "ignored"}
            self.buzz_log.append(entry)
            return entry

    # ---- snapshot ----

    def snapshot(self, now_tick: int) -> dict:
        with self._lock:
            teams = []
            for idx, team in enumerate(self._teams):
                locked_out = (self._locked_out_until.get(idx, 0) > now_tick) or (
                    idx in self._already_answered
                )
                teams.append({"name": team.name, "score": team.score, "locked_out": locked_out})

            board = []
            for cat in self._categories:
                board.append(
                    {
                        "category": cat.name,
                        "clues": [{"value": c.value, "used": c.used} for c in cat.clues],
                    }
                )

            active_clue = None
            if self.active_clue is not None:
                active_clue = {
                    "category": self.active_clue.category,
                    "row": self.active_clue.row,
                    "value": self.active_clue.value,
                    "text": self.active_clue.text,
                }

            return {
                "phase": self.phase.value,
                "teams": teams,
                "board": board,
                "active_clue": active_clue,
                "winner": self.winner,
                "reveal": self.reveal_text if self.phase is Phase.REVEALED else None,
                "warnings": list(self.warnings),
            }
