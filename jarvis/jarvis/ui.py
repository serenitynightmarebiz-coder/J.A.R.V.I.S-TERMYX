"""Terminal presentation. Light ANSI, no dependencies, degrades when piped."""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time

ENABLED = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if ENABLED else text


def cyan(t: str) -> str:
    return _c("36", t)


def gold(t: str) -> str:
    return _c("33", t)


def dim(t: str) -> str:
    return _c("2", t)


def red(t: str) -> str:
    return _c("31", t)


def green(t: str) -> str:
    return _c("32", t)


def bold(t: str) -> str:
    return _c("1", t)


def width() -> int:
    return shutil.get_terminal_size((80, 24)).columns


RULE = "─"


def banner(model: str, provider: str, voice: bool) -> None:
    inner = max(40, min(width(), 64) - 4)

    def row(text: str = "") -> None:
        print(gold("  \u2551") + text.ljust(inner) + gold("\u2551"))

    print()
    print(gold("  \u2554" + "\u2550" * inner + "\u2557"))
    row("  " + bold("J.A.R.V.I.S."))
    row("  " + dim(f"{provider} \u00b7 {model}"[: inner - 4]))
    row("  " + dim("Just A Rather Very Intelligent System"))
    if voice:
        row("  " + dim("voice: on"))
    print(gold("  \u255a" + "\u2550" * inner + "\u255d"))
    print(dim("  /help for commands \u00b7 Ctrl-C to interrupt \u00b7 /quit to exit"))
    print()


def jarvis(text: str) -> None:
    print(f"{gold('JARVIS')} {dim('›')} {text}")


def user_prompt() -> str:
    return f"\n{cyan('you')} {dim('›')} "


def tool_line(name: str, preview: str = "") -> None:
    tail = f" {dim(preview)}" if preview else ""
    print(dim(f"  ⚙ {name}") + tail)


def warn(text: str) -> None:
    print(red(f"  ! {text}"))


def info(text: str) -> None:
    print(dim(f"  · {text}"))


def ok(text: str) -> None:
    print(green(f"  ✓ {text}"))


class Spinner:
    """Backwards-compatible name for the HUD 'thinking' animation."""

    def __init__(self, label: str = "thinking", force: bool | None = None):
        self.label = label
        self.force = force

    def __enter__(self):
        from .animations import Thinking

        self._ctx = Thinking(self.label, force=self.force)
        self._ctx.__enter__()
        return self

    def __exit__(self, *exc):
        return self._ctx.__exit__(*exc)


def wrap(text: str, indent: int = 9) -> str:
    """Soft-wrap long replies for narrow phone terminals."""
    import textwrap

    w = max(40, width() - indent)
    out = []
    for para in text.split("\n"):
        out.extend(textwrap.wrap(para, w) or [""])
    return ("\n" + " " * indent).join(out)
