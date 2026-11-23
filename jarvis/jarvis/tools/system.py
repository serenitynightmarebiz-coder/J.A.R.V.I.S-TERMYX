"""System tools: shell, files, clock/alarms, web, and device housekeeping."""

from __future__ import annotations

import html
import json
import os
import re
import shlex
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from .. import shims
from .registry import tool

HOME = Path(os.environ.get("HOME", "/data/data/com.termux/files/home"))


# --------------------------------------------------------------------- shell
@tool(
    "run_shell",
    "Run a shell command in Termux (pkg, git, python, ffmpeg, curl...). Returns stdout+stderr.",
    {"command": {"type": "string", "required": True},
     "timeout": {"type": "integer", "default": 60}},
    danger=True,
    category="system",
)
def run_shell(command: str, timeout: int = 60) -> str:
    rc, out, err = shims.shell(command, timeout=min(int(timeout), 300))
    body = (out + ("\n" + err if err.strip() else "")).strip()
    if len(body) > 3500:
        body = body[:3500] + f"\n… truncated ({len(body)} chars total)"
    return f"exit={rc}\n{body or '(no output)'}"


@tool("time_now", "Current date, time and timezone on the device.", category="system")
def time_now() -> str:
    now = datetime.now()
    return json.dumps(
        {
            "iso": now.isoformat(timespec="seconds"),
            "human": now.strftime("%A %d %B %Y, %H:%M"),
            "timezone": now.astimezone().tzname(),
            "epoch": int(now.timestamp()),
        }
    )


@tool("disk_usage", "Free and used storage, plus memory pressure.", category="system")
def disk_usage() -> str:
    out = {}
    rc, text, _ = shims.shell("df -h /data 2>/dev/null | tail -1")
    if rc == 0 and text.strip():
        parts = text.split()
        if len(parts) >= 4:
            out["storage"] = {"size": parts[1], "used": parts[2], "free": parts[3], "use%": parts[4]}
    rc, text, _ = shims.shell("cat /proc/meminfo 2>/dev/null | head -3")
    if rc == 0 and text.strip():
        mem = {}
        for line in text.splitlines():
            k, _, v = line.partition(":")
            mem[k.strip()] = v.strip()
        out["memory"] = mem
    return json.dumps(out or {"error": "unavailable"})


# --------------------------------------------------------------------- files
def _safe_path(path: str) -> Path:
    p = Path(os.path.expanduser(path))
    if not p.is_absolute():
        p = HOME / p
    return p


@tool("read_file", "Read a text file (truncated to 4000 characters).",
      {"path": {"type": "string", "required": True}}, category="files")
def read_file(path: str) -> str:
    p = _safe_path(path)
    if not p.exists():
        return f"FAILED: {p} does not exist."
    if p.is_dir():
        return f"FAILED: {p} is a directory (use list_dir)."
    try:
        data = p.read_text(errors="replace")
    except OSError as exc:
        return f"FAILED: {exc}"
    return data[:4000] + ("\n… truncated" if len(data) > 4000 else "")


@tool("write_file", "Create or overwrite a text file. Creates parent directories.",
      {"path": {"type": "string", "required": True},
       "content": {"type": "string", "required": True}}, category="files")
def write_file(path: str, content: str) -> str:
    p = _safe_path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"Wrote {len(content)} bytes to {p}."
    except OSError as exc:
        return f"FAILED: {exc}"


@tool("append_file", "Append text to a file (notes, logs).",
      {"path": {"type": "string", "required": True},
       "content": {"type": "string", "required": True}}, category="files")
def append_file(path: str, content: str) -> str:
    p = _safe_path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as fh:
            fh.write(content if content.endswith("\n") else content + "\n")
        return f"Appended to {p}."
    except OSError as exc:
        return f"FAILED: {exc}"


@tool("list_dir", "List files in a directory with sizes.",
      {"path": {"type": "string", "default": "."}}, category="files")
def list_dir(path: str = ".") -> str:
    p = _safe_path(path)
    if not p.exists():
        return f"FAILED: {p} does not exist."
    if p.is_file():
        return f"{p} is a file ({p.stat().st_size} bytes)."
    rows = []
    for item in sorted(p.iterdir(), key=lambda i: (i.is_file(), i.name.lower()))[:60]:
        try:
            size = item.stat().st_size
        except OSError:
            size = 0
        rows.append(f"{item.name}{'/' if item.is_dir() else ''} ({size}b)")
    return f"{p}:\n" + ("\n".join(rows) or "(empty)")


@tool("delete_path", "Delete a file or directory recursively.",
      {"path": {"type": "string", "required": True}}, danger=True, category="files")
