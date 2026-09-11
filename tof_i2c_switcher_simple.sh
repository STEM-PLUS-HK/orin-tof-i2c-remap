#!/usr/bin/env bash
# Step 1 + 2 from the manual notes. Stays running so pin 29 stays HIGH.
set -eu

echo "==> step1: 0x29 -> 0x30"
i2ctransfer -y 1 w2@0x29 0x8A 0x30 || echo "no device at 0x29, skip rename"

echo "==> step2: poke pin 29"
busybox devmem 0x2430068 w 0x8
busybox devmem 0x2430068

echo "==> hold PQ.05 HIGH"
exec gpioset --mode=signal $(gpiofind PQ.05)=1
