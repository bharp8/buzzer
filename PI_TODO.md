# To do on the Raspberry Pi

This project was built and tested entirely on a laptop with `BUZZER_BACKEND=mock`.
Nothing in `buzzer/inputs/gpio.py` has run against real hardware yet. This file
tracks everything that still needs doing/verifying once this repo is on the Pi.
Do not consider the hardware path done until every item here is checked off.

## 1. Install

```bash
cd buzzer
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-gpio.txt
```

`requirements-gpio.txt` installs `lgpio`. Confirm it actually installs cleanly
on Raspberry Pi OS Bookworm — this was never attempted on the laptop (no `pip`
was even available there without an apt install, let alone `lgpio`, which
needs the Pi's GPIO character device).

## 2. Wire it up per the spec

- Team A → GPIO17 (header pin 11), Team B → GPIO27 (header pin 13)
- Both lines active-low: external 4.7 kΩ pull-up to 3.3V, buttons short to ground
- 100 nF from each line to ground (RC filter, ~100 µs)
- Idle state HIGH, press = falling edge

Verify with a multimeter before plugging into GPIO: idle should read ~3.3V,
pressed should read ~0V. If a line reads low with the button unpressed,
`GpioBackend.self_test()` should catch it and the host panel should show a
warning — but confirm this actually happens (see item 4).

## 3. Sanity-check `gpiochip0` numbering

The code assumes `gpiochip0` and BCM pin numbers 17/27 map the way `lgpio`
expects on this specific Pi. On some Pi 4/5 + lgpio + kernel combos the chip
number or offset differs. Run this before trusting anything else:

```python
import lgpio
h = lgpio.gpiochip_open(0)
lgpio.gpio_claim_input(h, 17)
print(lgpio.gpio_read(h, 17))  # should print 1 (idle/HIGH) with nothing pressed
```

If this doesn't print 1, stop and figure out the correct chip/offset before
running the app — everything downstream assumes this is right.

## 4. Run the app with the real backend and confirm, physically, in this order

```bash
BUZZER_BACKEND=gpio ./run.sh
```

- [ ] Startup self-test: with both buttons unpressed, no warnings on `/host`.
      Hold one button down at startup, restart the service, confirm the host
      panel shows a warning naming the correct team, and the app does not crash.
- [ ] Single press, each team independently, latches correctly and shows on
      `/board` from across the room.
- [ ] **Debounce**: tap a button rapidly/lightly to try to induce contact
      bounce. Confirm it registers as exactly one buzz, not several rejected-
      then-accepted flickers in the log.
- [ ] **Simultaneous press**: have two people press both buttons as close to
      the same instant as they can manage, repeatedly, and confirm the winner
      is consistent with a stopwatch/slow-motion video, not just "whichever
      team is checked first" — this is the property test_earlier_tick_wins
      only proves in software; it needs a real confirmation that both GPIO
      lines are actually being serviced by independent lgpio callbacks with
      no scanning order bias.
- [ ] False start: buzz during READING (before arming), confirm that team is
      locked out for ~250ms after the host arms, and can buzz normally after
      that window.
- [ ] Manual override (`1`/`2` on `/host`, or the on-screen buzz buttons)
      still works with the real backend in case a physical button fails
      mid-game.
- [ ] Full runthrough of an actual round (several clues, a wrong answer, a
      correct answer, a fully-answered-wrong clue) with real people racing
      real buttons, not curl.

## 5. systemd

```bash
sudo cp deploy/buzzer.service /etc/systemd/system/buzzer.service
sudo systemctl daemon-reload
sudo systemctl enable --now buzzer.service
```

- [ ] Confirm the unit's `WorkingDirectory`/`ExecStart` paths actually match
      wherever this repo lives on the Pi (adjust `deploy/buzzer.service` if
      cloned somewhere other than `/home/pi/buzzer`).
- [ ] Reboot the Pi and confirm the service comes up on its own and `/board`
      is reachable without manually running anything.
- [ ] `sudo systemctl kill -s SIGKILL buzzer.service` mid-game and confirm it
      restarts automatically (Restart=on-failure) and the board/host
      reconnect on their own once it's back.

## 6. Network

- [ ] Confirm the Pi's Wi-Fi AP (`10.42.0.1`) or ethernet setup actually
      serves `0.0.0.0:8000` to devices on that network — this repo doesn't
      configure the AP itself, only binds the server to `0.0.0.0`.
- [ ] Load `/board` on the actual TV/laptop and `/host` on the actual
      phone/laptop that will be used, at the actual distances described in
      the spec (55" TV across a room, phone in hand) — the `vw`/`clamp()`
      sizing was only checked by resizing a desktop browser window, never on
      real target hardware/screens.

## 7. Log review

Confirm `journalctl -u buzzer` (or wherever logging ends up) actually shows
`buzz team=... tick=... result=... phase=...` lines per spec section 9, so a
disputed call can be resolved after the fact.
