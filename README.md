# Buzzer

A two-team Jeopardy-style buzzer system built with FastAPI, running on Raspberry Pi with GPIO button inputs. Features a web-based game board display and mobile-friendly host control panel with keyboard shortcuts.

The Pi itself runs headless — no monitor attached, no clue/answer text
displayed anywhere (the host reads those from paper). `/board` and `/host`
are just pages any browser on the Pi's network can load: point a laptop at
`/board` for the shared display, and a phone (or that same laptop, in
another tab) at `/host` for the host controls. Running everything off a
single device instead? See `/dual` below.

## Running board + host on one device

`/dual` shows a trimmed host control strip on the left (phase, teams,
round info, the five action buttons, and nothing else — no board-select
grid, no phone-buzzer links, no reset button) and the board display on
the right, both live in a single tab. The board pane doubles as the
tile picker: click a category/value directly on it to select **and**
arm a clue in one action, instead of using a separate grid. Keyboard
shortcuts (space/1/2/y/n/r/esc) work anywhere on the page. `/board` and
`/host` are unaffected and still work exactly as before if loaded on
their own — `/dual` is a separate page, not a wrapper around either.

## Laptop Development Setup

Create a virtual environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run tests (no hardware needed):

```bash
pytest buzzer/tests/
```

Start the dev server with the mock backend (simulates buttons without GPIO):

```bash
BUZZER_BACKEND=mock uvicorn buzzer.app:app --reload
```

Simulate a button press without hardware by posting to the dev endpoint:

```bash
# Team A (0) or Team B (1)
curl -X POST http://localhost:8000/dev/buzz/0
```

Visit the pages:
- **Board display:** http://localhost:8000/board
- **Host panel:** http://localhost:8000/host (keyboard: space=arm, 1/2=manual buzz, y/n=correct/incorrect, esc=back to board)

## Raspberry Pi Setup

`lgpio` builds a native extension, so install its build dependencies first —
without these, `pip install -r requirements-gpio.txt` fails (first on a
missing `swig`, then on a linker error for `-llgpio` once swig's installed):

```bash
sudo apt-get install -y swig build-essential python3-dev liblgpio-dev
pip install -r requirements.txt -r requirements-gpio.txt
```

Wire the buttons according to the table below, then run via the startup script:

```bash
./run.sh
```

Or install as a systemd service for automatic startup:

```bash
sudo cp deploy/buzzer.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now buzzer
journalctl -u buzzer -f  # tail logs
```

The unit file has placeholder paths/user (`pi`, `/home/pi/buzzer`) — edit it
to match the actual account and clone location before installing.

### Wi-Fi access point

At the venue there's no internet, so the Pi broadcasts its own network via
NetworkManager (no hostapd/dnsmasq needed):

```bash
nmcli connection add type wifi ifname wlan0 con-name buzzer-ap autoconnect yes ssid Buzzer \
  802-11-wireless.mode ap 802-11-wireless.band bg \
  wifi-sec.key-mgmt wpa-psk wifi-sec.psk buzzerbuzzer \
  ipv4.method shared ipv6.method disabled
```

Default SSID `Buzzer`, password `buzzerbuzzer`, serving `10.42.0.1`. Change
either with `nmcli connection modify buzzer-ap 802-11-wireless.ssid <name> wifi-sec.psk <pass>`
then `nmcli connection up buzzer-ap`. If Wi-Fi doesn't come up, check
`nmcli radio wifi` is `enabled` and the regulatory domain
(`sudo raspi-config nonint do_wifi_country <CC>`) matches where you actually
are — both bit us once; see `PI_TODO.md` item 6 for details.

### Ethernet as the primary connection

If the Pi lives inside a metal enclosure (an ammo can, in this build), the
Wi-Fi AP alone is not reliable enough for the main display/host device —
a metal box attenuates 2.4GHz badly. `eth0` is set up as its own DHCP
server too, on a different subnet so it doesn't collide with the AP:

```bash
sudo nmcli connection modify "Wired connection 1" \
  ipv4.method shared ipv4.addresses 10.43.0.1/24 ipv6.method ignore \
  connection.autoconnect yes connection.autoconnect-priority 10
sudo nmcli connection up "Wired connection 1"
```

Plug the primary laptop into the Pi via ethernet — it gets an address
automatically (`10.43.0.x`, no manual config), then browse to
`http://10.43.0.1:8000/dual`. Keep the Wi-Fi AP up for phone-buzzer-fallback
pages, where Wi-Fi flakiness matters much less than it does for the main
display.

**Important:** Before deploying, check `PI_TODO.md` for the detailed hardware bring-up and verification checklist.

## GPIO Wiring (Raspberry Pi 4, Bookworm)

| Team | GPIO (BCM) | Header Pin | Notes                                             |
|------|-----------|-----------|---------------------------------------------------|
| Red  | 17        | 11        | Active-low; 4.7kΩ pull-up to 3.3V; 100nF → GND |
| Blue | 27        | 13        | Active-low; 4.7kΩ pull-up to 3.3V; 100nF → GND |

Both lines idle HIGH; a button press pulls LOW (falling edge). RC filters cap at ~100 µs.

## Environment Variables

| Variable | Default | Meaning |
|----------|---------|---------|
| `BUZZER_BACKEND` | `mock` | `mock` (dev, no hardware) or `gpio` (Raspberry Pi) |
| `BUZZER_HOST` | `0.0.0.0` | Bind address |
| `BUZZER_PORT` | `8000` | Bind port |
| `DEBOUNCE_MS` | `20` | Button debounce window (ms) |
| `FALSE_START_LOCKOUT_MS` | `250` | Lockout duration after false start (ms) |
| `TEAM_A_GPIO` | `17` | GPIO pin for Team A (BCM) |
| `TEAM_B_GPIO` | `27` | GPIO pin for Team B (BCM) |
| `GAME_CONTENT_PATH` | `buzzer/content/game.json` | Path to game content JSON |

## Game content

No clue or answer text lives anywhere in this system — the host reads both
from paper. `content/game.json` only ever needs category names, dollar
values, and which clues are Daily Doubles:

```json
{
  "title": "...",
  "teams": ["Red", "Blue"],
  "rounds": [
    {
      "name": "Jeopardy",
      "categories": [
        {"name": "World Capitals", "clues": [
          {"value": 200}, {"value": 400}, {"value": 600},
          {"value": 800, "daily_double": true}, {"value": 1000}
        ]}
      ]
    },
    {"name": "Double Jeopardy", "categories": [ /* same shape, 5 or 6 categories */ ]}
  ],
  "final_jeopardy": {"category": "20th Century History"}
}
```

- At least one round; each round needs 5 or 6 categories, each with exactly
  5 clues.
- `daily_double` is optional per clue (defaults to `false`). It's never
  shown on `/board`; the host's own board-select grid (on `/host` and
  `/dual`) shows a subtle marker so the host knows in advance, matching how
  real Jeopardy production knows even though contestants don't.
