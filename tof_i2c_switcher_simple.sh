#!/usr/bin/env bash
# Rename 0x29 -> 0x28, poke pinmux, drive pin 29 HIGH, then exit.
set -eu

echo "==> step1: 0x29 -> 0x28"
i2ctransfer -y 1 w2@0x29 0x8A 0x28 || echo "no device at 0x29, skip rename"

echo "==> step2: poke pin 29"
busybox devmem 0x2430068 w 0x8
busybox devmem 0x2430068

echo "==> pin 29 HIGH, then release"
# ponytail: --mode=exit drives HIGH and drops the line (high-Z). On this
# JetRacer the pad/pull-up keeps XSHUT high, so the service can exit.
# Ceiling: a board with no pull-up will let XSHUT fall and the 0x29 sensor
# vanishes. Upgrade path: gpioset --mode=signal and Type=simple.
gpioset --mode=exit $(gpiofind PQ.05)=1
echo "==> done"
