"""End-to-end CLI behaviour: wake gating, tool listing, self-test, status."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "jarvis.py", *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**__import__("os").environ, "JARVIS_MOCK": "1", "NO_COLOR": "1"},
    )


class TestCLI(unittest.TestCase):
    def test_unaddressed_one_shot_is_refused(self):
        proc = run("open spotify", "--mock", "--no-voice")
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertIn("jarvis", proc.stdout.lower())

    def test_no_wake_flag_bypasses_gate(self):
        proc = run("open spotify", "--mock", "--no-voice", "--no-wake", "--provider", "custom",
                   "--base-url", "http://127.0.0.1:9/v1", "--api-key", "x")
        # The gate lets it through, so it fails at the model instead (exit 2, not 3).
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)

    def test_tools_are_listed(self):
        proc = run("--tools")
        self.assertEqual(proc.returncode, 0)
        for name in ("launch_app", "self_edit", "screen_dump", "sms_send", "gate" if False else "torch"):
            self.assertIn(name, proc.stdout)

    def test_selftest_passes(self):
        proc = run("--selftest", timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("all checks passed", proc.stdout)

    def test_status_without_daemon(self):
        proc = run("--status")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("stopped", proc.stdout)

    def test_reload_keeps_every_tool_registered(self):
        proc = subprocess.run(
            [sys.executable, "jarvis.py", "--mock", "--no-voice"],
            cwd=str(ROOT),
            input="/reload\n/quit\n",
            capture_output=True,
            text=True,
            timeout=120,
            env={**__import__("os").environ, "JARVIS_MOCK": "1", "NO_COLOR": "1"},
        )
        out = proc.stdout + proc.stderr
        self.assertIn("reloaded", out)
        self.assertNotIn("0 tools", out)
        self.assertIn("83 tools", out)

    def test_help_lists_wake_commands(self):
        proc = run("--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--voice", proc.stdout)
        self.assertIn("--start", proc.stdout)
        self.assertIn("--no-wake", proc.stdout)


if __name__ == "__main__":
    unittest.main()
