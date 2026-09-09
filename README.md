# ToF XSHUT I2C address switcher

Remap one of two VL53 ToF sensors that share the same I2C address on a single Jetson Orin Nano bus by using **XSHUT** (active-low reset).

Default: leave one sensor at **0x29**, move the other to **0x30**. All settings live in [`config.json`](config.json).

## Why not two I2C buses?

Orin Nano can put devices on bus 7 (pins 3/5) and bus 1 (pins 27/28). This project assumes **both sensors stay on bus 1** (pins 27/28 → `/dev/i2c-1`). Isolation is software + XSHUT, not a second bus or TCA9548A mux.

## Wiring

| Signal | Typical Orin Nano header |
|--------|--------------------------|
| SDA / SCL | Pins 27 & 28 → `/dev/i2c-1` (confirm with `i2cdetect -l`) |
| XSHUT (sensor that stays at 0x29) | GPIO BOARD pin from `xshut_keep_default` (default **29**) |
| XSHUT (sensor to remap) | Optional `xshut_remapped`, or tie that XSHUT **high** so it always boots |
| Logic / pull-ups | 3.3 V |

Confirm the bus index on your image:

```bash
i2cdetect -l
sudo i2cdetect -y -r 1
```

Prefer BOARD pins **13 / 15 / 16 / 18 / 29+** (avoid 1–12 if those are unavailable on your carrier). Pin **29** is next to I2C pins 27/28 if you want short XSHUT wiring. Some header pins need pinmux as GPIO.

## Sensor models

Set `sensor_model` in `config.json`:

| Key | Reg width | Address register |
|-----|-----------|------------------|
| `vl53l0x` | 8-bit | `0x8A` |
| `vl53l1x`, `vl53l1cb`, `vl53l3cx` | 16-bit | `0x0001` |
| `vl53l4cd`, `vl53l4cx` | 16-bit | `0x0001` |

## Sequence

1. Hold keep-default sensor in reset via XSHUT (off the bus).
2. Write the new 7-bit address into the remapped sensor (still at 0x29).
3. Release XSHUT on the keep-default sensor so it boots at 0x29.
4. Verify both `0x29` and `0x30` ACK.

With two XSHUT pins configured, both are reset first; only the remapped sensor is released before the address write.

## Install (on the Jetson)

Pick **one** path. Both share [`config.json`](config.json) → `/etc/orin_nano_i2c_switcher/config.json`. Running one installer disables the other service so they cannot fight over GPIO.

### Simple (recommended for JetRacer / JP6)

Rename `0x29`→`0x30` once, poke pinmux, hold XSHUT HIGH. **No** poll loop, **no** cable-replug recovery.

```bash
sudo ./simple_install.sh
sudo i2cdetect -y -r 1          # expect 29 and 30
journalctl -u tof-i2c-switcher-simple -f
```

### Normal (monitor + udev)

Remap, then poll forever and re-run the XSHUT sequence if `0x30` disappears. Also starts `tof-xshut-pinmux.service` **before** the monitor (required so pin 29 can drive).

```bash
sudo ./install.sh
```

Safe to re-run after pulling updates: **scripts, systemd unit, and udev rules are always overwritten**. Existing `/etc/orin_nano_i2c_switcher/config.json` is **kept**; new defaults are saved as `/opt/orin_nano_i2c_switcher/config.json.example`.

This installs:

- `/opt/orin_nano_i2c_switcher/tof_i2c_switcher.py` (+ install/uninstall helpers)
- `/etc/orin_nano_i2c_switcher/config.json`
- systemd unit `tof-i2c-switcher.service` (starts `monitor` after the I2C device)
- udev rule that restarts the service when `/dev/i2c-N` is added

Edit config, then restart:

```bash
sudo nano /etc/orin_nano_i2c_switcher/config.json
sudo systemctl restart tof-i2c-switcher
```

## Uninstall

```bash
sudo ./uninstall.sh          # keep config under /etc
sudo ./uninstall.sh --purge  # also remove config + lock file
```

Or after install: `sudo /opt/orin_nano_i2c_switcher/uninstall.sh`.

## Manual usage

```bash
pip3 install -r requirements.txt

# Remap once
sudo python3 tof_i2c_switcher.py init -c config.json

# Probe expected addresses
sudo python3 tof_i2c_switcher.py status -c config.json

# Remap then poll forever (what systemd runs)
sudo python3 tof_i2c_switcher.py monitor -c config.json
```

CLI overrides: `--bus`, `--model`, `--default-addr`, `--remap-addr`, `--xshut-keep`, `--xshut-remap`, `--poll-interval`, `-v`.

## Reconnect handling

I2C **slaves** do not generate udev events when you unplug SDA/SCL. After a cable yank both chips typically come back at **0x29**, so **0x30** disappears.

| Mechanism | What it covers |
|-----------|----------------|
| **`monitor` poll loop** (primary) | Detects missing remapped address and re-runs the full XSHUT sequence. Uses a file lock so overlapping runs do not race. |
| **systemd on boot** | Runs `monitor` after `dev-i2c-N.device`. |
| **udev on `i2c-dev` add** | Restarts the service if the adapter node reappears (adapter re-probe), not a wire wiggle. |

Logs:

```bash
journalctl -u tof-i2c-switcher -f
```

## Application note

This tool only assigns addresses. Your ranging / ROS / app code should open the sensors at `default_addr` and `remapped_addr` on the configured bus after the switcher has run (or rely on the always-on monitor).
