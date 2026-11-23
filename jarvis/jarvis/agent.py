"""The agent loop: think → call tools → think again → answer.

Two tool protocols are supported:
  * native  — the model's own function-calling (OpenAI, Anthropic, Gemini, ...)
  * prompt  — a JSON protocol for local models that cannot do function calling

`tool_mode: auto` tries native, and drops to prompt mode if the server rejects
tool schemas (a common Ollama/llama.cpp situation with older models).
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

from . import animations
from . import llm as llm_mod
from . import shims, ui
from .config import Config
from .memory import Memory
from .tools import Budget, REGISTRY, schemas

PROMPT_PROTOCOL = """
TOOLS
You can act on the phone by replying with ONE JSON object and nothing else.

  To use a tool:   {"tool": "<name>", "args": {"<param>": <value>}}
  To answer:       {"final": "<your answer>"}

Rules:
- One tool call per reply. After each call you will see "RESULT: ...".
- Only these tools exist (name — description — params):
__TOOLS__
- Never invent tool names or parameters. If no tool fits, reply with {"final": ...}.
- Keep "final" short. Do not describe the JSON, just use it.
"""


def _summarise_args(args: dict) -> str:
    bits = []
    for k, v in list(args.items())[:3]:
        text = str(v)
        bits.append(f"{k}={text[:28]}" + ("…" if len(text) > 28 else ""))
    return ", ".join(bits)


def _extract_json(text: str) -> dict | None:
    """Pull the first balanced JSON object out of a model reply."""
    for start, ch in enumerate(text):
        if ch != "{":
            continue
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    return None


class _CompressingClient:
    """Route every request through Headroom when it is installed (no-op otherwise)."""

    def __init__(self, inner, model: str = ""):
        self._inner = inner
        self._model = model

    def chat(self, messages: list[dict], tools: list[dict] | None = None):
        from . import compress

        return self._inner.chat(compress.squeeze(messages, self._model), tools)


class Agent:
    def __init__(
        self,
        cfg: Config,
        memory: Memory,
        speaker: Callable[[str], None] | None = None,
        confirm: Callable[[str, dict], bool] | None = None,
        client=None,
    ):
        self.cfg = cfg
        self.memory = memory
        self.speaker = speaker
        self.confirm = confirm or (lambda name, args: True)
        self.client = _CompressingClient(
            client or llm_mod.build(cfg.provider), cfg.provider.model if cfg.compress_context else ""
        )
        self.prompt_mode = cfg.provider.tool_mode == "prompt"
        self.native_broken = False
        self.last_tools: list[str] = []
        self._tools_context = shims.android_info()

    # ------------------------------------------------------------ prompt bits
    def system_prompt(self, user_text: str) -> str:
        parts = [self.cfg.system_prompt()]
        now = time.strftime("%A %d %B %Y, %H:%M")
        ctx = [f"Local time: {now}"]
        if self._tools_context.get("model"):
            ctx.append(f"Device: {self._tools_context['model']} (Android {self._tools_context.get('android', '?')})")
        parts.append("Context: " + "; ".join(ctx) + ".")
        mem = self.memory.context_block(user_text)
        if mem:
            parts.append(mem)
        if self.prompt_mode:
            catalog = "\n".join(
                f"  {t.name} — {t.description.splitlines()[0][:110]} — "
                f"{', '.join(t.params) or 'no params'}"
                for t in sorted(REGISTRY.values(), key=lambda t: t.name)
            )
            parts.append(PROMPT_PROTOCOL.replace("__TOOLS__", catalog))
        return "\n\n".join(parts)

    # --------------------------------------------------------------- one turn
    def ask(self, user_text: str, on_token: Callable[[str], None] | None = None) -> str:
        budget = Budget()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt(user_text)}
        ]
        for turn in self.memory.trimmed_history(self.cfg.history_turns):
            messages.append(turn)
        messages.append({"role": "user", "content": user_text})

        transcript: list[str] = []
        self.last_tools = []

        for step in range(self.cfg.max_steps):
            tools = None if (self.prompt_mode or self.native_broken) else schemas()
            try:
                anim = animations.Thinking(
                    "thinking" if step == 0 else "working",
                    force=None if getattr(self.cfg, "animations", True) else False,
                )
                with anim:
                    turn = self.client.chat(messages, tools)
            except llm_mod.LLMError as exc:
                # Some OpenAI-compatible servers reject the `tools` field.
                if tools and self._is_tool_rejection(str(exc)):
                    ui.warn("model rejected function schemas — switching to JSON tool mode")
                    self.prompt_mode = True
                    messages[0] = {"role": "system", "content": self.system_prompt(user_text)}
                    continue
                return self._error(str(exc))

            if turn.tool_calls and not self.prompt_mode:
                messages.append(self._assistant_tool_message(turn))
                for call in turn.tool_calls:
                    name = call["function"]["name"]
                    args = self._parse_args(call["function"]["arguments"])
                    output = self._execute(name, args, budget)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "name": name,
                            "content": output[:6000],
                        }
                    )
                    transcript.append(f"{name} -> {output[:200]}")
                continue

            # prompt-mode JSON protocol
            if self.prompt_mode:
                payload = _extract_json(turn.content or "")
                if payload and payload.get("tool"):
                    name = str(payload["tool"])
                    args = payload.get("args") or {}
                    output = self._execute(name, dict(args), budget)
                    messages.append({"role": "assistant", "content": turn.content})
                    messages.append({"role": "user", "content": f"RESULT: {output[:4000]}"})
                    transcript.append(f"{name} -> {output[:200]}")
                    continue
                final = (
                    payload.get("final")
                    if payload and "final" in payload
                    else (turn.content or "").strip()
                )
                return self._finish(user_text, final, on_token)

            final = (turn.content or "").strip()
            if not final:
                if step == 0:
                    return self._error("the model returned an empty reply.")
                return self._finish(user_text, "Done.", on_token)
            return self._finish(user_text, final, on_token)

        # Ran out of steps: summarise rather than hang.
        tail = self._summarise(messages, transcript)
        return self._finish(user_text, tail, on_token)

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _is_tool_rejection(message: str) -> bool:
        low = message.lower()
        return any(
            phrase in low
            for phrase in (
                "does not support tools",
                "tools is not supported",
                "tool_choice",
                "unknown field `tools`",
                "unsupported parameter: tools",
                "function calling is not supported",
            )
        )

    @staticmethod
    def _parse_args(raw: str) -> dict:
        if isinstance(raw, dict):
            return raw
        try:
            parsed = json.loads(raw or "{}")
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _assistant_tool_message(turn) -> dict:
        return {"role": "assistant", "content": turn.content or "", "tool_calls": turn.tool_calls}

    def _execute(self, name: str, args: dict, budget: Budget) -> str:
        self.last_tools.append(name)
        tool = REGISTRY.get(name)
        if tool is None:
            close = [n for n in REGISTRY if name.lower() in n.lower() or n.lower() in name.lower()][:3]
            hint = f" Did you mean: {', '.join(close)}?" if close else ""
            return f"TOOL NOT FOUND: {name}.{hint} Available: {', '.join(sorted(REGISTRY))}"

        gate = budget.spend(name, args)
        if gate:
            return gate

        preview = _summarise_args(args)
        if tool.danger and self.cfg.confirm_danger and name not in self.cfg.auto_approve:
            ui.tool_line(name, preview)
            if not self.confirm(name, args):
                return f"CANCELLED by user: {name} was not executed. Ask what they would prefer."
        else:
            ui.tool_line(name, preview)

        started = time.monotonic()
        result = tool.call(**args)
        elapsed = time.monotonic() - started
        if elapsed > 2 and not ui.ENABLED:
            ui.info(f"{name} took {elapsed:.1f}s")
        return result

    def _finish(self, user_text: str, text: str, on_token: Callable[[str], None] | None) -> str:
        text = (text or "").strip()
        self.memory.add_turn("user", user_text)
        self.memory.add_turn("assistant", text)
        self.memory.save()
        if on_token:
            on_token(text)
        if self.speaker and text:
            try:
                self.speaker(text)
            except Exception:  # noqa: BLE001 - speech must never break a turn
                pass
        return text

    def _error(self, message: str) -> str:
        return f"I could not complete that: {message}"

    def _summarise(self, messages: list[dict], transcript: list[str]) -> str:
        if not transcript:
            return "I ran out of steps without completing that, sir."
        try:
            messages.append(
                {
                    "role": "user",
                    "content": "Stop using tools. In one or two sentences, tell the user what you "
                    "achieved and what is still outstanding.",
                }
            )
            turn = self.client.chat(messages, None)
            if turn.content.strip():
                return turn.content.strip()
        except llm_mod.LLMError:
            pass
        return "I got partway through: " + "; ".join(transcript[-3:])
