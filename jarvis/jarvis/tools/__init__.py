"""Tool package. Importing this registers every capability with the registry."""

from __future__ import annotations

from . import android, memory_tools, selfmod, system, termux_api  # noqa: F401
from .registry import REGISTRY, Budget, by_category, schemas, tool

__all__ = [
    "REGISTRY",
    "Budget",
    "schemas",
    "by_category",
    "tool",
    "memory_tools",
    "selfmod",
]
