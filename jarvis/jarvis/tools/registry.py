"""Tool registry: every capability JARVIS has is a decorated function here."""

from __future__ import annotations

import inspect
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Tool:
    name: str
    description: str
    params: dict[str, dict]
    fn: Callable[..., str]
    danger: bool = False
    category: str = "misc"

    # ------------------------------------------------------------------ schema
    def schema(self) -> dict[str, Any]:
        props: dict[str, Any] = {}
        required: list[str] = []
        for pname, spec in self.params.items():
            spec = dict(spec)
            required_flag = spec.pop("required", False)
            props[pname] = spec
            if required_flag:
                required.append(pname)
        out: dict[str, Any] = {
            "name": self.name,
            "description": self.description[:1024],
            "parameters": {"type": "object", "properties": props, "required": required},
        }
        return out

    def call(self, **kwargs: Any) -> str:
        try:
            sig = inspect.signature(self.fn)
            allowed = {
                k: v for k, v in kwargs.items() if k in sig.parameters and k != "self"
            }
            for pname, spec in self.params.items():
                if spec.get("default") is not None and pname not in allowed:
                    allowed[pname] = spec["default"]
            result = self.fn(**allowed)
            if isinstance(result, (dict, list)):
                return json.dumps(result, ensure_ascii=False)
            return str(result)
        except TypeError as exc:
            return f"TOOL ARGUMENT ERROR ({self.name}): {exc}. Expected parameters: {list(self.params)}"
        except Exception as exc:  # noqa: BLE001 - tools must never crash the agent
            return f"TOOL FAILED ({self.name}): {type(exc).__name__}: {exc}"


REGISTRY: dict[str, Tool] = {}


def tool(
    name: str | None = None,
    description: str = "",
    params: dict[str, dict] | None = None,
    danger: bool = False,
    category: str = "misc",
):
    """Register a function as a JARVIS tool."""

    def deco(fn: Callable[..., str]) -> Callable[..., str]:
        tool_name = name or fn.__name__.lstrip("_")
        REGISTRY[tool_name] = Tool(
            name=tool_name,
            description=description or (fn.__doc__ or tool_name).strip(),
            params=params or {},
            fn=fn,
            danger=danger,
            category=category,
        )
        return fn

    return deco


def schemas(exclude: set[str] | None = None) -> list[dict[str, Any]]:
    exclude = exclude or set()
    return [t.schema() for name, t in sorted(REGISTRY.items()) if name not in exclude]


def by_category() -> dict[str, list[Tool]]:
    out: dict[str, list[Tool]] = {}
    for t in REGISTRY.values():
        out.setdefault(t.category, []).append(t)
    for items in out.values():
        items.sort(key=lambda t: t.name)
    return dict(sorted(out.items()))


class Budget:
    """Per-task guard rails so a confused model cannot loop forever."""

    def __init__(self, max_calls: int = 40, max_seconds: float = 240.0):
        self.max_calls = max_calls
        self.max_seconds = max_seconds
        self.calls = 0
        self.started = time.monotonic()
        self.seen: dict[str, int] = {}

    def spend(self, name: str, args: dict) -> str | None:
        self.calls += 1
        if self.calls > self.max_calls:
            return f"STOP: tool budget of {self.max_calls} calls exhausted. Report what you have."
        if time.monotonic() - self.started > self.max_seconds:
            return "STOP: task took too long. Report what you have."
        fingerprint = name + json.dumps(args, sort_keys=True, default=str)
        self.seen[fingerprint] = self.seen.get(fingerprint, 0) + 1
        if self.seen[fingerprint] >= 3:
            return (
                f"STOP: you called {name} with identical arguments 3 times. "
                "It will not succeed. Try a different approach or report the failure."
            )
        return None
