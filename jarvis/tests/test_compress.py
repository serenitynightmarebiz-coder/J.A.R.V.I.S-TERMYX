"""Tests for the optional Headroom compression layer."""

import sys
import unittest

from jarvis import compress


class FakeResult:
    def __init__(self, messages):
        self.messages = messages


class FakeHeadroom:
    @staticmethod
    def compress(messages, model=""):
        # a "compressor" that marks what it touched
        squeezed = [dict(m) for m in messages]
        for m in squeezed:
            if m.get("role") == "tool":
                m["content"] = "squeezed"
        return FakeResult(squeezed)


class BrokenHeadroom:
    @staticmethod
    def compress(messages, model=""):
        raise RuntimeError("boom")


class TestCompress(unittest.TestCase):
    def setUp(self):
        compress._state = None
        self._saved = {k: sys.modules.pop("headroom", None) for k in ["headroom"]}
        self.env = {}
        import os

        self._env = os.environ.pop("JARVIS_COMPRESS", None)

    def tearDown(self):
        compress._state = None
        sys.modules.pop("headroom", None)
        import os

        if self._env is not None:
            os.environ["JARVIS_COMPRESS"] = self._env

    def test_passthrough_without_headroom(self):
        msgs = [{"role": "tool", "content": "big output"}]
        self.assertEqual(compress.squeeze(msgs), msgs)

    def test_compresses_when_installed(self):
        sys.modules["headroom"] = FakeHeadroom
        msgs = [{"role": "tool", "content": "big output"}]
        out = compress.squeeze(msgs, model="gpt-4o")
        self.assertEqual(out[0]["content"], "squeezed")
        # the original list is untouched
        self.assertEqual(msgs[0]["content"], "big output")

    def test_falls_back_on_error(self):
        sys.modules["headroom"] = BrokenHeadroom
        msgs = [{"role": "tool", "content": "keep me"}]
        self.assertEqual(compress.squeeze(msgs), msgs)

    def test_env_kill_switch(self):
        sys.modules["headroom"] = FakeHeadroom
        import os

        os.environ["JARVIS_COMPRESS"] = "0"
        msgs = [{"role": "tool", "content": "raw"}]
        self.assertEqual(compress.squeeze(msgs), msgs)


if __name__ == "__main__":
    unittest.main()
