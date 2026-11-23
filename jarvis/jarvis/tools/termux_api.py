"""Termux:API capabilities: sensors, telephony, media, clipboard, notifications."""

from __future__ import annotations

import json
import os
import time

from .. import shims
from ..shims import has, termux_json, termux_ok
from .registry import tool


def _fail(msg: str) -> str:
    return f"FAILED: {msg}"


def _j(data) -> str:
    return json.dumps(data, ensure_ascii=False)[:4000]


def _strip(d: dict, keep: tuple[str, ...]) -> dict:
    return {k: v for k, v in d.items() if k in keep and v not in (None, "")}


# --------------------------------------------------------------------- power
@tool(
    "battery_status",
    "Read battery percentage, charging state, temperature and health.",
    category="device",
)
def battery_status() -> str:
    ok, data, err = termux_json("termux-battery-status")
    if not ok:
        return _fail(err)
    if isinstance(data, dict):
        return _j(
            _strip(
                data,
                ("percentage", "plugged", "status", "health", "temperature", "current"),
            )
        )
    return str(data)


@tool("torch", "Turn the camera torch/flashlight on or off.", {"on": {"type": "boolean", "required": True}}, category="device")
def torch(on: bool = True) -> str:
    ok, msg = termux_ok("termux-torch", "on" if on else "off")
    return ("Torch on." if on else "Torch off.") if ok else _fail(msg)


@tool(
    "brightness",
    "Set screen brightness (0-255) or restore automatic brightness with auto=true.",
    {"level": {"type": "integer", "description": "0-255"}, "auto": {"type": "boolean"}},
    category="device",
)
def brightness(level: int | None = None, auto: bool = False) -> str:
    if auto:
        ok, msg = termux_ok("termux-brightness", "auto")
        return "Brightness set to automatic." if ok else _fail(msg)
    if level is None:
        return _fail("provide level (0-255) or auto=true")
    level = max(0, min(255, int(level)))
    ok, msg = termux_ok("termux-brightness", str(level))
    return f"Brightness set to {level}." if ok else _fail(msg)


@tool(
    "volume",
    "Get or set audio volume. Streams: music, ring, alarm, notification, call.",
    {
        "stream": {"type": "string", "enum": ["music", "ring", "alarm", "notification", "call"], "default": "music"},
        "level": {"type": "integer", "description": "New volume level; omit to read current"},
    },
    category="device",
)
def volume(stream: str = "music", level: int | None = None) -> str:
    if level is None:
        ok, data, err = termux_json("termux-volume", stream)
        if not ok:
            return _fail(err)
        if isinstance(data, dict):
            return f"{stream}: {data.get('volume')}/{data.get('max_volume')}"
        text = str(data)
        if "Usage" in text or "Invalid argument" in text:
            # newer Termux:API removed the read syntax; only setting works
            return (
                f"FAILED: this Termux:API version cannot read the {stream} volume "
                "(set-only). Use volume(stream, level) to change it."
            )
        return _j(data)
    ok, msg = termux_ok("termux-volume", stream, str(int(level)))
    return f"{stream} volume -> {level}." if ok else _fail(msg)


# ------------------------------------------------------------------ feedback
@tool("toast", "Show a short Android toast popup (JARVIS's quiet acknowledgement).",
      {"text": {"type": "string", "required": True}}, category="device")
def toast(text: str) -> str:
    ok, msg = termux_ok("termux-toast", "-g", "middle", text[:200])
    return "Toast shown." if ok else _fail(msg)


@tool("vibrate", "Vibrate the phone for a duration in milliseconds.",
      {"ms": {"type": "integer", "default": 400}}, category="device")
def vibrate(ms: int = 400) -> str:
    ok, msg = termux_ok("termux-vibrate", "-d", str(int(ms)), "-f")
    return f"Vibrated {ms}ms." if ok else _fail(msg)


# ----------------------------------------------------------------- clipboard
@tool("clipboard_read", "Read the current clipboard text.", category="device")
def clipboard_read() -> str:
    ok, out = termux_ok("termux-clipboard-get")
    return out if ok else _fail(out)


@tool("clipboard_write", "Copy text to the clipboard.",
      {"text": {"type": "string", "required": True}}, category="device")
def clipboard_write(text: str) -> str:
    if not has("termux-clipboard-set"):
        return _fail(shims.TERMUX_HINT)
    rc, _, err = shims.run(["termux-clipboard-set"], stdin=text)
    return f"Copied {len(text)} characters." if rc == 0 else _fail(err)


