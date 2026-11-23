# J.A.R.V.I.S. — Termux edition

> *Just A Rather Very Intelligent System.*

A butler for your Android phone. Runs inside **Termux**, speaks through
**Termux:API**, drives the whole device — apps, screens, calls, SMS, torch, camera,
sensors, files, shell — and thinks with **either a local model or any cloud API key**.

It only acts when you say its name, it can rewrite its own code, and it keeps
running when you close the terminal.

```
you › jarvis, find Pepper in whatsapp and tell her I'll be late
  ⚙ launch_app app=whatsapp
  ⚙ wait seconds=2
  ⚙ screen_dump
  ⚙ tap_text text=Pepper Potts
  ⚙ type_text text=I'll be late
  ⚙ press_key key=enter
JARVIS › Message sent to Pepper Potts, sir.
```

---

## Why this one

| | typical Termux "Jarvis" | this project |
|---|---|---|
| Understanding | regex intents (`"call X"`) | any phrasing, multi-step plans |
| Scope | 8–10 hardcoded actions | **83 tools** across device, UI, phone, files, web, self |
| Unknown apps | not supported | UI automation: dump screen, tap text, type |
| Model | one hardcoded API | 13 providers, incl. local Ollama / llama.cpp / LM Studio |
| Offline | needs internet | fully local with Ollama |
| Wake word | optional or absent | **required**, with a follow-up window and a sleep command |
| Feedback | plain text | **live waveform, HUD spinner, wake flash, vibration** |
| Self-repair | none | reads, edits, tests and reloads its own source |
| Background | dies with the terminal | supervised daemon + loopback API + boot hook |
| Safety | none | confirmation gate, loop budget, protected files, rollback |
| Tests | none | **102 offline tests** + `--selftest` on device |

---

## Install (5 minutes)

1. Install **Termux** and **Termux:API** from **F-Droid** (same source for both —
   Play Store builds are incompatible).
2. Open Termux:

```bash
pkg install -y git
git clone <your-fork-url> ~/jarvis && cd ~/jarvis
bash install.sh
```

3. Grant **Termux:API** permissions: Microphone, Camera, SMS, Phone, Contacts,
   Location, Storage.
4. Set Termux battery usage to **Unrestricted**.
5. Pick a brain (below), then `python jarvis.py`.

---

## Choose a brain

### Local, free, private — Ollama

```bash
pkg install -y ollama
ollama serve &
ollama pull llama3.1      # or qwen2.5:3b — lighter, still calls tools
```

### Cloud — a key in an env var, nothing written to disk

```bash
export GROQ_API_KEY=...            # free tier, fastest
python jarvis.py --provider groq --model llama-3.3-70b-versatile
```

Providers: `openai`, `anthropic`, `gemini`, `groq`, `openrouter`, `deepseek`,
`mistral`, `together`, `xai`, `custom`, plus local `ollama`, `llamacpp`,
`lmstudio`. Keys come from `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `DEEPSEEK_API_KEY`,
`MISTRAL_API_KEY`, `TOGETHER_API_KEY`, `XAI_API_KEY`, `JARVIS_API_KEY`.

`python jarvis.py --setup` walks you through it. `config.json` is chmod 600.

---

## Say my name

JARVIS does **nothing** unless addressed:

```
jarvis, open spotify          ✓ obeys
open spotify                  ✗ ignored: (ignored — armed — say "jarvis")
jarvis, torch on              ✓ obeys
and turn it off               ✓ follow-up inside the 25s window
jarvis, go to sleep           ✓ closes the window immediately
```

- The wake word is matched anywhere in the sentence, case-insensitively.
- After every reply the follow-up window is open for `awake_window` seconds
  (default 25), so you do not repeat the name mid-conversation.
- `"jarvis, go to sleep"`, `"stand down"`, `"that's all"` close it at once.
- `/wake off` disables gating for the current session; `/wake on` restores it.
- The gate is enforced in **voice, typed and HTTP** paths — a rogue app posting
  to the local API still has to say the name.
- Hands-free mode always keeps it on.

---

## It shows you it is listening

The terminal animates in place — a scrolling waveform while the mic is open, a
gold HUD with elapsed time while the model thinks, a pulse when it hears its
name, and a five-line boot sequence at startup.

```
  ◜ listening ◉ ▃▅▂▇▄▁▆▃▅▂▇▁▄▆▃▂▅▇▁▄▃▆▅▂▇▄▁
  ⠹ thinking...   1.4s
  ◝ JARVIS heard
