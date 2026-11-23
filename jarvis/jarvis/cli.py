"""CLI: setup wizard, REPL, slash commands, one-shot asks, service control."""

from __future__ import annotations

import importlib
import json
import os
import sys
import time
from pathlib import Path

from . import llm as llm_mod
from . import animations, service, shims, ui
from .agent import Agent
from .config import APP_DIR, CONFIG_PATH, Config, PRESETS, PROVIDER_HELP, write_example
from .gate import WakeGate
from .memory import Memory
from .tools import REGISTRY, by_category, memory_tools
from .voice import Voice, describe_devices

HELP = """
{title}
  /help              this list
  /tools [category]  every capability, grouped
  /apps              installed apps
  /voice on|off      speak replies aloud
  /anim on|off       terminal animations
  /listen            hold the mic for one instruction
  /handsfree         hands-free loop (wake word enforced)
  /wake on|off       require/disable the wake word
  /sleep             close the follow-up window now
  /model <name>      switch model at runtime
  /provider <name>   switch provider at runtime
  /memory [query]    what I remember about you
  /remember <fact>   store a fact
  /forget <query>    delete matching memories
  /clear             forget the conversation (keeps facts)
  /self              what I know about my own code
  /reload            apply code changes I made to myself
  /test [suite]      run my test suite
  /start /stop       background service (survives closing Termux)
  /status            service state
  /diag              dependency + model health check
  /save              persist settings to config.json
  /mock              toggle the fake device (dev only)
  /quit              exit

{dim}
"""


# --------------------------------------------------------------------- setup
def wizard() -> Config:
    ui.banner("setup", "first run", False)
    print("Providers (a key is only needed for cloud models):\n")
    names = list(PRESETS)
    for i, name in enumerate(names, 1):
        base, model, key_env = PRESETS[name]
        kind = "local" if name in {"ollama", "llamacpp", "lmstudio"} else (key_env or "-")
        print(f"  {i:2}. {name:<11} {model:<42} {kind}")
    print()
    try:
        choice = input("Provider [1 = ollama]: ").strip() or "1"
        name = names[int(choice) - 1] if choice.isdigit() and 1 <= int(choice) <= len(names) else choice
        if name not in PRESETS:
            print(f"Unknown provider '{name}', using ollama.")
            name = "ollama"
        _base, default_model, key_env = PRESETS[name]
        model = input(f"Model [{default_model}]: ").strip() or default_model
        key = ""
        if key_env:
            import getpass

            key = getpass.getpass(f"{key_env} (leave blank to use the env var): ").strip()
        user = input("What should I call you? [sir]: ").strip() or "sir"
        voice_ans = input("Spoken replies through the phone speaker? [Y/n]: ").strip().lower()
        gate_ans = input("Only obey when addressed as JARVIS? [Y/n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted. Writing a default config you can edit by hand.")
        write_example()
        cfg = Config.load()
        cfg.save()
        return cfg

    cfg = Config.load()
    cfg.provider.name = name
    cfg.provider.model = model
    cfg.provider.base_url = ""
    cfg.provider.api_key = key
    cfg.user = user
    cfg.address = user
    cfg.voice_output = voice_ans not in ("n", "no")
    cfg.require_wake_word = gate_ans not in ("n", "no")
    cfg.save()
    ui.ok(f"saved to {CONFIG_PATH} (permissions 600)")
    return cfg


# --------------------------------------------------------------------- confirm
def ask_confirm(name: str, args: dict) -> bool:
    if not sys.stdin.isatty():
        return False
    detail = json.dumps(args, ensure_ascii=False)[:300]
    try:
        answer = input(ui.red(f"  ⚠ {name} {detail}\n  authorise? [y/N] ")).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("y", "yes")


