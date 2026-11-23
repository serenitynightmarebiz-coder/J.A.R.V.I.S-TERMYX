"""Self-modification: guard rails first, capability second."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import shims  # noqa: E402
from jarvis.config import Config  # noqa: E402
from jarvis.tools import selfmod  # noqa: E402

shims.MOCK = True
ROOT = Path(shims.__file__).resolve().parent.parent


class TestSelfModification(unittest.TestCase):
    def setUp(self):
        self.cfg = Config()
        self.cfg.allow_self_modify = True
        selfmod.bind(self.cfg)
        self.target = ROOT / "tests" / "_scratch_selfmod.py"
        self.addCleanup(lambda: self.target.unlink(missing_ok=True))

    # -------------------------------------------------------------- reading
    def test_inspect_and_read_own_code(self):
        listing = selfmod.self_inspect("jarvis")
        self.assertIn("agent.py", listing)
        self.assertIn("tools registered", listing)
        body = selfmod.self_read("jarvis/gate.py", start=1, lines=5)
        self.assertIn("Wake gating", body)

    def test_capabilities_summary(self):
        summary = selfmod.self_capabilities()
        self.assertIn("jarvis", summary)
        self.assertIn("tools", summary)

    # -------------------------------------------------------------- writing
    def test_write_then_edit_roundtrip(self):
        written = selfmod.self_write("tests/_scratch_selfmod.py", "VALUE = 1\n")
        self.assertIn("Wrote", written)
        self.assertIn("VALUE = 1", self.target.read_text())

        edited = selfmod.self_edit("tests/_scratch_selfmod.py", "VALUE = 1", "VALUE = 42")
        self.assertIn("Edited", edited)
        self.assertIn("VALUE = 42", self.target.read_text())

    def test_edit_requires_exact_unique_snippet(self):
        selfmod.self_write("tests/_scratch_selfmod.py", "a = 1\nb = 1\n")
        missing = selfmod.self_edit("tests/_scratch_selfmod.py", "c = 9", "c = 10")
        self.assertIn("not found", missing)
        ambiguous = selfmod.self_edit("tests/_scratch_selfmod.py", "= 1", "= 2")
        self.assertIn("appears 2 times", ambiguous)

    def test_broken_python_is_rejected_and_rolled_back(self):
        selfmod.self_write("tests/_scratch_selfmod.py", "VALUE = 1\n")
        result = selfmod.self_edit(
            "tests/_scratch_selfmod.py", "VALUE = 1", "def broken(:\n    pass"
        )
        self.assertIn("REJECTED", result)
        self.assertIn("VALUE = 1", self.target.read_text(), "original must be restored")

    def test_broken_json_is_rejected(self):
        result = selfmod.self_write("tests/_scratch.json", "{not json}")
        self.assertIn("REJECTED", result)
        (ROOT / "tests" / "_scratch.json").unlink(missing_ok=True)

    def test_backup_is_created(self):
        selfmod.self_write("tests/_scratch_selfmod.py", "VALUE = 1\n")
        selfmod.self_edit("tests/_scratch_selfmod.py", "VALUE = 1", "VALUE = 2")
        backups = list((ROOT / "data" / "backups").glob("_scratch_selfmod.py.*.bak"))
        self.assertTrue(backups)

    def test_rollback_restores_previous_version(self):
        selfmod.self_write("tests/_scratch_selfmod.py", "VALUE = 1\n")
        selfmod.self_edit("tests/_scratch_selfmod.py", "VALUE = 1", "VALUE = 2")
        restored = selfmod.self_rollback("tests/_scratch_selfmod.py")
        self.assertIn("Restored", restored)
        self.assertIn("VALUE = 1", self.target.read_text())

    # ------------------------------------------------------------- protection
    def test_cannot_escape_project(self):
        for path in ("/etc/passwd", "../../etc/passwd", "~/secret.txt"):
            result = selfmod.self_write(path, "x")
            self.assertIn("outside the project", result, path)

    def test_protected_files_and_dirs(self):
        self.assertIn("protected", selfmod.self_write("config.json", "{}"))
        self.assertIn("off limits", selfmod.self_write("data/memory.json", "{}"))
        self.assertIn("off limits", selfmod.self_write("logs/daemon.log", "x"))

    def test_disallowed_extension(self):
        result = selfmod.self_write("tests/evil.shx", "#!/bin/sh\n")
        self.assertIn("not editable", result)

    def test_disabled_by_config(self):
        self.cfg.allow_self_modify = False
        self.assertIn("disabled", selfmod.self_write("tests/_scratch_selfmod.py", "x = 1\n"))
        self.cfg.allow_self_modify = True

    def test_restart_flag_is_written(self):
        flag = ROOT / "data" / "restart.flag"
        flag.unlink(missing_ok=True)
        msg = selfmod.self_restart()
        self.assertIn("/reload", msg)
        self.assertTrue(flag.exists())
        flag.unlink(missing_ok=True)

    def test_tests_can_run_themselves(self):
        out = selfmod.self_test("tests.test_gate", timeout=120)
        self.assertIn("PASSED", out, out[:300])


if __name__ == "__main__":
    unittest.main()
