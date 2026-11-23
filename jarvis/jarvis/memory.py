"""Persistent memory: durable facts about the user, plus rolling conversation."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import MEMORY_PATH


@dataclass
class Memory:
    path: Path = MEMORY_PATH
    facts: list[dict[str, Any]] = field(default_factory=list)
    history: list[dict[str, str]] = field(default_factory=list)
    prefs: dict[str, Any] = field(default_factory=dict)
    max_facts: int = 200

    # ------------------------------------------------------------------ io
    @classmethod
    def load(cls, path: Path = MEMORY_PATH) -> "Memory":
        mem = cls(path=path)
        if path.exists():
            try:
                raw = json.loads(path.read_text())
                mem.facts = raw.get("facts", [])
                mem.history = raw.get("history", [])
                mem.prefs = raw.get("prefs", {})
            except (json.JSONDecodeError, OSError):
                pass
        return mem

    def save(self) -> bool:
        """Persist; a read-only or missing filesystem must never kill the agent loop."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"facts": self.facts[-self.max_facts :], "history": self.history[-60:], "prefs": self.prefs}
            self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
            return True
        except OSError:
            return False

    # --------------------------------------------------------------- facts
    def remember(self, fact: str, tag: str = "general") -> str:
        fact = fact.strip()
        if not fact:
            return "Nothing to remember."
        for existing in self.facts:
            if existing["text"].lower() == fact.lower():
                existing["ts"] = int(time.time())
                self.save()
                return f"Already known: {fact}"
        self.facts.append({"text": fact[:500], "tag": tag, "ts": int(time.time())})
        self.facts = self.facts[-self.max_facts :]
        self.save()
        return f"Remembered: {fact}"

    def forget(self, query: str) -> str:
        q = query.lower().strip()
        before = len(self.facts)
        self.facts = [f for f in self.facts if q not in f["text"].lower()]
        self.save()
        removed = before - len(self.facts)
        return f"Forgot {removed} item(s) matching '{query}'." if removed else f"Nothing matched '{query}'."

    def recall(self, query: str = "", limit: int = 12) -> str:
        if not self.facts:
            return "No stored facts yet."
        if not query.strip():
            rows = [f["text"] for f in self.facts[-limit:]]
            return "Known facts:\n- " + "\n- ".join(rows)
        terms = [t for t in re.split(r"\W+", query.lower()) if len(t) > 2]
        scored = []
        for fact in self.facts:
            text = fact["text"].lower()
            score = sum(1 for t in terms if t in text)
            if score:
                scored.append((score, fact["text"]))
        scored.sort(reverse=True)
        if not scored:
            return f"Nothing stored about '{query}'."
        return "Related:\n- " + "\n- ".join(text for _, text in scored[:limit])

    def context_block(self, query: str = "") -> str:
        """Compact memory text injected into the system prompt."""
        if not self.facts and not self.prefs:
            return ""
        lines = [f"- {f['text']}" for f in self.facts[-25:]]
        if self.prefs:
            lines.append(f"- preferences: {json.dumps(self.prefs, ensure_ascii=False)}")
        head = "What you already know about the user"
        if query:
            relevant = self.recall(query, limit=6)
            if not relevant.startswith(("No ", "Nothing")):
                return f"{head}:\n" + "\n".join(lines) + f"\nMost relevant now:\n{relevant}"
        return f"{head}:\n" + "\n".join(lines)

    # ------------------------------------------------------------- history
    def add_turn(self, role: str, content: str) -> None:
        if not content:
            return
        self.history.append({"role": role, "content": content})

    def trimmed_history(self, turns: int = 12) -> list[dict[str, str]]:
        limit = max(2, turns * 2)
        return [m for m in self.history[-limit:] if m.get("content")]

    def clear_history(self) -> None:
        self.history = []
        self.save()
