"""Background service: JARVIS that keeps working when no terminal is attached.

Exposes a loopback HTTP API so widgets, Tasker, shortcuts and other apps can
talk to the same running assistant. The wake gate is enforced by the server, and
the follow-up window is shared, so "jarvis, torch on" then "jarvis, torch off"
behaves exactly as it does in the terminal.

    python jarvis.py --serve        run in the foreground
    python jarvis.py --start        detach, hold a wake lock, keep a notification
    python jarvis.py --status       is it alive, and what does it say?
    python jarvis.py --stop         shut it down
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import llm as llm_mod
from . import shims, ui
from .agent import Agent
from .config import APP_DIR, Config
from .gate import WakeGate
from .memory import Memory
from .tools import memory_tools
from .voice import Voice

PID_FILE = Path(APP_DIR) / "data" / "jarvis.pid"
LOG_FILE = Path(APP_DIR) / "logs" / "daemon.log"
NOTIFY_ID = 42


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def running_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return None
    return pid if _pid_alive(pid) else None


def _ensure_token(cfg: Config) -> str:
    if not cfg.api_token:
        import secrets

        cfg.api_token = secrets.token_urlsafe(24)
        cfg.save()
    return cfg.api_token


# --------------------------------------------------------------------- server
class JarvisServer:
    def __init__(self, cfg: Config, port: int | None = None, host: str = "127.0.0.1"):
        self.cfg = cfg
        self.host = host
        self.port = int(port or cfg.service_port)
        self.gate = WakeGate(
            word=cfg.wake_word, enabled=cfg.require_wake_word, window=cfg.awake_window
        )
        self.memory = Memory.load()
        memory_tools.bind(self.memory)
        from .tools import selfmod

        selfmod.bind(cfg)
        self.voice = Voice(cfg) if cfg.voice_output else None
        self.agent = Agent(
            cfg,
            self.memory,
            speaker=self.voice.speak if self.voice and self.voice.enabled else None,
            confirm=lambda name, args: False,  # never auto-approve danger in the daemon
        )
        self.lock = threading.Lock()
        self.started = time.time()
        self.requests = 0
        self.httpd: ThreadingHTTPServer | None = None

    # ------------------------------------------------------------------ state
    def snapshot(self) -> dict:
        return {
            "ok": True,
            "pid": os.getpid(),
            "model": self.cfg.provider.model,
            "provider": self.cfg.provider.name,
            "gate": self.gate.status,
            "awake": self.gate.awake,
            "uptime_s": int(time.time() - self.started),
            "requests": self.requests,
            "voice": bool(self.voice and self.voice.enabled),
        }

    # ----------------------------------------------------------------- ask API
    def handle_ask(self, text: str) -> tuple[int, dict]:
        decision = self.gate.check(text)
        if not decision.addressed:
            return 403, {
                "error": "not addressed",
                "hint": f'Say "{self.cfg.wake_word}" first, e.g. "{self.cfg.wake_word}, torch on".',
                "gate": self.gate.status,
            }
        if not decision.request:
            if decision.reason == "woken":
                line = f"At your service, {self.cfg.address}."
                if self.voice:
                    self.voice.speak(line)
                return 200, {"reply": line, "reason": "woken", "gate": self.gate.status}
            return 200, {"reply": "Standing by.", "reason": "sleep", "gate": self.gate.status}

        with self.lock:
            self.requests += 1
            try:
                reply = self.agent.ask(decision.request)
            except llm_mod.LLMError as exc:
                return 502, {"error": str(exc), "gate": self.gate.status}
        if decision.reason != "sleep":
            self.gate.open()
        return 200, {"reply": reply, "gate": self.gate.status, "reason": decision.reason}

    # ------------------------------------------------------------------- serve
    def build_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "JARVIS/1.0"

            def _json(self, status: int, payload: dict) -> None:
                raw = json.dumps(payload, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _authorised(self) -> bool:
                supplied = self.headers.get("X-Jarvis-Token", "")
                if not supplied:
                    from urllib.parse import parse_qs, urlsplit

                    supplied = (parse_qs(urlsplit(self.path).query).get("token") or [""])[0]
                return bool(supplied) and supplied == server.cfg.api_token

            def log_message(self, fmt, *args):  # keep the log readable
                return

            def do_GET(self):  # noqa: N802
                from urllib.parse import parse_qs, urlsplit

                parts = urlsplit(self.path)
                if parts.path in ("/health", "/"):
                    return self._json(200, server.snapshot())
                if parts.path == "/ask":
                    if not self._authorised():
                        return self._json(401, {"error": "missing or wrong token"})
                    text = (parse_qs(parts.query).get("text") or [""])[0]
                    status, payload = server.handle_ask(text)
                    return self._json(status, payload)
                return self._json(404, {"error": "unknown path"})

            def do_POST(self):  # noqa: N802
                from urllib.parse import urlsplit

                length = int(self.headers.get("Content-Length") or 0)
                body: dict = {}
                if length:
                    try:
                        body = json.loads(self.rfile.read(length) or b"{}")
                    except json.JSONDecodeError:
                        return self._json(400, {"error": "body must be JSON"})
                path = urlsplit(self.path).path
                if path == "/reload":
                    if not self._authorised():
                        return self._json(401, {"error": "missing or wrong token"})
                    self._json(202, {"reply": "Restarting."})
                    threading.Thread(target=restart_self, daemon=True).start()
                    return
                if path != "/ask":
                    return self._json(404, {"error": "unknown path"})
                if not self._authorised():
                    return self._json(401, {"error": "missing or wrong token"})
                status, payload = server.handle_ask(str(body.get("text", "")))
                return self._json(status, payload)

        return Handler

    def serve_forever(self) -> None:
        self.httpd = ThreadingHTTPServer((self.host, self.port), self.build_handler())
        self.httpd.daemon_threads = True
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(os.getpid()))
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        watch_restart_flag(self)
        ui.info(f"JARVIS service on http://{self.host}:{self.port} (token required)")
        try:
            self.httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            self.httpd.server_close()
            if PID_FILE.exists() and PID_FILE.read_text().strip() == str(os.getpid()):
                PID_FILE.unlink()
            ui.info("service stopped")


def restart_self() -> None:
    """Re-exec the same interpreter so edited code takes effect."""
    time.sleep(0.4)
    entry = Path(APP_DIR) / "jarvis.py"
    os.execv(sys.executable, [sys.executable, str(entry), "--serve"])


def watch_restart_flag(server: JarvisServer) -> None:
    flag = Path(APP_DIR) / "data" / "restart.flag"
    if not flag.exists():
        return
    flag.unlink()
    threading.Thread(target=restart_self, daemon=True).start()


# ------------------------------------------------------------------ lifecycle
def start(cfg: Config) -> int:
    pid = running_pid()
    if pid:
        ui.info(f"already running (pid {pid}) on port {cfg.service_port}")
        return 0
    token = _ensure_token(cfg)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = Path(APP_DIR) / "jarvis.py"
    env = {**os.environ, "JARVIS_HOME": str(APP_DIR)}
    with LOG_FILE.open("ab") as log:
        proc = subprocess.Popen(
            [sys.executable, str(entry), "--serve", "--port", str(cfg.service_port)],
            cwd=str(APP_DIR),
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
        )
    time.sleep(1.2)
    if proc.poll() is not None:
        tail = LOG_FILE.read_text(errors="replace").strip().splitlines()[-6:]
        ui.warn("service failed to start:\n  " + "\n  ".join(tail or ["(no log)"]))
        return 1

    shims.run(["termux-wake-lock"], timeout=5)
    shims.run(
        [
            "termux-notification",
            "--id",
            str(NOTIFY_ID),
            "--title",
            "JARVIS",
            "--content",
            f'At your service — say "{cfg.wake_word}" (port {cfg.service_port})',
            "--ongoing",
            "--priority",
            "low",
        ],
        timeout=5,
    )
    ui.ok(f"JARVIS running (pid {proc.pid}) on 127.0.0.1:{cfg.service_port}")
    ui.info(f"token: {token}")
    ui.info("ask it with: python jarvis.py --ask 'jarvis, battery'   (or POST /ask)")
    return 0


def stop() -> int:
    pid = running_pid()
    if not pid:
        ui.info("not running")
        PID_FILE.unlink(missing_ok=True)
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        ui.warn(f"could not stop pid {pid}: {exc}")
        return 1
    for _ in range(20):
        if not _pid_alive(pid):
            break
        time.sleep(0.1)
    else:
        os.kill(pid, signal.SIGKILL)
    PID_FILE.unlink(missing_ok=True)
    shims.run(["termux-notification-remove", str(NOTIFY_ID)], timeout=5)
    shims.run(["termux-wake-unlock"], timeout=5)
    ui.ok("JARVIS stopped")
    return 0


def status(cfg: Config) -> dict:
    pid = running_pid()
    info: dict = {"pid": pid, "port": cfg.service_port, "gate": None, "health": None}
    if not pid:
        info["state"] = "stopped"
        return info
    info["state"] = "running"
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{cfg.service_port}/health")
        with urllib.request.urlopen(req, timeout=2) as resp:
            info["health"] = json.loads(resp.read().decode())
            info["gate"] = info["health"].get("gate")
    except (urllib.error.URLError, socket.timeout, json.JSONDecodeError, OSError) as exc:
        info["health"] = f"unreachable: {exc}"
    return info


def ask_daemon(cfg: Config, text: str, timeout: int = 180) -> str:
    """Send a request to the running service (used by --ask when it is up)."""
    payload = json.dumps({"text": text}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{cfg.service_port}/ask",
        data=payload,
        headers={"Content-Type": "application/json", "X-Jarvis-Token": cfg.api_token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode()).get("reply", "")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        return f"FAILED: HTTP {exc.code} — {detail}"
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        return f"FAILED: service unreachable ({exc})"
