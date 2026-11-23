"""Optional context compression via Headroom (pip install headroom-ai).

JARVIS stays zero-dependency: when headroom is not installed this module is a
pass-through. When it is installed, long tool outputs (screen dumps, app lists,
notification JSON) are compressed before they reach the model — same answers,
fewer input tokens. Compression runs locally; nothing is sent anywhere.
"""

from __future__ import annotations

import os

_state: bool | None = None


def available() -> bool:
    global _state
    if _state is None:
        try:
            import headroom  # noqa: F401

            _state = True
        except Exception:
            _state = False
    return _state


def squeeze(messages: list[dict], model: str = "") -> list[dict]:
    """Compress the message history when headroom is installed. Never raises."""
    if os.environ.get("JARVIS_COMPRESS") == "0" or not available():
        return messages
    try:
        from headroom import compress

        kwargs: dict = {"messages": messages}
        if model:
            kwargs["model"] = model
        result = compress(**kwargs)
        return getattr(result, "messages", None) or messages
    except Exception:
        return messages
