#!/usr/bin/env bash
# One-shot: step0 env, install unit that runs step1 + step2 at boot.
set -eu

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/orin_nano_i2c_switcher"
UNIT=tof-i2c-switcher-simple.service

echo "==> step0: env"
apt-get install -y busybox i2c-tools gpiod
# Blinka + VL53L0X driver are only for test_two_sensors.py (board/busio).
# Boot service uses no Python. --upgrade only these two: old Blinka lacks
# board.SCL_1 on Orin Nano, and this does not touch jetcard's OLED stack.
pip3 install -r "${SCRIPT_DIR}/requirements.txt"
# pip3 install --upgrade adafruit-blinka adafruit-circuitpython-vl53l0x
pip3 install --upgrade --force-reinstall adafruit-blinka

echo "==> stop other ToF units from this repo (may not exist; that is OK)"
systemctl disable --now tof-i2c-switcher.service tof-xshut-pinmux.service 2>/dev/null || true
systemctl unmask "${UNIT}" 2>/dev/null || true

echo "==> install ${UNIT}"
mkdir -p "${INSTALL_DIR}"
install -m 0755 "${SCRIPT_DIR}/tof_i2c_switcher_simple.sh" "${INSTALL_DIR}/tof_i2c_switcher_simple.sh"
install -m 0644 "${SCRIPT_DIR}/systemd/${UNIT}" "/etc/systemd/system/${UNIT}"

systemctl daemon-reload
systemctl enable "${UNIT}"
systemctl restart "${UNIT}"

echo "==> done"
systemctl --no-pager --full status "${UNIT}" || true
echo "logs: journalctl -u ${UNIT} -n 30 --no-pager"
echo "scan: sudo i2cdetect -y -r 1"
