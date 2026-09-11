# ToF XSHUT I2C address switcher

Two VL53 ToF sensors share **0x29** on one Orin Nano I2C bus. This repo remaps one chip to **0x30** using **XSHUT** (active-low reset) on header pin 29.

JetRacer / JetPack 6 notes: [README_JETRACER.md](README_JETRACER.md).

## Tech stack

- bash + systemd
- `i2c-tools` (`i2ctransfer`, `i2cdetect`)
- `busybox` (`devmem` pinmux poke)
- `gpiod` (`gpiofind`, `gpioset`)
- Python only for `test_two_sensors.py` (`smbus2`, Blinka, Adafruit VL53L0X)

## Architecture

Both sensors stay on **bus 1** (pins 27/28 → `/dev/i2c-1`). Isolation is software + one XSHUT pin, not a second bus or TCA9548A.

| Signal | Header |
|--------|--------|
| SDA / SCL | Pins 27 / 28 → `/dev/i2c-1` |
| XSHUT (sensor that stays at 0x29) | Pin **29** (`PQ.05`) |
| XSHUT (sensor remapped to 0x30) | Tie **high** (breakout pull-up) |
| OLED (jetcard, do not touch) | Bus **7**, pins 3/5; buttons 13/15/16/18/19 |

Boot unit `tof-i2c-switcher-simple.service` runs [`tof_i2c_switcher_simple.sh`](tof_i2c_switcher_simple.sh):

1. `i2ctransfer` `0x29` → `0x30` (skip if 0x29 is already gone)
2. Poke pinmux `0x2430068` → `0x8` so pin 29 can drive
3. `gpioset --mode=signal` hold `PQ.05` **HIGH** until the service stops

`--mode=signal` is required under systemd. `--mode=wait` waits for Enter, exits immediately, and **releases** the pin (service shows `inactive (dead)` / SUCCESS). Release is high-Z, not a forced LOW; a pull-up may still keep XSHUT high.

The VL53 address change is **RAM only**. A full power cut brings both chips back to 0x29. A `reboot` often does **not** cut 3.3 V, so 0x30 can remain.

## Install / run (on the Jetson)

```bash
sudo ./simple_install.sh
sudo i2cdetect -y -r 1                    # expect 29 and 30
systemctl status tof-i2c-switcher-simple  # want: active (running)
journalctl -u tof-i2c-switcher-simple -n 30 --no-pager
```

Wanted log line: `hold PQ.05 HIGH`, then the unit **stays running** (`ps` shows `gpioset --mode=signal`).

```bash
sudo systemctl restart tof-i2c-switcher-simple
sudo systemctl disable --now tof-i2c-switcher-simple
sudo ./uninstall.sh
```

Range check (after 29+30 are on the bus):

```bash
sudo python3 test_two_sensors.py
```

## Manual (same as the service)

```bash
sudo i2ctransfer -y 1 w2@0x29 0x8A 0x30
sudo busybox devmem 0x2430068 w 0x8
sudo gpioset --mode=signal $(gpiofind PQ.05)=1
```