# ------------------------------------------------------------- notifications
@tool(
    "notify",
    "Post an Android notification (title, body, optional urgency and action URL).",
    {
        "title": {"type": "string", "required": True},
        "content": {"type": "string", "required": True},
        "priority": {"type": "string", "enum": ["min", "low", "default", "high", "max"], "default": "default"},
        "id": {"type": "integer", "description": "Reuse an id to update an existing notification"},
    },
    category="device",
)
def notify(title: str, content: str, priority: str = "default", id: int | None = None) -> str:
    args = ["--title", title[:80], "--content", content[:400], "--priority", priority]
    if id is not None:
        args += ["--id", str(int(id))]
    ok, out = termux_ok("termux-notification", *args)
    return "Notification posted." if ok else _fail(out)


@tool("notifications_list", "List current Android notifications (title, app, content).",
      {"limit": {"type": "integer", "default": 10}}, category="device")
def notifications_list(limit: int = 10) -> str:
    ok, data, err = termux_json("termux-notification-list")
    if not ok:
        return _fail(err)
    if not isinstance(data, list):
        return _j(data)
    rows = [
        _strip(n, ("id", "packageName", "title", "content", "priority"))
        for n in data[: int(limit)]
    ]
    if not rows:
        return (
            "No notifications. (If you expected one, grant Termux:API notification "
            "access: Settings → Notifications → Device & app notifications.)"
        )
    return _j(rows)


@tool("notification_dismiss", "Remove a notification by id.",
      {"id": {"type": "integer", "required": True}}, category="device")
def notification_dismiss(id: int) -> str:
    ok, msg = termux_ok("termux-notification-remove", str(int(id)))
    return f"Notification {id} removed." if ok else _fail(msg)


# --------------------------------------------------------------- connectivity
@tool("wifi_info", "Current Wi-Fi SSID, IP address, link speed and frequency.", category="network")
def wifi_info() -> str:
    ok, data, err = termux_json("termux-wifi-connectioninfo")
    if not ok:
        return _fail(err)
    if isinstance(data, dict):
        return _j(_strip(data, ("ssid", "bssid", "ip", "link_speed_mbps", "frequency", "supplicant_state")))
    return str(data)


@tool("wifi_scan", "Scan nearby Wi-Fi networks (SSID, signal level, security).",
      {"limit": {"type": "integer", "default": 8}}, category="network")
def wifi_scan(limit: int = 8) -> str:
    ok, data, err = termux_json("termux-wifi-scaninfo", timeout=45)
    if not ok:
        return _fail(err)
    if not isinstance(data, list):
        return _j(data)
    rows = [
        _strip(n, ("ssid", "bssid", "level", "frequency", "capabilities"))
        for n in sorted(data, key=lambda n: n.get("level", -100), reverse=True)[: int(limit)]
    ]
    return _j(rows) if rows else "No networks found."


@tool("location", "GPS/network location with latitude, longitude, accuracy and provider.",
      {"provider": {"type": "string", "enum": ["gps", "network", "passive"], "default": "gps"}},
      category="device")
def location(provider: str = "gps") -> str:
    ok, data, err = termux_json("termux-location", "-p", provider, timeout=40)
    if not ok:
        return _fail(err)
    if isinstance(data, dict):
        return _j({**_strip(data, ("latitude", "longitude", "accuracy", "provider", "speed")),
                   "maps": f"https://maps.google.com/?q={data.get('latitude')},{data.get('longitude')}"})
    return str(data)


# ------------------------------------------------------------------- sensors
@tool("sensors", "Read phone sensors (accelerometer, light, proximity, gyroscope...).",
      {"sensor": {"type": "string", "description": "Sensor name, e.g. acceleration", "default": "acceleration"},
       "seconds": {"type": "integer", "default": 1}}, category="device")
def sensors(sensor: str = "acceleration", seconds: int = 1) -> str:
    ok, data, err = termux_json("termux-sensor", "-s", sensor, "-n", "1", timeout=max(5, seconds + 10))
    if not ok:
        return _fail(err)
    if isinstance(data, dict):
        val = data.get(sensor, data)
        return _j({"sensor": sensor, "value": val})
    return str(data)


@tool("device_info", "Model, Android version, hardware and Termux:API readiness.",
      category="device")
