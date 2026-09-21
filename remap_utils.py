"""Auto-detect and fix the VL53 ToF 0x29/0x30 remap state.

Used by reconnect.ipynb (the TLDR "Resolve!" button). Same steps as the
manual cells: detect with i2cdetect, then Fix A (wake XSHUT) or
Fix B (full re-remap). This file must sit next to the notebook.

CLI self-check:  python3 remap_utils.py
"""
import re
import subprocess

SUDO_PASSWORD = "jetson"  # default Jetson password; change if you changed it
SERVICE = "tof-i2c-switcher-simple.service"


def _sudo(*cmd):
    return subprocess.run(
        ["sudo", "-S", *cmd],
        input=SUDO_PASSWORD + "\n", capture_output=True, text=True,
    )


def _xshut(value):
    """gpioset --mode=exit $(gpiofind PQ.05)=<value>"""
    parts = subprocess.run(["gpiofind", "PQ.05"], capture_output=True, text=True).stdout.split()
    if len(parts) != 2:
        raise RuntimeError("gpiofind PQ.05 failed — is gpiod installed?")
    chip, line = parts  # e.g. "gpiochip0", "81"
    return _sudo("gpioset", "--mode=exit", chip, f"{line}={value}")


def detect():
    """Return (has29, has30, raw_i2cdetect_output)."""
    p = _sudo("i2cdetect", "-y", "-r", "1")
    if p.returncode != 0:
        raise RuntimeError(f"i2cdetect failed: {p.stderr.strip()}")
    found = set()
    for line in p.stdout.splitlines():
        if re.match(r"^[0-7]0:", line):      # grid rows look like '30: -- -- 33 ...'
            found.update(line[4:].split())   # skip the row label, keep cell values
    return "29" in found, "30" in found, p.stdout


def resolve(log=print):
    """Detect, apply the matching fix, verify. Returns True if 29+30 both present."""
    has29, has30, raw = detect()
    log(raw)
    if has29 and has30:
        log("OK: both sensors present (0x29 + 0x30). Nothing to do.")
        return True
    if not has29 and not has30:
        log("HARDWARE: neither address on the bus.")
        log("Check sensor power/wiring, reseat VIN, or reboot. No software fix.")
        return False

    if has30:  # CASE A: XSHUT sensor asleep -> pulse pin 29 LOW then HIGH
        log("CASE A: only 0x30 — waking XSHUT sensor (pin 29 HIGH)...")
        steps = [
            ("stop service (free the pin)", lambda: _sudo("systemctl", "stop", SERVICE)),
            ("pin 29 LOW (XSHUT reset)", lambda: _xshut(0)),
            ("pin 29 HIGH (XSHUT wakes at 0x29)", lambda: _xshut(1)),
        ]
    else:  # CASE B: both reset, colliding on 0x29 -> low, rename, high
        log("CASE B: only 0x29 — both sensors reset; re-remapping...")
        steps = [
            ("stop service (free the pin)", lambda: _sudo("systemctl", "stop", SERVICE)),
            ("poke pinmux", lambda: _sudo("busybox", "devmem", "0x2430068", "w", "0x8")),
            ("pin 29 LOW (XSHUT deaf)", lambda: _xshut(0)),
            ("rename 0x29 -> 0x30", lambda: _sudo("i2ctransfer", "-y", "1", "w2@0x29", "0x8A", "0x30")),
            ("pin 29 HIGH (XSHUT wakes at 0x29)", lambda: _xshut(1)),
        ]
    # ponytail: service left stopped on purpose (user's call) — pin stays HIGH on
    # this hardware after gpioset exits, but nothing holds it until next boot.

    for name, fn in steps:
        r = fn()
        log(f"  [{'ok' if r.returncode == 0 else 'warn'}] {name}"
            + (f": {r.stderr.strip()}" if r.returncode != 0 else ""))

    has29, has30, raw = detect()
    log(raw)
    ok = has29 and has30
    log("DONE: both sensors on the bus." if ok else "NOT FIXED — likely wiring/power; see above.")
    return ok


def remap_button():
    """Display the TLDR 'Resolve!' button (ipywidgets, preinstalled on jetcard)."""
    import ipywidgets as widgets
    from IPython.display import display

    out = widgets.Output()
    btn = widgets.Button(description="Resolve!", button_style="warning",
                         icon="wrench", layout=widgets.Layout(width="140px"))

    def _on_click(_):
        with out:
            out.clear_output()
            try:
                resolve()
            except Exception as e:
                print(f"ERROR: {e}")

    btn.on_click(_on_click)
    display(widgets.VBox([btn, out]))


if __name__ == "__main__":
    resolve()
