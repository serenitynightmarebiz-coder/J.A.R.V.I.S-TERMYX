"""Android control: launch apps, read and drive the UI, screenshots, intents.

No root required. Uses the same primitives a human uses — `am`, `pm`, `input`,
`screencap` and `uiautomator` — so it works on any stock Android.
"""

from __future__ import annotations

import html
import json
import os
import re
import shlex
import time
import xml.etree.ElementTree as ET
from functools import lru_cache

from .. import shims
from .registry import tool

KEYCODES = {
    "home": "KEYCODE_HOME",
    "back": "KEYCODE_BACK",
    "recents": "KEYCODE_APP_SWITCH",
    "enter": "KEYCODE_ENTER",
    "delete": "KEYCODE_DEL",
    "power": "KEYCODE_POWER",
    "wake": "KEYCODE_WAKEUP",
    "sleep": "KEYCODE_SLEEP",
    "volume_up": "KEYCODE_VOLUME_UP",
    "volume_down": "KEYCODE_VOLUME_DOWN",
    "play": "KEYCODE_MEDIA_PLAY_PAUSE",
    "next": "KEYCODE_MEDIA_NEXT",
    "previous": "KEYCODE_MEDIA_PREVIOUS",
    "tab": "KEYCODE_TAB",
    "escape": "KEYCODE_ESCAPE",
    "menu": "KEYCODE_MENU",
    "camera": "KEYCODE_CAMERA",
}

# Common apps so "open whatsapp" works even when the package name is opaque
APP_ALIASES = {
    "whatsapp": "com.whatsapp",
    "chrome": "com.android.chrome",
    "browser": "com.android.chrome",
    "youtube": "com.google.android.youtube",
    "maps": "com.google.android.apps.maps",
    "settings": "com.android.settings",
    "phone": "com.google.android.dialer",
    "dialer": "com.google.android.dialer",
    "camera": "com.android.camera2",
    "gallery": "com.google.android.apps.photos",
    "photos": "com.google.android.apps.photos",
    "spotify": "com.spotify.music",
    "gmail": "com.google.android.gm",
    "calendar": "com.google.android.calendar",
    "clock": "com.google.android.deskclock",
    "calculator": "com.google.android.calculator",
    "files": "com.google.android.documentsui",
    "termux": "com.termux",
    "telegram": "org.telegram.messenger",
    "signal": "org.thoughtcrime.securesms",
    "instagram": "com.instagram.android",
    "facebook": "com.facebook.katana",
    "twitter": "com.twitter.android",
    "x": "com.twitter.android",
    "reddit": "com.reddit.frontpage",
    "netflix": "com.netflix.mediaclient",
    "bank": "com.android.vending",
    "store": "com.android.vending",
    "playstore": "com.android.vending",
    "play store": "com.android.vending",
    "messages": "com.google.android.apps.messaging",
    "notes": "com.google.android.keep",
    "keep": "com.google.android.keep",
    "translate": "com.google.android.apps.translate",
}


def _sh(cmd: str, timeout: int = 20) -> tuple[int, str, str]:
    return shims.shell(cmd, timeout=timeout)


def _ok(cmd: str, timeout: int = 20) -> tuple[bool, str]:
    rc, out, err = _sh(cmd, timeout)
    return rc == 0, (out.strip() or err.strip())


# ------------------------------------------------------------------- apps
def _packages_from_dumpsys() -> list[str]:
    """Read installed packages from `dumpsys package`.

    On Android 14+ some ROMs block `cmd package` (and therefore `pm`) from
    app contexts with a binder Failed transaction; dumpsys is often allowed.
    """
    rc, out, _ = _sh("dumpsys package 2>/dev/null")
    pkgs = re.findall(r"Package \[([^\]]+)\]", out)
    return sorted(set(pkgs))


@lru_cache(maxsize=1)
def _packages() -> list[str]:
    rc, out, _ = _sh("pm list packages -3 2>/dev/null || pm list packages")
    pkgs = [line.split(":", 1)[1].strip() for line in out.splitlines() if line.startswith("package:")]
    if not pkgs:
        pkgs = _packages_from_dumpsys()
    return sorted(set(pkgs))


@tool("list_apps", "List installed apps (package names). Filter with a search term.",
      {"query": {"type": "string", "default": ""},
       "limit": {"type": "integer", "default": 40}}, category="apps")
