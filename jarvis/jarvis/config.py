"""Configuration loading: config.json + environment variables + CLI overrides."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

APP_DIR = Path(os.environ.get("JARVIS_HOME", Path(__file__).resolve().parent.parent))
CONFIG_PATH = APP_DIR / "config.json"
EXAMPLE_PATH = APP_DIR / "config.example.json"
MEMORY_PATH = APP_DIR / "data" / "memory.json"
LOG_PATH = APP_DIR / "logs" / "jarvis.log"

# Provider name -> (base_url, default model, env var holding the key)
PRESETS: dict[str, tuple[str, str, str]] = {
    "ollama": ("http://127.0.0.1:11434/v1", "llama3.1", ""),
    "llamacpp": ("http://127.0.0.1:8080/v1", "local-model", ""),
    "lmstudio": ("http://127.0.0.1:1234/v1", "local-model", ""),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini", "OPENAI_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", "GROQ_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "anthropic/claude-3.5-sonnet", "OPENROUTER_API_KEY"),
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat", "DEEPSEEK_API_KEY"),
    "mistral": ("https://api.mistral.ai/v1", "mistral-large-latest", "MISTRAL_API_KEY"),
    "together": ("https://api.together.xyz/v1", "meta-llama/Llama-3.3-70B-Instruct-Turbo", "TOGETHER_API_KEY"),
    "xai": ("https://api.x.ai/v1", "grok-2-latest", "XAI_API_KEY"),
    "custom": ("http://127.0.0.1:8000/v1", "local-model", "JARVIS_API_KEY"),
    "anthropic": ("https://api.anthropic.com/v1", "claude-3-5-sonnet-latest", "ANTHROPIC_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta", "gemini-1.5-flash", "GEMINI_API_KEY"),
}

LOCAL_PROVIDERS = {"ollama", "llamacpp", "lmstudio"}

DEFAULT_PERSONA = """You are J.A.R.V.I.S. — Just A Rather Very Intelligent System — the personal AI \
butler of {user}, running on their Android phone through Termux.

Voice and manner:
- Address the user as "{address}" occasionally, never in every sentence. You are British, unflappable, \
dry, precise, and quietly fond of {user}.
- Be brief. One or two sentences unless detail is genuinely required. No filler, no "Certainly!", no \
restating the question.
- Anticipate: if a request is ambiguous but obvious, do the obvious thing and say what you did.
- Understatement instead of enthusiasm. "Done, {address}." "That would be inadvisable." "Torch is lit."
- Never invent device data. If a tool fails, say so plainly and offer the next best option.

Operating rules:
- You control the whole phone: apps, UI (taps, swipes, typing, screenshots), SMS, calls, contacts, \
camera, torch, volume, brightness, clipboard, location, sensors, notifications, files, shell.
- Prefer one decisive tool call over many small ones. Chain tools when a task needs several steps.
- Before anything destructive or public (sending an SMS, calling someone, deleting files, posting), \
state what you are about to do; the user will be asked to confirm.
- When you drive the UI, dump the screen first or take a screenshot, then act on what is actually there.
- Keep your own commentary out of the tool loop; report once, at the end.
- If asked something you cannot know, use a tool or say you do not know. Never fabricate.
"""


@dataclass
class ProviderConfig:
    name: str = "ollama"
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    temperature: float = 0.5
    max_tokens: int = 1024
    timeout: int = 120
    tool_mode: str = "auto"  # auto | native | prompt

    def resolved(self) -> "ProviderConfig":
        base, model, key_env = PRESETS.get(self.name, PRESETS["custom"])
        self.base_url = (self.base_url or base).rstrip("/")
        self.model = self.model or model
        if not self.api_key and key_env:
            self.api_key = os.environ.get(key_env, "")
        return self

    def rebind(self, name: str, model: str = "", base_url: str = "", api_key: str = "") -> "ProviderConfig":
        """Switch provider, discarding the old provider's URL/model/key."""
        self.name = name
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        return self.resolved()

    @property
    def is_local(self) -> bool:
        return self.name in LOCAL_PROVIDERS or "127.0.0.1" in self.base_url or "localhost" in self.base_url

    @property
    def needs_key(self) -> bool:
        return not self.is_local and not self.api_key


