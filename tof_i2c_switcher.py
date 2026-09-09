#!/usr/bin/env python3
"""Jetson Orin Nano: remap one of two VL53 ToF sensors sharing the same I2C address via XSHUT."""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

try:
    import smbus2
    from smbus2 import i2c_msg
except ImportError:  # pragma: no cover
    smbus2 = None  # type: ignore
    i2c_msg = None  # type: ignore

try:
    import Jetson.GPIO as GPIO
except ImportError:  # pragma: no cover
    GPIO = None  # type: ignore

LOG = logging.getLogger("tof_i2c_switcher")

DEFAULT_LOCK_PATH = "/var/run/tof_i2c_switcher.lock"

# ---------------------------------------------------------------------------
# Sensor profiles: address-change register and boot behaviour per chip family
# ---------------------------------------------------------------------------

SENSOR_PROFILES: Dict[str, Dict[str, Any]] = {
    "vl53l0x": {
        "reg_width": 8,
        "addr_reg": 0x8A,
        "boot_ms": 5,
        "boot_status_reg": None,
        "boot_status_value": None,
    },
    "vl53l1x": {
        "reg_width": 16,
        "addr_reg": 0x0001,
        "boot_ms": 40,
        "boot_status_reg": 0x00E5,
        "boot_status_value": 0x03,
    },
    "vl53l1cb": {
        "reg_width": 16,
        "addr_reg": 0x0001,
        "boot_ms": 40,
        "boot_status_reg": 0x00E5,
        "boot_status_value": 0x03,
    },
    "vl53l3cx": {
        "reg_width": 16,
        "addr_reg": 0x0001,
        "boot_ms": 40,
        "boot_status_reg": 0x00E5,
        "boot_status_value": 0x03,
    },
    "vl53l4cd": {
        "reg_width": 16,
        "addr_reg": 0x0001,
        "boot_ms": 40,
        "boot_status_reg": None,
        "boot_status_value": None,
    },
    "vl53l4cx": {
        "reg_width": 16,
        "addr_reg": 0x0001,
        "boot_ms": 40,
        "boot_status_reg": None,
        "boot_status_value": None,
    },
}


def parse_addr(value: Any) -> int:
    """Parse an I2C address from int or hex/decimal string."""
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        value = value.strip().lower()
        return int(value, 0)
    raise TypeError(f"Unsupported address type: {type(value)!r}")


def default_config() -> Dict[str, Any]:
    return {
        "i2c_bus": 1,
        "default_addr": "0x29",
        "remapped_addr": "0x30",
        "sensor_model": "vl53l1x",
        "gpio_mode": "BOARD",
        "xshut_keep_default": 29,
        "xshut_remapped": None,
        "xshut_active_low": True,
        "boot_timeout_ms": 100,
        "poll_interval_s": 2,
        "settle_ms": 5,
        "max_retries": 3,
        "lock_path": DEFAULT_LOCK_PATH,
    }


