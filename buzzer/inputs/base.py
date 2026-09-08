"""The hardware input interface. Every backend implements this."""
from abc import ABC, abstractmethod
from typing import Callable


class BuzzerInput(ABC):
    @abstractmethod
    def start(self, on_buzz: Callable[[int, int], None]) -> None:
        """Start listening for buzzes.

        on_buzz(team_index, tick_ns) is called from a background thread,
        once per accepted edge. tick_ns must be a monotonic-clock
        nanosecond timestamp captured as close to the physical edge as
        possible.
        """

    @abstractmethod
    def stop(self) -> None:
        """Stop listening and release any resources."""

    @abstractmethod
    def self_test(self) -> dict:
        """Return {team_index: True} if the line reads healthy (HIGH/idle)
        at rest, False if it reads low (stuck button or shorted cable)."""