def delete_path(path: str) -> str:
    p = _safe_path(path)
    if str(p) in ("/", str(HOME)):
        return "FAILED: refusing to delete that path."
    if not p.exists():
        return f"FAILED: {p} does not exist."
    try:
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        return f"Deleted {p}."
    except OSError as exc:
        return f"FAILED: {exc}"


@tool("search_files", "Search file contents or names under a directory.",
      {"query": {"type": "string", "required": True},
       "path": {"type": "string", "default": "."},
       "by_name": {"type": "boolean", "default": False}}, category="files")
def search_files(query: str, path: str = ".", by_name: bool = False) -> str:
    p = _safe_path(path)
    if by_name:
        hits = [str(f) for f in p.rglob(f"*{query}*")][:40]
        return "\n".join(hits) if hits else f"No files matching {query}."
    rc, out, err = shims.shell(
        f"grep -rIl --exclude-dir=.git -e {shlex.quote(query)} {shlex.quote(str(p))} 2>/dev/null | head -20",
        timeout=45,
    )
    if rc != 0 or not out.strip():
        return err.strip() or f"No files containing '{query}'."
    files = out.strip().splitlines()
    detail = []
    for f in files[:6]:
        rc2, lines, _ = shims.shell(f"grep -n -m 3 -e {shlex.quote(query)} {shlex.quote(f)} 2>/dev/null")
        if rc2 == 0:
            detail.append(f"{f}:\n{lines.strip()}")
    return "\n".join(detail)[:3000] or "\n".join(files)


# ----------------------------------------------------------------------- web
def _http_get(url: str, timeout: int = 20, headers: dict | None = None) -> tuple[bool, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Linux; Android 14; Termux) JARVIS/1.0",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    text = html.unescape(raw)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


@tool("web_fetch", "Fetch a URL and return readable text (for reading articles, docs, JSON APIs).",
      {"url": {"type": "string", "required": True},
       "chars": {"type": "integer", "default": 3000}}, category="web")
def web_fetch(url: str, chars: int = 3000) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    ok, body = _http_get(url)
    if not ok:
        return f"FAILED: {body}"
    text = _html_to_text(body) if "<" in body[:400] else body
    return text[: int(chars)] or "(empty page)"


@tool("web_search", "Search the web (DuckDuckGo) and return titles, URLs and snippets.",
      {"query": {"type": "string", "required": True},
       "count": {"type": "integer", "default": 5}}, category="web")
def web_search(query: str, count: int = 5) -> str:
    ok, body = _http_get(
        "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query),
        headers={"Accept-Language": "en-GB,en;q=0.9"},
    )
    if not ok:
        return f"FAILED: {body}"
    results = []
    for m in re.finditer(
        r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'class="result__snippet"[^>]*>(.*?)</a>',
        body,
        re.S,
    ):
        url, title, snippet = m.groups()
        url = html.unescape(url)
        url = re.sub(r"^//duckduckgo\.com/l/\?uddg=", "", url)
        results.append(
            {
                "title": _html_to_text(title)[:120],
                "url": urllib.parse.unquote(url)[:200],
                "snippet": _html_to_text(snippet)[:180],
            }
        )
        if len(results) >= int(count):
            break
    if not results:
        return f"No results for '{query}' (or the search engine blocked us)."
    return json.dumps(results, ensure_ascii=False)


@tool("weather", "Current weather and today's forecast for a place (no API key).",
      {"place": {"type": "string", "default": ""}}, category="web")
def weather(place: str = "") -> str:
    target = place.strip().replace(" ", "+") or ""
    ok, body = _http_get(f"https://wttr.in/{target}?format=j1", timeout=25)
    if not ok:
        return f"FAILED: {body}"
    try:
        data = json.loads(body)
        cur = data["current_condition"][0]
        today = data["weather"][0]
        area = data["nearest_area"][0]["areaName"][0]["value"]
        return json.dumps(
            {
                "place": area,
                "temp_c": cur["temp_C"],
                "feels_like_c": cur["FeelsLikeC"],
                "condition": cur["weatherDesc"][0]["value"],
                "humidity": cur["humidity"],
                "wind_kmph": cur["windspeedKmph"],
                "max_c": today["maxtempC"],
                "min_c": today["mintempC"],
                "chance_of_rain": today["hourly"][4]["chanceofrain"],
            }
        )
    except (KeyError, IndexError, json.JSONDecodeError):
        return "FAILED: weather service returned an unexpected shape."


# ------------------------------------------------------------- clock / alarms
@tool("set_alarm", "Set a system alarm. Time as HH:MM (24h).",
      {"time": {"type": "string", "required": True},
       "label": {"type": "string", "default": "JARVIS"}}, category="system")
