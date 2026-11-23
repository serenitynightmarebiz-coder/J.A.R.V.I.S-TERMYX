"""Wake gating: the rule that JARVIS only acts when addressed by name."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.gate import WakeGate  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class TestWakeGate(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.gate = WakeGate("jarvis", enabled=True, window=25, clock=self.clock)

    def test_ignores_unaddressed(self):
        decision = self.gate.check("open spotify")
        self.assertFalse(decision.addressed)
        self.assertFalse(self.gate.awake)

    def test_obeys_prefixed_command(self):
        decision = self.gate.check("jarvis, open spotify")
        self.assertTrue(decision.addressed)
        self.assertEqual(decision.request, "open spotify")

    def test_obeys_command_mid_sentence(self):
        decision = self.gate.check("I say jarvis please turn the torch on")
        self.assertTrue(decision.addressed)
        self.assertEqual(decision.request, "turn the torch on")

    def test_case_and_punctuation_insensitive(self):
        decision = self.gate.check("JARVIS: what's my battery?")
        self.assertTrue(decision.addressed)
        self.assertEqual(decision.request, "what's my battery?")

    def test_bare_wake_word(self):
        decision = self.gate.check("jarvis")
        self.assertTrue(decision.addressed)
        self.assertEqual(decision.request, "")
        self.assertEqual(decision.reason, "woken")

    def test_follow_up_window(self):
        self.gate.check("jarvis, what is my battery")
        self.clock.advance(5)
        follow = self.gate.check("and the weather")
        self.assertTrue(follow.addressed)
        self.assertEqual(follow.reason, "follow-up")

    def test_window_expires(self):
        self.gate.check("jarvis, what is my battery")
        self.clock.advance(26)
        self.assertFalse(self.gate.check("and the weather").addressed)

    def test_sleep_command_closes_window(self):
        self.gate.check("jarvis, what is my battery")
        sleep = self.gate.check("jarvis, go to sleep")
        self.assertTrue(sleep.addressed)
        self.assertEqual(sleep.reason, "sleep")
        self.assertFalse(self.gate.awake)
        self.assertFalse(self.gate.check("open spotify").addressed)

    def test_random_sleep_phrase_is_not_obeyed(self):
        # Without the wake word, "good night" must not unlock anything.
        decision = self.gate.check("good night everyone")
        self.assertFalse(decision.addressed)

    def test_disabled_gate_passes_everything(self):
        gate = WakeGate("jarvis", enabled=False, clock=self.clock)
        decision = gate.check("open spotify")
        self.assertTrue(decision.addressed)
        self.assertEqual(decision.request, "open spotify")

    def test_other_wake_words(self):
        gate = WakeGate("computer", clock=self.clock)
        self.assertFalse(gate.check("jarvis, hello").addressed)
        self.assertTrue(gate.check("computer, hello").addressed)

    def test_empty_input(self):
        self.assertFalse(self.gate.check("   ").addressed)

    def test_status_strings(self):
        self.assertIn("armed", self.gate.status)
        self.gate.check("jarvis, hello")
        self.assertIn("awake", self.gate.status)
        self.gate.reset(enabled=False)
        self.assertEqual(self.gate.status, "gate off")


if __name__ == "__main__":
    unittest.main()
