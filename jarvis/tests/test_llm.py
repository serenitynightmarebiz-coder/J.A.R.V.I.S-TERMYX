"""Provider tests against a local fake server — no network, no API keys.

Run with:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import llm  # noqa: E402
from jarvis.config import ProviderConfig  # noqa: E402


class FakeAPI(BaseHTTPRequestHandler):
    routes: dict[str, tuple[int, dict]] = {}
    seen: list[dict] = []
    seen_headers: list[dict] = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        FakeAPI.seen.append({"path": self.path, "body": body})
        FakeAPI.seen_headers.append(dict(self.headers))
        key = self.path.split("?")[0]
        status, payload = FakeAPI.routes.get(key, (404, {"error": {"message": "no route"}}))
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):  # silence
        pass


class ProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeAPI)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        FakeAPI.routes.clear()
        FakeAPI.seen.clear()
        FakeAPI.seen_headers.clear()

    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    # ---------------------------------------------------------------- OpenAI
    def test_openai_text_reply(self):
        FakeAPI.routes["/v1/chat/completions"] = (
            200,
            {"choices": [{"message": {"content": "At once, sir."}}]},
        )
        cfg = ProviderConfig(name="custom", model="m", base_url=self.base(), api_key="k")
        turn = llm.build(cfg).chat([{"role": "user", "content": "hi"}])
        self.assertEqual(turn.content, "At once, sir.")
        self.assertFalse(turn.has_tools)
        sent = FakeAPI.seen[0]["body"]
        self.assertEqual(sent["model"], "m")
        self.assertEqual(FakeAPI.seen_headers[0]["Authorization"], "Bearer k")

    def test_openai_tool_calls_parsed(self):
        FakeAPI.routes["/v1/chat/completions"] = (
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_9",
                                    "type": "function",
                                    "function": {
                                        "name": "launch_app",
                                        "arguments": '{"app": "spotify"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )
        cfg = ProviderConfig(name="custom", model="m", base_url=self.base())
        tools = [{"name": "launch_app", "description": "d", "parameters": {"type": "object", "properties": {}}}]
        turn = llm.build(cfg).chat([{"role": "user", "content": "open spotify"}], tools)
        self.assertEqual(turn.tool_calls[0]["function"]["name"], "launch_app")
        self.assertEqual(json.loads(turn.tool_calls[0]["function"]["arguments"])["app"], "spotify")
        sent = FakeAPI.seen[0]["body"]
        self.assertEqual(sent["tools"][0]["type"], "function")
        self.assertEqual(sent["tool_choice"], "auto")

    def test_http_error_is_actionable(self):
        FakeAPI.routes["/v1/chat/completions"] = (401, {"error": {"message": "bad key"}})
        cfg = ProviderConfig(name="openai", model="gpt-4o-mini", base_url=self.base(), api_key="nope")
        with self.assertRaises(llm.LLMError) as ctx:
            llm.build(cfg).chat([{"role": "user", "content": "hi"}])
        msg = str(ctx.exception)
        self.assertIn("401", msg)
        self.assertIn("API key", msg)

    def test_connection_refused_says_how_to_fix(self):
        cfg = ProviderConfig(name="ollama", model="llama3.1", base_url="http://127.0.0.1:9/v1")
        with self.assertRaises(llm.LLMError) as ctx:
            llm.build(cfg).chat([{"role": "user", "content": "hi"}])
        self.assertIn("start it first", str(ctx.exception))

    # ------------------------------------------------------------- Anthropic
    def test_anthropic_conversion_and_reply(self):
        FakeAPI.routes["/v1/messages"] = (
            200,
            {
                "content": [
                    {"type": "text", "text": "Checking."},
                    {"type": "tool_use", "id": "tu_1", "name": "battery_status", "input": {}},
                ]
            },
        )
        cfg = ProviderConfig(name="anthropic", model="claude", base_url=self.base(), api_key="k")
        messages = [
            {"role": "system", "content": "You are JARVIS."},
            {"role": "user", "content": "battery?"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "tu_0", "type": "function",
                     "function": {"name": "torch", "arguments": '{"on": true}'}}
                ],
            },
            {"role": "tool", "tool_call_id": "tu_0", "name": "torch", "content": "Torch on."},
        ]
        tools = [{"name": "torch", "description": "d", "parameters": {"type": "object", "properties": {}}}]
        turn = llm.build(cfg).chat(messages, tools)
        self.assertEqual(turn.content, "Checking.")
        self.assertEqual(turn.tool_calls[0]["function"]["name"], "battery_status")
        sent = FakeAPI.seen[0]["body"]
        self.assertEqual(sent["system"], "You are JARVIS.")
        self.assertEqual(sent["tools"][0]["input_schema"]["type"], "object")
        roles = [m["role"] for m in sent["messages"]]
        self.assertEqual(roles, ["user", "assistant", "user"])  # tool result becomes a user block
        tool_result = sent["messages"][-1]["content"][0]
        self.assertEqual(tool_result["type"], "tool_result")
        self.assertEqual(tool_result["tool_use_id"], "tu_0")
        self.assertEqual({k.lower(): v for k, v in FakeAPI.seen_headers[0].items()}["x-api-key"], "k")

    # ---------------------------------------------------------------- Gemini
    def test_gemini_conversion_and_reply(self):
        FakeAPI.routes["/v1beta/models/gemini-1.5-flash:generateContent"] = (
            200,
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "Right away."},
                                {"functionCall": {"name": "torch", "args": {"on": True}}},
                            ]
                        }
                    }
                ]
            },
        )
        cfg = ProviderConfig(
            name="gemini", model="gemini-1.5-flash", base_url=self.base() + "beta", api_key="k"
        )
        cfg.base_url = f"http://127.0.0.1:{self.port}/v1beta"
        turn = llm.build(cfg).chat(
            [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "torch"},
                {"role": "tool", "tool_call_id": "x", "name": "torch", "content": "on"},
            ],
            [{"name": "torch", "description": "d", "parameters": {"type": "object", "properties": {}}}],
        )
        self.assertEqual(turn.content, "Right away.")
        self.assertEqual(turn.tool_calls[0]["function"]["name"], "torch")
        self.assertEqual(json.loads(turn.tool_calls[0]["function"]["arguments"]), {"on": True})
        sent = FakeAPI.seen[0]["body"]
        self.assertEqual(sent["systemInstruction"]["parts"][0]["text"], "sys")
        self.assertEqual(sent["tools"][0]["functionDeclarations"][0]["name"], "torch")
        self.assertIn("functionResponse", json.dumps(sent["contents"]))

    # ------------------------------------------------------------- config glue
    def test_provider_presets_and_key_env(self):
        cfg = ProviderConfig(name="groq").resolved()
        self.assertIn("groq.com", cfg.base_url)
        self.assertEqual(cfg.model, "llama-3.3-70b-versatile")
        self.assertTrue(cfg.needs_key)
        local = ProviderConfig(name="ollama").resolved()
        self.assertTrue(local.is_local)
        self.assertFalse(local.needs_key)
        self.assertIn("11434", local.base_url)

    def test_unknown_provider_is_rejected(self):
        with self.assertRaises(llm.LLMError):
            llm.build(ProviderConfig(name="skynet"))


if __name__ == "__main__":
    unittest.main()
