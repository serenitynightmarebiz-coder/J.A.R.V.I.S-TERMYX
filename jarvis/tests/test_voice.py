"""Voice layer tests: speech cleanup, TTS invocation, wake word, hands-free loop."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import shims  # noqa: E402
from jarvis.config import Config  # noqa: E402
from jarvis.mockdevice import calls, reset  # noqa: E402
from jarvis.voice import Voice, clean_for_speech  # noqa: E402

shims.MOCK = True


class TestSpeechCleanup(unittest.TestCase):
    def test_strips_markdown_and_urls(self):
        text = "**Done**, sir. See [the docs](https://x.dev/a) and `code`."
        out = clean_for_speech(text)
        self.assertNotIn("*", out)
        self.assertNotIn("http", out)
        self.assertNotIn("`", out)
        self.assertIn("Done", out)
        self.assertIn("the docs", out)

    def test_strips_code_blocks_and_emoji(self):
        out = clean_for_speech("```\nprint(1)\n```\nAll good 🔥👍")
        self.assertNotIn("print", out)
        self.assertNotIn("🔥", out)
        self.assertIn("All good", out)

    def test_empty_input(self):
        self.assertEqual(clean_for_speech(""), "")


class TestVoice(unittest.TestCase):
    def setUp(self):
        reset()
        self.cfg = Config()
        self.cfg.voice_output = True
        self.voice = Voice(self.cfg)

    def test_speak_calls_termux_tts(self):
        self.assertTrue(self.voice.speak("At once, sir."))
        cmds = [c[0] for c in calls()]
        self.assertTrue(any("termux-tts-speak" in c for c in cmds))

    def test_long_text_is_truncated_for_tts(self):
        self.voice.speak("word " * 400)
        spoken = [c for c in calls() if "termux-tts-speak" in c[0]][-1]
        self.assertLess(len(spoken[-1]), 760)

    def test_speak_disabled_returns_false(self):
        silent = Voice(Config())
        silent.enabled = False
        self.assertFalse(silent.speak("nothing"))

    def test_wake_word_extracts_request(self):
        heard = iter(["background chatter", "jarvis, what is my battery"])
        self.voice.listen = lambda *a, **k: next(heard)  # type: ignore[assignment]
        self.assertEqual(self.voice.wait_for_wake_word(), "what is my battery")

    def test_wake_word_without_request(self):
        self.voice.listen = lambda *a, **k: "hey jarvis"  # type: ignore[assignment]
        self.assertEqual(self.voice.wait_for_wake_word(), "")

    def _gate(self):
        from jarvis.gate import WakeGate

        return WakeGate("jarvis", True, 25)

    @staticmethod
    def _listener(*items):
        """Feeds utterances to converse(); a BaseException item is raised."""
        seq = iter(items)

        def listen(*_a, **_k):
            item = next(seq)
            if isinstance(item, BaseException):
                raise item
            return item

        return listen

    def test_converse_requires_wake_word(self):
        class FakeAgent:
            def __init__(self):
                self.asked = []

            def ask(self, text):
                self.asked.append(text)
                return "Battery at 76%, sir."

        agent = FakeAgent()
        self.voice.listen = self._listener("battery", "jarvis, battery", KeyboardInterrupt())
        spoken = []
        self.voice.speak = lambda text, blocking=True: spoken.append(text) or True  # type: ignore[assignment]
        self.voice.converse(agent, gate=self._gate())
        self.assertEqual(agent.asked, ["battery"], "unaddressed speech must be ignored")
        self.assertIn("76", spoken[0])

    def test_converse_obeys_sleep(self):
        class FakeAgent:
            def __init__(self):
                self.called = False

            def ask(self, text):
                self.called = True
                return "ok"

        agent = FakeAgent()
        self.voice.listen = self._listener("jarvis, go to sleep", KeyboardInterrupt())
        spoken = []
        self.voice.speak = lambda text, blocking=True: spoken.append(text) or True  # type: ignore[assignment]
        self.voice.converse(agent, gate=self._gate())
        self.assertFalse(agent.called)
        self.assertIn("Standing by", spoken[0])

    def test_converse_stops_cleanly(self):
        class FakeAgent:
            def ask(self, text):
                raise KeyboardInterrupt

        self.voice.listen = self._listener("jarvis, do something", KeyboardInterrupt())
        self.voice.speak = lambda *a, **k: True  # type: ignore[assignment]
        self.voice.converse(FakeAgent(), gate=self._gate())  # must not raise

    def test_converse_bare_wake_word_answers_yes(self):
        class FakeAgent:
            def ask(self, text):
                raise AssertionError("a bare wake word is not a task")

        self.voice.listen = self._listener("jarvis", KeyboardInterrupt())
        spoken = []
        self.voice.speak = lambda text, blocking=True: spoken.append(text) or True  # type: ignore[assignment]
        self.voice.converse(FakeAgent(), gate=self._gate())
        self.assertTrue(spoken and "Yes" in spoken[0])


if __name__ == "__main__":
    unittest.main()