```

On the phone it also vibrates twice when the mic opens and once when the wake
word lands, so you can feel it listening without looking.

```bash
python jarvis.py --demo        # preview all animations
```

Control it with `/anim on|off`, or `animations: false` in `config.json`. The
haptics have their own switch: `haptics: false`. Everything falls back to a
single quiet line when output is piped, on a slow terminal, or with `NO_COLOR=1`,
and each animation runs in a daemon thread that is always joined — a dead
animation can never wedge the assistant (tested).

## It can rewrite itself

Ask it to fix or extend its own code:

```
you › jarvis, add a tool that flashes the screen three times
  ⚙ self_inspect path=jarvis/tools
  ⚙ self_read path=jarvis/tools/termux_api.py
  ⚙ self_edit path=jarvis/tools/termux_api.py find=... replace=...
  ⚙ self_test
  ⚙ self_restart
JARVIS › Added `flash_screen`, tests pass, reloaded.
```

| tool | what it does |
|---|---|
| `self_inspect` / `self_read` | browse its own source with line numbers |
| `self_edit` | exact-snippet replacement, then syntax check |
| `self_write` | create a new module, then syntax check |
| `self_rollback` | restore the newest backup |
| `self_test` | run the real 102-test suite and report the tail |
| `self_logs` | read its own daemon/test logs |
| `self_restart` | apply changes (`/reload` in a session, automatic in the daemon) |

Guard rails, all tested:

- writes confined to the project — `config.json`, `data/`, `logs/`, `.git` refused
- extensions limited to `.py .md .json .sh .txt .yml`; `/etc/passwd` and `../` refused
- every write backed up to `data/backups/` first
- Python that does not compile is **rolled back immediately**
- edits wait for `/reload`, so a half-finished edit never silently replaces a
  working assistant
- `allow_self_modify: false` in config turns the whole capability off

`/reload` re-imports 15 modules and rebuilds the registry in place.

---

## It keeps working when Termux is closed

```bash
python jarvis.py --start      # detach + wake lock + ongoing notification
python jarvis.py --status     # pid, gate state, uptime, requests
python jarvis.py --stop
python jarvis.py --serve      # foreground, for debugging
```

The daemon exposes a token-authenticated loopback API, so widgets, Tasker,
Shizuku, curl and other apps drive the same running assistant:

```bash
TOKEN=$(python3 -c "import json;print(json.load(open('config.json'))['api_token'])")

curl -s -X POST http://127.0.0.1:8765/ask \
  -H "X-Jarvis-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"text":"jarvis, torch on"}'
# {"reply":"Torch on, sir.","gate":"awake (25s left)","reason":"named"}

