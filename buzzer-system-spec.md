# Jeopardy Buzzer System — Implementation Spec

Target implementer: an agentic coding model with a shell, no Raspberry Pi attached.

Everything here must be runnable and testable on a normal laptop. Hardware access is
behind an interface with a mock implementation. **Do not write code that can only be
run on a Pi.**

---

## 1. What this is

A two-team Jeopardy buzzer system. Five physical buttons per team, wired in parallel,
so the system knows *which team* buzzed, never *which player*. A Raspberry Pi 4 reads
the buttons over GPIO, runs a game server, and serves two web pages: a game board for
a TV or laptop, and a host control panel for a laptop or phone.

The core requirement is **lockout**: once one team buzzes, further buzzes are ignored
until the host resets.

### Hardware context

- Raspberry Pi 4, Raspberry Pi OS Bookworm, `gpiochip0`
- Team A → `GPIO17` (header pin 11), Team B → `GPIO27` (header pin 13)
- Both lines **active-low**: external 4.7 kΩ pull-up to 3.3 V, buttons short to ground
- 100 nF from each line to ground (RC filter, ~100 µs)
- Idle state is HIGH. A press is a **falling edge**.
- The Pi runs a Wi-Fi AP (`10.42.0.1`) or connects over ethernet. No internet at runtime.

---

## 2. Non-negotiable technical requirements

These are the things that are easy to get wrong and expensive to get wrong. Treat them
as acceptance criteria.

### 2.1 Use kernel edge timestamps, not Python-side timestamps

Use `lgpio` alerts. Do **not** use `gpiozero`, `RPi.GPIO` (deprecated, broken on Pi 5),
or a polling loop.

```python
h = lgpio.gpiochip_open(0)
lgpio.gpio_claim_alert(h, pin, lgpio.FALLING_EDGE)
cb = lgpio.callback(h, pin, lgpio.FALLING_EDGE, handler)
# handler(chip, gpio, level, tick) — tick is nanoseconds, kernel monotonic clock
```

The `tick` value is captured by the kernel at the edge. Use it as the buzz timestamp.
Never call `time.monotonic_ns()` inside the handler and use *that* as the buzz time —
it includes Python dispatch jitter, which is exactly what we are trying to exclude.

A polling loop is forbidden because scanning pins in a fixed order gives whichever team
is checked first a systematic advantage.

### 2.2 Never use `time.time()`

`time.monotonic_ns()` everywhere for durations and comparisons. An NTP step mid-game
must not be able to produce a negative interval.

### 2.3 Latch under a lock, before debouncing

The GPIO callback runs on a background thread while HTTP/WebSocket handlers run
elsewhere. The check-then-set is a race and the GIL does not protect it.

```python
with self._lock:
    if self.phase is not Phase.ARMED:
        return
    if team in self.locked_out:
        return
    self.phase = Phase.LOCKED
    self.winner = team
    self.winner_tick = tick
```

Capture the winner **first**, then apply debounce. Debouncing before latching adds an
arbitrary delay ahead of the comparison and throws away the resolution the whole design
exists to protect.

Debounce is per-pin: ignore a falling edge if it is within `DEBOUNCE_MS` (default 20 ms)
of the previous accepted edge on that same pin.

### 2.4 Broadcast full snapshots to every client

Game state is tiny — two scores and a 6×5 grid of booleans. Do **not** implement deltas.
On every state change, serialise the entire game state and send it to all connected
WebSocket clients. On a new connection, send the same snapshot immediately, before
anything else.

The failure this prevents: the TV dies mid-game, the host opens the board on a laptop,
and a delta-only implementation renders a blank board.

### 2.5 Startup self-test

On boot, read both input lines. If either reads LOW at rest, a button is stuck or a
cable is shorted, and that team can never buzz. Log a clear warning naming the team and
surface it on the host panel. Do not crash.

---

## 3. State machine

```
IDLE ──select_clue──> READING ──arm──> ARMED ──buzz──> LOCKED
 ^                       |                ^               |
 |                       |                |          mark_correct
 |                  (early buzz:          |          mark_incorrect
 |                   penalise,            └───────────────┤
 |                   stay in READING)         (incorrect, │
 |                                         teams remain)  │
 └──────────────return_to_board────── REVEALED <──────────┘
```