def load_config(path: Optional[str]) -> Dict[str, Any]:
    cfg = default_config()
    if path:
        with open(path, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        if not isinstance(loaded, dict):
            raise ValueError(f"Config root must be a JSON object: {path}")
        cfg.update(loaded)
    return cfg


@dataclass
class SwitcherConfig:
    i2c_bus: int
    default_addr: int
    remapped_addr: int
    sensor_model: str
    gpio_mode: str
    xshut_keep_default: int
    xshut_remapped: Optional[int]
    xshut_active_low: bool
    boot_timeout_ms: int
    poll_interval_s: float
    settle_ms: int
    max_retries: int
    lock_path: str

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "SwitcherConfig":
        model = str(raw.get("sensor_model", "vl53l1x")).lower()
        if model not in SENSOR_PROFILES:
            known = ", ".join(sorted(SENSOR_PROFILES))
            raise ValueError(f"Unknown sensor_model {model!r}; choose one of: {known}")
        remapped_pin = raw.get("xshut_remapped", None)
        if remapped_pin is not None:
            remapped_pin = int(remapped_pin)
        return cls(
            i2c_bus=int(raw["i2c_bus"]),
            default_addr=parse_addr(raw["default_addr"]),
            remapped_addr=parse_addr(raw["remapped_addr"]),
            sensor_model=model,
            gpio_mode=str(raw.get("gpio_mode", "BOARD")).upper(),
            xshut_keep_default=int(raw["xshut_keep_default"]),
            xshut_remapped=remapped_pin,
            xshut_active_low=bool(raw.get("xshut_active_low", True)),
            boot_timeout_ms=int(raw.get("boot_timeout_ms", 100)),
            poll_interval_s=float(raw.get("poll_interval_s", 2)),
            settle_ms=int(raw.get("settle_ms", 5)),
            max_retries=int(raw.get("max_retries", 3)),
            lock_path=str(raw.get("lock_path", DEFAULT_LOCK_PATH)),
        )

    @property
    def profile(self) -> Dict[str, Any]:
        return SENSOR_PROFILES[self.sensor_model]


# ---------------------------------------------------------------------------
# File lock (udev + systemd must not overlap)
# ---------------------------------------------------------------------------

class FileLock:
    def __init__(self, path: str):
        self.path = path
        self._fh: Optional[Any] = None

    def acquire(self, blocking: bool = True) -> bool:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._fh = open(self.path, "a+", encoding="utf-8")
        flags = fcntl.LOCK_EX
        if not blocking:
            flags |= fcntl.LOCK_NB
        try:
            fcntl.flock(self._fh.fileno(), flags)
            return True
        except BlockingIOError:
            self._fh.close()
            self._fh = None
            return False

    def release(self) -> None:
        if self._fh is not None:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            finally:
                self._fh.close()
                self._fh = None

    def __enter__(self) -> "FileLock":
        if not self.acquire(blocking=True):
            raise RuntimeError(f"Failed to acquire lock {self.path}")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


# ---------------------------------------------------------------------------
# I2C helpers
# ---------------------------------------------------------------------------

class I2CBus:
    def __init__(self, bus_id: int):
        if smbus2 is None:
            raise RuntimeError("smbus2 is required; pip install smbus2")
        self.bus_id = bus_id
        self._bus = smbus2.SMBus(bus_id)

    def close(self) -> None:
        self._bus.close()

    def probe(self, addr: int, profile: Optional[Dict[str, Any]] = None) -> bool:
        """Return True if the 7-bit address ACKs a register read."""
        # ponytail: Tegra I2C rejects zero-length writes. Read the model-ID
        # register instead of pointer 0x00 (VL53L0X 0x00 is SYSRANGE_START;
        # poking it while an app is ranging makes probes flake).
        try:
            if profile is not None and int(profile.get("reg_width", 8)) == 16:
                self.read_reg16(addr, 0x010F)
            else:
                self.read_reg8(addr, 0xC0)
            return True
        except OSError:
            return False

    def write_reg8(self, addr: int, reg: int, value: int) -> None:
        msg = i2c_msg.write(addr, [reg & 0xFF, value & 0xFF])
        self._bus.i2c_rdwr(msg)

    def write_reg16(self, addr: int, reg: int, value: int) -> None:
        msg = i2c_msg.write(addr, [(reg >> 8) & 0xFF, reg & 0xFF, value & 0xFF])
        self._bus.i2c_rdwr(msg)

    def read_reg8(self, addr: int, reg: int) -> int:
        w = i2c_msg.write(addr, [reg & 0xFF])
        r = i2c_msg.read(addr, 1)
        self._bus.i2c_rdwr(w, r)
        return list(r)[0]

    def read_reg16(self, addr: int, reg: int) -> int:
        w = i2c_msg.write(addr, [(reg >> 8) & 0xFF, reg & 0xFF])
        r = i2c_msg.read(addr, 1)
        self._bus.i2c_rdwr(w, r)
        return list(r)[0]

    def write_address_register(self, profile: Dict[str, Any], current_addr: int, new_addr: int) -> None:
        reg = int(profile["addr_reg"])
        if profile["reg_width"] == 8:
            self.write_reg8(current_addr, reg, new_addr & 0x7F)
        else:
            self.write_reg16(current_addr, reg, new_addr & 0x7F)

    def wait_boot(self, profile: Dict[str, Any], addr: int, timeout_ms: int) -> bool:
        """Wait until the sensor boots at addr. Prefer status poll when available."""
        boot_ms = int(profile.get("boot_ms", 5))
        status_reg = profile.get("boot_status_reg")
        status_value = profile.get("boot_status_value")
        deadline = time.monotonic() + (timeout_ms / 1000.0)

        if status_reg is None or status_value is None:
            time.sleep(max(boot_ms, 1) / 1000.0)
            return self.probe(addr, profile)

        # Initial short settle then poll
        time.sleep(min(boot_ms, 5) / 1000.0)
        while time.monotonic() < deadline:
            try:
                if profile["reg_width"] == 8:
                    val = self.read_reg8(addr, int(status_reg))
                else:
                    val = self.read_reg16(addr, int(status_reg))
                if val == int(status_value):
                    return True
            except OSError:
                pass
            time.sleep(0.002)
        # Fall back to presence probe after timeout
        return self.probe(addr, profile)


# ---------------------------------------------------------------------------
# GPIO / XSHUT
# ---------------------------------------------------------------------------

class XshutController:
    """Drive one or two XSHUT pins. Active-low reset by default."""

    def __init__(self, cfg: SwitcherConfig):
        if GPIO is None:
            raise RuntimeError("Jetson.GPIO is required; install Jetson.GPIO")
        self.cfg = cfg
        self._owned = False
        mode = getattr(GPIO, cfg.gpio_mode, None)
        if mode is None:
            raise ValueError(f"Unknown gpio_mode {cfg.gpio_mode!r}")
        GPIO.setmode(mode)
        GPIO.setwarnings(False)
        pins = [cfg.xshut_keep_default]
        if cfg.xshut_remapped is not None:
            pins.append(cfg.xshut_remapped)
        for pin in pins:
            GPIO.setup(pin, GPIO.OUT, initial=self._inactive_level())
        self._owned = True

    def _active_level(self) -> int:
        # Active = hold in reset. Active-low => LOW resets.
        return GPIO.LOW if self.cfg.xshut_active_low else GPIO.HIGH

    def _inactive_level(self) -> int:
        return GPIO.HIGH if self.cfg.xshut_active_low else GPIO.LOW

    def _settle(self) -> None:
        time.sleep(self.cfg.settle_ms / 1000.0)

    def hold_reset(self, pin: int) -> None:
        GPIO.output(pin, self._active_level())
        self._settle()

    def release(self, pin: int) -> None:
        GPIO.output(pin, self._inactive_level())
        self._settle()

    def hold_all_reset(self) -> None:
        self.hold_reset(self.cfg.xshut_keep_default)
        if self.cfg.xshut_remapped is not None:
            self.hold_reset(self.cfg.xshut_remapped)

    def cleanup(self) -> None:
        if self._owned and GPIO is not None:
            # Leave sensors enabled (inactive = out of reset) before cleanup
            try:
                self.release(self.cfg.xshut_keep_default)
                if self.cfg.xshut_remapped is not None:
                    self.release(self.cfg.xshut_remapped)
            except Exception:  # noqa: BLE001
                pass
            GPIO.cleanup()
            self._owned = False


# ---------------------------------------------------------------------------
# Remap sequence
# ---------------------------------------------------------------------------

def bus_health(bus: I2CBus, cfg: SwitcherConfig) -> Dict[str, bool]:
    profile = cfg.profile
    return {
        "default": bus.probe(cfg.default_addr, profile),
        "remapped": bus.probe(cfg.remapped_addr, profile),
    }


def is_healthy(health: Dict[str, bool]) -> bool:
    return bool(health["default"] and health["remapped"])


def remap_addresses(bus: I2CBus, xshut: XshutController, cfg: SwitcherConfig) -> bool:
    """
    Hold sensor A (keep_default) in reset, remap sensor B to remapped_addr,
    then release A so it boots at default_addr.

    If remapped_addr is already live, skip the address write — only release
    XSHUT. Leaving keep_default held low on failure is what made i2cdetect
    flicker (only 0x30, then only 0x29).
    """
    profile = cfg.profile
    ok = False
    LOG.info(
        "Starting remap: bus=%s model=%s keep=%#x -> remap=%#x",
        cfg.i2c_bus,
        cfg.sensor_model,
        cfg.default_addr,
        cfg.remapped_addr,
    )
    try:
        # 0x30 already there: A is either muted (XSHUT low) or a false NACK.
        # Do not hold-reset + write — that is the flicker loop.
        if bus.probe(cfg.remapped_addr, profile):
            LOG.info("%#x already present; releasing keep-default XSHUT only", cfg.remapped_addr)
            xshut.release(cfg.xshut_keep_default)
            if not bus.wait_boot(profile, cfg.default_addr, cfg.boot_timeout_ms):
                LOG.error("Keep-default sensor did not appear at %#x after XSHUT release", cfg.default_addr)
                return False
            health = bus_health(bus, cfg)
            ok = is_healthy(health)
            if ok:
                LOG.info("Recovered: %#x and %#x both present", cfg.default_addr, cfg.remapped_addr)
            return ok

        if cfg.xshut_remapped is not None:
            xshut.hold_all_reset()
            time.sleep(cfg.settle_ms / 1000.0)
            xshut.release(cfg.xshut_remapped)
            if not bus.wait_boot(profile, cfg.default_addr, cfg.boot_timeout_ms):
                LOG.error("Remapped sensor did not boot at %#x after XSHUT release", cfg.default_addr)
                return False
        else:
            xshut.hold_reset(cfg.xshut_keep_default)
            if not bus.probe(cfg.default_addr, profile):
                if not bus.wait_boot(profile, cfg.default_addr, cfg.boot_timeout_ms):
                    LOG.error(
                        "No sensor responding at %#x while keep_default is held in reset",
                        cfg.default_addr,
                    )
                    return False

        try:
            bus.write_address_register(profile, cfg.default_addr, cfg.remapped_addr)
        except OSError as exc:
            LOG.error("Failed to write new I2C address: %s", exc)
            return False

        time.sleep(cfg.settle_ms / 1000.0)

        if not bus.probe(cfg.remapped_addr, profile):
            LOG.error("Sensor did not respond at new address %#x after remap", cfg.remapped_addr)
            return False
        LOG.info("Remapped sensor now at %#x", cfg.remapped_addr)

        xshut.release(cfg.xshut_keep_default)
        if not bus.wait_boot(profile, cfg.default_addr, cfg.boot_timeout_ms):
            LOG.error("Keep-default sensor did not appear at %#x after XSHUT release", cfg.default_addr)
            return False

        health = bus_health(bus, cfg)
        if not is_healthy(health):
            LOG.error(
                "Post-remap verify failed: default=%s remapped=%s",
                health["default"],
                health["remapped"],
            )
            return False

        LOG.info("Remap OK: %#x and %#x both present", cfg.default_addr, cfg.remapped_addr)
        ok = True
        return True
    finally:
        if not ok:
            try:
                xshut.release(cfg.xshut_keep_default)
                if cfg.xshut_remapped is not None:
                    xshut.release(cfg.xshut_remapped)
            except Exception:  # noqa: BLE001
                pass


def remap_with_retries(bus: I2CBus, xshut: XshutController, cfg: SwitcherConfig) -> bool:
    for attempt in range(1, cfg.max_retries + 1):
        LOG.info("Remap attempt %d/%d", attempt, cfg.max_retries)
        if remap_addresses(bus, xshut, cfg):
            return True
        time.sleep(0.05 * attempt)
    return False


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_status(cfg: SwitcherConfig) -> int:
    bus = I2CBus(cfg.i2c_bus)
    try:
        health = bus_health(bus, cfg)
        print(f"bus={cfg.i2c_bus} model={cfg.sensor_model}")
        print(f"  {cfg.default_addr:#x} (default):  {'ACK' if health['default'] else 'NACK'}")
        print(f"  {cfg.remapped_addr:#x} (remapped): {'ACK' if health['remapped'] else 'NACK'}")
        print(f"healthy={is_healthy(health)}")
        return 0 if is_healthy(health) else 1
    finally:
        bus.close()


def cmd_init(cfg: SwitcherConfig) -> int:
    with FileLock(cfg.lock_path):
        bus = I2CBus(cfg.i2c_bus)
        xshut = XshutController(cfg)
        try:
            health = bus_health(bus, cfg)
            if is_healthy(health):
                LOG.info("Already healthy; skipping remap")
                return 0
            ok = remap_with_retries(bus, xshut, cfg)
            return 0 if ok else 2
        finally:
            xshut.cleanup()
            bus.close()


def cmd_monitor(cfg: SwitcherConfig) -> int:
    bus = I2CBus(cfg.i2c_bus)
    xshut = XshutController(cfg)
    backoff_s = cfg.poll_interval_s
    failures = 0
    try:
        # Initial remap under lock
        with FileLock(cfg.lock_path):
            health = bus_health(bus, cfg)
            if not is_healthy(health):
                if not remap_with_retries(bus, xshut, cfg):
                    LOG.error("Initial remap failed; will keep retrying in monitor loop")
                    failures += 1
                else:
                    failures = 0
            else:
                LOG.info("Initial bus state healthy")

        LOG.info("Monitor loop started (poll every %.2fs)", cfg.poll_interval_s)
        streak = 0
        while True:
            time.sleep(cfg.poll_interval_s)
            health = bus_health(bus, cfg)
            if is_healthy(health):
                if failures:
                    LOG.info("Bus recovered / healthy")
                failures = 0
                streak = 0
                backoff_s = cfg.poll_interval_s
                continue

            streak += 1
            if streak < 2:
                LOG.warning(
                    "Unhealthy once (default=%s remapped=%s) — confirming next poll",
                    health["default"],
                    health["remapped"],
                )
                continue

            LOG.warning(
                "Unhealthy bus: default=%s remapped=%s — remapping",
                health["default"],
                health["remapped"],
            )
            streak = 0
            with FileLock(cfg.lock_path):
                # Re-check under lock in case another instance fixed it
                health = bus_health(bus, cfg)
                if is_healthy(health):
                    continue
                ok = remap_with_retries(bus, xshut, cfg)
            if ok:
                failures = 0
                backoff_s = cfg.poll_interval_s
            else:
                failures += 1
                backoff_s = min(cfg.poll_interval_s * (2 ** min(failures, 4)), 60.0)
                LOG.error("Remap failed (%d); backing off %.1fs", failures, backoff_s)
                time.sleep(backoff_s)
    except KeyboardInterrupt:
        LOG.info("Monitor stopped by user")
        return 0
    finally:
        xshut.cleanup()
        bus.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Remap one of two VL53 ToF sensors on the same I2C bus using XSHUT",
    )
    p.add_argument(
        "command",
        choices=("init", "monitor", "status"),
        help="init: remap once; monitor: remap + watch; status: probe addresses",
    )
    p.add_argument("--config", "-c", help="Path to config.json")
    p.add_argument("--bus", type=int, help="I2C bus number (e.g. 1)")
    p.add_argument("--model", choices=sorted(SENSOR_PROFILES.keys()), help="Sensor model profile")
    p.add_argument("--default-addr", help='Default address, e.g. "0x29"')
    p.add_argument("--remap-addr", help='Remapped address, e.g. "0x30"')
    p.add_argument("--xshut-keep", type=int, help="BOARD pin for sensor that stays at default_addr")
    p.add_argument("--xshut-remap", type=int, help="BOARD pin for sensor to remap (optional)")
    p.add_argument("--poll-interval", type=float, help="Monitor poll interval seconds")
    p.add_argument("--lock-path", help="Exclusive lock file path")
    p.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    return p


def apply_cli_overrides(raw: Dict[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    if args.bus is not None:
        raw["i2c_bus"] = args.bus
    if args.model is not None:
        raw["sensor_model"] = args.model
    if args.default_addr is not None:
        raw["default_addr"] = args.default_addr
    if args.remap_addr is not None:
        raw["remapped_addr"] = args.remap_addr
    if args.xshut_keep is not None:
        raw["xshut_keep_default"] = args.xshut_keep
    if args.xshut_remap is not None:
        raw["xshut_remapped"] = args.xshut_remap
    if args.poll_interval is not None:
        raw["poll_interval_s"] = args.poll_interval
    if args.lock_path is not None:
        raw["lock_path"] = args.lock_path
    return raw


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        raw = load_config(args.config)
        raw = apply_cli_overrides(raw, args)
        cfg = SwitcherConfig.from_dict(raw)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        LOG.error("Config error: %s", exc)
        return 1

    commands: Dict[str, Callable[[SwitcherConfig], int]] = {
        "init": cmd_init,
        "monitor": cmd_monitor,
        "status": cmd_status,
    }
    return commands[args.command](cfg)


if __name__ == "__main__":
    sys.exit(main())