# -------------------------------------------------------------------- greeting
def greet(cfg: Config, memory: Memory) -> str:
    hour = time.localtime().tm_hour
    part = "Morning" if 5 <= hour < 12 else "Afternoon" if hour < 18 else "Evening"
    bits = [f"{part}, {cfg.address}."]
    if shims.has("termux-battery-status"):
        ok, data, _ = shims.termux_json("termux-battery-status", timeout=8)
        if ok and isinstance(data, dict):
            battery = data.get("percentage")
            plugged = str(data.get("plugged", "")).upper() not in ("UNPLUGGED", "0", "")
            if battery is not None:
                bits.append(
                    f"Battery at {battery}%" + (" and charging." if plugged else ".")
                    if battery < 40
                    else f"Battery at {battery}%."
                )
    if memory.facts:
        bits.append(f"{len(memory.facts)} things remembered.")
    if cfg.require_wake_word:
        bits.append(f'Say "{cfg.wake_word}" and I shall attend to it.')
    else:
        bits.append("All systems nominal. At your service.")
    return " ".join(bits)


# ----------------------------------------------------------------------- wiring
def build_agent(cfg: Config, memory: Memory, speaker=None) -> Agent:
    from .tools import selfmod

    memory_tools.bind(memory)
    selfmod.bind(cfg)
    try:
        return Agent(cfg, memory, speaker=speaker, confirm=ask_confirm)
    except llm_mod.LLMError as exc:
        ui.warn(str(exc))
        raise SystemExit(2) from exc


# registry itself is never reloaded: every @tool decorator must keep writing to
# the same dict that the REPL is holding.
RELOAD_ORDER = (
    "jarvis.shims",
    "jarvis.config",
    "jarvis.gate",
    "jarvis.memory",
    "jarvis.tools.termux_api",
    "jarvis.tools.android",
    "jarvis.tools.system",
    "jarvis.tools.memory_tools",
    "jarvis.tools.selfmod",
    "jarvis.tools",
    "jarvis.llm",
    "jarvis.voice",
    "jarvis.agent",
    "jarvis.service",
)


def reload_code() -> list[str]:
    """Re-import JARVIS's modules so edits to its own source take effect."""
    REGISTRY.clear()
    reloaded, failed = [], []

    def load(name: str) -> None:
        try:
            importlib.reload(importlib.import_module(name))
            reloaded.append(name.rsplit(".", 1)[-1])
        except Exception as exc:  # noqa: BLE001 - report, never crash the session
            failed.append(f"{name}: {type(exc).__name__}: {exc}")

    for name in RELOAD_ORDER:
        load(name)
    # rebind the classes this module holds by name, so edits to them take effect
    for module_name, attr, target in (
        ("jarvis.agent", "Agent", "Agent"),
        ("jarvis.voice", "Voice", "Voice"),
        ("jarvis.gate", "WakeGate", "WakeGate"),
        ("jarvis.service", "JarvisServer", "JarvisServer"),
    ):
        if module_name.rsplit(".", 1)[-1] not in " ".join(failed):
            try:
                globals()[target] = getattr(importlib.import_module(module_name), attr)
            except Exception:  # noqa: BLE001
                pass
    return reloaded if not failed else [f"FAILED {f}" for f in failed]


def consume_restart_flag() -> bool:
    flag = Path(__file__).resolve().parent.parent / "data" / "restart.flag"
    if flag.exists():
        flag.unlink(missing_ok=True)
        return True
    return False


# ------------------------------------------------------------------- one shot
def one_shot(cfg: Config, text: str, speak_reply: bool) -> int:
    pid = service.running_pid()
    if pid and cfg.api_token:
        reply = service.ask_daemon(cfg, text)
        print(reply)
        if speak_reply:
            Voice(cfg).speak(reply)
        return 2 if reply.startswith("FAILED") else 0

    memory = Memory.load()
    voice = Voice(cfg) if speak_reply else None
    gate = WakeGate(cfg.wake_word, cfg.require_wake_word, cfg.awake_window)
    decision = gate.check(text)
    if not decision.addressed:
        print(gate.explain())
        return 3
    if not decision.request:
        print(f"At your service, {cfg.address}.")
        return 0
    agent = build_agent(cfg, memory, speaker=voice.speak if voice and voice.enabled else None)
    try:
        answer = agent.ask(decision.request)
    except llm_mod.LLMError as exc:
        ui.warn(str(exc))
        return 2
    print(answer)
    return 2 if answer.startswith("I could not complete") else 0