def list_apps(query: str = "", limit: int = 40) -> str:
    pkgs = _packages()
    if not pkgs:
        return "FAILED: could not read the app list (pm list packages returned nothing)."
    q = query.lower().strip()
    hits = [p for p in pkgs if not q or q in p.lower()]
    if not hits:
        return f"No installed app matches '{query}'."
    return "Installed: " + ", ".join(hits[: int(limit)]) + (
        f" … (+{len(hits) - int(limit)} more)" if len(hits) > int(limit) else ""
    )


def resolve_package(name: str) -> str | None:
    """Turn 'whatsapp', 'WhatsApp' or a package name into a real package."""
    name = name.strip().lower()
    if not name:
        return None
    pkgs = _packages()
    if name in pkgs:
        return name
    if name in APP_ALIASES and APP_ALIASES[name] in pkgs:
        return APP_ALIASES[name]
    # substring match on package, then on alias
    slug = re.sub(r"[^a-z0-9]", "", name)
    for p in pkgs:
        tail = p.split(".")[-1].lower()
        if slug and (slug == tail or slug in p.replace(".", "")):
            return p
    for alias, pkg in APP_ALIASES.items():
        if slug and (slug in alias.replace(" ", "") or alias.replace(" ", "") in slug) and pkg in pkgs:
            return pkg
    return None


def _launcher_activity_from_dumpsys(pkg: str) -> str:
    """Find the launcher activity of <pkg> by parsing `dumpsys package`.

    Filter lines look like `a1ff30d com.pkg/.Activity filter 4b6a2c2` and are
    followed by their Action/Category lines, so the last component seen before
    a LAUNCHER category is the launcher activity. This works on ROMs that
    block `cmd package resolve-activity` from app contexts.
    """
    rc, out, _ = _sh(f"dumpsys package {shlex.quote(pkg)} 2>/dev/null")
    component = ""
    for line in out.splitlines():
        m = re.match(r"\s*[0-9a-f]{6,}\s+(\S+/\S+)\s+filter\s+[0-9a-f]+", line)
        if m:
            component = m.group(1)
        elif 'Category: "android.intent.category.LAUNCHER"' in line and component:
            return component
    return ""


@tool("launch_app", "Open an app by common name or package, e.g. 'whatsapp', 'spotify', 'settings'.",
      {"app": {"type": "string", "required": True}}, category="apps")
def launch_app(app: str) -> str:
    pkg = resolve_package(app)
    if not pkg:
        near = [p for p in _packages() if app.lower()[:4] in p.lower()][:5]
        hint = f" Candidates: {', '.join(near)}" if near else ""
        return f"FAILED: no app matching '{app}'.{hint}"
    # 1) resolve the launcher activity, 2) parse dumpsys, 3) fall back to monkey
    rc, out, _ = _sh(
        f"cmd package resolve-activity --brief -a android.intent.action.MAIN "
        f"-c android.intent.category.LAUNCHER {shlex.quote(pkg)}"
    )
    component = out.strip().splitlines()[-1] if rc == 0 and "/" in out else ""
    if not component:
        component = _launcher_activity_from_dumpsys(pkg)
    if component:
        ok, msg = _ok(f"am start -W -n {shlex.quote(component)}")
        if ok:
            return f"Opened {pkg}."
    ok, msg = _ok(f"monkey -p {shlex.quote(pkg)} -c android.intent.category.LAUNCHER 1")
    return f"Opened {pkg}." if ok else f"FAILED to open {pkg}: {msg}"


@tool("current_app", "Report which app and screen is currently in the foreground.",
      category="apps")
def current_app() -> str:
    for cmd, pattern in (
        ("dumpsys window 2>/dev/null | grep -E 'mCurrentFocus|mFocusedApp'", r"([\w.]+)/([\w.$]+)"),
        ("dumpsys activity activities 2>/dev/null | grep -E 'mResumedActivity|topResumedActivity'", r"([\w.]+)/([\w.$]+)"),
    ):
        rc, out, _ = _sh(cmd)
        if rc == 0 and out.strip():
            m = re.search(pattern, out)
            if m:
                return json.dumps({"package": m.group(1), "activity": m.group(2)})
    return "foreground app unknown"


@tool("app_info", "Details about an installed app: version and install path.",
      {"app": {"type": "string", "required": True}}, category="apps")