| Phase | Meaning | Buzzes |
|---|---|---|
| `IDLE` | Board showing, no clue selected | ignored |
| `READING` | Clue displayed, host still reading aloud | **penalised** (false start) |
| `ARMED` | Buzzers live | first one latches |
| `LOCKED` | A team is in, awaiting adjudication | ignored |
| `REVEALED` | Correct answer shown | ignored |

### False start rule

A falling edge during `READING` does not latch. It locks that team out for
`FALSE_START_LOCKOUT_MS` (default 250 ms) measured **from the moment the host arms**.
Track `locked_out_until[team]` as a tick value. Phase stays `READING`.

The board should show a brief visual indication that the team jumped early.

### Incorrect answer re-arm

From `LOCKED`, `mark_incorrect` deducts the clue value from that team's score and
returns to `ARMED` with the wrong team added to a `already_answered` set for this clue.
That team cannot buzz again on this clue. If every team has answered incorrectly,
transition to `REVEALED` instead.

`mark_correct` adds the clue value and transitions to `REVEALED`.

---

## 4. Architecture

```
buzzer/
├── app.py              FastAPI app: routes, WebSocket, broadcast
├── game.py             Phase enum, GameState, transitions — PURE, no I/O
├── inputs/
│   ├── base.py         BuzzerInput ABC
│   ├── gpio.py         lgpio implementation
│   └── mock.py         HTTP-triggered fake, for dev and tests
├── config.py           env-driven settings
├── content/
│   └── game.json       categories, clues, answers, values
├── static/
│   ├── board.html  board.css  board.js
│   └── host.html   host.css   host.js
└── tests/
    └── test_game.py    state machine tests, no hardware
```

### 4.1 The input interface

```python
class BuzzerInput(ABC):
    def start(self, on_buzz: Callable[[int, int], None]) -> None:
        """on_buzz(team_index, tick_ns) — called from a background thread."""
    def stop(self) -> None: ...
    def self_test(self) -> dict[int, bool]:
        """team_index -> True if the line reads healthy (HIGH) at rest."""
```

`BUZZER_BACKEND=gpio|mock` selects the implementation. **Default to `mock`** so the app
runs anywhere. `gpio.py` must import `lgpio` lazily inside the class, never at module
top level, so the package imports cleanly on a laptop.

`MockBackend` exposes `POST /dev/buzz/{team}` and generates its tick with
`time.monotonic_ns()`. Register this route only when the mock backend is active.

### 4.2 `game.py` must be pure

No FastAPI imports, no GPIO imports, no I/O. It takes events and ticks in and produces
state out. This is what makes the state machine testable, and it is where all the
interesting logic lives. Everything in section 3 is tested here.

---

## 5. HTTP and WebSocket API

### Pages
- `GET /` → redirect to `/board`
- `GET /board` → game board
- `GET /host` → host control panel

### WebSocket
- `WS /ws` → on connect, send a full snapshot. Then a full snapshot on every change.

```json
{
  "phase": "ARMED",
  "teams": [
    {"name": "Team A", "score": 1200, "locked_out": false},
    {"name": "Team B", "score": 800,  "locked_out": false}
  ],
  "board": [
    {"category": "Potent Potables",
     "clues": [{"value": 200, "used": true}, {"value": 400, "used": false}]}
  ],
  "active_clue": {"category": 0, "row": 2, "value": 600, "text": "..."},
  "winner": 0,
  "reveal": null,
  "warnings": ["Team B input reads low at rest — check for a stuck button"]
}
```

`reveal` holds the answer text only in the `REVEALED` phase — the board must never
receive an answer it is not currently supposed to display.

### Host actions
`POST /api/select_clue`, `/api/arm`, `/api/mark_correct`, `/api/mark_incorrect`,
`/api/reveal`, `/api/return_to_board`, `/api/adjust_score`, `/api/reset_game`.

Each validates the transition against the current phase and returns 409 on an illegal
one. Never let an invalid transition corrupt state.

### Keyboard shortcuts on `/host`
`space` arm · `1`/`2` manual buzz override · `y` correct · `n` incorrect ·
`r` reveal · `esc` back to board

Manual override matters: if a button fails mid-game the host must be able to keep
playing.

