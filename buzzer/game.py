"""Pure Jeopardy buzzer state machine.

No FastAPI imports, no GPIO imports, no file or network I/O, no calls to the
clock. Every method that needs "now" takes it as an explicit tick (ns)
argument from the caller, so behaviour is fully deterministic and testable.

No clue or answer text lives anywhere in this module (or the content file):
the host reads both from paper. The system only ever needs a category name,
a dollar value, and whether a clue is a Daily Double.
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
    FINAL_JEOPARDY = "FINAL_JEOPARDY"


class IllegalTransitionError(Exception):
    """Raised when a host action is invalid for the current phase.

    Raised before any state is mutated -- callers can rely on the game
    being untouched when this is raised.
    """


@dataclass
class Clue:
    value: int
    used: bool = False
    daily_double: bool = False


@dataclass
class Category:
    name: str
    clues: list


@dataclass
class Round:
    name: str
    categories: list


@dataclass
class Team:
    name: str
    score: int = 0


@dataclass
class ActiveClue:
    category: int
    row: int
    value: int
    daily_double: bool = False


def build_content(data: dict):
    """Validate already-parsed game content and build (teams, rounds,
    final_jeopardy_category).

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

    rounds_raw = data.get("rounds")
    if not isinstance(rounds_raw, list) or not rounds_raw:
        raise ValueError("game content must have a non-empty 'rounds' list")

    rounds = []
    for ri, round_data in enumerate(rounds_raw):
        if not isinstance(round_data, dict) or "name" not in round_data or "categories" not in round_data:
            raise ValueError(f"round {ri} must have a 'name' and 'categories'")
        categories_raw = round_data["categories"]
        if not isinstance(categories_raw, list) or not (5 <= len(categories_raw) <= 6):
            raise ValueError(f"round {ri} ('{round_data.get('name')}') must have 5 or 6 categories")
        categories = []
        for ci, cat in enumerate(categories_raw):
            if not isinstance(cat, dict) or "name" not in cat or "clues" not in cat:
                raise ValueError(f"round {ri} category {ci} must have a 'name' and 'clues'")
            clues_raw = cat["clues"]
            if not isinstance(clues_raw, list) or len(clues_raw) != 5:
                raise ValueError(
                    f"round {ri} category {ci} ('{cat.get('name')}') must have exactly 5 clues"
                )
            clues = []
            for cli, clue in enumerate(clues_raw):
                if not isinstance(clue, dict) or "value" not in clue:
                    raise ValueError(f"round {ri} category {ci} clue {cli} missing 'value'")
                clues.append(
                    Clue(value=int(clue["value"]), daily_double=bool(clue.get("daily_double", False)))
                )
            categories.append(Category(name=str(cat["name"]), clues=clues))
        rounds.append(Round(name=str(round_data["name"]), categories=categories))

    final_jeopardy_raw = data.get("final_jeopardy")
    if not isinstance(final_jeopardy_raw, dict) or "category" not in final_jeopardy_raw:
        raise ValueError("game content must have a 'final_jeopardy' object with a 'category'")
    final_jeopardy_category = str(final_jeopardy_raw["category"])

    return teams, rounds, final_jeopardy_category


