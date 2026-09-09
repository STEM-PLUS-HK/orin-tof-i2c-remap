#!/usr/bin/env bash
# Simplest ToF bring-up: env → rename 0x29→0x30 → poke pin 29 → hold HIGH.
# Does NOT start tof-xshut-pinmux.service first (that would wake sensor A
# before the address write). Poke is inside tof_i2c_switcher_simple.py.
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/orin_nano_i2c_switcher"
CONFIG_DIR="/etc/orin_nano_i2c_switcher"
SIMPLE_SRC="${SCRIPT_DIR}/tof_i2c_switcher_simple.py"
SIMPLE_UNIT_SRC="${SCRIPT_DIR}/systemd/tof-i2c-switcher-simple.service"
SIMPLE_NAME="tof-i2c-switcher-simple.service"
FANCY_NAME="tof-i2c-switcher.service"
PINMUX_NAME="tof-xshut-pinmux.service"

if [[ ! -f "${SIMPLE_SRC}" ]]; then
  echo "Missing ${SIMPLE_SRC}" >&2
  exit 1
fi

echo "==> Step 0: env (busybox + Python deps)"
if ! command -v busybox >/dev/null 2>&1; then
  apt-get install -y busybox
fi
if command -v pip3 >/dev/null 2>&1; then
  if [[ -f "${SCRIPT_DIR}/requirements.txt" ]]; then
    pip3 install --upgrade -r "${SCRIPT_DIR}/requirements.txt"
  else
    pip3 install --upgrade smbus2 Jetson.GPIO adafruit-circuitpython-vl53l0x
  fi
else
  echo "pip3 not found" >&2
  exit 1
fi

echo "==> Stopping fancy monitor / early pinmux (must not poke before rename)"
systemctl stop "${FANCY_NAME}" 2>/dev/null || true
systemctl disable "${FANCY_NAME}" 2>/dev/null || true
systemctl stop "${PINMUX_NAME}" 2>/dev/null || true
systemctl disable "${PINMUX_NAME}" 2>/dev/null || true

if [[ -f /etc/udev/rules.d/99-tof-i2c-remap.rules ]]; then
  rm -f /etc/udev/rules.d/99-tof-i2c-remap.rules
  udevadm control --reload-rules 2>/dev/null || true
fi

echo "==> Installing ${SIMPLE_NAME}"
mkdir -p "${INSTALL_DIR}/systemd" "${CONFIG_DIR}"
install -m 0755 "${SIMPLE_SRC}" "${INSTALL_DIR}/tof_i2c_switcher_simple.py"
install -m 0755 "${SCRIPT_DIR}/simple_install.sh" "${INSTALL_DIR}/simple_install.sh"
install -m 0644 "${SIMPLE_UNIT_SRC}" "${INSTALL_DIR}/systemd/tof-i2c-switcher-simple.service"
if [[ -f "${SCRIPT_DIR}/config.json" ]]; then
  install -m 0644 "${SCRIPT_DIR}/config.json" "${INSTALL_DIR}/config.json.example"
  if [[ -f "${CONFIG_DIR}/config.json" ]]; then
    echo "==> Keeping existing ${CONFIG_DIR}/config.json"
  else
    echo "==> Installing ${CONFIG_DIR}/config.json"
    install -m 0644 "${SCRIPT_DIR}/config.json" "${CONFIG_DIR}/config.json"
  fi
fi

I2C_BUS="$(python3 -c "import json; print(json.load(open('${CONFIG_DIR}/config.json'))['i2c_bus'])")"
echo "==> Using i2c_bus=${I2C_BUS} from ${CONFIG_DIR}/config.json"
sed "s/dev-i2c-1\\.device/dev-i2c-${I2C_BUS}.device/g" "${SIMPLE_UNIT_SRC}" \
  > "/etc/systemd/system/${SIMPLE_NAME}"

systemctl daemon-reload
systemctl enable "${SIMPLE_NAME}"
systemctl restart "${SIMPLE_NAME}"

echo "==> Done"
systemctl --no-pager --full status "${SIMPLE_NAME}" || true
echo
echo "Logs:    journalctl -u tof-i2c-switcher-simple -f"
echo "Config:  ${CONFIG_DIR}/config.json"
echo "Scan:    sudo i2cdetect -y -r ${I2C_BUS}"
echo "Disable: sudo systemctl disable --now ${SIMPLE_NAME}"
