"""HTTP-triggered fake input backend, for dev and tests without hardware."""
import time
from typing import Callable, Optional

from .base import BuzzerInput


class MockBackend(BuzzerInput):
    def __init__(self, num_teams: int = 2):
        self._num_teams = num_teams
        self._on_buzz: Optional[Callable[[int, int], None]] = None
        self._stuck: set = set()

    def start(self, on_buzz: Callable[[int, int], None]) -> None:
        self._on_buzz = on_buzz

    def stop(self) -> None:
        self._on_buzz = None

    def self_test(self) -> dict:
        return {team: team not in self._stuck for team in range(self._num_teams)}

    def set_stuck(self, team: int, stuck: bool = True) -> None:
        """Test/dev helper: simulate a stuck button on this team's line."""
        if stuck:
            self._stuck.add(team)
        else:
            self._stuck.discard(team)

    def trigger(self, team: int) -> int:
        """Simulate a press, as if a kernel edge just arrived. Returns the
        tick used (for callers that want it)."""
        if self._on_buzz is None:
            raise RuntimeError("mock backend not started")
        tick = time.monotonic_ns()
        self._on_buzz(team, tick)
        return tick