def app_info(app: str) -> str:
    pkg = resolve_package(app) or app
    rc, out, _ = _sh(f"dumpsys package {shlex.quote(pkg)} 2>/dev/null | head -40")
    if rc != 0 or not out.strip():
        return f"FAILED: could not read package {pkg}"
    keep = [ln.strip() for ln in out.splitlines() if re.search(r"versionName|firstInstallTime|codePath|pkg=", ln)]
    return "\n".join(keep[:8]) or out.strip()[:600]


@tool("force_stop", "Force-stop an app (closes it completely).",
      {"app": {"type": "string", "required": True}}, danger=True, category="apps")
def force_stop(app: str) -> str:
    pkg = resolve_package(app) or app
    ok, msg = _ok(f"am force-stop {shlex.quote(pkg)}")
    return f"Stopped {pkg}." if ok else f"FAILED: {msg}"


@tool("open_intent",
      "Fire a raw Android intent: view a URL, open a file, dial a number, share text, "
      "open app settings, or send a broadcast.",
      {
          "action": {"type": "string", "default": "android.intent.action.VIEW"},
          "data": {"type": "string", "default": ""},
          "package": {"type": "string", "default": ""},
          "mime": {"type": "string", "default": ""},
          "extras": {"type": "string", "description": "extra key=value pairs, comma separated", "default": ""},
      },
      category="apps",
  )
def open_intent(action: str = "android.intent.action.VIEW", data: str = "", package: str = "",
                mime: str = "", extras: str = "") -> str:
    parts = ["am", "start", "-a", shlex.quote(action)]
    if data:
        parts += ["-d", shlex.quote(data)]
    if package:
        parts += ["-p", shlex.quote(package)]
    if mime:
        parts += ["-t", shlex.quote(mime)]
    for pair in filter(None, (p.strip() for p in extras.split(","))):
        if "=" in pair:
            k, v = pair.split("=", 1)
            parts += ["--es", shlex.quote(k.strip()), shlex.quote(v.strip())]
    ok, msg = _ok(" ".join(parts))
    return "Intent sent." if ok else f"FAILED: {msg}"


@tool("install_apk", "Install an APK file from storage.",
      {"path": {"type": "string", "required": True}}, danger=True, category="apps")
def install_apk(path: str) -> str:
    ok, msg = _ok(f"pm install -r {shlex.quote(path)}", timeout=120)
    return f"Installed {path}." if ok and "Success" in msg else f"FAILED: {msg}"


# ---------------------------------------------------------------- UI control
@tool("screen_dump",
      "Read the current screen as a list of visible elements (text, class, tap coordinates). "
      "Always do this before tapping so you act on what is really on screen.",
      {"max_elements": {"type": "integer", "default": 60}}, category="ui")
def screen_dump(max_elements: int = 60) -> str:
    dump_path = str(shims.media_dir() / "ui.xml")
    rc, out, err = _sh(f"uiautomator dump {dump_path} >/dev/null 2>&1; cat {dump_path} 2>/dev/null")
    xml_text = out.strip()
    if "<hierarchy" not in xml_text:
        rc, out, _ = _sh("uiautomator dump /dev/tty 2>/dev/null")
        xml_text = out.strip()
    if "<hierarchy" not in xml_text:
        rc, probe, err = _sh("uiautomator dump 2>&1")
        combined = (probe + " " + err).strip()
        if "Permission denied" in combined or "Killed" in combined:
            return (
                "FAILED: uiautomator is blocked by this ROM's policy "
                "(common on Android 14+ cloud phones); the screen tree "
                "cannot be read from Termux on this device."
            )
        return "FAILED: could not read the screen (uiautomator produced nothing). Is the screen locked?"
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        return f"FAILED to parse screen XML: {exc}"
    elements: list[dict] = []
    for node in root.iter("node"):
        text = (node.get("text") or "").strip()
        desc = (node.get("content-desc") or "").strip()
        label = text or desc
        cls = (node.get("class") or "").split(".")[-1]
        bounds = node.get("bounds") or ""
        clickable = node.get("clickable") == "true"
        if not label and not clickable:
            continue
        m = re.findall(r"\[(\d+),(\d+)\]", bounds)
        x = y = None
        if len(m) == 2:
            (x1, y1), (x2, y2) = (int(m[0][0]), int(m[0][1])), (int(m[1][0]), int(m[1][1]))
            x, y = (x1 + x2) // 2, (y1 + y2) // 2
        item = {"text": label[:80], "type": cls, "clickable": clickable}
        if x is not None:
            item["tap"] = [x, y]
        if node.get("password") == "true":
            item["text"] = "••••"
        elements.append(item)
        if len(elements) >= max_elements:
            break
    if not elements:
        return "Screen has no readable elements (blank or locked screen)."
    return json.dumps({"count": len(elements), "elements": elements}, ensure_ascii=False)[:6000]


