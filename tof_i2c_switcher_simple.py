#!/usr/bin/env python3
"""Simplest boot sequence from config.json:

1. Rename whoever is at default_addr → remapped_addr
   (XSHUT pin still not driven, so that sensor stays off the bus)
2. Poke pinmux so the XSHUT pin can drive
3. Hold XSHUT HIGH so the second sensor boots at default_addr

Sleeps afterward so the GPIO line stays claimed.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from typing import Any, Dict

import Jetson.GPIO as GPIO
from smbus2 import SMBus, i2c_msg

DEFAULT_CONFIG = "/etc/orin_nano_i2c_switcher/config.json"
L0X_MODELS = {"vl53l0x"}


def parse_addr(value: Any) -> int:
    if isinstance(value, int):
        return value
    return int(str(value).strip(), 0)


def load_config(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, dict):
        raise ValueError(f"Config root must be an object: {path}")
    model = str(raw.get("sensor_model", "vl53l0x")).lower()
    return {
        "i2c_bus": int(raw["i2c_bus"]),
        "default_addr": parse_addr(raw["default_addr"]),
        "remapped_addr": parse_addr(raw["remapped_addr"]),
        "sensor_model": model,
        "gpio_mode": str(raw.get("gpio_mode", "BOARD")).upper(),
        "xshut_keep_default": int(raw["xshut_keep_default"]),
        "pinmux_reg": str(raw.get("pinmux_reg", "0x2430068")),
    }


def probe(bus: SMBus, addr: int, model: str) -> bool:
    try:
        if model in L0X_MODELS:
            bus.i2c_rdwr(i2c_msg.write(addr, [0xC0]), i2c_msg.read(addr, 1))
        else:
            bus.i2c_rdwr(i2c_msg.write(addr, [0x01, 0x0F]), i2c_msg.read(addr, 1))
        return True
    except OSError:
        return False


def write_new_addr(bus: SMBus, model: str, old: int, new: int) -> None:
    if model in L0X_MODELS:
        bus.i2c_rdwr(i2c_msg.write(old, [0x8A, new & 0x7F]))
    else:
        bus.i2c_rdwr(i2c_msg.write(old, [0x00, 0x01, new & 0x7F]))


def poke(reg: str) -> None:
    subprocess.check_call(["busybox", "devmem", reg, "w", "0x8"])
    out = subprocess.check_output(["busybox", "devmem", reg], text=True).strip()
    print(f"pinmux {reg} = {out}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("-c", "--config", default=DEFAULT_CONFIG)
    args = p.parse_args()
    cfg = load_config(args.config)
    old, new = cfg["default_addr"], cfg["remapped_addr"]
    model, pin = cfg["sensor_model"], cfg["xshut_keep_default"]
    print(
        f"config={args.config} bus={cfg['i2c_bus']} "
        f"{old:#x}->{new:#x} pin={pin} model={model}"
    )

    bus = SMBus(cfg["i2c_bus"])
    try:
        if probe(bus, new, model):
            print(f"{new:#x} already present; skip rename")
        elif probe(bus, old, model):
            write_new_addr(bus, model, old, new)
            time.sleep(0.02)
            print(f"wrote {old:#x} -> {new:#x}")
        else:
            print(f"no sensor at {old:#x}; nothing to rename")
            return 1
        print(f"after rename: {old:#x}={probe(bus, old, model)} {new:#x}={probe(bus, new, model)}")

        poke(cfg["pinmux_reg"])
        mode = getattr(GPIO, cfg["gpio_mode"], None)
        if mode is None:
            raise ValueError(f"Unknown gpio_mode {cfg['gpio_mode']!r}")
        GPIO.setmode(mode)
        GPIO.setwarnings(False)
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)
        time.sleep(0.05)
        print(
            f"pin {pin} HIGH; {old:#x}={probe(bus, old, model)} "
            f"{new:#x}={probe(bus, new, model)}"
        )
        print(f"holding pin {pin} HIGH; sleeping")
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0
    finally:
        bus.close()


if __name__ == "__main__":
    sys.exit(main())
