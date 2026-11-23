# Forking an existing Termux assistant and redesigning it

You asked whether this could instead be built on top of an existing GitHub
project. You can — here is the honest trade-off and the shortest path.

## Why this repo is not a fork

The popular Termux assistants are **intent routers**: a list of regexes such as
`"call (\w+)"` mapped to `termux-telephony-call`. Their value is the Termux:API
bindings; their ceiling is that any phrasing you did not anticipate does nothing.

To reach "do it like JARVIS", you need the *inverse*: one loop where the model
chooses from a menu of capabilities. That is a different core, not a patch, so
starting clean was cheaper than deleting 80% of a fork. Everything worth reusing
from those projects is public knowledge (`termux-*` CLI flags), not code.

## If you still want to fork one

Pick by what you need:

| need | fork |
|---|---|
| mature `termux-*` bindings, voice loop | `ayusc/termux-bot` (GPL-3.0 — your fork must stay GPL) |
| wake word + local ASR experiments | `RenzMc/Renz_Assistant` |
| memory, shell guard, Gmail/WhatsApp glue | `suyogpawate-lab/jarvis` (MIT) |
| minimal Gemini terminal chat | `Amitminer/Jarvis-Termux` |

```bash
git clone https://github.com/<owner>/<repo> jarvis-fork
cd jarvis-fork && git checkout -b redesign
```

Then port these files, in this order — they are self-contained and drop in
without touching the host project's layout:

1. `jarvis/tools/registry.py` — the decorator, schema generation, `Budget`.
2. `jarvis/tools/termux_api.py` + `android.py` — replace their command wrappers.
   Match each of their existing features to a tool; keep their CLI flags, swap
   the plumbing for `shims.run`.
3. `jarvis/agent.py` — delete their dispatcher, call `Agent.ask()` from their
   REPL/voice handler.
4. `jarvis/llm.py` — keep their provider if it works, add the rest behind the
   same `chat(messages, tools) -> Turn` interface.
5. `jarvis/gate.py` — wrap their input path so nothing runs unless the wake word
   is present. This is a five-line change at their entry point and the single
   biggest behavioural upgrade.
6. `jarvis/service.py` — only if they lack background operation; it needs the
   agent, a token and a pidfile, nothing else.

Keep from the fork: the installer, the wake-word captures, any device quirks
they documented (different OEM permission behaviour, TTS engine names).

Drop from the fork: intent dictionaries, their per-command functions, and any
hardcoded API key handling.

## Checks before you call it done

```bash
python -m unittest discover -s tests      # agent + providers, no phone needed
JARVIS_MOCK=1 python jarvis.py --selftest # tools against a fake device
python jarvis.py --diag                   # real device
python jarvis.py "open settings and tell me the android version"
```

## Licence hygiene

GPL-3.0 forks stay GPL-3.0 and must keep their notices; MIT forks may relicense
but must keep the copyright line. This repo is MIT and contains no copied code,
so it can be merged into either — but not the other way round.
