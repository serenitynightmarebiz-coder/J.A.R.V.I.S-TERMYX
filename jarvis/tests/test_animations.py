"""Animations: frames, silence when inappropriate, and clean shutdown."""

from __future__ import annotations

import io
import sys
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import animations  # noqa: E402


class TestFrames(unittest.TestCase):
    def test_waveform_shape(self):
        frame = animations.waveform_frame(3, width=30)
        self.assertEqual(len(frame), 30)
        self.assertTrue(set(frame).issubset(set(animations.BARS)))

    def test_waveform_moves(self):
        self.assertNotEqual(
            animations.waveform_frame(0, width=24),
            animations.waveform_frame(7, width=24),
        )

    def test_disabled_outside_a_terminal(self):
        self.assertFalse(animations.enabled(force=False))


class TestSilence(unittest.TestCase):
    def test_everything_is_silent_when_forced_off(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            animations.wake_flash(force=False)
            animations.boot_sequence(force=False)
            with animations.Listening(force=False):
                pass
            with animations.Thinking(force=False):
                pass
        self.assertEqual(buffer.getvalue(), "")


class TestRendering(unittest.TestCase):
    def test_listening_renders_and_stops(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            with animations.Listening("listening", force=True) as anim:
                time.sleep(0.35)
            thread = anim._thread
        output = buffer.getvalue()
        self.assertIn("listening", output)
        self.assertTrue(any(ch in output for ch in animations.BARS))
        self.assertFalse(thread.is_alive(), "animation thread must be joined")

    def test_thinking_shows_elapsed_time(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            with animations.Thinking("thinking", force=True):
                time.sleep(0.3)
        output = buffer.getvalue()
        self.assertIn("thinking", output)
        self.assertIn("s", output)

    def test_thinking_label_progresses(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            with animations.Thinking("working", force=True):
                time.sleep(0.5)
        self.assertIn("working.", buffer.getvalue())

    def test_boot_sequence_prints_every_stage(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            animations.boot_sequence(force=True, delay=0.001)
        output = buffer.getvalue()
        for line in animations.BOOT_LINES:
            self.assertIn(line, output)
        self.assertEqual(output.count("ok"), len(animations.BOOT_LINES))

    def test_wake_flash(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            animations.wake_flash("jarvis", force=True)
        self.assertIn("JARVIS", buffer.getvalue())

    def test_animation_clears_its_line(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            with animations.Listening(force=True):
                time.sleep(0.15)
        self.assertIn("\r", buffer.getvalue(), "should overwrite its own line")

    def test_haptics_never_crash_off_device(self):
        from jarvis import shims

        shims.MOCK = True
        with redirect_stdout(io.StringIO()):
            with animations.Listening(force=False, haptics=True):
                pass


if __name__ == "__main__":
    unittest.main()