# ------------------------------------------------------------------------ repl
def repl(cfg: Config) -> int:
    memory = Memory.load()
    voice = Voice(cfg)
    agent = build_agent(cfg, memory, speaker=voice.speak if voice.enabled else None)
    gate = WakeGate(cfg.wake_word, cfg.require_wake_word, cfg.awake_window)

    if not cfg.provider.is_local and not cfg.provider.api_key:
        ui.warn(f"no API key for {cfg.provider.name}. Use a local model or run --setup.")
    if not shims.MOCK:
        ready, detail = shims.device_ready()
        if not ready:
            ui.warn(detail)

    ui.banner(cfg.provider.model, cfg.provider.name, voice.enabled)
    if cfg.animations:
        animations.boot_sequence(force=None)
    if cfg.greeting:
        line = greet(cfg, memory)
        ui.jarvis(line)
        if voice.enabled and not gate.enabled:
            voice.speak(line)
    print(ui.dim(f"  {gate.explain()}"))
    if service.running_pid():
        ui.info(f"background service running on port {cfg.service_port}")

    while True:
        try:
            raw = input(ui.user_prompt()).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            if voice.enabled:
                voice.speak("Powering down.")
            ui.info("session ended")
            return 0
        if not raw:
            continue

        # ------------------------------------------------------- slash commands
        if raw.startswith("/"):
            command, _, arg = raw.partition(" ")
            arg = arg.strip()
            cmd = command.lower()

            if cmd in ("/quit", "/exit", "/q"):
                if voice.enabled:
                    voice.speak("Powering down.")
                ui.info("session ended")
                return 0
            elif cmd == "/help":
                print(HELP.format(
                    title=ui.bold("Commands"),
                    dim=ui.dim(f"Say '{cfg.wake_word}' first — or use /wake off to stop that."),
                ))
            elif cmd == "/wake":
                if arg in ("on", "off"):
                    cfg.require_wake_word = arg == "on"
                    gate.reset(enabled=cfg.require_wake_word)
                    ui.ok(f"wake gate {'on' if cfg.require_wake_word else 'off'}")
                else:
                    ui.info(gate.status)
            elif cmd == "/sleep":
                gate.close()
                ui.info("standing by — say the wake word to continue")
            elif cmd == "/tools":
                for category, tools in by_category().items():
                    if arg and arg.lower() not in category.lower():
                        continue
                    print(ui.gold(f"\n  {category}"))
                    for t in tools:
                        flag = ui.red(" [confirm]") if t.danger else ""
                        print(f"   {t.name:<22}{ui.dim(t.description.splitlines()[0][:68])}{flag}")
                print()
            elif cmd == "/apps":
                from .tools.android import list_apps

                print(list_apps(arg, 60))
            elif cmd == "/anim":
                if arg in ("on", "off"):
                    cfg.animations = arg == "on"
                    ui.ok(f"animations {'on' if cfg.animations else 'off'}")
                else:
                    ui.info(f"animations are {'on' if cfg.animations else 'off'}")
            elif cmd == "/voice":
                if arg in ("on", "off"):
                    cfg.voice_output = arg == "on"
                    voice.enabled = cfg.voice_output and shims.has("termux-tts-speak")
                    agent.speaker = voice.speak if voice.enabled else None
                    ui.ok(f"voice {'on' if cfg.voice_output else 'off'}")
                else:
                    ui.info(f"voice is {'on' if cfg.voice_output else 'off'} ({json.dumps(describe_devices())})")
            elif cmd == "/listen":
                heard = voice.listen(prompt="JARVIS listening")
                if heard:
                    decision = gate.check(heard)
                    ui.jarvis(f"{ui.dim('(heard)')} {heard}")
                    if not decision.addressed:
                        ui.warn(f'not addressed — say "{cfg.wake_word}" first')
                        continue
                    answer = agent.ask(decision.request or heard)
                    print(f"{ui.gold('JARVIS')} {ui.dim('›')} {answer}")
                    gate.open()
                else:
                    ui.warn("nothing heard")
            elif cmd == "/handsfree":
                voice.converse(agent, gate=gate)
            elif cmd == "/model":
                if not arg:
                    ui.info(f"{cfg.provider.name} / {cfg.provider.model}")
                else:
                    cfg.provider.model = arg
                    agent.client = llm_mod.build(cfg.provider)
                    ui.ok(f"model -> {arg}")
            elif cmd == "/provider":
                if arg in PRESETS:
                    cfg.provider.rebind(arg)
                    agent.client = llm_mod.build(cfg.provider)
                    agent.prompt_mode = cfg.provider.tool_mode == "prompt"
                    ui.ok(f"provider -> {arg} ({cfg.provider.model})")
                else:
                    print(PROVIDER_HELP)
            elif cmd == "/memory":
                print(memory.recall(arg))
            elif cmd == "/forget":
                print(memory.forget(arg) if arg else "usage: /forget <query>")
            elif cmd == "/remember":
                print(memory.remember(arg) if arg else "usage: /remember <fact>")
            elif cmd == "/clear":
                memory.clear_history()
                ui.ok("conversation cleared")
            elif cmd == "/self":
                from .tools.selfmod import self_capabilities

                print(self_capabilities())
            elif cmd == "/reload":
                result = reload_code()
                agent = build_agent(cfg, memory, speaker=voice.speak if voice.enabled else None)
                gate = WakeGate(cfg.wake_word, cfg.require_wake_word, cfg.awake_window)
                if any(r.startswith("FAILED") for r in result):
                    ui.warn("reload incomplete: " + "; ".join(result))
                else:
                    ui.ok(f"reloaded {len(result)} modules, {len(REGISTRY)} tools")
            elif cmd == "/test":
                from .tools.selfmod import self_test

                print(self_test(arg))
            elif cmd == "/start":
                service.start(cfg)
            elif cmd == "/stop":
                service.stop()
            elif cmd == "/status":
                print(json.dumps(service.status(cfg), indent=2))
            elif cmd == "/diag":
                print(json.dumps(diagnostics(cfg), indent=2))
            elif cmd == "/save":
                cfg.save()
                ui.ok("config saved")
            elif cmd == "/mock":
                shims.MOCK = not shims.MOCK
                ui.info(f"mock device {'on' if shims.MOCK else 'off'}")
            else:
                ui.warn(f"unknown command {cmd} — /help")
            continue

        # ------------------------------------------------------- the wake gate
        decision = gate.check(raw)
        if not decision.addressed:
            print(ui.dim(f"  (ignored — {gate.status})"))
            continue
        if decision.reason == "sleep":
            ui.jarvis("Standing by.")
            if voice.enabled:
                voice.speak("Standing by.")
            continue
        request = decision.request or raw
        if decision.reason == "woken":
            ui.jarvis(f"Yes, {cfg.address}?")
            continue

        try:
            answer = agent.ask(request)
        except llm_mod.LLMError as exc:
            ui.warn(str(exc))
            continue
        except KeyboardInterrupt:
            ui.warn("interrupted")
            continue
        print(f"{ui.gold('JARVIS')} {ui.dim('›')} {ui.wrap(answer)}")
        if decision.reason != "sleep":
            gate.open()

        if consume_restart_flag():
            result = reload_code()
            agent = build_agent(cfg, memory, speaker=voice.speak if voice.enabled else None)
            gate = WakeGate(cfg.wake_word, cfg.require_wake_word, cfg.awake_window)
            ui.ok(f"restarted in place ({len(result)} modules, {len(REGISTRY)} tools)")