---

## 6. Game content

`content/game.json`, loaded at startup and validated on load. Fail loudly with a useful
message if the shape is wrong — discovering a malformed clue file mid-game is bad.

```json
{
  "title": "...",
  "teams": ["Team A", "Team B"],
  "categories": [
    {"name": "...",
     "clues": [{"value": 200, "clue": "...", "answer": "..."}]}
  ]
}
```

Support 5 or 6 categories and 5 clues each. Out of scope for v1: Daily Doubles,
Final Jeopardy, wagering, more than two teams. Do not build them. Do keep the team list
a list rather than hardcoding two, so adding a third later is a config change.

---

## 7. Front end

Two pages, plain HTML/CSS/JS. No build step, no framework, no CDN — the venue has no
internet. Vendor anything you need into `static/`.

### Board (`/board`)

Read at two very different distances: a 55" TV across a room, and a 14" laptop at desk
range. Size everything in `vw`/`vh` and `clamp()`, never fixed pixels. Test by resizing
the window — if it is unreadable at either extreme, it is wrong.

Three views, driven by phase:
- `IDLE` — the 6×5 grid, used clues visibly spent
- `READING`/`ARMED`/`LOCKED` — the clue, large, filling the screen
- `REVEALED` — the answer

Scores always visible. When a team buzzes, that team's panel must change state
**unmistakably from across a room** — this is the single most important visual moment
in the whole interface. A subtle border colour change is not enough.

Design direction: this is a physical game show being run out of an ammo can. Lean into
the game-show register — heavy, confident, high-contrast, saturated — rather than a
clean SaaS dashboard. Avoid the current generative-design defaults: cream backgrounds
with a terracotta accent, uniform rounded cards with soft grey shadows, tracked-out
all-caps eyebrow labels, monospace for small data. Pick a real display face with weight
to it and set the type large. Spend the boldness on the buzz-in moment and keep
everything else quiet.

Respect `prefers-reduced-motion`.

### Host (`/host`)

Dense, functional, usable one-handed on a phone. Current phase always visible. Buttons
for every action, disabled when the transition is illegal. Score adjustment with manual
+/− for when the host makes a mistake. Connection status indicator — the host needs to
know instantly if the socket has dropped.

### Reconnection

Both pages must reconnect automatically with backoff and re-request state on reconnect.
A dropped Wi-Fi frame must not require a manual refresh mid-game.

---

## 8. Tests

`pytest`, no hardware, must pass on any laptop:

1. First buzz in `ARMED` latches; second is ignored
2. Buzz in `IDLE`, `LOCKED`, `REVEALED` does nothing
3. Buzz in `READING` locks that team out for 250 ms from arm, does not latch
4. A team locked out by a false start cannot latch until the window expires
5. Two buzzes 1 ms apart: the earlier tick wins, regardless of callback order —
   **call the handler with the later tick first** and assert the earlier one still wins
6. Debounce: two edges on one pin 5 ms apart count once
7. `mark_incorrect` deducts, re-arms, and excludes that team from the clue
8. All teams wrong → `REVEALED`
9. Every illegal transition is rejected without mutating state
10. Snapshot in non-`REVEALED` phases never contains answer text

Test 5 is the one that actually proves the fairness property. Write it first.

---

## 9. Deployment

- `requirements.txt`; `lgpio` in an optional extra so laptop installs do not need it
- `run.sh` — sets `BUZZER_BACKEND=gpio`, binds `0.0.0.0:8000`
- A systemd unit that starts on boot and restarts on failure
- `README.md`: laptop dev setup with the mock backend, Pi setup, GPIO wiring table,
  and the keyboard shortcuts

Log every buzz with team, tick, and resulting phase. When someone disputes a call, the
log is the only record.

---

## 10. Build order

1. `game.py` and its tests. Nothing else until they pass.
2. `MockBackend` + FastAPI + WebSocket broadcast. Verify with `curl` and two browser
   tabs that both stay in sync.
3. Board page, then host page.
4. `GpioBackend` last. It is the only part that cannot be tested without hardware, so
   it should be the smallest and dumbest piece of code in the project — translate an
   `lgpio` callback into `on_buzz(team, tick)` and nothing more.