@tool("tap", "Tap the screen at pixel coordinates.",
      {"x": {"type": "integer", "required": True}, "y": {"type": "integer", "required": True}},
      category="ui")
def tap(x: int, y: int) -> str:
    ok, msg = _ok(f"input tap {int(x)} {int(y)}")
    return f"Tapped ({x},{y})." if ok else f"FAILED: {msg}"


@tool("tap_text", "Find visible text on screen and tap it (e.g. 'Send', 'Search').",
      {"text": {"type": "string", "required": True},
       "index": {"type": "integer", "description": "which match if several (0-based)", "default": 0}},
      category="ui")
def tap_text(text: str, index: int = 0) -> str:
    raw = screen_dump(80)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    needle = text.lower().strip()
    hits = [
        e for e in data.get("elements", [])
        if needle in str(e.get("text", "")).lower() and e.get("tap")
    ]
    if not hits:
        return f"FAILED: no element containing '{text}' on screen."
    target = hits[min(index, len(hits) - 1)]
    x, y = target["tap"]
    ok, msg = _ok(f"input tap {x} {y}")
    return f"Tapped '{target['text']}' at ({x},{y})." if ok else f"FAILED: {msg}"


@tool("swipe", "Swipe from one coordinate to another (scroll, dismiss, unlock).",
      {"x1": {"type": "integer", "required": True}, "y1": {"type": "integer", "required": True},
       "x2": {"type": "integer", "required": True}, "y2": {"type": "integer", "required": True},
       "ms": {"type": "integer", "default": 300}}, category="ui")
def swipe(x1: int, y1: int, x2: int, y2: int, ms: int = 300) -> str:
    ok, msg = _ok(f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(ms)}")
    return "Swiped." if ok else f"FAILED: {msg}"


@tool("scroll", "Scroll the current screen down or up by one screen-height.",
      {"direction": {"type": "string", "enum": ["down", "up", "left", "right"], "default": "down"},
       "amount": {"type": "number", "description": "fraction of the screen, 0.1-0.9", "default": 0.6}},
      category="ui")
def scroll(direction: str = "down", amount: float = 0.6) -> str:
    size = screen_size()
    w, h = size.get("width", 1080), size.get("height", 2400)
    frac = max(0.1, min(0.9, float(amount)))
    cx, cy = w // 2, h // 2
    dx, dy = int(w * frac / 2), int(h * frac / 2)
    coords = {
        "down": (cx, cy + dy, cx, cy - dy),
        "up": (cx, cy - dy, cx, cy + dy),
        "left": (cx + dx, cy, cx - dx, cy),
        "right": (cx - dx, cy, cx + dx, cy),
    }[direction]
    ok, msg = _ok(f"input swipe {coords[0]} {coords[1]} {coords[2]} {coords[3]} 250")
    return f"Scrolled {direction}." if ok else f"FAILED: {msg}"


def screen_size() -> dict:
    rc, out, _ = _sh("wm size")
    m = re.search(r"(\d+)x(\d+)", out)
    if m:
        return {"width": int(m.group(1)), "height": int(m.group(2))}
    return {"width": 1080, "height": 2400}


@tool("type_text", "Type text into the focused input field (spaces are handled).",
      {"text": {"type": "string", "required": True}}, category="ui")
def type_text(text: str) -> str:
    safe = text.replace(" ", "%s").replace("'", "").replace('"', "")
    ok, msg = _ok(f"input text {shlex.quote(safe)}")
    return f"Typed {len(text)} characters." if ok else f"FAILED: {msg}"


@tool("press_key", "Press a hardware/system key.",
      {"key": {"type": "string", "description": "home, back, recents, enter, delete, power, wake, "
                                                "volume_up, volume_down, play, next, previous, tab, menu",
               "required": True}}, category="ui")
