"""Real GPIO input backend, using lgpio kernel edge timestamps.

Deliberately the smallest, dumbest piece of this project: translate an
lgpio callback into on_buzz(team, tick_ns) and nothing else. All the game
logic lives in game.py, not here.

lgpio is imported lazily inside the class (never at module top level) so
this package imports cleanly on a laptop that doesn't have it installed.
Only exercised for real on the Raspberry Pi -- see PI_SETUP.md.
"""
from typing import Callable, Optional

from .base import BuzzerInput


class GpioBackend(BuzzerInput):
    def __init__(self, team_pins: dict, chip: int = 0):
        """team_pins: {team_index: bcm_gpio_number}, e.g. {0: 17, 1: 27}."""
        self._team_pins = dict(team_pins)
        self._pin_teams = {pin: team for team, pin in self._team_pins.items()}
        self._chip_num = chip
        self._handle = None
        self._callbacks: list = []
        self._on_buzz: Optional[Callable[[int, int], None]] = None

    def start(self, on_buzz: Callable[[int, int], None]) -> None:
        import lgpio

        self._on_buzz = on_buzz
        self._handle = lgpio.gpiochip_open(self._chip_num)

        for team, pin in self._team_pins.items():
            lgpio.gpio_claim_alert(self._handle, pin, lgpio.FALLING_EDGE)
            cb = lgpio.callback(self._handle, pin, lgpio.FALLING_EDGE, self._make_handler(pin))
            self._callbacks.append(cb)

    def _make_handler(self, pin: int):
        def handler(chip, gpio, level, tick):
            # tick is nanoseconds, captured by the kernel at the edge --
            # never substitute a Python-side timestamp here (spec 2.1).
            team = self._pin_teams.get(gpio)
            if team is not None and self._on_buzz is not None:
                self._on_buzz(team, tick)

        return handler

    def stop(self) -> None:
        import lgpio

        for cb in self._callbacks:
            cb.cancel()
        self._callbacks = []
        if self._handle is not None:
            lgpio.gpiochip_close(self._handle)
            self._handle = None
        self._on_buzz = None

    def self_test(self) -> dict:
        import lgpio

        handle = self._handle
        opened_here = False
        if handle is None:
            handle = lgpio.gpiochip_open(self._chip_num)
            opened_here = True

        result = {}
        try:
            for team, pin in self._team_pins.items():
                if self._handle is None:
                    lgpio.gpio_claim_input(handle, pin)
                level = lgpio.gpio_read(handle, pin)
                result[team] = level == 1  # HIGH at rest is healthy
        finally:
            if opened_here:
                lgpio.gpiochip_close(handle)

        return result
