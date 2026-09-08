# Buzzer

A two-team Jeopardy-style buzzer system built with FastAPI, running on Raspberry Pi with GPIO button inputs. Features a web-based game board display and mobile-friendly host control panel with keyboard shortcuts.

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

Install the GPIO requirements in addition to the main ones:

```bash
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
