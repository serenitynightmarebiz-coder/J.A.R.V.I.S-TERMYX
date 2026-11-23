"""Background service: wake gating, token auth, HTTP surface."""

from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import shims  # noqa: E402
from jarvis.config import Config  # noqa: E402
from jarvis.service import JarvisServer  # noqa: E402

shims.MOCK = True


class StubAgent:
    def __init__(self, reply: str = "At once, sir."):
        self.reply = reply
        self.asked: list[str] = []

    def ask(self, text: str) -> str:
        self.asked.append(text)
        return self.reply


class TestServiceLogic(unittest.TestCase):
    def setUp(self):
        cfg = Config()
        cfg.require_wake_word = True
        cfg.awake_window = 25
        cfg.api_token = "test-token"
        cfg.voice_output = False
        self.server = JarvisServer(cfg)
        self.server.agent = StubAgent()
        self.server.voice = None

    def test_health_snapshot(self):
        snap = self.server.snapshot()
        self.assertTrue(snap["ok"])
        self.assertIn("gate", snap)

    def test_unaddressed_request_is_refused(self):
        status, payload = self.server.handle_ask("open spotify")
        self.assertEqual(status, 403)
        self.assertIn("not addressed", payload["error"])
        self.assertEqual(self.server.agent.asked, [])

    def test_addressed_request_runs(self):
        status, payload = self.server.handle_ask("jarvis, open spotify")
        self.assertEqual(status, 200)
        self.assertEqual(payload["reply"], "At once, sir.")
        self.assertEqual(self.server.agent.asked, ["open spotify"])

    def test_follow_up_within_window(self):
        self.server.handle_ask("jarvis, open spotify")
        status, payload = self.server.handle_ask("and pause the music")
        self.assertEqual(status, 200)
        self.assertEqual(self.server.agent.asked[-1], "and pause the music")

    def test_sleep_then_refusal(self):
        self.server.handle_ask("jarvis, open spotify")
        status, payload = self.server.handle_ask("jarvis, go to sleep")
        self.assertEqual(status, 200)
        self.assertEqual(payload["reason"], "sleep")
        status, _ = self.server.handle_ask("open spotify")
        self.assertEqual(status, 403)

    def test_bare_wake_word_greets_without_running_a_task(self):
        status, payload = self.server.handle_ask("jarvis")
        self.assertEqual(status, 200)
        self.assertEqual(payload["reason"], "woken")
        self.assertEqual(self.server.agent.asked, [])


class TestHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cfg = Config()
        cfg.api_token = "test-token"
        cfg.voice_output = False
        cfg.require_wake_word = True
        cls.server = JarvisServer(cfg)
        cls.server.agent = StubAgent("Battery at 76%, sir.")
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), cls.server.build_handler())
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        self.server.gate.close()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def post(self, path: str, body: dict, token: str | None = "test-token"):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Jarvis-Token"] = token
        req = urllib.request.Request(
            self.url(path), data=json.dumps(body).encode(), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode() or "{}")

    def test_health_needs_no_token(self):
        with urllib.request.urlopen(self.url("/health"), timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertTrue(json.loads(resp.read().decode())["ok"])

    def test_token_required(self):
        status, payload = self.post("/ask", {"text": "jarvis, hi"}, token=None)
        self.assertEqual(status, 401)
        status, payload = self.post("/ask", {"text": "jarvis, hi"}, token="wrong")
        self.assertEqual(status, 401)

    def test_ask_with_token(self):
        status, payload = self.post("/ask", {"text": "jarvis, battery"})
        self.assertEqual(status, 200)
        self.assertIn("76", payload["reply"])

    def test_unaddressed_is_403(self):
        status, payload = self.post("/ask", {"text": "battery"})
        self.assertEqual(status, 403)

    def test_get_ask_query(self):
        req = urllib.request.Request(
            self.url("/ask?text=jarvis,+battery&token=test-token"), method="GET"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(json.loads(resp.read().decode())["reply"], "Battery at 76%, sir.")

    def test_unknown_path(self):
        status, _ = self.post("/nope", {})
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