class Game:
    """Holds all game state and enforces the buzzer state machine.

    Thread-safe: `buzz()` is called from a GPIO callback thread while host
    actions arrive from HTTP handler threads. Every state read/mutation
    happens under `self._lock`.
    """

    def __init__(
        self,
        teams,
        rounds,
        final_jeopardy_category: str,
        debounce_ms: int = 20,
        false_start_lockout_ms: int = 250,
    ):
        self._lock = threading.Lock()
        self._teams = list(teams)
        self._rounds = list(rounds)
        self._round_index = 0
        self._final_jeopardy_category = final_jeopardy_category
        self._debounce_ns = debounce_ms * 1_000_000
        self._false_start_lockout_ns = false_start_lockout_ms * 1_000_000

        self.phase: Phase = Phase.IDLE
        self.active_clue: Optional[ActiveClue] = None
        self.winner: Optional[int] = None
        self.winner_tick: Optional[int] = None
        self.warnings: list = []

        self._already_answered: set = set()
        self._false_started: set = set()
        self._locked_out_until: dict = {}
        self._last_edge_tick: dict = {}

        self.buzz_log: list = []

    @property
    def _categories(self):
        return self._rounds[self._round_index].categories

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
                category=category, row=row, value=clue.value, daily_double=clue.daily_double
            )
            self.winner = None
            self.winner_tick = None
            self._already_answered = set()
            self._false_started = set()
            self._locked_out_until = {}
            if clue.daily_double:
                # No buzzer race for a Daily Double: the host picks every
                # tile (not a team), so there's no "team that found it" to
                # hand it to automatically. Skip straight to REVEALED --
                # the host picks who attempts it and applies the result
                # with adjust_score, same as everything else paper-based.
                self.phase = Phase.REVEALED
            else:
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
            self.phase = Phase.REVEALED

    def mark_incorrect(self, tick_ns: int) -> None:
        # No score penalty -- a wrong answer just passes the clue on.
        with self._lock:
            if self.phase is not Phase.LOCKED:
                raise IllegalTransitionError(f"cannot mark incorrect from {self.phase}")
            self._already_answered.add(self.winner)
            self.winner = None
            self.winner_tick = None

            remaining = [i for i in range(len(self._teams)) if i not in self._already_answered]
            if not remaining:
                self.phase = Phase.REVEALED
            elif len(remaining) == 1:
                # Exactly one team left with a default two-team game: there's
                # no one to race against, so hand it to them directly rather
                # than re-arming and making them buzz for an empty contest.
                self.phase = Phase.LOCKED
                self.winner = remaining[0]
                self.winner_tick = tick_ns
            else:
                # More than two teams and more than one still eligible --
                # no single team to auto-select, so they compete for it.
                self.phase = Phase.ARMED

    def reveal(self, tick_ns: int) -> None:
        with self._lock:
            if self.phase not in (Phase.READING, Phase.ARMED, Phase.LOCKED):
                raise IllegalTransitionError(f"cannot reveal from {self.phase}")
            self.phase = Phase.REVEALED

    def return_to_board(self, tick_ns: int) -> None:
        with self._lock:
            if self.phase not in (Phase.REVEALED, Phase.FINAL_JEOPARDY):
                raise IllegalTransitionError(f"cannot return to board from {self.phase}")
            self.active_clue = None
            self.winner = None
            self.winner_tick = None
            self.phase = Phase.IDLE

    def next_round(self, tick_ns: int) -> None:
        with self._lock:
            if self.phase is not Phase.IDLE:
                raise IllegalTransitionError(f"cannot advance round from {self.phase}")
            if self._round_index >= len(self._rounds) - 1:
                raise IllegalTransitionError("no further rounds")
            self._round_index += 1

    def start_final_jeopardy(self, tick_ns: int) -> None:
        with self._lock:
            if self.phase is not Phase.IDLE:
                raise IllegalTransitionError(f"cannot start final jeopardy from {self.phase}")
            self.phase = Phase.FINAL_JEOPARDY

    def adjust_score(self, team: int, delta: int) -> None:
        with self._lock:
            if not (0 <= team < len(self._teams)):
                raise IllegalTransitionError("team index out of range")
            self._teams[team].score += delta

    def reset_game(self) -> None:
        with self._lock:
            for round_ in self._rounds:
                for cat in round_.categories:
                    for clue in cat.clues:
                        clue.used = False
            for team in self._teams:
                team.score = 0
            self._round_index = 0
            self.phase = Phase.IDLE
            self.active_clue = None
            self.winner = None
            self.winner_tick = None
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

            # IDLE, REVEALED, FINAL_JEOPARDY -- no buzzer race in any of these
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
                    "daily_double": self.active_clue.daily_double,
                }

            return {
                "phase": self.phase.value,
                "teams": teams,
                "board": board,
                "round_index": self._round_index,
                "round_name": self._rounds[self._round_index].name,
                "total_rounds": len(self._rounds),
                "final_jeopardy_category": self._final_jeopardy_category,
                "active_clue": active_clue,
                "winner": self.winner,
                "warnings": list(self.warnings),
            }