def set_alarm(time: str, label: str = "JARVIS") -> str:
    m = re.match(r"^(\d{1,2}):(\d{2})$", time.strip())
    if not m:
        return "FAILED: time must be HH:MM (24h)."
    hh, mm = int(m.group(1)), int(m.group(2))
    cmd = (
        f"am start -a android.intent.action.SET_ALARM --ei android.intent.extra.alarm.HOUR {hh} "
        f"--ei android.intent.extra.alarm.MINUTES {mm} "
        f"--es android.intent.extra.alarm.MESSAGE {shlex.quote(label)} "
        "--ez android.intent.extra.alarm.SKIP_UI true"
    )
    rc, _, err = shims.shell(cmd)
    return f"Alarm set for {time}." if rc == 0 else f"FAILED: {err.strip()}"


@tool("set_timer", "Start a countdown timer in seconds.",
      {"seconds": {"type": "integer", "required": True},
       "label": {"type": "string", "default": "JARVIS timer"}}, category="system")
def set_timer(seconds: int, label: str = "JARVIS timer") -> str:
    secs = max(1, int(seconds))
    cmd = (
        f"am start -a android.intent.action.SET_TIMER --ei android.intent.extra.alarm.LENGTH {secs} "
        f"--es android.intent.extra.alarm.MESSAGE {shlex.quote(label)} "
        "--ez android.intent.extra.alarm.SKIP_UI true"
    )
    rc, _, err = shims.shell(cmd)
    return f"Timer started for {secs}s." if rc == 0 else f"FAILED: {err.strip()}"


@tool("add_calendar_event",
      "Create a calendar event. start/end are ISO strings (2025-01-31T18:00).",
      {"title": {"type": "string", "required": True},
       "start": {"type": "string", "required": True},
       "end": {"type": "string", "required": True},
       "location": {"type": "string", "default": ""}}, category="system")
def add_calendar_event(title: str, start: str, end: str, location: str = "") -> str:
    cmd = (
        "am start -a android.intent.action.INSERT -d content://com.android.calendar/events "
        f"--es title {shlex.quote(title)} --es eventLocation {shlex.quote(location)} "
        f"--ez beginTime {int(datetime.fromisoformat(start).timestamp() * 1000)} "
        f"--ez endTime {int(datetime.fromisoformat(end).timestamp() * 1000)}"
    )
    rc, _, err = shims.shell(cmd)
    return f"Calendar event '{title}' opened." if rc == 0 else f"FAILED: {err.strip()}"


@tool("send_whatsapp",
      "Open WhatsApp with a pre-filled message to a number (user still taps send).",
      {"number": {"type": "string", "required": True},
       "text": {"type": "string", "default": ""}}, category="apps")
def send_whatsapp(number: str, text: str = "") -> str:
    digits = re.sub(r"[^\d]", "", number)
    url = f"https://wa.me/{digits}" + (f"?text={urllib.parse.quote(text)}" if text else "")
    rc, _, err = shims.shell(f"am start -a android.intent.action.VIEW -d {shlex.quote(url)}")
    return f"WhatsApp opened for {number}." if rc == 0 else f"FAILED: {err.strip()}"


@tool("send_email", "Open the mail app with a pre-filled email (user taps send).",
      {"to": {"type": "string", "required": True},
       "subject": {"type": "string", "default": ""},
       "body": {"type": "string", "default": ""}}, category="apps")
def send_email(to: str, subject: str = "", body: str = "") -> str:
    cmd = (
        f"am start -a android.intent.action.SENDTO -d mailto: --es android.intent.extra.EMAIL "
        f"{shlex.quote(to)} --es android.intent.extra.SUBJECT {shlex.quote(subject)} "
        f"--es android.intent.extra.TEXT {shlex.quote(body)}"
    )
    rc, _, err = shims.shell(cmd)
    return f"Mail composer opened for {to}." if rc == 0 else f"FAILED: {err.strip()}"


@tool("termux_diagnostics", "Check that every JARVIS dependency is present on this device.",
      category="system")
def termux_diagnostics() -> str:
    checks = {
        "termux-api": "termux-battery-status",
        "termux-tts": "termux-tts-speak",
        "termux-stt": "termux-speech-to-text",
        "am": "am",
        "input": "input",
        "uiautomator": "uiautomator",
        "screencap": "screencap",
        "pm": "pm",
        "python": "python3",
        "curl": "curl",
        "git": "git",
    }
    rows = {name: ("ok" if shims.has(cmd) else "MISSING") for name, cmd in checks.items()}
    ready, msg = shims.device_ready()
    return json.dumps({"ready": ready, "detail": msg, "components": rows}, indent=2)
