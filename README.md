# Buzzer

A two-team Jeopardy-style buzzer system built with FastAPI, running on Raspberry Pi with GPIO button inputs. Features a web-based game board display and mobile-friendly host control panel with keyboard shortcuts.

The Pi itself runs headless — no monitor attached, no clue/answer text
displayed anywhere (the host reads those from paper). `/board` and `/host`
are just pages any browser on the Pi's network can load: point a laptop at
`/board` for the shared display, and a phone (or that same laptop, in
another tab) at `/host` for the host controls. Running everything off a
single device instead? See `/dual` below.

## Running board + host on one device

`/dual` shows both `/board` and `/host` side by side in a single browser
tab (each in its own `<iframe>` — neither page changes, both still work
fine loaded standalone too). Host controls on the left, board on the
right; stacks vertically instead on narrow/short windows. Keyboard
shortcuts work no matter which pane has focus — `/dual` has its own
top-level listener for exactly that, since a browser only ever routes
keypresses to whichever iframe (or the outer page) currently has focus,
and the board pane has no shortcut handler of its own.

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
- **Host panel:** http://localhost:8000/host (keyboard: space=arm, 1/2=manual buzz, y/n=correct/incorrect, r=reveal, esc=back to board)

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

**Important:** Before deploying, check `PI_TODO.md` for the detailed hardware bring-up and verification checklist.

## GPIO Wiring (Raspberry Pi 4, Bookworm)

| Team   | GPIO (BCM) | Header Pin | Notes                                             |
|--------|-----------|-----------|---------------------------------------------------|
| Team A | 17        | 11        | Active-low; 4.7kΩ pull-up to 3.3V; 100nF → GND |
| Team B | 27        | 13        | Active-low; 4.7kΩ pull-up to 3.3V; 100nF → GND |

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

## Host Panel Keyboard Shortcuts

| Key | Action |
|-----|--------|
| Space | Arm the buzzer |
| `1` | Manual buzz override for Team A |
| `2` | Manual buzz override for Team B |
| `y` | Mark the buzzed-in team correct |
| `n` | Mark the buzzed-in team incorrect |
| `r` | Reveal answer |
| Esc | Return to board |

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
