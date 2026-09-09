# JetRacer Orin Nano (JetPack 6.2) — two VL53L0X bring-up notes

Field notes from getting two VL53L0X ToF sensors working on a JetRacer /
Orin Nano with JP 6.2. Read this before the main [README.md](README.md) —
the stock instructions assume an ideal Jetson, and JP 6.2 is not one.

## Final working setup

| Item | Value |
|------|-------|
| I2C wiring | Pins **27 (SDA) / 28 (SCL)** → `/dev/i2c-1` |
| Sensor model | `vl53l0x` (verified: reg `0xC0` reads `0xEE`) |
| XSHUT GPIO | Header pin **29** (`PQ.05`, gpiochip0) → one sensor's XSHUT; the other sensor's XSHUT floats (breakout pull-up keeps it high) |
| Result | `0x29` + `0x30` on bus 1 |

Note the role swap: the sensor **on pin 29** is the one that stays at `0x29`;
the floating-XSHUT sensor is the one that gets renamed to `0x30`. (The script
renames whichever sensor is awake while the pin is held low.)

`config.json` for this setup:

```json
{
  "i2c_bus": 1,
  "default_addr": "0x29",
  "remapped_addr": "0x30",
  "sensor_model": "vl53l0x",
  "gpio_mode": "BOARD",
  "xshut_keep_default": 29,
  "xshut_remapped": null,
  "xshut_active_low": true
}
```

## What went wrong, and why it works now

Four separate problems stacked on top of each other. Any one of them alone
looked like "it doesn't work".

### 1. `/sys/class/gpio` does not exist on JetPack 6

The classic `echo N > /sys/class/gpio/export` interface is compiled out of
the JP6 kernel. Old guides (and JP5 sysfs numbers like gpio453) are useless.
Use **libgpiod** (`gpioset`/`gpioget`/`gpiofind`) or **Jetson.GPIO**, which
talks to `/dev/gpiochip*` directly.

### 2. Pinmux: pin 29 boots as an input

JP 6.2's default pinmux leaves header pin 29 unable to drive output, so every
GPIO write silently did nothing. Jetson.GPIO prints the fix as a warning:

```
sudo busybox devmem 0x2430068 w 0x8
```

This write is the **"poke"**: poking = writing one 32-bit value directly into
a hardware register through `/dev/mem`, bypassing all drivers. It flips pin
29's pad from its boot default to GPIO mode. **It is not persistent** — the
register resets every reboot, which is exactly what `tof-xshut-pinmux.service`
automates (below).

Same trap applies to pin 13 (defaults to SPI1_SCK) and most other header
GPIOs — each has its own register address; let the Jetson.GPIO warning tell
you the right one rather than guessing.

### 3. The Tegra I2C driver rejects zero-length writes

`i2cdetect` saw the sensor, but Python probes using empty writes
(`i2c_msg.write(addr, [])`, also used by Blinka's `i2c.scan()`) failed. This
made a healthy sensor look absent and sent all debugging in circles. Fixed
in `tof_i2c_switcher.py` by probing with a 1-byte register read instead.

### 4. Only one sensor was actually on the bus

With the above fixed, the script's error ("No sensor responding at 0x29 while
keep_default is held in reset") correctly identified that the second sensor
never answered — its XSHUT/wiring needed attention. Lesson: the XSHUT test
("does `0x29` vanish when the pin goes low?") is the fastest way to prove
which sensor is which.

## Will it survive a reboot? (No — unless you do this)

Two things reset on every boot:

1. **The pinmux poke** — pin 29 becomes an input again.
2. **The sensor's `0x30` address** — VL53 chips always boot at `0x29`.

The fix is two systemd units running in order at boot:

```
tof-xshut-pinmux.service   (pokes pin 29 into GPIO mode)
        ↓ Before=
tof-i2c-switcher.service   (runs monitor: remaps 0x29→0x30, re-remaps after cable yanks)
```

### Install (on the Jetson)

```bash
cd ~/dist_sensor/orin_nano_i2c_switcher

sudo ./install.sh   # installs switcher + pinmux poke unit + deps, enables both
sudo reboot
```

`install.sh` uses `config.json` from this folder on first install; afterwards
edit `/etc/orin_nano_i2c_switcher/config.json` and
`sudo systemctl restart tof-i2c-switcher`.

### Verify after reboot

```bash
sudo busybox devmem 0x2430068     # must print 0x00000008
systemctl status tof-xshut-pinmux tof-i2c-switcher
journalctl -u tof-i2c-switcher -f # want: "Remap OK: 0x29 and 0x30 both present"
sudo i2cdetect -y -r 1            # want: 29 and 30
```

## Manual command reference (debugging)

```bash
# which bus is which pins
i2cdetect -l                        # 27/28 → i2c-1, 3/5 → i2c-7 on this image
sudo i2cdetect -y -r 1              # scan bus 1 (pins 27/28)

# GPIO by name (never trust table line numbers across JetPack versions)
gpiofind "PQ.05"                    # pin 29  → e.g. gpiochip0 81
gpiofind "PN.01"                    # pin 13  → e.g. gpiochip0 85

# drive XSHUT (0 = sensor in reset, 1 = sensor boots). --mode=wait is
# mandatory, otherwise the line floats the instant the command exits.
sudo gpioset --mode=wait gpiochip0 <LINE>=0   # hold reset (Ctrl-C to release)
sudo gpioset --mode=wait gpiochip0 <LINE>=1   # hold awake

# read / poke the pinmux register (busybox required)
sudo busybox devmem 0x2430068          # read; 0x8 = GPIO mode
sudo busybox devmem 0x2430068 w 0x8    # poke

# VL53L0X over I2C (bus 1)
sudo i2ctransfer -y 1 w1@0x29 0xC0 r1  # model ID, expect 0xee
sudo i2ctransfer -y 1 w2@0x29 0x8A 0x30  # rename 0x29 → 0x30 (volatile!)

# helper scripts in this repo
sudo python3 xshut_pulse.py --pin 29 --low-s 10 --high-s 10   # toggle XSHUT
sudo python3 move_addr.py --bus 1                              # rename with checks
```

## App code notes

- **Do not use `busio.I2C(board.SCL, board.SDA)`.** That is header pins 3/5
  (`/dev/i2c-7`). Sensors live on pins 27/28 (`/dev/i2c-1`):

  ```python
  i2c = busio.I2C(board.SCL_1, board.SDA_1)
  ```

  `test_two_sensors.py` already does this. Jupyter needs the `i2c` group
  (`sudo usermod -aG i2c $USER`) and a **full JupyterLab restart**, not just
  a kernel restart.
- Open **one** I2C object; talk to both sensors by address (`0x29`, `0x30`).
- Avoid Blinka's `i2c.scan()` on this platform (zero-length writes, see
  problem 3) — probe specific addresses with real reads/writes instead.
