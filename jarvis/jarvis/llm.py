"""LLM backends. Stdlib only — no pip installs required.

Internal message format is OpenAI's, because it is the lingua franca:

    {"role": "system"|"user"|"assistant"|"tool", "content": str, ...}
    assistant + tools: {"tool_calls": [{"id", "type": "function",
                                        "function": {"name", "arguments"}}]}
    tool result:       {"role": "tool", "tool_call_id": ..., "name": ..., "content": ...}

Anthropic and Gemini are translated on the way in and out.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .config import PRESETS, ProviderConfig


class LLMError(RuntimeError):
    """Raised with a human-readable, actionable message."""


@dataclass
class Turn:
    """One assistant reply: prose and/or tool calls."""

    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def has_tools(self) -> bool:
        return bool(self.tool_calls)


def _origin(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _post(url: str, payload: dict, headers: dict, timeout: int) -> dict:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:600]
        try:
            parsed = json.loads(detail)
            detail = (
                parsed.get("error", {}).get("message")
                if isinstance(parsed.get("error"), dict)
                else parsed.get("error") or parsed.get("message") or detail
            )
        except Exception:
            pass
        hint = {
            401: "API key rejected. Check provider.api_key / the env var.",
            403: "Key accepted but the request is forbidden (region, model access, or billing).",
            404: "Endpoint or model not found. Check provider.model and provider.base_url.",
            429: "Rate limit or out of quota. Wait, or switch provider.",
        }.get(exc.code, "")
        raise LLMError(f"HTTP {exc.code} from {_origin(url)}: {detail} {hint}".strip()) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (ConnectionRefusedError, socket.timeout)) or "refused" in str(reason):
            base = _origin(url)
            raise LLMError(
                f"Cannot reach the model server at {base}. If it is a local model, start it "
                f"first (ollama serve / llama-server). Otherwise check provider.base_url."
            ) from exc
        raise LLMError(f"Network error calling {url}: {reason}") from exc
    except socket.timeout as exc:
        raise LLMError("Model timed out. Try a smaller model, or raise provider.timeout.") from exc
    except json.JSONDecodeError as exc:
        raise LLMError(f"Model server returned non-JSON: {exc}") from exc


# --------------------------------------------------------------------------- #
# OpenAI-compatible (OpenAI, Groq, OpenRouter, DeepSeek, Ollama, llama.cpp, ...)
# --------------------------------------------------------------------------- #
class OpenAICompat:
    def __init__(self, cfg: ProviderConfig):
        self.cfg = cfg

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            h["Authorization"] = f"Bearer {self.cfg.api_key}"
        if self.cfg.name == "openrouter":
            h["HTTP-Referer"] = "https://termux.dev"
            h["X-Title"] = "JARVIS-Termux"
        return h

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> Turn:
        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "temperature": self.cfg.temperature,
            "max_tokens": self.cfg.max_tokens,
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": t} for t in tools]
            payload["tool_choice"] = "auto"
        data = _post(
            f"{self.cfg.base_url}/chat/completions", payload, self._headers(), self.cfg.timeout
        )
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"Unexpected reply shape: {json.dumps(data)[:400]}") from exc
        calls = []
        for call in msg.get("tool_calls") or []:
            fn = call.get("function", {})
            calls.append(
                {
                    "id": call.get("id") or f"call_{len(calls)}",
                    "type": "function",
                    "function": {
                        "name": fn.get("name", ""),
                        "arguments": fn.get("arguments") or "{}",
                    },
                }
            )
        return Turn(content=msg.get("content") or "", tool_calls=calls, raw=data)


# --------------------------------------------------------------------------- #
# Anthropic
# --------------------------------------------------------------------------- #
class Anthropic:
    def __init__(self, cfg: ProviderConfig):
        self.cfg = cfg

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "x-api-key": self.cfg.api_key,
            "anthropic-version": "2023-06-01",
        }

    @staticmethod
    def _convert(messages: list[dict]) -> tuple[str, list[dict]]:
        system_parts, out = [], []
        for m in messages:
            role = m["role"]
            if role == "system":
                system_parts.append(m.get("content") or "")
            elif role == "user":
                out.append({"role": "user", "content": m.get("content") or ""})
            elif role == "assistant":
                blocks: list[dict] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for call in m.get("tool_calls") or []:
                    try:
                        args = json.loads(call["function"]["arguments"] or "{}")
                    except json.JSONDecodeError:
                        args = {"input": call["function"]["arguments"]}
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": call["id"],
                            "name": call["function"]["name"],
                            "input": args,
                        }
                    )
                out.append({"role": "assistant", "content": blocks or m.get("content") or ""})
            elif role == "tool":
                out.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m.get("tool_call_id", ""),
                                "content": str(m.get("content") or "")[:6000],
                            }
                        ],
                    }
                )
        return "\n\n".join(p for p in system_parts if p), out

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> Turn:
        system, converted = self._convert(messages)
        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "max_tokens": self.cfg.max_tokens,
            "temperature": self.cfg.temperature,
            "messages": converted,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "input_schema": t["parameters"],
                }
                for t in tools
            ]
        data = _post(f"{self.cfg.base_url}/messages", payload, self._headers(), self.cfg.timeout)
        text_parts, calls = [], []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                calls.append(
                    {
                        "id": block.get("id", f"call_{len(calls)}"),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input", {})),
                        },
                    }
                )
        return Turn(content="".join(text_parts), tool_calls=calls, raw=data)


# --------------------------------------------------------------------------- #
# Gemini
# --------------------------------------------------------------------------- #
class Gemini:
    def __init__(self, cfg: ProviderConfig):
        self.cfg = cfg

    @staticmethod
    def _convert(messages: list[dict]) -> tuple[dict, list[dict]]:
        system = {"parts": []}
        contents: list[dict] = []
        for m in messages:
            role = m["role"]
            if role == "system":
                system["parts"].append({"text": m.get("content") or ""})
            elif role == "user":
                contents.append({"role": "user", "parts": [{"text": m.get("content") or ""}]})
            elif role == "assistant":
                parts: list[dict] = []
                if m.get("content"):
                    parts.append({"text": m["content"]})
                for call in m.get("tool_calls") or []:
                    try:
                        args = json.loads(call["function"]["arguments"] or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    parts.append({"functionCall": {"name": call["function"]["name"], "args": args}})
                contents.append({"role": "model", "parts": parts})
            elif role == "tool":
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": m.get("name") or "tool",
                                    "response": {"result": str(m.get("content") or "")[:6000]},
                                }
                            }
                        ],
                    }
                )
        return system, contents

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> Turn:
        system, contents = self._convert(messages)
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": self.cfg.temperature,
                "maxOutputTokens": self.cfg.max_tokens,
            },
        }
        if system["parts"]:
            payload["systemInstruction"] = system
        if tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t["name"],
                            "description": t["description"],
                            "parameters": t["parameters"],
                        }
                        for t in tools
                    ]
                }
            ]
        url = f"{self.cfg.base_url}/models/{self.cfg.model}:generateContent?key={self.cfg.api_key}"
        data = _post(url, payload, {"Content-Type": "application/json"}, self.cfg.timeout)
        cands = data.get("candidates") or []
        if not cands:
            feedback = data.get("promptFeedback", {})
            raise LLMError(f"Gemini returned no candidates: {json.dumps(feedback)[:300]}")
        text_parts, calls = [], []
        for part in cands[0].get("content", {}).get("parts", []):
            if "text" in part:
                text_parts.append(part["text"])
            if "functionCall" in part:
                fc = part["functionCall"]
                calls.append(
                    {
                        "id": f"call_{len(calls)}_{fc.get('name', '')}",
                        "type": "function",
                        "function": {
                            "name": fc.get("name", ""),
                            "arguments": json.dumps(fc.get("args", {})),
                        },
                    }
                )
        return Turn(content="".join(text_parts), tool_calls=calls, raw=data)


PROVIDER_CLASSES = {
    "openai": OpenAICompat,
    "groq": OpenAICompat,
    "openrouter": OpenAICompat,
    "deepseek": OpenAICompat,
    "mistral": OpenAICompat,
    "together": OpenAICompat,
    "xai": OpenAICompat,
    "custom": OpenAICompat,
    "ollama": OpenAICompat,
    "llamacpp": OpenAICompat,
    "lmstudio": OpenAICompat,
    "anthropic": Anthropic,
    "gemini": Gemini,
}


def build(cfg: ProviderConfig):
    cls = PROVIDER_CLASSES.get(cfg.name)
    if cls is None:
        known = ", ".join(sorted(PROVIDER_CLASSES))
        raise LLMError(f"Unknown provider '{cfg.name}'. Known providers: {known}.")
    if cfg.needs_key:
        env = PRESETS.get(cfg.name, ("", "", "JARVIS_API_KEY"))[2] or "JARVIS_API_KEY"
        raise LLMError(
            f"No API key for '{cfg.name}'. Export {env}, run `python jarvis.py --setup`, "
            f"or use a local model (--provider ollama)."
        )
    return cls(cfg)
