"""Low-level process helpers: Termux:API calls and plain shell.

Everything degrades gracefully. When termux-api is not installed (desktop, CI,
or the user forgot the app), tools return a clear instruction instead of
crashing, and `JARVIS_MOCK=1` lets the whole system be exercised off-device.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from functools import lru_cache
from pathlib import Path

MOCK = os.environ.get("JARVIS_MOCK") == "1"

# Commands that exist on modern Android/Termux
ANDROID_BIN = {
    "am": "/system/bin/am",
    "input": "/system/bin/input",
    "pm": "/system/bin/pm",
    "cmd": "/system/bin/cmd",
    "screencap": "/system/bin/screencap",
    "dumpsys": "/system/bin/dumpsys",
    "settings": "/system/bin/settings",
    "uiautomator": "/system/bin/uiautomator",
    "wm": "/system/bin/wm",
    "getprop": "/system/bin/getprop",
    "content": "/system/bin/content",
    "monkey": "/system/bin/monkey",
    "termux-open": "/data/data/com.termux/files/usr/bin/termux-open",
    "termux-open-url": "/data/data/com.termux/files/usr/bin/termux-open-url",
    "termux-toast": "/data/data/com.termux/files/usr/bin/termux-toast",
    "termux-tts-speak": "/data/data/com.termux/files/usr/bin/termux-tts-speak",
    "termux-speech-to-text": "/data/data/com.termux/files/usr/bin/termux-speech-to-text",
}


@lru_cache(maxsize=256)
def which(cmd: str) -> str | None:
    if MOCK:
        return f"/mock/{cmd}"
    found = shutil.which(cmd)
    if found:
        return found
    return ANDROID_BIN.get(cmd)


def has(cmd: str) -> bool:
    return which(cmd) is not None


def run(
    argv: list[str],
    timeout: int = 30,
    stdin: str | None = None,
    check: bool = False,
) -> tuple[int, str, str]:
    """Run a command, never raise on non-zero exit."""
    if MOCK:
        from . import mockdevice as mock

        return mock.handle(argv, stdin)
    exe = which(argv[0])
    if exe is None:
        return 127, "", f"{argv[0]}: not found"
    if exe != argv[0]:
        argv = [exe] + argv[1:]
    try:
        proc = subprocess.run(
            argv,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            errors="replace",
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"{argv[0]} timed out after {timeout}s"
    except (OSError, ValueError) as exc:
        return 126, "", f"{argv[0]}: {exc}"


def shell(cmd: str, timeout: int = 30) -> tuple[int, str, str]:
    if MOCK:
        from . import mockdevice as mock

        return mock.handle(cmd.split(), None)
    # Termux does not put Android's system binaries (pm, am, input, screencap,
    # dumpsys, uiautomator) on PATH; append the standard system directories so
    # device-control commands work from inside Termux.
    wrapped = "export PATH=\"$PATH:/system/bin:/system/xbin:/vendor/bin\"; " + cmd
    try:
        proc = subprocess.run(
            ["sh", "-c", wrapped], capture_output=True, text=True, timeout=timeout, errors="replace"
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s"


TERMUX_HINT = (
    "Termux:API is not available. Install the Termux:API app (F-Droid) and run "
    "`pkg install termux-api`, then reopen Termux."
)


def termux_json(cmd: str, *args: str, timeout: int = 30, stdin: str | None = None):
    """Call a termux-api command that prints JSON. Returns (ok, data, error)."""
    if not has(cmd):
        return False, None, TERMUX_HINT
    argv = [cmd, *[str(a) for a in args]]
    rc, out, err = run(argv, timeout=timeout, stdin=stdin)
    text = (out or "").strip()
    if rc != 0 and not text:
        return False, None, (err or out or f"{cmd} failed (exit {rc})").strip()
    try:
        return True, json.loads(text), ""
    except json.JSONDecodeError:
        if text:
            return True, text, ""
        return False, None, (err or "no output").strip()


def termux_ok(cmd: str, *args: str, timeout: int = 30) -> tuple[bool, str]:
    if not has(cmd):
        return False, TERMUX_HINT
    rc, out, err = run([cmd, *[str(a) for a in args]], timeout=timeout)
    if rc == 0:
        return True, (out or "").strip()
    return False, (err or out or f"{cmd} failed (exit {rc})").strip() or "failed (no message)"


@lru_cache(maxsize=1)
def media_dir() -> Path:
    """A writable directory for screenshots/photos/UI dumps.

    /sdcard needs the storage permission on modern Android, and /tmp is a
    read-only ramfs on Termux, so probe candidates and fall back to $HOME.
    """
    candidates = [] if MOCK else [Path("/sdcard/jarvis")]
    candidates.append(Path.home() / "jarvis")
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".probe"
            probe.write_text("x")
            probe.unlink()
            return candidate
        except OSError:
            continue
    return Path.home()


def device_ready() -> tuple[bool, str]:
    """Is this a Termux device with the API app present?"""
    if MOCK:
        return True, "mock device"
    missing = [c for c in ("termux-battery-status", "termux-toast") if not has(c)]
    if missing:
        return False, TERMUX_HINT
    ok, data = termux_ok("termux-toast", "-g", "middle", "JARVIS online")
    if not ok:
        return False, f"Termux:API commands exist but failed: {data}"
    return True, "Termux:API responding"


def android_info() -> dict:
    info: dict = {"termux_api": has("termux-battery-status"), "mock": MOCK}
    if MOCK:
        info.update({"model": "Pixel 8 (mock)", "android": "14", "termux": "0.118"})
        return info
    for key, cmd in (
        ("model", ["getprop", "ro.product.model"]),
        ("android", ["getprop", "ro.build.version.release"]),
        ("sdk", ["getprop", "ro.build.version.sdk"]),
        ("termux", ["getprop", "ro.build.version.incremental"]),
    ):
        rc, out, _ = run(cmd, timeout=5)
        if rc == 0 and out.strip():
            info[key] = out.strip()
    return info


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")
