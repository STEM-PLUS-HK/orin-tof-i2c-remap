#!/usr/bin/env python3
"""Pulse an XSHUT pin: LOW (sensor held in reset) then HIGH (sensor boots), repeating.

Use the LOW window to scan the bus / run move_addr.py while the sensor on
this pin is silenced. Ctrl-C to stop; the pin is left HIGH (sensor enabled).
"""
import argparse
import time

import Jetson.GPIO as GPIO


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pin", type=int, default=29, help="BOARD pin (default 29)")
    p.add_argument("--low-s", type=float, default=15)
    p.add_argument("--high-s", type=float, default=15)
    args = p.parse_args()

    GPIO.setmode(GPIO.BOARD)
    GPIO.setup(args.pin, GPIO.OUT, initial=GPIO.HIGH)
    try:
        while True:
            GPIO.output(args.pin, GPIO.LOW)
            print(f"pin {args.pin} LOW  for {args.low_s}s — sensor in reset, scan now", flush=True)
            time.sleep(args.low_s)
            GPIO.output(args.pin, GPIO.HIGH)
            print(f"pin {args.pin} HIGH for {args.high_s}s — sensor awake", flush=True)
            time.sleep(args.high_s)
    except KeyboardInterrupt:
        pass
    finally:
        GPIO.output(args.pin, GPIO.HIGH)  # leave sensor out of reset
        GPIO.cleanup()
        print("stopped; pin left HIGH")


if __name__ == "__main__":
    main()
