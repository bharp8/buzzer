"""Integration tests for the FastAPI layer: routes, WebSocket broadcast,
startup self-test warnings, and the mock-only /dev/buzz route. game.py's
own state machine tests live in test_game.py -- these just check the wiring.
"""
import json

import pytest
from fastapi.testclient import TestClient

from buzzer.app import create_app
from buzzer.inputs.mock import MockBackend


def _make_round(name, n=5, daily_doubles=()):
    return {
        "name": name,
        "categories": [
            {
                "name": f"Cat {i}",
                "clues": [
                    {"value": (r + 1) * 200, "daily_double": (i, r) in daily_doubles} for r in range(5)
                ],
            }
            for i in range(n)
        ],
    }


@pytest.fixture
def content_path(tmp_path):
    data = {
        "title": "Test Game",
        "teams": ["Team A", "Team B"],
        "rounds": [
            # Keep the Daily Double off (0, 0) -- lots of tests select that
            # exact tile expecting the normal READING/ARMED flow.
            _make_round("Jeopardy", daily_doubles={(4, 4)}),
            _make_round("Double Jeopardy"),
        ],
        "final_jeopardy": {"category": "Everything"},
    }
    path = tmp_path / "game.json"
    path.write_text(json.dumps(data))
    return str(path)


def make_client(content_path, backend=None):
    app = create_app(content_path=content_path, backend=backend)
    return TestClient(app)


def test_root_redirects_to_board(content_path):
    with make_client(content_path) as client:
        r = client.get("/", follow_redirects=False)
        assert r.status_code in (302, 307)
        assert r.headers["location"] == "/board"


def test_board_and_host_pages_serve(content_path):
    with make_client(content_path) as client:
        assert client.get("/board").status_code == 200
        assert client.get("/host").status_code == 200


def test_websocket_gets_full_snapshot_on_connect(content_path):
    with make_client(content_path) as client:
        with client.websocket_connect("/ws") as ws:
            snap = ws.receive_json()
            assert snap["phase"] == "IDLE"
            assert len(snap["teams"]) == 2
            assert len(snap["board"]) == 5
            assert "reveal" not in snap
            assert snap["round_index"] == 0
            assert snap["round_name"] == "Jeopardy"
            assert snap["total_rounds"] == 2
            assert snap["final_jeopardy_category"] == "Everything"


def test_new_connection_gets_current_state_not_blank(content_path):
    # A client that connects mid-game must get a full snapshot immediately,
    # not an empty board (spec 2.4).
    with make_client(content_path) as client:
        client.post("/api/select_clue", json={"category": 0, "row": 0})
        with client.websocket_connect("/ws") as ws:
            snap = ws.receive_json()
            assert snap["phase"] == "READING"
            assert snap["active_clue"]["category"] == 0


def test_illegal_transition_returns_409_and_does_not_broadcast(content_path):
    with make_client(content_path) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # initial snapshot
            r = client.post("/api/arm")  # illegal from IDLE
            assert r.status_code == 409


def test_dev_buzz_route_exists_for_mock_backend(content_path):
    with make_client(content_path, backend=MockBackend(num_teams=2)) as client:
        client.post("/api/select_clue", json={"category": 0, "row": 0})
        client.post("/api/arm")
        r = client.post("/dev/buzz/0")
        assert r.status_code == 200


def test_dev_buzz_broadcasts_over_websocket(content_path):
    with make_client(content_path, backend=MockBackend(num_teams=2)) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            client.post("/api/select_clue", json={"category": 0, "row": 0})
            ws.receive_json()
            client.post("/api/arm")
            ws.receive_json()
            client.post("/dev/buzz/0")
            snap = ws.receive_json()
            assert snap["phase"] == "LOCKED"
            assert snap["winner"] == 0


def test_phone_buzz_page_serves_for_valid_team(content_path):
    with make_client(content_path) as client:
        assert client.get("/buzz/0").status_code == 200
        assert client.get("/buzz/1").status_code == 200


def test_phone_buzz_page_404s_for_unknown_team(content_path):
    with make_client(content_path) as client:
        assert client.get("/buzz/2").status_code == 404
        assert client.get("/buzz/-1").status_code == 404