- `final_jeopardy` is required — just a category name, no clue/answer text.
- Selecting a Daily Double skips the buzzer race entirely (see "Daily
  Double & Final Jeopardy" below).

## Host Panel Keyboard Shortcuts

| Key | Action |
|-----|--------|
| Space | Arm the buzzer (redundant most of the time — selecting a tile arms it automatically now) |
| `1` | Manual buzz override for Team A |
| `2` | Manual buzz override for Team B |
| `y` | Mark the buzzed-in team correct |
| `n` | Mark the buzzed-in team incorrect |
| Esc | Back to board — works at any point (nobody buzzed? assume nothing happened, no score change) |

## Daily Double & Final Jeopardy

Both are intentionally paper-based, like the rest of the clue content — no
digital wagering, no auto-scoring. The host applies results by hand with
the existing manual score-adjust control: the "Adjust" button per team, on
`/host` and `/dual` alike (or `POST /api/adjust_score` directly).

**Daily Double**: clicking a flagged tile shows "Daily Double" instead of a
dollar value and skips arming entirely — there's no buzzer race, because
the host picks every tile (not a team), so there's no "team that found it"
to hand it to automatically. Ask whichever team you choose to wager and
answer verbally, then adjust their score by hand and return to the board.

**Final Jeopardy**: click "Start Final Jeopardy" on `/host` or `/dual`
(enabled any time from the board view). The board shows the category with
no buzzing possible. Teams wager and answer on paper; reveal the answer
verbally, adjust each team's score by hand, then "Back to board" to finish.

(Shortcuts work on `/host` page; test on the device it will display on.)

## Fallback: phone buzzers

If the physical buttons stop working mid-game, each team can buzz in from
their own phone instead: send Team A to `/buzz/0` and Team B to `/buzz/1`
(the host panel lists these links under "Fallback: phone buzzers" so you
don't have to remember the URLs). Each page is a single big button in that
team's color, live-updated over the same WebSocket as `/board` and `/host`.

This is a deliberately lower-fidelity fallback, not a replacement for the
real buzzers: a phone buzz is timestamped when the request reaches the
server, not by a kernel GPIO edge, so it inherits Wi-Fi and browser latency
and can't offer the same fairness guarantee described in section 2.1 of the
spec. Use it to keep a game moving when hardware fails, not as the primary
input.

Under the hood it's the same manual-override endpoint the host's own
`1`/`2` keys use (`POST /api/manual_buzz/{team}`), just called from a
page the players hold instead of the host. Every buzz is still logged with
its source (`host`, `phone`, `gpio`, or `mock`) for dispute resolution.

## Hardware test mode

`/test` (linked from `/host`) prompts for each team's button in turn, shows a
green check once the right pin fires, then loops continuously. It's
independent of game phase — it sees a press even while `IDLE`, where the
main game would just ignore it — so it works before any clue has ever been
selected. A raw event log underneath shows every edge as it happens
(including presses on the *wrong* team's button, useful for catching
cross-wired GPIO pins) and is the fastest way to visually confirm debounce
is filtering contact bounce down to one event per press.
