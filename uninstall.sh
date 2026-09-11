#!/usr/bin/env bash
set -eu
if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

systemctl disable --now tof-i2c-switcher-simple.service 2>/dev/null || true
systemctl disable --now tof-i2c-switcher.service tof-xshut-pinmux.service 2>/dev/null || true
systemctl unmask tof-i2c-switcher-simple.service tof-i2c-switcher.service tof-xshut-pinmux.service 2>/dev/null || true

rm -f /etc/systemd/system/tof-i2c-switcher-simple.service \
      /etc/systemd/system/tof-i2c-switcher.service \
      /etc/systemd/system/tof-xshut-pinmux.service \
      /etc/udev/rules.d/99-tof-i2c-remap.rules

rm -rf /opt/orin_nano_i2c_switcher
# optional: rm -rf /etc/orin_nano_i2c_switcher

systemctl daemon-reload
echo "uninstalled"