# ---------------------------------------------------------------- diagnostics
def diagnostics(cfg: Config) -> dict:
    ready, detail = shims.device_ready()
    out = {
        "device_ready": ready,
        "device_detail": detail,
        "device": shims.android_info(),
        "voice": describe_devices(),
        "tools": len(REGISTRY),
        "wake_gate": {
            "enabled": cfg.require_wake_word,
            "word": cfg.wake_word,
            "follow_up_window_s": cfg.awake_window,
        },
        "self_modify": cfg.allow_self_modify,
        "service": service.status(cfg),
        "provider": {
            "name": cfg.provider.name,
            "model": cfg.provider.model,
            "base_url": cfg.provider.base_url,
            "key_present": bool(cfg.provider.api_key),
            "mode": "prompt" if cfg.provider.tool_mode == "prompt" else "native",
        },
        "config_path": str(CONFIG_PATH),
    }
    try:
        client = llm_mod.build(cfg.provider)
        turn = client.chat([{"role": "user", "content": "Reply with exactly: OK"}], None)
        out["model_reply"] = (turn.content or "")[:80]
        out["model_ok"] = True
    except llm_mod.LLMError as exc:
        out["model_ok"] = False
        out["model_error"] = str(exc)
    return out


def selftest() -> int:
    """Exercise the registry, agent loop, wake gate and mock device without a network."""
    os.environ["JARVIS_MOCK"] = "1"
    shims.MOCK = True
    failures: list[str] = []

    def check(label: str, value: str, expect: str | None = None) -> None:
        bad = str(value).startswith(("FAILED", "TOOL FAILED", "TOOL NOT FOUND"))
        if bad or (expect and expect not in str(value)):
            failures.append(f"{label}: {str(value)[:160]}")

    from .tools import android, selfmod, system, termux_api  # noqa: F401

    check("battery", termux_api.battery_status(), "76")
    check("toast", termux_api.toast("hi"), "Toast")
    check("apps", android.list_apps("whatsapp"), "com.whatsapp")
    check("launch", android.launch_app("whatsapp"), "Opened")
    check("screen", android.screen_dump(), "WhatsApp")
    check("tap_text", android.tap_text("WhatsApp"), "Tapped")
    check("swipe", android.swipe(500, 1500, 500, 500), "Swiped")
    check("time", system.time_now(), "iso")
    tmp = APP_DIR / "data" / "selftest"
    tmp.mkdir(parents=True, exist_ok=True)
    note = str(tmp / "note.txt")
    check("write", system.write_file(note, "hello"), "Wrote")
    check("read", system.read_file(note), "hello")
    check("delete", system.delete_path(note), "Deleted")
    check("diag", system.termux_diagnostics(), "components")
    check("self_inspect", selfmod.self_inspect(), "tools registered")

    # wake gate must refuse an unaddressed instruction
    gate = WakeGate("jarvis", True, 25)
    if gate.check("open spotify").addressed:
        failures.append("gate: obeyed without the wake word")
    if not gate.check("jarvis, open spotify").addressed:
        failures.append("gate: refused an addressed instruction")
    if gate.check("go to sleep").reason != "sleep":
        failures.append("gate: ignored sleep command")

    # agent loop with a scripted model
    memory = Memory.load(tmp / "memory.json")
    cfg = Config()
    cfg.provider.tool_mode = "native"
    cfg.greeting = False

    class ScriptedClient:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, tools=None):
            self.calls += 1
            if self.calls == 1:
                return llm_mod.Turn(
                    tool_calls=[
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "battery_status", "arguments": "{}"},
                        }
                    ]
                )
            assert any(m["role"] == "tool" for m in messages), "tool result missing"
            return llm_mod.Turn(content="Battery is at 76%, sir.")

    agent = Agent(cfg, memory, client=ScriptedClient(), confirm=lambda n, a: True)
    answer = agent.ask("battery?")
    if "76" not in answer:
        failures.append(f"agent loop: {answer!r}")
    if agent.last_tools != ["battery_status"]:
        failures.append(f"agent tools: {agent.last_tools}")

    memory.clear_history()
    memory.facts.clear()

    if failures:
        print(ui.red(f"{len(failures)} failure(s):"))
        for f in failures:
            print("  -", f)
        return 1
    print(ui.green(f"all checks passed — {len(REGISTRY)} tools registered"))
    return 0


