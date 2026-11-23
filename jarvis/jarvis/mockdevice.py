"""Fake device for development and CI (JARVIS_MOCK=1).

Returns plausible JSON for every command JARVIS uses, so the agent loop, voice
layer and tools can all be exercised on a laptop or in a container.
"""

from __future__ import annotations

import json
import os
import time

_CALLS: list[list[str]] = []
_CLIP = "mock clipboard contents"
_NOTIFY: dict[int, dict] = {}
_NEXT_ID = 42

SCREEN = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout"
        package="com.android.launcher3" content-desc="" clickable="false"
        bounds="[0,0][1080,2400]">
    <node index="0" text="WhatsApp" resource-id="" class="android.widget.TextView"
          package="com.android.launcher3" content-desc="WhatsApp" clickable="true"
          bounds="[60,1800][300,1900]" />
    <node index="1" text="Search" resource-id="com.google.android.apps.nexuslauncher:id/search"
          class="android.widget.EditText" package="com.android.launcher3" content-desc="Search"
          clickable="true" bounds="[60,2100][1020,2250]" />
  </node>
</hierarchy>"""

BATTERY = {
    "health": "GOOD",
    "percentage": 76,
    "plugged": "UNPLUGGED",
    "status": "DISCHARGING",
    "temperature": 31.4,
    "current": -412,
}

APPS = [
    "com.termux",
    "com.android.chrome",
    "com.whatsapp",
    "com.spotify.music",
    "com.google.android.youtube",
    "com.android.settings",
    "com.google.android.dialer",
    "com.google.android.apps.maps",
]


def calls() -> list[list[str]]:
    return list(_CALLS)


def reset() -> None:
    global _CLIP, _NEXT_ID
    _CALLS.clear()
    _NOTIFY.clear()
    _CLIP = "mock clipboard contents"
    _NEXT_ID = 42


def _env(*args) -> str:
    return json.dumps(list(args))


def handle(argv: list[str], stdin: str | None = None) -> tuple[int, str, str]:
    global _CLIP, _NEXT_ID
    if not argv:
        return 1, "", "empty command"
    _CALLS.append(list(argv))
    cmd = argv[0].rsplit("/", 1)[-1]
    args = argv[1:]
    out = ""
    rc = 0

    if cmd == "termux-battery-status":
        out = json.dumps(BATTERY)
    elif cmd in ("termux-toast", "termux-vibrate", "termux-tts-speak", "termux-media-scan",
                 "termux-notification-remove", "termux-wallpaper", "termux-download"):
        out = ""
    elif cmd == "termux-tts-engines":
        out = json.dumps([{"name": "com.google.android.tts", "label": "Google TTS"}])
    elif cmd == "termux-speech-to-text":
        out = json.dumps({"text": "what is my battery level"})
    elif cmd == "termux-location":
        out = json.dumps(
            {"latitude": 51.5007, "longitude": -0.1246, "accuracy": 12.0,
             "provider": "gps", "speed": 0.0}
        )
    elif cmd == "termux-volume":
        if args and args[0] == "music":
            out = json.dumps({"stream": "music", "volume": 8, "max_volume": 15})
        else:
            out = json.dumps([{"stream": "music", "volume": 8, "max_volume": 15}])
    elif cmd == "termux-clipboard-get":
        out = _CLIP
    elif cmd == "termux-clipboard-set":
        _CLIP = stdin or ""
    elif cmd == "termux-wifi-connectioninfo":
        out = json.dumps(
            {"ssid": "JARVIS-NET", "bssid": "aa:bb:cc:dd:ee:ff", "ip": "192.168.1.42",
             "link_speed_mbps": 433, "frequency": 5180, "supplicant_state": "COMPLETED"}
        )
    elif cmd == "termux-wifi-scaninfo":
        out = json.dumps(
            [{"ssid": "JARVIS-NET", "bssid": "aa:bb:cc:dd:ee:ff", "level": -52,
              "frequency": 5180, "timestamp": "2024-01-01T00:00:00"}]
        )
    elif cmd == "termux-telephony-deviceinfo":
        out = json.dumps({"device_id": "000000000000000", "network_operator_name": "TestNet",
                          "sim_operator_name": "TestNet", "network_type": "LTE"})
    elif cmd == "termux-telephony-cellinfo":
        out = json.dumps([{"type": "lte", "registered": True, "ci": 1, "pci": 2}])
    elif cmd == "termux-contact-list":
        out = json.dumps(
            [
                {"name": "Pepper Potts", "number": "+15550100"},
                {"name": "James Rhodes", "number": "+15550101"},
                {"name": "Happy Hogan", "number": "+15550102"},
            ]
        )
    elif cmd == "termux-call-log":
        out = json.dumps(
            [{"number": "+15550100", "name": "Pepper Potts", "type": "INCOMING",
              "date": "1700000000", "duration": "62"}]
        )
    elif cmd == "termux-sms-list":
        out = json.dumps(
            [{"number": "+15550100", "name": "Pepper Potts", "body": "Dinner at eight?",
              "received": "1700000000000", "read": "true"}]
        )
    elif cmd == "termux-sms-inbox":
        out = json.dumps(
            [{"number": "+15550100", "body": "Dinner at eight?", "received": "1700000000000"}]
        )
    elif cmd == "termux-sms-send":
        out = ""
    elif cmd == "termux-notification":
        title = content = ""
        for i, arg in enumerate(args):
            if arg == "--title" and i + 1 < len(args):
                title = args[i + 1]
            elif arg == "--content" and i + 1 < len(args):
                content = args[i + 1]
        _NOTIFY[_NEXT_ID] = {"id": _NEXT_ID, "packageName": "com.termux", "title": title,
                             "content": content, "priority": 0}
        out = str(_NEXT_ID)
        _NEXT_ID += 1
    elif cmd == "termux-notification-list":
        rows = list(_NOTIFY.values()) + [
            {"id": 7, "packageName": "com.whatsapp", "title": "Pepper Potts",
             "content": "Dinner at eight?", "priority": 0}
        ]
        out = json.dumps(rows)
    elif cmd == "termux-sensor":
        out = json.dumps({"acceleration": [0.1, 9.7, 0.2]})
    elif cmd == "termux-camera-info":
        out = json.dumps({"id": "0", "facing": "back", "photo_resolutions": ["4032x3024"]})
    elif cmd == "termux-fingerprint":
        out = json.dumps({"auth_result": "AUTH_SUCCESS"})
    elif cmd == "termux-torch":
        out = ""
    elif cmd == "termux-brightness":
        out = ""
    elif cmd == "termux-share":
        out = ""
    elif cmd == "termux-open":
        out = ""
    elif cmd == "termux-open-url":
        out = ""
    elif cmd == "termux-media-player":
        out = "playing"
    elif cmd == "termux-dialog":
        out = json.dumps({"code": 2, "text": "yes"})
    elif cmd == "termux-camera-photo":
        out = ""
    elif cmd == "termux-microphone-record":
        out = "Recording started"
    elif cmd == "termux-brightness":
        out = ""
    # ---------------- android system binaries ----------------
    elif cmd == "pm":
        if "list" in args:
            out = "\n".join(f"package:{p}" for p in APPS)
        else:
            out = ""
    elif cmd == "cmd" and args[:1] == ["package"]:
        out = "priority=0 preferredOrder=0 match=0x108000 specificIndex=-1"
    elif cmd == "am":
        if args[:1] == ["start"]:
            out = "Starting: Intent { cmp=com.whatsapp/.Main }"
        else:
            out = ""
    elif cmd == "input":
        out = ""
    elif cmd == "uiautomator":
        out = SCREEN.replace("\n", " ")
    elif cmd == "dumpsys":
        if args and args[0] == "window":
            out = "mCurrentFocus=Window{abc u0 com.whatsapp/com.whatsapp.Main}"
        elif args and args[0] == "battery":
            out = "  level: 76"
        else:
            out = ""
    elif cmd == "getprop":
        props = {
            "ro.product.model": "Pixel 8 (mock)",
            "ro.build.version.release": "14",
            "ro.build.version.sdk": "34",
            "ro.build.version.incremental": "termux-mock",
        }
        out = props.get(args[0], "") if args else ""
    elif cmd == "screencap":
        target = args[-1] if args else ""
        if target:
            import pathlib as _p

            dest = _p.Path(target)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"\x89PNG\r\n\x1a\n" + os.urandom(96))
        out = ""
    elif cmd == "wm":
        out = "Physical size: 1080x2400"
    elif cmd == "settings":
        out = "1"
    elif cmd == "monkey":
        out = "Events injected: 1"
    elif cmd == "content":
        out = "Row: 0 _id=1"
    elif cmd in ("sh", "bash"):
        if args[:2] == ["-c", "date +%s"]:
            out = str(int(time.time()))
        else:
            out = "mock-sh: ok"
    elif cmd == "which":
        out = "/data/data/com.termux/files/usr/bin/" + (args[0] if args else "")
    else:
        rc, out = 0, ""
    return rc, out, ""