def device_info() -> str:
    info = shims.android_info()
    ok, data, _ = termux_json("termux-telephony-deviceinfo")
    if ok and isinstance(data, dict):
        info["network"] = data.get("network_operator_name")
        info["network_type"] = data.get("network_type")
    return _j(info)


# ------------------------------------------------------------------ telephony
@tool("contacts", "Search contacts by name or number and return matching names/numbers.",
      {"query": {"type": "string", "description": "Name fragment; omit to list all", "default": ""},
       "limit": {"type": "integer", "default": 10}}, category="phone")
def contacts(query: str = "", limit: int = 10) -> str:
    ok, data, err = termux_json("termux-contact-list")
    if not ok:
        return _fail(err)
    if not isinstance(data, list):
        return str(data)
    q = query.lower().strip()
    hits = [c for c in data if not q or q in str(c.get("name", "")).lower() or q in str(c.get("number", ""))]
    rows = [{"name": c.get("name"), "number": c.get("number")} for c in hits[: int(limit)]]
    return _j(rows) if rows else f"No contacts matching '{query}'."


@tool("sms_list", "Read recent SMS messages (optionally filtered by number or text).",
      {"limit": {"type": "integer", "default": 5},
       "query": {"type": "string", "description": "Filter by sender or body text", "default": ""}},
      category="phone")
def sms_list(limit: int = 5, query: str = "") -> str:
    ok, data, err = termux_json("termux-sms-list", "-l", str(int(limit)))
    if not ok:
        return _fail(err)
    if not isinstance(data, list):
        return str(data)
    q = query.lower().strip()
    rows = []
    for m in data:
        if q and q not in f"{m.get('name')} {m.get('number')} {m.get('body')}".lower():
            continue
        rows.append(_strip(m, ("number", "name", "body", "received", "read")))
    return _j(rows[: int(limit)]) if rows else f"No messages matching '{query}'."


@tool("sms_send", "Send an SMS to a number. This is a real message to a real person.",
      {"number": {"type": "string", "required": True},
       "text": {"type": "string", "required": True}},
      danger=True, category="phone")
def sms_send(number: str, text: str) -> str:
    ok, msg = termux_ok("termux-sms-send", "-n", number, text)
    return f"SMS sent to {number}." if ok else _fail(msg)


@tool("call_log", "Recent call history (incoming/outgoing/missed).",
      {"limit": {"type": "integer", "default": 5}}, category="phone")
def call_log(limit: int = 5) -> str:
    ok, data, err = termux_json("termux-call-log", "-l", str(int(limit)))
    if not ok:
        return _fail(err)
    if isinstance(data, list):
        return _j([_strip(c, ("name", "number", "type", "date", "duration")) for c in data[: int(limit)]])
    return str(data)


@tool("dial", "Place a phone call to a number. The call is really placed.",
      {"number": {"type": "string", "required": True}}, danger=True, category="phone")
def dial(number: str) -> str:
    ok, msg = termux_ok("termux-telephony-call", number)
    return f"Calling {number}." if ok else _fail(msg)


# --------------------------------------------------------------------- media
@tool("say", "Speak text out loud through the phone's text-to-speech engine.",
      {"text": {"type": "string", "required": True},
       "speed": {"type": "number", "description": "1.0 = normal", "default": 1.0}},
      category="media")
def say(text: str, speed: float = 1.0) -> str:
    if not has("termux-tts-speak"):
        return _fail(shims.TERMUX_HINT)
    args = ["-r", str(speed), text[:800]]
    if os.environ.get("JARVIS_TTS_ENGINE"):
        args = ["-e", os.environ["JARVIS_TTS_ENGINE"], *args]
    rc, out, err = shims.run(["termux-tts-speak", *args], timeout=180)
    return "Spoken." if rc == 0 else _fail(err)


@tool("listen", "Capture one spoken utterance and return the transcribed text.",
      {"language": {"type": "string", "default": "en-US"}}, category="media")
def listen(language: str = "en-US") -> str:
    ok, data, err = termux_json("termux-speech-to-text", "-l", language, timeout=60)
    if not ok:
        return _fail(err)
    if isinstance(data, dict) and data.get("text"):
        return str(data["text"])
    return "FAILED: no speech detected (or the microphone dialog was dismissed)."