def press_key(key: str) -> str:
    code = KEYCODES.get(key.lower(), key.upper())
    ok, msg = _ok(f"input keyevent {code}")
    return f"Pressed {key}." if ok else f"FAILED: {msg}"


@tool("screenshot", "Capture the screen to a PNG file (also useful for debugging a flow).",
      {"path": {"type": "string", "description": "Output path; defaults to the JARVIS media folder"}}, category="ui")
def screenshot(path: str = "") -> str:
    path = path or str(shims.media_dir() / "screen.png")
    ok, msg = _ok(f"screencap -p {shlex.quote(path)}")
    if not ok:
        return f"FAILED: {msg}"
    size = os.path.getsize(path) if os.path.exists(path) else 0
    if size == 0:
        return (
            "FAILED: screencap produced no data. This ROM denies frame-buffer "
            "access to apps (common on Android 14+ cloud phones); screenshots "
            "are unavailable from Termux on this device."
        )
    return json.dumps({"saved": path, "bytes": size})


@tool("wait", "Pause for a few seconds while an app loads.",
      {"seconds": {"type": "number", "default": 2}}, category="ui")
def wait(seconds: float = 2) -> str:
    time.sleep(max(0.0, min(30.0, float(seconds))))
    return f"Waited {seconds}s."


@tool("notification_shade", "Pull down (open=true) or dismiss (open=false) the notification shade.",
      {"open": {"type": "boolean", "default": True}}, category="ui")
def notification_shade(open: bool = True) -> str:
    cmd = "cmd statusbar expand-notifications" if open else "cmd statusbar collapse"
    ok, msg = _ok(cmd)
    if not ok:
        h = screen_size().get("height", 2400)
        swipe_kwargs = dict(x1=540, y1=10, x2=540, y2=h - 200) if open else dict(x1=540, y1=h - 200, x2=540, y2=10)
        return swipe(**swipe_kwargs)
    return "Shade opened." if open else "Shade closed."


@tool("screen_on", "Wake the screen and dismiss the keyguard (if no PIN is set).", category="ui")
def screen_on() -> str:
    steps = ["input keyevent KEYCODE_WAKEUP", "wm dismiss-keyguard"]
    results = []
    for cmd in steps:
        ok, msg = _ok(cmd)
        results.append("ok" if ok else msg)
    return "Screen on. " + ("; ".join(results))


@tool("screen_state", "Is the screen on or off, and is the device locked?", category="ui")
def screen_state() -> str:
    rc, out, _ = _sh("dumpsys power 2>/dev/null | grep -E 'mWakefulness=|Display Power'")
    locked = _sh("dumpsys window 2>/dev/null | grep -E 'mDreamingLockscreen|isStatusBarKeyguard'")[1]
    return json.dumps(
        {
            "wakefulness": "Awake" if "Awake" in out else ("Asleep" if "Asleep" in out else "unknown"),
            "keyguard": "true" in locked.lower(),
            "raw": out.strip()[:200],
        }
    )


@tool("ui_pipeline",
      "Run a short scripted UI sequence in one call: a list of steps such as "
      "['launch:whatsapp', 'wait:3', 'tap_text:Pepper Potts', 'type:Hello', 'key:enter'].",
      {"steps": {"type": "array", "items": {"type": "string"}, "required": True}},
      category="ui")
def ui_pipeline(steps: list[str]) -> str:
    log = []
    for step in steps or []:
        action, _, arg = str(step).partition(":")
        action, arg = action.strip().lower(), arg.strip()
        if action == "launch":
            log.append(launch_app(arg))
        elif action == "wait":
            log.append(wait(float(arg or 2)))
        elif action == "tap":
            x, _, y = arg.partition(",")
            log.append(tap(int(x), int(y)))
        elif action == "tap_text":
            log.append(tap_text(arg))
        elif action == "type":
            log.append(type_text(arg))
        elif action == "key":
            log.append(press_key(arg))
        elif action == "swipe":
            nums = [int(n) for n in re.findall(r"-?\d+", arg)]
            log.append(swipe(*nums[:4]) if len(nums) >= 4 else "FAILED: swipe needs x1,y1,x2,y2")
        elif action == "scroll":
            log.append(scroll(arg or "down"))
        elif action == "dump":
            log.append(screen_dump())
        elif action == "home":
            log.append(press_key("home"))
        else:
            log.append(f"skipped unknown step '{step}'")
        time.sleep(0.35)
    return "\n".join(log)
