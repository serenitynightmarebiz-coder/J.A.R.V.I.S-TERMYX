"""Core tests: registry, mock device, agent loop, memory, safety gates.

Run with:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["JARVIS_MOCK"] = "1"

from jarvis import llm, shims  # noqa: E402
from jarvis.agent import Agent  # noqa: E402
from jarvis.config import Config  # noqa: E402
from jarvis.memory import Memory  # noqa: E402
from jarvis.tools import REGISTRY, Budget, by_category, schemas  # noqa: E402
from jarvis.tools import android, memory_tools, system, termux_api  # noqa: E402

shims.MOCK = True


def cfg(**kw) -> Config:
    c = Config()
    c.confirm_danger = kw.pop("confirm_danger", False)
    c.greeting = False
    for k, v in kw.items():
        setattr(c, k, v)
    return c


class ScriptedClient:
    """Replays a fixed list of turns, recording the messages it saw."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.seen = []

    def chat(self, messages, tools=None):
        self.seen.append((json.loads(json.dumps(messages)), tools))
        if not self.turns:
            return llm.Turn(content="Done.")
        return self.turns.pop(0)


class TestRegistry(unittest.TestCase):
    def test_every_schema_is_valid(self):
        names = set()
        for schema in schemas():
            self.assertEqual(schema["parameters"]["type"], "object")
            self.assertIn("name", schema)
            self.assertTrue(schema["description"])
            for prop in schema["parameters"]["properties"].values():
                self.assertIn(prop["type"], {"string", "integer", "number", "boolean", "array", "object"})
            self.assertNotIn(schema["name"], names, "duplicate tool name")
            names.add(schema["name"])

    def test_required_fields_exist_in_properties(self):
        for schema in schemas():
            for req in schema["parameters"]["required"]:
                self.assertIn(req, schema["parameters"]["properties"])

    def test_core_capabilities_present(self):
        needed = {
            "launch_app", "screen_dump", "tap_text", "type_text", "press_key",
            "sms_send", "notify", "battery_status", "run_shell", "web_fetch",
            "remember", "recall", "torch", "volume",
        }
        self.assertTrue(needed.issubset(REGISTRY), needed - set(REGISTRY))

    def test_categories(self):
        cats = by_category()
        for expected in ("apps", "ui", "phone", "device", "system", "memory"):
            self.assertIn(expected, cats)

    def test_bad_arguments_do_not_raise(self):
        result = REGISTRY["tap"].call(nonsense=1)
        self.assertTrue(result.startswith(("TOOL ARGUMENT ERROR", "TOOL FAILED")) or "Tapped" in result)


class TestMockDevice(unittest.TestCase):
    def test_battery(self):
        self.assertIn("76", termux_api.battery_status())

    def test_apps_launch_and_foreground(self):
        self.assertIn("com.whatsapp", android.list_apps("whatsapp"))
        self.assertIn("Opened", android.launch_app("whatsapp"))
        self.assertIn("com.whatsapp", android.current_app())

    def test_ui_roundtrip(self):
        dump = json.loads(android.screen_dump())
        self.assertGreater(dump["count"], 0)
        self.assertTrue(any("WhatsApp" == e["text"] for e in dump["elements"]))
        self.assertIn("Tapped", android.tap_text("WhatsApp"))
        self.assertIn("not", android.tap_text("nope-not-there"))

    def test_files_roundtrip(self):
        tmp = tempfile.mkdtemp()
        p = f"{tmp}/nested/note.txt"
        self.assertIn("Wrote", system.write_file(p, "hello"))
        self.assertEqual(system.read_file(p), "hello")
        self.assertIn("note.txt", system.list_dir(f"{tmp}/nested"))
        self.assertIn("hello", system.search_files("hello", tmp))
        self.assertIn("Deleted", system.delete_path(p))

    def test_path_traversal_guard(self):
        self.assertIn("refusing", system.delete_path("/"))


