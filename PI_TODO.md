# To do on the Raspberry Pi

This project was built and tested entirely on a laptop with `BUZZER_BACKEND=mock`,
then (2026-09-10) connected to over SSH and exercised against real hardware for
the first time -- see "Done so far" below for what that covered. This file
tracks everything that still needs doing/verifying. Do not consider the
hardware path done until every item here is checked off.

**Actual OS**: this card is Raspberry Pi OS on **Debian 13 "trixie"** (kernel
6.18, Python 3.13), not Bookworm as the spec assumed. Nothing so far has
needed anything Bookworm-specific, but if something behaves unexpectedly,
check whether it's a trixie-vs-bookworm difference before assuming it's a
code bug.

## Done so far (2026-09-10, via SSH from the laptop, no buttons soldered)

- Got SSH working: the SD card had **no customization applied at all**
  (Imager's advanced-options hostname/user/SSH settings never actually got
  written -- don't trust that dialog silently; verify `userconf.txt`/`ssh`/
  `user-data` on the boot partition exist before assuming it worked next
  time). Fixed by writing `userconf.txt` (user `bmh`) and an empty `ssh` file
  directly onto the boot partition. Hostname set to `bmh` via `hostnamectl`
  after first login.
- Installed `requirements.txt` cleanly. `requirements-gpio.txt` (`lgpio`)
  **did not** install cleanly out of the box -- see item 1, now fixed and
  documented in the README's Pi setup section.
- Verified `gpiochip0` + BCM pins 17/27 read correctly via `lgpio` directly
  (item 3 below) -- both read LOW at rest, which is *expected* right now
  since nothing is wired (no external pull-up), not a real fault.
- Booted the actual app with `BUZZER_BACKEND=gpio`: it starts cleanly, the
  startup self-test correctly flags both teams as unhealthy (again, expected
  with floating pins), `/board` serves, `POST /api/*` all work. This is the
  first real confirmation `GpioBackend` (`buzzer/inputs/gpio.py`) works
  end-to-end against the real GPIO character device, not just imports cleanly.
- Ran the full `pytest` suite on-device (Python 3.13, aarch64): 30/30 pass,
  same as the laptop.
- Network right now is a direct Ethernet cable to the laptop with Internet
  Connection Sharing (laptop NATs its Wi-Fi to the Pi at 10.42.0.1/10.42.0.x)
  so the Pi could reach apt/GitHub during setup. **This is not the venue
  network** -- the Pi's own Wi-Fi AP (spec: 10.42.0.1) still needs setting up
  separately; see item 6.

## 1. Install

```bash
cd buzzer
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-gpio.txt
```

**Update**: on a fresh trixie install, this failed twice before working:

1. `lgpio`'s wheel build needs `swig` (SWIG isn't installed by default):
   ```
   error: command 'swig' failed: No such file or directory
   ```
2. After installing swig, it built the wrapper but failed to **link** against
   the system GPIO library:
   ```
   /usr/bin/ld: cannot find -llgpio: No such file or directory
   ```
   `liblgpio1` (the runtime `.so`) was already present, but not
   `liblgpio-dev` (which provides the unversioned symlink + headers needed to
   build against it).

Fix, before `pip install -r requirements-gpio.txt`:
```bash
sudo apt-get install -y swig build-essential python3-dev liblgpio-dev
```
This is now documented in README.md's Pi setup section — keep both in sync
if either changes.

## 2. Wire it up per the spec

- Team A → GPIO17 (header pin 11), Team B → GPIO27 (header pin 13)
- Both lines active-low: external 4.7 kΩ pull-up to 3.3V, buttons short to ground
- 100 nF from each line to ground (RC filter, ~100 µs)
- Idle state HIGH, press = falling edge

Verify with a multimeter before plugging into GPIO: idle should read ~3.3V,
pressed should read ~0V. If a line reads low with the button unpressed,
`GpioBackend.self_test()` should catch it and the host panel should show a
warning — but confirm this actually happens (see item 4).

## 3. Sanity-check `gpiochip0` numbering — ✅ done 2026-09-10

Confirmed: `gpiochip0` is `pinctrl-bcm2711` (58 lines) and BCM 17/27 map the
way `lgpio` expects -- `gpio_claim_input`/`gpio_read` on both work. Both
currently read 0 (LOW), which is *expected* since nothing is wired yet (no
external pull-up) -- do not treat that as a chip/offset problem. Re-verify
this reads 1 (HIGH) once the pull-ups are actually wired (item 2/4).

```python
import lgpio
h = lgpio.gpiochip_open(0)
lgpio.gpio_claim_input(h, 17)
print(lgpio.gpio_read(h, 17))  # should print 1 (idle/HIGH) once wired with pull-ups
```

## 4. Run the app with the real backend and confirm, physically, in this order

```bash
BUZZER_BACKEND=gpio ./run.sh
```

- [x] Startup self-test doesn't crash and correctly surfaces a warning per
      unhealthy team — confirmed 2026-09-10 with both pins floating (no
      pull-ups wired yet), both teams correctly flagged. Still need: re-check
      with pull-ups actually wired that *no* warning shows at rest, and that
      holding one button down at startup still names the right team.
- [ ] Single press, each team independently, latches correctly and shows on
      `/board` from across the room. Use `/test` (linked from `/host` under
      "Hardware bring-up") to verify each button fires the correct pin
      before ever playing a real round -- it prompts for each team in turn
      and shows a live raw event log, independent of game phase.
