#!/usr/bin/env bash
# Install or upgrade ToF XSHUT I2C switcher on Jetson Orin Nano (requires root).
# Re-running this always refreshes scripts, systemd unit, and udev rules.
# Existing /etc/orin_nano_i2c_switcher/config.json is preserved.
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/orin_nano_i2c_switcher"
CONFIG_DIR="/etc/orin_nano_i2c_switcher"
SERVICE_NAME="tof-i2c-switcher.service"
SERVICE_SRC="${SCRIPT_DIR}/systemd/tof-i2c-switcher.service"
UDEV_SRC="${SCRIPT_DIR}/udev/99-tof-i2c-remap.rules"
UDEV_DST="/etc/udev/rules.d/99-tof-i2c-remap.rules"

if [[ ! -f "${SCRIPT_DIR}/tof_i2c_switcher.py" ]]; then
  echo "Missing ${SCRIPT_DIR}/tof_i2c_switcher.py" >&2
  exit 1
fi

# Stop running service before replacing files (ok if not installed yet).
systemctl stop "${SERVICE_NAME}" 2>/dev/null || true
systemctl stop tof-i2c-switcher-simple.service 2>/dev/null || true
systemctl disable tof-i2c-switcher-simple.service 2>/dev/null || true

echo "==> Installing / upgrading files under ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}" "${CONFIG_DIR}"

# Always overwrite application files so updates take effect.
install -m 0755 "${SCRIPT_DIR}/tof_i2c_switcher.py" "${INSTALL_DIR}/tof_i2c_switcher.py"
install -m 0755 "${SCRIPT_DIR}/test_two_sensors.py" "${INSTALL_DIR}/test_two_sensors.py"
install -m 0755 "${SCRIPT_DIR}/install.sh" "${INSTALL_DIR}/install.sh"
install -m 0755 "${SCRIPT_DIR}/uninstall.sh" "${INSTALL_DIR}/uninstall.sh"
install -m 0644 "${SCRIPT_DIR}/requirements.txt" "${INSTALL_DIR}/requirements.txt"
install -m 0644 "${SCRIPT_DIR}/config.json" "${INSTALL_DIR}/config.json.example"
if [[ -f "${SCRIPT_DIR}/README.md" ]]; then
  install -m 0644 "${SCRIPT_DIR}/README.md" "${INSTALL_DIR}/README.md"
fi
# Keep packaged systemd/udev sources for reference / re-install from /opt.
mkdir -p "${INSTALL_DIR}/systemd" "${INSTALL_DIR}/udev"
install -m 0644 "${SERVICE_SRC}" "${INSTALL_DIR}/systemd/tof-i2c-switcher.service"
install -m 0644 "${UDEV_SRC}" "${INSTALL_DIR}/udev/99-tof-i2c-remap.rules"

if [[ -f "${CONFIG_DIR}/config.json" ]]; then
  echo "==> Keeping existing ${CONFIG_DIR}/config.json (not overwritten)"
  echo "    New defaults are at ${INSTALL_DIR}/config.json.example"
else
  echo "==> Installing default ${CONFIG_DIR}/config.json"
  install -m 0644 "${SCRIPT_DIR}/config.json" "${CONFIG_DIR}/config.json"
fi

echo "==> Installing / upgrading Python dependencies"
if command -v pip3 >/dev/null 2>&1; then
  pip3 install --upgrade -r "${INSTALL_DIR}/requirements.txt"
else
  echo "pip3 not found; install smbus2 and Jetson.GPIO manually" >&2
fi

# busybox provides devmem for the pinmux poke unit.
if ! command -v busybox >/dev/null 2>&1; then
  echo "==> Installing busybox (needed for pinmux poke)"
  apt-get install -y busybox || echo "busybox install failed; pinmux unit will not work" >&2
fi

# Pinmux poke unit: puts the XSHUT pin into GPIO mode at boot (JP6 boots it
# as input, and the poke does not survive reboots).
if [[ -f "${SCRIPT_DIR}/systemd/tof-xshut-pinmux.service" ]]; then
  echo "==> Installing pinmux poke unit"
  install -m 0644 "${SCRIPT_DIR}/systemd/tof-xshut-pinmux.service" \
    "/etc/systemd/system/tof-xshut-pinmux.service"
fi

I2C_BUS="$(python3 -c "import json; print(json.load(open('${CONFIG_DIR}/config.json'))['i2c_bus'])")"
echo "==> Using i2c_bus=${I2C_BUS} from ${CONFIG_DIR}/config.json"

echo "==> Installing / replacing systemd unit"
sed "s/dev-i2c-1\\.device/dev-i2c-${I2C_BUS}.device/g" "${SERVICE_SRC}" \
  > "/etc/systemd/system/${SERVICE_NAME}"

echo "==> Installing / replacing udev rule"
sed "s/i2c-1/i2c-${I2C_BUS}/g" "${UDEV_SRC}" > "${UDEV_DST}"

udevadm control --reload-rules
systemctl daemon-reload
if [[ -f "/etc/systemd/system/tof-xshut-pinmux.service" ]]; then
  systemctl enable tof-xshut-pinmux.service
  systemctl restart tof-xshut-pinmux.service
fi
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

echo "==> Done (scripts, unit, and udev refreshed)"
systemctl --no-pager --full status "${SERVICE_NAME}" || true
echo
echo "Edit config:  ${CONFIG_DIR}/config.json"
echo "Status:       ${INSTALL_DIR}/tof_i2c_switcher.py status -c ${CONFIG_DIR}/config.json"
echo "Logs:         journalctl -u tof-i2c-switcher -f"
echo "Uninstall:    sudo ${INSTALL_DIR}/uninstall.sh"
echo "              (or sudo ${SCRIPT_DIR}/uninstall.sh)"
