#!/usr/bin/env bash
# Uninstall ToF XSHUT I2C switcher from Jetson Orin Nano (requires root).
#
# Removes the systemd service, udev rule, and /opt install tree.
# By default keeps /etc/orin_nano_i2c_switcher/config.json.
# Use --purge to also delete config and the lock file.
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo $0 [--purge]" >&2
  exit 1
fi

PURGE=0
for arg in "$@"; do
  case "${arg}" in
    --purge|-p) PURGE=1 ;;
    -h|--help)
      echo "Usage: sudo $0 [--purge]"
      echo "  --purge  also remove /etc/orin_nano_i2c_switcher and lock file"
      exit 0
      ;;
    *)
      echo "Unknown option: ${arg}" >&2
      echo "Usage: sudo $0 [--purge]" >&2
      exit 1
      ;;
  esac
done

INSTALL_DIR="/opt/orin_nano_i2c_switcher"
CONFIG_DIR="/etc/orin_nano_i2c_switcher"
SERVICE_NAME="tof-i2c-switcher.service"
UDEV_DST="/etc/udev/rules.d/99-tof-i2c-remap.rules"
LOCK_PATH="/var/run/tof_i2c_switcher.lock"

echo "==> Stopping and disabling ${SERVICE_NAME}"
systemctl stop "${SERVICE_NAME}" 2>/dev/null || true
systemctl disable "${SERVICE_NAME}" 2>/dev/null || true

SIMPLE_NAME="tof-i2c-switcher-simple.service"
systemctl stop "${SIMPLE_NAME}" 2>/dev/null || true
systemctl disable "${SIMPLE_NAME}" 2>/dev/null || true
rm -f "/etc/systemd/system/${SIMPLE_NAME}" \
      "/etc/systemd/system/multi-user.target.wants/${SIMPLE_NAME}"

PINMUX_SERVICE="tof-xshut-pinmux.service"
systemctl stop "${PINMUX_SERVICE}" 2>/dev/null || true
systemctl disable "${PINMUX_SERVICE}" 2>/dev/null || true
rm -f "/etc/systemd/system/${PINMUX_SERVICE}" \
      "/etc/systemd/system/multi-user.target.wants/${PINMUX_SERVICE}"

if [[ -f "/etc/systemd/system/${SERVICE_NAME}" ]]; then
  echo "==> Removing systemd unit"
  rm -f "/etc/systemd/system/${SERVICE_NAME}"
fi
# Drop any leftover symlink under multi-user.target.wants
rm -f "/etc/systemd/system/multi-user.target.wants/${SERVICE_NAME}"

if [[ -f "${UDEV_DST}" ]]; then
  echo "==> Removing udev rule"
  rm -f "${UDEV_DST}"
fi

if [[ -d "${INSTALL_DIR}" ]]; then
  echo "==> Removing ${INSTALL_DIR}"
  rm -rf "${INSTALL_DIR}"
fi

if [[ "${PURGE}" -eq 1 ]]; then
  if [[ -d "${CONFIG_DIR}" ]]; then
    echo "==> Purging ${CONFIG_DIR}"
    rm -rf "${CONFIG_DIR}"
  fi
  if [[ -e "${LOCK_PATH}" ]]; then
    echo "==> Removing lock ${LOCK_PATH}"
    rm -f "${LOCK_PATH}"
  fi
else
  if [[ -d "${CONFIG_DIR}" ]]; then
    echo "==> Keeping ${CONFIG_DIR} (pass --purge to delete)"
  fi
fi

udevadm control --reload-rules 2>/dev/null || true
systemctl daemon-reload

echo "==> Uninstall complete"
if systemctl list-units --full -all "${SERVICE_NAME}" 2>/dev/null | grep -q "${SERVICE_NAME}"; then
  systemctl --no-pager --full status "${SERVICE_NAME}" || true
else
  echo "Service ${SERVICE_NAME} is gone."
fi