- [ ] **Debounce**: tap a button rapidly/lightly to try to induce contact
      bounce. Confirm it registers as exactly one buzz, not several rejected-
      then-accepted flickers in the log. `/test`'s raw log is the easiest way
      to watch for this directly.
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
- [ ] **Auto-arm on tile click** (added 2026-09-11, only tested via curl/
      scripted requests so far, never with a real finger on a real button
      immediately after a real click): select a tile on `/host` or `/dual`
      and confirm a real buzz registers correctly right after, with no
      perceptible gap where the buzzer isn't actually live yet.
- [ ] **Daily Double** (never tested with real hardware): select a
      preselected Daily Double tile and confirm a real button press during
      that window does *nothing* -- no latch, no false-start penalty,
      nothing in the game state, even though the log will still show the
      raw edge. Board should show "Daily Double", not a dollar value.
- [ ] **Round transition / Final Jeopardy** (never tested with real
      hardware, no reason to expect hardware-specific issues since neither
      touches the buzzer path, but worth a real runthrough once real people
      are playing): confirm "Next round" moves to round 2's categories with
      round 1's used-clue state untouched, and "Start Final Jeopardy"
      correctly blocks all buzzing (board, host, and phone fallback alike).

## 5. systemd — ✅ done 2026-09-10

```bash
sudo cp deploy/buzzer.service /etc/systemd/system/buzzer.service
sudo systemctl daemon-reload
sudo systemctl enable --now buzzer.service
```

Installed with `User=bmh` and paths pointed at `/home/bmh/buzzer` (the
placeholder `pi`/`/home/pi/buzzer` in the committed unit file is generic —
this Pi's actual account is `bmh`, adjust again if the account ever changes).

- [x] Confirmed the unit's `WorkingDirectory`/`ExecStart` match the real
      install path and account on this Pi.
- [x] Rebooted and confirmed the service comes up on its own, `/board`
      reachable within ~20s, no manual steps.
- [x] `systemctl kill -s SIGKILL` mid-run → confirmed it restarts
      automatically (Restart=on-failure) with a new PID.

## 6. Network — Wi-Fi AP ✅ done 2026-09-10, phone fallback still untested

Set up via NetworkManager (no hostapd/dnsmasq needed — NM handles both):

```bash
nmcli connection add type wifi ifname wlan0 con-name buzzer-ap autoconnect yes ssid Buzzer \
  802-11-wireless.mode ap 802-11-wireless.band bg \
  wifi-sec.key-mgmt wpa-psk wifi-sec.psk buzzerbuzzer \
  ipv4.method shared ipv6.method disabled
```

SSID `Buzzer`, password `buzzerbuzzer` — change both if you want something
else (`nmcli connection modify buzzer-ap 802-11-wireless.ssid ... wifi-sec.psk ...`
then `nmcli connection up buzzer-ap`). Gives `10.42.0.1` on `wlan0`, matching
the spec, via NetworkManager's own DHCP server for that connection.

Two gotchas hit while setting this up, in case they recur (e.g. after a
Raspberry Pi OS update resets NetworkManager state):

1. **Wi-Fi radio was administratively off by default** (`nmcli radio wifi`
   showed `disabled` even though the hardware/firmware were fine and `WIFI-HW`
   showed `enabled`). Fixed with `nmcli radio wifi on` — confirmed this
   persists across reboots on its own now.
2. **Regulatory domain was hardcoded to GB** in `/boot/firmware/cmdline.txt`
   (`cfg80211.ieee80211_regdom=GB`), presumably an image default, not
   anything set here. Fixed for a US venue with
   `sudo raspi-config nonint do_wifi_country US` (this edits `cmdline.txt`
   directly, so it's persistent). If this Pi ever travels to a different
   country, redo this or Wi-Fi channels/power may be wrong for the local
   regulatory environment.

If you ever also want the ethernet port sharing a laptop's internet for
setup (like this session did), do **not** let it default to the same
`10.42.0.0/24` NetworkManager uses for `shared` mode by default — that
collides with the AP's own subnet on the Pi itself once both are up
simultaneously (discovered the hard way: the Pi's `eth0` and `wlan0` both
tried to claim `10.42.0.1/24` at once, and connectivity broke until one side
was moved with `ipv4.addresses` to a different subnet).

- [ ] Actual deployment plan: Pi runs headless (no TV/monitor attached), the
      laptop is the `/board` display, and the host uses `/host` from a
      phone (or the laptop, if that's easier on the night — both just work,
      it's a browser tab either way, no server-side distinction between
      "board device" and "host device"). Load `/board` on the actual laptop
      and `/host` on the actual phone that will be used, connected to the
      Pi's AP — the `vw`/`clamp()` sizing was only checked by resizing a
      desktop browser window, never on the real target screens. Since the
      board no longer shows clue/answer text (host reads those from paper),
      the "55in TV across a room" legibility concern from the original spec
      matters less, but still worth a real check on the actual laptop.
- [ ] Phone buzzer fallback (`/buzz/0`, `/buzz/1`): load both on real phones
      connected to the Pi's actual AP/ethernet, not localhost. This is a
      best-effort fallback (server-receipt timestamp, not a kernel edge —
      see README "Fallback: phone buzzers"), so confirm it's at least
      *usably* fair on the real network before trusting it mid-game: two
      people tapping their phone buzzers close together should get a
      believable winner, not one that's consistently biased toward
      whichever phone happens to have lower Wi-Fi latency. If the bias is
      bad enough to matter, say so rather than treating this as done.

## 7. Log review

Confirm `journalctl -u buzzer` (or wherever logging ends up) actually shows
`buzz source=... team=... tick=... result=... phase=...` lines per spec
section 9, so a disputed call can be resolved after the fact. Also worth
confirming this now that logging config was fixed to not silently depend on
whatever uvicorn happens to set up (see commit adding the phone fallback) --
verify actual `journalctl` output on the Pi, not just that the code runs.
