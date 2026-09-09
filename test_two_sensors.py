#!/usr/bin/env python3
"""Range both VL53 sensors using bus/addresses from config.json.

Prereq: the switcher already ran (both addresses show in i2cdetect).
On Orin Nano JP6, Blinka maps:
  bus 1 (pins 27/28) -> board.SCL_1 / SDA_1
  bus 7 (pins 3/5)   -> board.SCL / SDA
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import adafruit_vl53l0x
import board
import busio

DEFAULT_CONFIGS = (
    "/etc/orin_nano_i2c_switcher/config.json",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"),
)

# Linux bus number -> Blinka pin names (Orin Nano 40-pin header, JP6).
BLINKA_PINS = {
    1: ("SCL_1", "SDA_1"),
    7: ("SCL", "SDA"),
}


def parse_addr(value: Any) -> int:
    if isinstance(value, int):
        return value
    return int(str(value).strip(), 0)


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    return {
        "i2c_bus": int(raw["i2c_bus"]),
        "default_addr": parse_addr(raw["default_addr"]),
        "remapped_addr": parse_addr(raw["remapped_addr"]),
    }


def open_i2c(bus_id: int):
    names = BLINKA_PINS.get(bus_id)
    if names is None:
        known = ", ".join(str(k) for k in sorted(BLINKA_PINS))
        raise SystemExit(
            f"No Blinka pin map for i2c_bus={bus_id} (known: {known}). "
            "Edit BLINKA_PINS in this script."
        )
    scl, sda = (getattr(board, names[0]), getattr(board, names[1]))
    print(f"bus {bus_id}: board.{names[0]} / board.{names[1]}")
    return busio.I2C(scl, sda)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("-c", "--config", help="Path to config.json")
    args, _ = p.parse_known_args()
    path = args.config
    if not path:
        path = next((c for c in DEFAULT_CONFIGS if os.path.isfile(c)), None)
    if not path:
        raise SystemExit("No config.json found (tried /etc and this directory)")

    cfg = load_config(path)
    addr_a, addr_b = cfg["default_addr"], cfg["remapped_addr"]
    print(f"config={path} {addr_a:#x} + {addr_b:#x}")
    i2c = open_i2c(cfg["i2c_bus"])
    try:
        sensor_a = adafruit_vl53l0x.VL53L0X(i2c, address=addr_a)
        sensor_b = adafruit_vl53l0x.VL53L0X(i2c, address=addr_b)
        print("Both sensors connected. Starting measurements...")
        print("-" * 30)
        while True:
            print(
                f"A ({addr_a:#04x}): {sensor_a.range:>4} mm    "
                f"B ({addr_b:#04x}): {sensor_b.range:>4} mm"
            )
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nTest terminated by user.")
        return 0
    except Exception as e:
        print(f"An error occurred: {e}")
        return 1
    finally:
        i2c.deinit()


if __name__ == "__main__":
    sys.exit(main())