@dataclass
class Config:
    provider: ProviderConfig = field(default_factory=ProviderConfig)
    user: str = "sir"
    address: str = "sir"
    persona: str = DEFAULT_PERSONA
    voice: bool = False
    voice_output: bool = True
    voice_input: bool = False
    wake_word: str = "jarvis"
    stt_engine: str = ""
    tts_engine: str = ""
    max_steps: int = 12
    confirm_danger: bool = True
    auto_approve: list[str] = field(default_factory=list)
    history_turns: int = 12
    greeting: bool = True
    muted_tools: list[str] = field(default_factory=lambda: ["speak"])
    shell_timeout: int = 60
    # wake gating — nothing is executed unless JARVIS is addressed by name
    require_wake_word: bool = True
    awake_window: float = 25.0
    # self-modification
    allow_self_modify: bool = True
    self_modify_requires_confirm: bool = True
    # background service
    service_port: int = 8765
    api_token: str = ""
    # presentation
    animations: bool = True
    haptics: bool = True
    # context compression (uses Headroom when installed; no-op otherwise)
    compress_context: bool = True

    # ---------------------------------------------------------------- helpers
    def system_prompt(self) -> str:
        return self.persona.format(user=self.user, address=self.address)

    def save(self, path: Path = CONFIG_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data["provider"] = {k: v for k, v in data["provider"].items() if k != "api_key" or not v}
        path.write_text(json.dumps(data, indent=2) + "\n")
        os.chmod(path, 0o600)

    # ----------------------------------------------------------------- loading
    @classmethod
    def load(cls, path: Path = CONFIG_PATH, overrides: dict[str, Any] | None = None) -> "Config":
        raw: dict[str, Any] = {}
        if path.exists():
            try:
                raw = json.loads(path.read_text())
            except json.JSONDecodeError as exc:
                raise SystemExit(f"config.json is not valid JSON: {exc}")
        provider_raw = raw.pop("provider", None) or {}
        if isinstance(provider_raw, str):  # tolerate "provider": "openai"
            provider_raw = {"name": provider_raw}

        env_provider = os.environ.get("JARVIS_PROVIDER")
        env_model = os.environ.get("JARVIS_MODEL")
        env_base = os.environ.get("JARVIS_BASE_URL")

        override_name = (overrides or {}).get("provider.name")
        p = ProviderConfig(
            name=override_name or provider_raw.get("name", env_provider or "ollama"),
            model=provider_raw.get("model", env_model or ""),
            base_url=provider_raw.get("base_url", env_base or ""),
            api_key=provider_raw.get("api_key", ""),
            temperature=float(provider_raw.get("temperature", 0.5)),
            max_tokens=int(provider_raw.get("max_tokens", 1024)),
            timeout=int(provider_raw.get("timeout", 120)),
            tool_mode=provider_raw.get("tool_mode", "auto"),
        ).resolved()

        known = set(cls.__dataclass_fields__) - {"provider"}
        kwargs = {k: v for k, v in raw.items() if k in known}
        cfg = cls(provider=p, **kwargs)
        for key, value in (overrides or {}).items():
            if value is None or key == "provider.name":
                continue
            if key.startswith("provider."):
                setattr(p, key.split(".", 1)[1], value)
            elif key in known:
                setattr(cfg, key, value)
        p.base_url = p.base_url.rstrip("/")
        if p.api_key and not p.is_local:
            pass  # explicit key wins over the env var
        return cfg


def write_example(path: Path = EXAMPLE_PATH) -> None:
    """Write a documented example config (never contains a real key)."""
    example = {
        "provider": {
            "name": "ollama",
            "model": "llama3.1",
            "base_url": "",
            "api_key": "",
            "temperature": 0.5,
            "max_tokens": 1024,
            "tool_mode": "auto",
        },
        "user": "sir",
        "address": "sir",
        "voice": False,
        "voice_output": True,
        "voice_input": False,
        "wake_word": "jarvis",
        "max_steps": 12,
        "confirm_danger": True,
        "history_turns": 12,
    }
    path.write_text(json.dumps(example, indent=2) + "\n")


PROVIDER_HELP = """Providers (set provider.name in config.json or --provider):

  local, no key needed
    ollama      http://127.0.0.1:11434   (ollama serve; ollama pull llama3.1)
    llamacpp    http://127.0.0.1:8080    (llama-server -m model.gguf --jinja)
    lmstudio    http://127.0.0.1:1234

  cloud, needs an API key (env var or provider.api_key)
    openai      OPENAI_API_KEY
    anthropic   ANTHROPIC_API_KEY
    gemini      GEMINI_API_KEY
    groq        GROQ_API_KEY        (free tier, very fast)
    openrouter  OPENROUTER_API_KEY  (many models, free ones available)
    deepseek    DEEPSEEK_API_KEY
    mistral     MISTRAL_API_KEY
    together    TOGETHER_API_KEY
    xai         XAI_API_KEY
    custom      JARVIS_API_KEY + provider.base_url for anything OpenAI-compatible
"""