class TestAgent(unittest.TestCase):
    def setUp(self):
        self.mem = Memory.load(Path(tempfile.mkdtemp()) / "memory.json")
        memory_tools.bind(self.mem)

    def test_native_tool_loop_feeds_result_back(self):
        client = ScriptedClient(
            [
                llm.Turn(
                    tool_calls=[
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "battery_status", "arguments": "{}"},
                        }
                    ]
                ),
                llm.Turn(content="Battery at 76%, sir."),
            ]
        )
        agent = Agent(cfg(), self.mem, client=client)
        answer = agent.ask("how is the battery")
        self.assertIn("76", answer)
        second_messages, second_tools = client.seen[1]
        tool_msgs = [m for m in second_messages if m["role"] == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertEqual(tool_msgs[0]["tool_call_id"], "c1")
        self.assertIn("76", tool_msgs[0]["content"])
        self.assertTrue(second_tools, "tools should be offered on every step")

    def test_multi_step_tool_chain(self):
        def call(name, wid):
            return llm.Turn(
                tool_calls=[
                    {"id": wid, "type": "function", "function": {"name": name, "arguments": "{}"}}
                ]
            )

        client = ScriptedClient(
            [call("torch", "a"), call("battery_status", "b"), llm.Turn(content="Torch on.")]
        )
        agent = Agent(cfg(), self.mem, client=client)
        self.assertEqual(agent.ask("torch on and check battery"), "Torch on.")
        self.assertEqual(agent.last_tools, ["torch", "battery_status"])

    def test_unknown_tool_is_reported_not_crashed(self):
        client = ScriptedClient(
            [
                llm.Turn(
                    tool_calls=[
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "launch_missiles", "arguments": "{}"},
                        }
                    ]
                ),
                llm.Turn(content="I have no such capability."),
            ]
        )
        agent = Agent(cfg(), self.mem, client=client)
        agent.ask("fire the missiles")
        tool_msg = [m for m in client.seen[1][0] if m["role"] == "tool"][0]
        self.assertIn("TOOL NOT FOUND", tool_msg["content"])

    def test_dangerous_tool_requires_confirmation(self):
        asked = []

        def confirm(name, args):
            asked.append(name)
            return False

        client = ScriptedClient(
            [
                llm.Turn(
                    tool_calls=[
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {
                                "name": "sms_send",
                                "arguments": json.dumps({"number": "+15550100", "text": "hi"}),
                            },
                        }
                    ]
                ),
                llm.Turn(content="Cancelled."),
            ]
        )
        agent = Agent(cfg(confirm_danger=True), self.mem, client=client, confirm=confirm)
        agent.ask("text pepper")
        self.assertEqual(asked, ["sms_send"])
        tool_msg = [m for m in client.seen[1][0] if m["role"] == "tool"][0]
        self.assertIn("CANCELLED", tool_msg["content"])

    def test_prompt_mode_json_protocol(self):
        c = cfg()
        c.provider.tool_mode = "prompt"
        client = ScriptedClient(
            [
                llm.Turn(content='{"tool": "battery_status", "args": {}}'),
                llm.Turn(content='{"final": "Battery at 76%, sir."}'),
            ]
        )
        agent = Agent(c, self.mem, client=client)
        answer = agent.ask("battery")
        self.assertIn("76", answer)
        self.assertIsNone(client.seen[0][1], "prompt mode must not send schemas")
        self.assertIn("TOOLS", client.seen[0][0][0]["content"])

    def test_fallback_when_server_rejects_tools(self):
        class PickyClient:
            def __init__(self):
                self.n = 0

            def chat(self, messages, tools=None):
                self.n += 1
                if tools:
                    raise llm.LLMError("HTTP 400: this model does not support tools")
                return llm.Turn(content='{"final": "Understood."}')

        agent = Agent(cfg(), self.mem, client=PickyClient())
        self.assertEqual(agent.ask("hello"), "Understood.")
        self.assertTrue(agent.prompt_mode)

    def test_history_is_kept_and_trimmed(self):
        client = ScriptedClient([llm.Turn(content="Aye.")] * 30)
        agent = Agent(cfg(), self.mem, client=client)
        for i in range(20):
            agent.ask(f"question {i}")
        self.assertLessEqual(len(self.mem.history), 60)
        self.assertEqual(self.mem.history[-1]["role"], "assistant")

    def test_memory_persists_across_reload(self):
        self.mem.remember("prefers tea over coffee", "preference")
        reloaded = Memory.load(self.mem.path)
        self.assertIn("tea", reloaded.recall("tea"))
        self.assertIn("Forgot", reloaded.forget("tea"))
        self.assertIn(reloaded.recall("tea"), ("Nothing matched", "No stored facts yet."))

    def test_budget_stops_runaway_loop(self):
        budget = Budget(max_calls=2)
        self.assertIsNone(budget.spend("tap", {"x": 1}))
        self.assertIsNone(budget.spend("tap", {"x": 2}))
        self.assertIn("STOP", budget.spend("tap", {"x": 3}))

    def test_identical_repeat_is_blocked(self):
        budget = Budget()
        budget.spend("tap", {"x": 1})
        budget.spend("tap", {"x": 1})
        self.assertIn("STOP", budget.spend("tap", {"x": 1}))

    def test_max_steps_summarises(self):
        c = cfg(max_steps=2)

        class Looper:
            def chat(self, messages, tools=None):
                if messages[-1]["role"] == "user" and "Stop using tools" in messages[-1]["content"]:
                    return llm.Turn(content="Partly done, sir.")
                return llm.Turn(
                    tool_calls=[
                        {"id": f"c{len(messages)}", "type": "function",
                         "function": {"name": "battery_status", "arguments": "{}"}}
                    ]
                )

        agent = Agent(c, self.mem, client=Looper())
        self.assertIn("Partly done", agent.ask("loop forever"))


if __name__ == "__main__":
    unittest.main()
