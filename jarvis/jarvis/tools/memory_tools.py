"""Memory tools — bound to the live Memory instance by the agent at startup."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .registry import tool

if TYPE_CHECKING:  # pragma: no cover
    from ..memory import Memory

_MEMORY = None


def bind(memory) -> None:
    global _MEMORY
    _MEMORY = memory


def _mem():
    return _MEMORY


@tool(
    "remember",
    "Store a durable fact about the user (preferences, names, routines, birthdays, "
    "favourite things). Use this whenever the user tells you something worth keeping.",
    {"fact": {"type": "string", "required": True},
     "tag": {"type": "string", "default": "general"}},
    category="memory",
)
def remember(fact: str, tag: str = "general") -> str:
    mem = _mem()
    if mem is None:
        return "Memory unavailable."
    return mem.remember(fact, tag)


@tool("recall", "Search stored facts about the user. Empty query lists recent facts.",
      {"query": {"type": "string", "default": ""}}, category="memory")
def recall(query: str = "") -> str:
    mem = _mem()
    if mem is None:
        return "Memory unavailable."
    return mem.recall(query)


@tool("forget", "Delete stored facts matching a phrase.",
      {"query": {"type": "string", "required": True}}, danger=True, category="memory")
def forget(query: str) -> str:
    mem = _mem()
    if mem is None:
        return "Memory unavailable."
    return mem.forget(query)