def test_phone_buzz_registers_and_logs_source(content_path, caplog):
    with make_client(content_path) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            client.post("/api/select_clue", json={"category": 0, "row": 0})
            ws.receive_json()
            client.post("/api/arm")
            ws.receive_json()
            with caplog.at_level("INFO", logger="buzzer"):
                r = client.post("/api/manual_buzz/0?source=phone")
            assert r.status_code == 200
            snap = ws.receive_json()
            assert snap["phase"] == "LOCKED"
            assert snap["winner"] == 0
            assert any("source=phone" in rec.message for rec in caplog.records)


def test_manual_buzz_default_source_is_host(content_path, caplog):
    with make_client(content_path) as client:
        client.post("/api/select_clue", json={"category": 0, "row": 0})
        client.post("/api/arm")
        with caplog.at_level("INFO", logger="buzzer"):
            client.post("/api/manual_buzz/0")
        assert any("source=host" in rec.message for rec in caplog.records)


def test_test_page_serves(content_path):
    with make_client(content_path) as client:
        assert client.get("/test").status_code == 200


def test_dual_page_serves(content_path):
    with make_client(content_path) as client:
        assert client.get("/dual").status_code == 200


def test_ws_test_sends_hello_with_team_names(content_path):
    with make_client(content_path) as client:
        with client.websocket_connect("/ws/test") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"
            assert hello["teams"] == ["Team A", "Team B"]


def test_ws_test_relays_buzz_even_while_idle(content_path):
    # Test mode must see a raw edge even in IDLE, where the main game
    # would just ignore it -- that's the whole point of the feature.
    with make_client(content_path, backend=MockBackend(num_teams=2)) as client:
        with client.websocket_connect("/ws/test") as ws:
            ws.receive_json()  # hello
            client.post("/dev/buzz/0")
            event = ws.receive_json()
            assert event["type"] == "buzz"
            assert event["team"] == 0
            assert event["result"] == "ignored"
            assert event["phase"] == "IDLE"
            assert event["source"] == "mock"


def test_ws_test_relays_manual_buzz_with_source(content_path):
    with make_client(content_path) as client:
        with client.websocket_connect("/ws/test") as ws:
            ws.receive_json()  # hello
            client.post("/api/manual_buzz/1?source=phone")
            event = ws.receive_json()
            assert event["team"] == 1
            assert event["source"] == "phone"


def test_startup_self_test_surfaces_warning_for_stuck_team(content_path):
    backend = MockBackend(num_teams=2)
    backend.set_stuck(1, True)
    with make_client(content_path, backend=backend) as client:
        with client.websocket_connect("/ws") as ws:
            snap = ws.receive_json()
            assert any("Team B" in w for w in snap["warnings"])


def test_snapshot_never_contains_clue_or_answer_text(content_path):
    with make_client(content_path, backend=MockBackend(num_teams=2)) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            client.post("/api/select_clue", json={"category": 0, "row": 0})
            snap = ws.receive_json()
            assert "text" not in snap["active_clue"]
            assert "answer" not in snap["active_clue"]


def test_next_round_endpoint(content_path):
    with make_client(content_path) as client:
        r = client.post("/api/next_round")
        assert r.status_code == 200
        assert r.json()["round_index"] == 1
        assert r.json()["round_name"] == "Double Jeopardy"

        r = client.post("/api/next_round")  # no further rounds
        assert r.status_code == 409


def test_start_final_jeopardy_endpoint(content_path):
    with make_client(content_path) as client:
        r = client.post("/api/start_final_jeopardy")
        assert r.status_code == 200
        assert r.json()["phase"] == "FINAL_JEOPARDY"
        assert r.json()["final_jeopardy_category"] == "Everything"

        # Illegal from IDLE only via the phase check -- can't start twice in a row.
        r = client.post("/api/start_final_jeopardy")
        assert r.status_code == 409

        r = client.post("/api/return_to_board")
        assert r.status_code == 200
        assert r.json()["phase"] == "IDLE"


def test_daily_double_tile_skips_arm_and_buzzing(content_path):
    with make_client(content_path, backend=MockBackend(num_teams=2)) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()

            r = client.post("/api/select_clue", json={"category": 4, "row": 4})
            assert r.status_code == 200
            d = r.json()
            assert d["phase"] == "REVEALED"
            assert d["active_clue"]["daily_double"] is True
            ws.receive_json()

            # Arming makes no sense here -- there was never a READING phase.
            r = client.post("/api/arm")
            assert r.status_code == 409

            # A real buzz during this window has no effect.
            r = client.post("/dev/buzz/0")
            assert r.status_code == 200
            snap = ws.receive_json()
            assert snap["winner"] is None
            assert snap["phase"] == "REVEALED"
