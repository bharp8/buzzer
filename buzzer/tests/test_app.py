"""Integration tests for the FastAPI layer: routes, WebSocket broadcast,
startup self-test warnings, and the mock-only /dev/buzz route. game.py's
own state machine tests live in test_game.py -- these just check the wiring.
"""
import json

import pytest
from fastapi.testclient import TestClient

from buzzer.app import create_app
from buzzer.inputs.mock import MockBackend


@pytest.fixture
def content_path(tmp_path):
    data = {
        "title": "Test Game",
        "teams": ["Team A", "Team B"],
        "categories": [
            {
                "name": f"Cat {i}",
                "clues": [
                    {"value": (r + 1) * 200, "clue": f"clue {i}-{r}", "answer": f"answer {i}-{r}"}
                    for r in range(5)
                ],
            }
            for i in range(5)
        ],
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
            assert snap["reveal"] is None


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


def test_snapshot_never_includes_answer_before_reveal(content_path):
    with make_client(content_path, backend=MockBackend(num_teams=2)) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            client.post("/api/select_clue", json={"category": 0, "row": 0})
            snap = ws.receive_json()
            assert "answer" not in json.dumps(snap["active_clue"])