curl -s http://127.0.0.1:8765/health
```

Without the wake word the answer is `403 not addressed`. No token, `401`.

### Survive a reboot and a swipe-away

```bash
pkg install -y termux-services
bash scripts/service-setup.sh          # supervised by runit: auto-restarts
mkdir -p ~/.termux/boot
cp scripts/boot.sh ~/.termux/boot/50-jarvis
```

`Termux:Boot` (F-Droid) starts the daemon after a reboot; runit restarts it if it
crashes; the wake lock keeps the CPU awake; the ongoing notification keeps Android
from treating it as idle.

**Honest limits:** if you *swipe Termux away* on an aggressive OEM ROM, Android
can kill the process anyway — runit + boot bring it back. Truly app-less operation
would need an APK, not Termux. The mic dialog used by `termux-speech-to-text`
also needs the Termux app in the foreground, so continuous wake-word listening is
a foreground activity; the daemon handles *requests* in the background.

---

## Use it

```bash
python jarvis.py                        # interactive butler
python jarvis.py --voice                # hands-free "jarvis, …"
python jarvis.py "jarvis, battery"      # one-shot (routed to the daemon if running)
python jarvis.py --diag                 # is everything wired up?
python jarvis.py --tools                # all 83 capabilities
python jarvis.py --selftest             # offline proof nothing is broken
python -m unittest discover -s tests    # 102 tests, no phone needed
```

### Things it can actually do

```
"jarvis, open spotify and play my liked songs"
"jarvis, what's my battery, and turn the torch off"
"jarvis, read my last three messages from Pepper"
"jarvis, text Pepper that I'll be twenty minutes late"
"jarvis, find the nearest pharmacy"          → location + web + maps
"jarvis, screenshot the screen and tell me what app this is"
"jarvis, set an alarm for 06:30 and a timer for 10 minutes"
"jarvis, wifi scan, strongest first"
"jarvis, search my notes folder for the router password"
"jarvis, add a tool that flashes the screen, then test it"
"jarvis, remember that I take my coffee black"
```

### Slash commands

| | |
|---|---|
| `/help`, `/tools [cat]`, `/apps` | discover |
| `/wake on\|off`, `/sleep` | wake gating |
| `/voice`, `/listen`, `/handsfree`, `/anim` | voice + animations |
| `/demo` (`--demo`) | preview the animations |
| `/model`, `/provider` | switch brains live |
| `/memory`, `/remember`, `/forget`, `/clear` | memory |
| `/self`, `/reload`, `/test [suite]` | self-modification |
| `/start`, `/stop`, `/status` | background daemon |
| `/diag`, `/save`, `/mock`, `/quit` | health, persist, dev, exit |

---

## How it works

```
jarvis.py
└── jarvis/
    ├── cli.py         REPL, wizard, slash commands, diagnostics, routing
    ├── gate.py        wake-word rules: named / follow-up / sleep
    ├── agent.py       the loop: model → tools → model → answer
    ├── llm.py         OpenAI-compatible, Anthropic, Gemini (stdlib urllib only)
    ├── service.py     daemon, HTTP API, pidfile, wake lock, restart watcher
    ├── config.py      config.json + env + presets + JARVIS persona
    ├── memory.py      durable facts + rolling history
    ├── voice.py       TTS, STT, wake-word loop
    ├── shims.py       process runner, Termux:API detection, graceful failure
    ├── mockdevice.py  fake phone, so the whole system runs on a laptop
    └── tools/         83 registered capabilities
        ├── termux_api.py   battery, torch, SMS, calls, sensors, TTS, camera…
        ├── android.py      apps, uiautomator, tap/swipe/type, intents
        ├── system.py       shell, files, web, weather, alarms, calendar
        ├── selfmod.py      read/edit/test/reload its own code
        └── registry.py     schema generation, argument validation, budget
```

**Tool calling.** Every capability is a decorated function; its signature becomes
a JSON schema sent to the model. The agent executes calls, feeds results back and
repeats up to `max_steps`. If a model cannot do function calling, the agent
injects a compact JSON protocol instead — automatically, including mid-session
when a server rejects the `tools` field.

**App control** needs no root: `pm list packages` for discovery, `cmd package
resolve-activity` + `am start` to launch, `dumpsys window` for the foreground app,
`uiautomator dump` to read the screen, `input tap|swipe|text|keyevent` to drive it.

**Safety.** `sms_send`, `dial`, `run_shell`, `delete_path`, `force_stop`,
`install_apk`, `self_edit`, `self_write`, `self_rollback` and `forget` require a
`y` at the terminal. `auto_approve: ["sms_send"]` whitelists individual tools.
The daemon never auto-approves anything. A per-task budget stops runaway loops and
identical repeated calls are refused.

---

## Add a capability by hand

```python
# jarvis/tools/system.py
from .registry import tool

