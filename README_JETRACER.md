# JetRacer Orin Nano (JetPack 6.2) — two VL53L0X bring-up notes

Field notes from getting two VL53L0X sensors working on a JetRacer / Orin Nano
with JP 6.2. Install path is in [README.md](README.md): `sudo ./simple_install.sh`.

## Working setup

| Item | Value |
|------|-------|
| I2C | Pins **27 (SDA) / 28 (SCL)** → `/dev/i2c-1` |
| Sensor | `vl53l0x` (reg `0xC0` reads `0xEE`) |
| XSHUT | Pin **29** (`PQ.05`) → keep-default sensor (stays **0x29**) |
| Other XSHUT | Float / breakout pull-up → remapped to **0x30** |
| OLED (jetcard) | I2C **bus 7** (pins 3/5). Buttons **13 / 15 / 16 / 18 / 19**. Do not use those pins for ToF. |

The service renames whoever is at `0x29` when it runs. With pin 29 **not** driven low first, that is the chip whose XSHUT is already high (the floating one). Then it pokes pinmux and holds pin 29 HIGH so the second chip boots at `0x29`.

## What went wrong (JP 6.2)

Any one of these looked like “it doesn’t work”.

### 1. `/sys/class/gpio` does not exist

JP6 has no sysfs GPIO export. Use **libgpiod** (`gpioset` / `gpiofind`). Do **not** also open pin 29 with Jetson.GPIO while `gpioset` holds it — that fights `jetcard_display` (OLED buttons).

### 2. Pin 29 boots as an input

GPIO writes do nothing until the pad is GPIO:

```bash
sudo busybox devmem 0x2430068 w 0x8
sudo busybox devmem 0x2430068     # expect 0x00000008
```

This poke is **not** persistent across reboot. The simple service does it every start.

### 3. Tegra I2C rejects zero-length writes

`i2cdetect` can see the chip; Blinka `i2c.scan()` / empty writes fail. Probe with a real read:

```bash
sudo i2ctransfer -y 1 w1@0x29 0xC0 r1   # expect 0xee
```

### 4. Address `0x30` is RAM

VL53 always boots at `0x29` after **power loss** or **XSHUT low**. A Jetson `reboot` often leaves header 3.3 V up, so `0x30` can survive even if systemd is `dead`.

## Boot

`tof-i2c-switcher-simple.service` (`WantedBy=multi-user.target`, `After=dev-i2c-1.device`):

1. `i2ctransfer -y 1 w2@0x29 0x8A 0x30`
2. poke `0x2430068`
3. `gpioset --mode=signal $(gpiofind PQ.05)=1` — **must stay running**

Use `--mode=signal` (systemd). `--mode=wait` waits for Enter, exits, service shows `inactive (dead)` / SUCCESS, pin is **released** (high-Z, not forced 0).

```bash
sudo ./simple_install.sh
systemctl status tof-i2c-switcher-simple   # want: active (running)
ps aux | grep gpioset
sudo i2cdetect -y -r 1                     # want: 29 and 30
```

Uninstall: `sudo ./uninstall.sh`.

## Debug CLI

```bash
i2cdetect -l                         # 27/28 → i2c-1, 3/5 → i2c-7
sudo i2cdetect -y -r 1
gpiofind PQ.05                       # e.g. gpiochip0 81
gpioinfo | grep PQ.05

sudo gpioset --mode=signal $(gpiofind PQ.05)=1   # hold HIGH (Ctrl-C / SIGTERM)
sudo busybox devmem 0x2430068 w 0x8
sudo i2ctransfer -y 1 w2@0x29 0x8A 0x30
```

## App code

Do **not** use `busio.I2C(board.SCL, board.SDA)` (pins 3/5, bus 7). Sensors are on 27/28:

```python
i2c = busio.I2C(board.SCL_1, board.SDA_1)
```

`test_two_sensors.py` already does this. Jupyter: `sudo usermod -aG i2c $USER` and a full JupyterLab restart. Open **one** I2C object; talk to `0x29` and `0x30`. Skip Blinka `i2c.scan()`.
