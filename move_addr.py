#!/usr/bin/env python3
"""Rename a VL53L0X's I2C address (e.g. 0x29 -> 0x30).

Run this while ONLY the sensor you want to rename is awake on the bus
(e.g. during the LOW window of xshut_pulse.py). Anything else answering
at the old address would get renamed too.
"""
import argparse
import sys
import time

from smbus2 import SMBus, i2c_msg

ADDR_REG = 0x8A  # VL53L0X I2C address register
ID_REG = 0xC0    # VL53L0X model ID register (reads 0xEE)


def probe(bus, addr):
    # ponytail: zero-length writes are rejected by the Tegra I2C driver, so
    # probe with a 1-byte register read instead of i2c_msg.write(addr, []).
    try:
        bus.i2c_rdwr(i2c_msg.write(addr, [ID_REG]), i2c_msg.read(addr, 1))
        return True
    except OSError:
        return False


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bus", type=int, default=1)
    p.add_argument("--from", dest="old", default="0x29", help="current address (default 0x29)")
    p.add_argument("--to", dest="new", default="0x30", help="new address (default 0x30)")
    args = p.parse_args()

    old, new = int(args.old, 0), int(args.new, 0)
    bus = SMBus(args.bus)

    if not probe(bus, old):
        print(f"FAIL: nothing answering at {old:#x} on bus {args.bus}")
        return 1
    if probe(bus, new):
        print(f"NOTE: {new:#x} already answers; a sensor may already be renamed")

    bus.i2c_rdwr(i2c_msg.write(old, [ADDR_REG, new & 0x7F]))
    time.sleep(0.01)

    if probe(bus, new) and not probe(bus, old):
        print(f"OK: {old:#x} -> {new:#x} (lasts until sensor loses power/reset)")
        return 0
    print(f"FAIL: write done but new={probe(bus, new)} old={probe(bus, old)}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
