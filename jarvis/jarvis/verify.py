"""On-device verification: prove JARVIS can actually drive this phone.

Runs without an LLM or an API key — it exercises the registered tools directly,
which is exactly what the model would call. Intended for a real Termux device
(or the MobileRun cloud phone) and safe to run repeatedly:

    python jarvis.py --verify
    python jarvis.py --verify --with-sound     # also speaks out loud

Writes data/device-report.json, which is small enough to send back from a phone.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import shims
from .config import APP_DIR
from .tools import REGISTRY

REPORT = Path(APP_DIR) / "data" / "device-report.json"


def call(name: str, **kwargs) -> str:
    tool = REGISTRY.get(name)
    if tool is None:
        return f"FAILED: tool {name} missing"
    return tool.call(**kwargs)


def _failed(result: str) -> bool:
    return str(result).startswith(("FAILED", "TOOL FAILED", "TOOL NOT FOUND"))


class Checker:
    def __init__(self):
        self.checks: list[dict] = []

    def add(self, name: str, result: str, detail: str = "", skip: bool = False) -> None:
        passed = not _failed(result) and not skip
        self.checks.append(
            {
                "check": name,
                "status": "skip" if skip else ("pass" if passed else "fail"),
                "detail": (detail or str(result))[:300],
            }
        )

    def run(self, name: str, fn, skip_if: str | None = None) -> str:
        if skip_if:
            self.add(name, skip_if, skip_if, skip=True)
            return skip_if
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001 - a check must never abort the report
            result = f"FAILED: {type(exc).__name__}: {exc}"
        self.add(name, result)
        return result

    @property
    def failed(self) -> int:
        return sum(1 for c in self.checks if c["status"] == "fail")

    @property
    def skipped(self) -> int:
        return sum(1 for c in self.checks if c["status"] == "skip")

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c["status"] == "pass")


def verify(with_sound: bool = False, include_sms: bool = True) -> dict:
    c = Checker()
    mock = shims.MOCK

    info = shims.android_info()
    ready, detail = shims.device_ready()
    c.add("termux_api", "ok" if ready else f"FAILED: {detail}", detail)
    c.run("battery", lambda: call("battery_status"))
    c.run("toast", lambda: call("toast", text="JARVIS verification"))
    c.run("vibrate", lambda: call("vibrate", ms=80))
    c.run("volume_read", lambda: call("volume", stream="music"))
    c.run("brightness_read", lambda: call("brightness", auto=True))

    # clipboard: write then read back, the cheapest true round-trip
    marker = f"jarvis-{int(time.time())}"

    def clipboard_roundtrip() -> str:
        write = call("clipboard_write", text=marker)
        if _failed(write):
            return write
        read = call("clipboard_read")
        return read if marker in read else f"FAILED: clipboard returned {read[:80]!r}"

    c.run("clipboard_roundtrip", clipboard_roundtrip)

    c.run("contacts", lambda: call("contacts", query="", limit=3))
    if include_sms:
        c.run("sms_read", lambda: call("sms_list", limit=2))
    c.run("tts_engines", lambda: call("say", text="Verification", speed=1.0) if with_sound
          else call("sensors", sensor="acceleration", seconds=1))

    # notification: post, find it in the list, dismiss it
    def notification_roundtrip() -> str:
        title = "JARVIS verify"
        posted = call("notify", title=title, content="round-trip test", priority="low")
        if _failed(posted):
            return posted
        time.sleep(0.6)
        listing = call("notifications_list", limit=20)
        if title.lower() not in str(listing).lower():
            return f"FAILED: notification not found in list"
        return listing

    c.run("notification_roundtrip", notification_roundtrip)

    c.run("apps_list", lambda: call("list_apps", query="", limit=5))

    # the real test: open Settings, read the screen, press back
    def ui_roundtrip() -> str:
        opened = call("launch_app", app="settings")
        if _failed(opened):
            return opened
        time.sleep(2.5)
        fg = call("current_app")
        dump = call("screen_dump", max_elements=40)
        if _failed(dump):
            return dump
        call("press_key", key="back")
        return f"{opened} | {fg[:90]} | elements={len(str(dump))}"

    c.run("ui_roundtrip", ui_roundtrip)

    def screenshot_check() -> str:
        path = str(shims.media_dir() / "verify.png")
        result = call("screenshot", path=path)
        if _failed(result):
            return result
        size = 0
        try:
            size = json.loads(result).get("bytes", 0)
        except json.JSONDecodeError:
            pass
        return result if size > 0 else f"FAILED: screenshot empty ({result[:80]})"

    c.run("screenshot", screenshot_check)
    if with_sound:
        c.run("speak", lambda: call("say", text="All systems verified, sir."))

    report = {
        "ok": c.failed == 0,
        "device": {
            "model": info.get("model", "unknown"),
            "android": info.get("android", "unknown"),
            "mock": mock,
            "termux_api": info.get("termux_api", False),
        },
        "summary": {"passed": c.passed, "failed": c.failed, "skipped": c.skipped},
        "checks": c.checks,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2) + "\n")
    return report


def main(with_sound: bool = False) -> int:
    from . import ui

    if shims.MOCK and os.environ.get("JARVIS_VERIFY_ALLOW_MOCK") != "1":
        ui.warn("this is the mock device — run on the phone, or set JARVIS_VERIFY_ALLOW_MOCK=1")
        return 2

    ui.info("verifying real device control (no model needed)…")
    report = verify(with_sound=with_sound)
    print()
    for check in report["checks"]:
        mark = {
            "pass": ui.green("✓"),
            "fail": ui.red("✗"),
            "skip": ui.dim("–"),
        }[check["status"]]
        print(f"  {mark} {check['check']:<24}{ui.dim(check['detail'][:70])}")
    s = report["summary"]
    print()
    if report["ok"]:
        ui.ok(f"device verified — {s['passed']} checks passed, {s['skipped']} skipped")
    else:
        ui.warn(f"{s['failed']} check(s) failed — see {REPORT}")
    print(ui.dim(f"  report: {REPORT}"))
    return 0 if report["ok"] else 1
