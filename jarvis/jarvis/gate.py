"""Wake gating: JARVIS does nothing unless it is addressed by name.

Rules, deliberately strict:

  * "jarvis, <request>"          → does it
  * "<anything> jarvis <request>" → does it (wake word stripped)
  * follow-up inside the window  → does it, no name needed
  * anything else                → ignored, with a quiet hint
  * "jarvis, go to sleep"        → closes the window immediately

Gating is on by default. `/wake off` (typed at the terminal) disables it for the
session; hands-free voice mode always keeps it on.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

SLEEP_PHRASES = (
    "go to sleep",
    "stand down",
    "power down",
    "that's all",
    "thats all",
    "goodbye",
    "good night",
    "goodnight",
    "dismissed",
)

OPEN_PHRASES = ("wake up", "are you there", "you there", "you awake")


@dataclass
class Decision:
    addressed: bool
    request: str = ""
    reason: str = ""

    def __bool__(self) -> bool:  # `if gate.check(text):`
        return self.addressed


class WakeGate:
    def __init__(
        self,
        word: str = "jarvis",
        enabled: bool = True,
        window: float = 25.0,
        clock=time.monotonic,
    ):
        self.word = word.strip().lower() or "jarvis"
        self.enabled = enabled
        self.window = max(0.0, float(window))
        self._clock = clock
        self._until = 0.0
        self._pattern = re.compile(rf"\b{re.escape(self.word)}\b", re.IGNORECASE)

    # ------------------------------------------------------------------ state
    @property
    def awake(self) -> bool:
        return self.enabled and self.window > 0 and self._clock() < self._until

    def open(self, seconds: float | None = None) -> None:
        """Extend the follow-up window (after answering, or on an explicit wake)."""
        span = self.window if seconds is None else max(0.0, float(seconds))
        self._until = self._clock() + span

    def close(self) -> None:
        self._until = 0.0

    def reset(self, enabled: bool | None = None) -> None:
        self.close()
        if enabled is not None:
            self.enabled = enabled

    @property
    def status(self) -> str:
        if not self.enabled:
            return "gate off"
        if self.awake:
            return f"awake ({self._until - self._clock():.0f}s left)"
        return f'armed — say "{self.word}"'

    # ------------------------------------------------------------------- check
    def check(self, text: str) -> Decision:
        raw = (text or "").strip()
        if not raw:
            return Decision(False, "", "empty")

        lowered = raw.lower()

        if not self.enabled:
            return Decision(True, raw, "gate off")

        # Sleep command — only honoured if it names JARVIS or we are already awake.
        if any(phrase in lowered for phrase in SLEEP_PHRASES):
            if self._pattern.search(raw) or self.awake:
                self.close()
                return Decision(True, raw, "sleep")

        match = self._pattern.search(raw)
        if match:
            request = raw[match.end() :]
            request = request.lstrip(" ,.:;!?—-")
            request = re.sub(r"^(please|can you|could you|would you)\s+", "", request, flags=re.I)
            self.open()
            if not request:
                return Decision(True, "", "woken")
            return Decision(True, request, "named")

        if self.awake:
            return Decision(True, raw, "follow-up")

        return Decision(False, "", "not addressed")

    def explain(self) -> str:
        if not self.enabled:
            return "Wake gating is off — every instruction is obeyed."
        return (
            f'Address me as "{self.word}" — e.g. "{self.word}, open spotify". '
            f"I stay awake for {int(self.window)}s after each reply, then need the name again."
        )