@tool("media_player", "Control media playback: play, pause, stop, info.",
      {"action": {"type": "string", "enum": ["play", "pause", "stop", "info"], "required": True},
       "file": {"type": "string", "description": "Absolute path or URL for action=play"}},
      category="media")
def media_player(action: str, file: str | None = None) -> str:
    args = [action] + ([file] if file else [])
    ok, msg = termux_ok("termux-media-player", *args)
    return f"Media {action}." if ok else _fail(msg)


@tool("share", "Share a file or URL to another Android app via the share sheet.",
      {"path": {"type": "string", "required": True},
       "content_type": {"type": "string", "default": "text/plain"}}, category="media")
def share(path: str, content_type: str = "text/plain") -> str:
    ok, msg = termux_ok("termux-share", "-c", content_type, path)
    return f"Shared {path}." if ok else _fail(msg)


@tool("camera_photo", "Take a photo with the front or back camera and save it to a file.",
      {"path": {"type": "string", "description": "Output path; defaults to the JARVIS media folder"},
       "camera": {"type": "integer", "description": "0 = back, 1 = front", "default": 0}},
      category="media")
def camera_photo(path: str = "", camera: int = 0) -> str:
    path = path or str(shims.media_dir() / "capture.jpg")
    os.makedirs(os.path.dirname(path), exist_ok=True) if path.startswith("/") else None
    ok, msg = termux_ok("termux-camera-photo", "-c", str(int(camera)), path, timeout=60)
    return f"Photo saved to {path}." if ok else _fail(msg)


@tool("fingerprint", "Ask for a fingerprint scan to authenticate the user.",
      {"title": {"type": "string", "default": "JARVIS authorisation"}}, category="device")
def fingerprint(title: str = "JARVIS authorisation") -> str:
    ok, data, err = termux_json("termux-fingerprint", "-t", title, timeout=60)
    if not ok:
        return _fail(err)
    result = data.get("auth_result", "") if isinstance(data, dict) else str(data)
    return "AUTH_SUCCESS" if result == "AUTH_SUCCESS" else f"AUTH_FAILED ({result})"


@tool("ask_dialog", "Show an Android input/prompt dialog and return what the user typed.",
      {"hint": {"type": "string", "default": "JARVIS"}, "text": {"type": "string", "default": ""}},
      category="device")
def ask_dialog(hint: str = "JARVIS", text: str = "") -> str:
    ok, data, err = termux_json("termux-dialog", "text", "-t", hint, "-i", text, timeout=180)
    if not ok:
        return _fail(err)
    if isinstance(data, dict):
        return str(data.get("text") or "(cancelled)")
    return str(data)


@tool("download", "Download a URL with Android's DownloadManager (shows in notifications).",
      {"url": {"type": "string", "required": True},
       "filename": {"type": "string", "default": ""}}, category="network")
def download(url: str, filename: str = "") -> str:
    args = ["-u", url]
    if filename:
        args += ["-f", filename]
    ok, msg = termux_ok("termux-download", *args)
    return "Download started." if ok else _fail(msg)


@tool("wallpaper", "Set the phone wallpaper from an image file.",
      {"path": {"type": "string", "required": True}}, category="device")
def wallpaper(path: str) -> str:
    ok, msg = termux_ok("termux-wallpaper", "-f", path)
    return "Wallpaper set." if ok else _fail(msg)


@tool("open_url", "Open a URL in the default browser.",
      {"url": {"type": "string", "required": True}}, category="network")
def open_url(url: str) -> str:
    ok, msg = termux_ok("termux-open-url", url)
    if not ok:
        ok, msg = shims.shell(f"am start -a android.intent.action.VIEW -d '{url}'")[0] == 0, msg
    return f"Opened {url}." if ok else _fail(msg)


@tool("clipboard_status", "Report clipboard availability (sanity check).", category="device")
def clipboard_status() -> str:
    return "clipboard commands available" if has("termux-clipboard-get") else _fail(shims.TERMUX_HINT)


@tool("wake_lock", "Keep the device awake briefly while JARVIS works.",
      {"seconds": {"type": "integer", "default": 20}}, category="device")
def wake_lock(seconds: int = 20) -> str:
    if has("termux-wake-lock"):
        rc, _, _ = shims.run(["termux-wake-lock"], timeout=5)
        time.sleep(min(seconds, 30))
        shims.run(["termux-wake-unlock"], timeout=5)
        return "Held wake lock."
    return "Wake lock unavailable."