@tool("flash_screen", "Pulse the screen brightness to get attention.",
      {"times": {"type": "integer", "default": 3}}, category="device")
def flash_screen(times: int = 3) -> str:
    for _ in range(times):
        shims.run(["termux-brightness", "255"]); time.sleep(0.1)
        shims.run(["termux-brightness", "10"]);  time.sleep(0.1)
    return f"Flashed {times} times."
```

Restart, or `/reload`. The model sees it immediately — no prompt editing.

---

## Troubleshooting

| symptom | fix |
|---|---|
| `Termux:API is not available` | install the Termux:API **app**, `pkg install termux-api`, reopen Termux |
| `Cannot reach the model server` | `ollama serve &` — or fix `provider.base_url` |
| `HTTP 401` | key wrong/missing: `export GROQ_API_KEY=...` |
| Vosk-less voice silence | `termux-speech-to-text` needs the mic dialog; grant microphone |
| daemon dies after swipe | `pkg install termux-services && bash scripts/service-setup.sh` |
| `403 not addressed` from the API | say `"jarvis"` first — the wake gate is on by design |
| `uiautomator` returns nothing | screen locked, or the ROM blocks it (see below) |
| `screencap` produces no data | ROM denies frame-buffer access to apps — screenshots unavailable from Termux on that device |
| `pm list packages` fails | Android 14+ ROMs block `cmd package` from apps; JARVIS falls back to `dumpsys` automatically |
| edits refused | path outside project, or a protected file (`config.json`, `data/`) |
| self-test fails after an edit | `/reload` was skipped, or `self_rollback` the file |

### Android 14+ / cloud-phone ROM restrictions

Verified on a real Android 14 (API 34) cloud phone: some hardened ROMs deny
untrusted-app access to `cmd` services, `uiautomator` and the frame buffer.
JARVIS detects each case and says so plainly instead of failing silently:
app listing still works (via `dumpsys`), app launching still works (via
`am start` with the launcher activity resolved from `dumpsys`), and the
Termux:API suite (battery, clipboard, notifications, sensors, torch, TTS…)
works fully. `python jarvis.py --verify` prints a per-check PASS/FAIL report
of exactly what this device allows.

### Fewer tokens (optional)

Tool outputs (screen dumps, app lists, notification JSON) can eat a small
model's context. If [Headroom](https://github.com/headroomlabs-ai/headroom) is
installed, JARVIS compresses message history before every model call — same
answers, fewer input tokens, all locally:

```sh
pip install headroom-ai        # optional; JARVIS stays zero-dep without it
```

No other change is needed; set `"compress_context": false` in config.json (or
`JARVIS_COMPRESS=0`) to turn it off.

---

## Extras

- **Termux:Widget** — `cp scripts/widget.sh ~/.shortcuts/JARVIS` for a tap-to-ask
  launcher.
- **Termux:Boot** — `cp scripts/boot.sh ~/.termux/boot/50-jarvis`.
- **runit** — `bash scripts/service-setup.sh` for supervision.
- **Alias** — `echo 'alias jarvis="python ~/jarvis/jarvis.py"' >> ~/.bashrc`.

## Credits

Original implementation. Prior art that shaped the design — `ayusc/termux-bot`,
`RenzMc/Renz_Assistant`, `Amitminer/Jarvis-Termux`, `suyogpawate-lab/jarvis` — is
credited in [ATTRIBUTIONS.md](ATTRIBUTIONS.md); forking advice is in
[FORKING.md](FORKING.md). MIT licensed, see [LICENSE](LICENSE).
