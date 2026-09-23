# ToF XSHUT I2C address switcher

Two VL53 ToF sensors share **0x29** on one Orin Nano I2C bus. This repo remaps one chip to **0x28** using **XSHUT** (active-low reset) on header pin 29.

JetRacer / JetPack 6 notes: [README_JETRACER.md](README_JETRACER.md).

## Tech stack

- bash + systemd
- `i2c-tools` (`i2ctransfer`, `i2cdetect`)
- `busybox` (`devmem` pinmux poke)
- `gpiod` (`gpiofind`, `gpioset`)
- Python only for `test_two_sensors.py` (Blinka `board`/`busio` + Adafruit VL53L0X; needs recent Blinka for `board.SCL_1`)

## Architecture

Both sensors stay on **bus 1** (pins 27/28 → `/dev/i2c-1`). Isolation is software + one XSHUT pin, not a second bus or TCA9548A.

| Signal | Header |
|--------|--------|
| SDA / SCL | Pins 27 / 28 → `/dev/i2c-1` |
| XSHUT (sensor that stays at 0x29) | Pin **29** (`PQ.05`) |
| XSHUT (sensor remapped to 0x28) | Tie **high** (breakout pull-up) |
| OLED (jetcard, do not touch) | Bus **7**, pins 3/5; buttons 13/15/16/18/19 |

Boot unit `tof-i2c-switcher-simple.service` starts after `/dev/i2c-1` exists
(`After=dev-i2c-1.device` **only** — ordering against the jetcard units caused a
systemd ordering cycle and systemd silently dropped the start job; see
[README_JETRACER.md](README_JETRACER.md) #5). It runs [`tof_i2c_switcher_simple.sh`](tof_i2c_switcher_simple.sh):

1. `i2ctransfer` `0x29` → `0x28` (skip if 0x29 is already gone)
2. Poke pinmux `0x2430068` → `0x8` so pin 29 can drive
3. `gpioset --mode=exit` drives `PQ.05` **HIGH**, releases the line, and the script exits

The service is `Type=oneshot` with `RemainAfterExit=yes`, so status is `active (exited)` and no process stays behind. On this JetRacer the pad pull-up keeps XSHUT high after the release. A board with no pull-up will drop the pin; that case needs `gpioset --mode=signal` and `Type=simple` again.

The VL53 address change is **RAM only**. A full power cut brings both chips back to 0x29. A `reboot` often does **not** cut 3.3 V, so 0x28 can remain.

## Install / run (on the Jetson)

```bash
git clone <this-repo-url> && cd orin_nano_i2c_switcher
sudo ./simple_install.sh
sudo i2cdetect -y -r 1                    # expect 29 and 28
systemctl status tof-i2c-switcher-simple  # want: active (exited)
journalctl -u tof-i2c-switcher-simple -n 30 --no-pager
```

Wanted log line: `pin 29 HIGH, then release`, then `done`. No `gpioset` process remains.

```bash
sudo systemctl restart tof-i2c-switcher-simple
sudo systemctl disable --now tof-i2c-switcher-simple
sudo ./uninstall.sh
```

Range check (after 28+29 are on the bus). Addresses come from [`config.json`](config.json):

```bash
sudo python3 test_two_sensors.py -c config.json
```

## Recovery (no reboot)

Sensors drop off the bus mid-session (bad contact, XSHUT unplugged, power dip —
the `0x28` address is RAM-only)? Open [`reconnect.ipynb`](reconnect.ipynb) in
Jupyter. Cell 1 runs `i2cdetect`, detects which case you are in, and tells you
which fix cell to run: wake XSHUT (pin 29 HIGH), full re-remap
(low → rename → high), or hardware check. No reboot needed.

## Files

| File | Purpose |
|------|---------|
| `tof_i2c_switcher_simple.sh` | boot-time remap + pinmux poke + pin 29 HIGH, then exit |
| `systemd/tof-i2c-switcher-simple.service` | the boot unit |
| `simple_install.sh` / `uninstall.sh` | install / remove everything |
| `reconnect.ipynb` | Jupyter recovery when sensors drop mid-session |
| `remap_utils.py` | auto-detect + fix logic behind the notebook's `Resolve!` button |
| `test_two_sensors.py` + `config.json` | range both sensors (bus/address check) |
| `move_addr.py`, `xshut_pulse.py` | debug leftovers — not used by the service |

## Manual (same as the service)

```bash
sudo i2ctransfer -y 1 w2@0x29 0x8A 0x28
sudo busybox devmem 0x2430068 w 0x8
sudo gpioset --mode=exit $(gpiofind PQ.05)=1
```
