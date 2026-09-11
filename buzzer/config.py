"""Env-driven settings. No I/O beyond reading environment variables."""
import os


def _int_env(name: str, default: int) -> int:
    val = os.environ.get(name)
    return int(val) if val else default


BUZZER_BACKEND: str = os.environ.get("BUZZER_BACKEND", "mock")

DEBOUNCE_MS: int = _int_env("DEBOUNCE_MS", 20)
FALSE_START_LOCKOUT_MS: int = _int_env("FALSE_START_LOCKOUT_MS", 250)

TEAM_A_GPIO: int = _int_env("TEAM_A_GPIO", 17)
TEAM_B_GPIO: int = _int_env("TEAM_B_GPIO", 27)

GAME_CONTENT_PATH: str = os.environ.get(
    "GAME_CONTENT_PATH",
    os.path.join(os.path.dirname(__file__), "content", "game.json"),
)

HOST: str = os.environ.get("BUZZER_HOST", "0.0.0.0")
PORT: int = _int_env("BUZZER_PORT", 8000)

# How often to send a full snapshot to every client regardless of whether
# anything changed -- lets clients detect a silently-dead WebSocket
# (Wi-Fi power-save, an idle NAT/AP timeout) that never fires onclose.
HEARTBEAT_INTERVAL_S: int = _int_env("HEARTBEAT_INTERVAL_S", 15)