# ------------------------------------------------------------------- entry pt
def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="jarvis",
        description="J.A.R.V.I.S. — a Termux assistant with full control of your Android device.",
        epilog=(
            "Examples:  jarvis \"jarvis, open spotify\"   |   jarvis --voice   |   "
            "jarvis --start   |   jarvis --status"
        ),
    )
    parser.add_argument("ask", nargs="*", help="one instruction (must address JARVIS by name)")
    parser.add_argument("-a", "--ask-text", dest="ask_text", help="one instruction and exit")
    parser.add_argument("--setup", action="store_true", help="interactive first-run wizard")
    parser.add_argument("--diag", action="store_true", help="dependency and model health check")
    parser.add_argument("--tools", action="store_true", help="list every tool and exit")
    parser.add_argument("--selftest", action="store_true", help="run offline self-test")
    parser.add_argument("--verify", action="store_true", help="verify real device control (no model needed)")
    parser.add_argument("--with-sound", action="store_true", help="--verify also speaks out loud")
    parser.add_argument("--voice", action="store_true", help="start in hands-free voice mode")
    parser.add_argument("--demo", action="store_true", help="preview the terminal animations")
    parser.add_argument("--serve", action="store_true", help="run the background API in the foreground")
    parser.add_argument("--start", action="store_true", help="start the background service")
    parser.add_argument("--stop", action="store_true", help="stop the background service")
    parser.add_argument("--status", action="store_true", help="show background service state")
    parser.add_argument("--port", type=int, help="override service port")
    parser.add_argument("--mock", action="store_true", help="fake device for development")
    parser.add_argument("--provider", help="override provider name")
    parser.add_argument("--model", help="override model name")
    parser.add_argument("--base-url", help="override API base URL (local servers)")
    parser.add_argument("--api-key", help="override API key for this run only")
    parser.add_argument("--no-voice", action="store_true", help="never speak")
    parser.add_argument("--no-wake", action="store_true", help="do not require the wake word")
    parser.add_argument("--user", help="what JARVIS calls you")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args(argv)

    if args.mock:
        os.environ["JARVIS_MOCK"] = "1"
        shims.MOCK = True
    if args.selftest:
        return selftest()
    if args.verify:
        from . import verify

        return verify.main(with_sound=args.with_sound)
    if args.version:
        from . import __version__

        print(f"JARVIS {__version__}")
        return 0
    if args.setup:
        cfg = wizard()
    else:
        cfg = Config.load(
            overrides={
                "provider.name": args.provider,
                "provider.model": args.model,
                "provider.base_url": args.base_url,
                "provider.api_key": args.api_key,
                "voice_output": False if args.no_voice else None,
                "require_wake_word": False if args.no_wake else None,
                "user": args.user,
                "address": args.user,
            }
        )
    if args.no_voice:
        cfg.voice_output = False
    if args.no_wake:
        cfg.require_wake_word = False
    if args.port:
        cfg.service_port = int(args.port)

    if args.demo:
        animations.demo()
        return 0
    if args.tools:
        for category, tools in by_category().items():
            print(ui.gold(f"\n{category}"))
            for t in tools:
                print(f"  {t.name:<22} {t.description.splitlines()[0]}")
        return 0
    if args.diag:
        print(json.dumps(diagnostics(cfg), indent=2))
        return 0
    if args.status:
        print(json.dumps(service.status(cfg), indent=2))
        return 0
    if args.stop:
        return service.stop()
    if args.start:
        return service.start(cfg)
    if args.serve:
        service.JarvisServer(cfg, port=args.port).serve_forever()
        return 0

    text = " ".join(args.ask).strip() or (args.ask_text or "").strip()
    if text:
        return one_shot(cfg, text, speak_reply=cfg.voice_output)
    if args.voice:
        memory = Memory.load()
        voice = Voice(cfg)
        agent = build_agent(cfg, memory, speaker=voice.speak if voice.enabled else None)
        gate = WakeGate(cfg.wake_word, True, cfg.awake_window)
        voice.converse(agent, gate=gate)
        return 0
    try:
        return repl(cfg)
    except KeyboardInterrupt:
        ui.info("interrupted")
        return 130
