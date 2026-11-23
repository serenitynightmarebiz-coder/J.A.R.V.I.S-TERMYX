# Prior art and attribution

This project is an original implementation. Before writing it I surveyed the
existing Termux assistant ecosystem; these projects informed *what* a Termux
assistant needs, not *how* this one is built. No code, prompts or assets were
copied, and none of their licences are inherited here (this repo is MIT).

| project | licence | what it taught | what this project does differently |
|---|---|---|---|
| [ayusc/termux-bot](https://github.com/ayusc/termux-bot) (`sriparna`) | GPL-3.0 | Termux:API voice loop basics, wake-with-name UX | no intent matcher; arbitrary tool calling, UI automation, 12 model providers |
| [RenzMc/Renz_Assistant](https://github.com/RenzMc/Renz_Assistant) | none stated | wake-word fallbacks (Vosk/Whisper), breadth of Termux:API usage | no heavy ASR deps; graceful degradation, provider abstraction |
| [Amitminer/Jarvis-Termux](https://github.com/Amitminer/Jarvis-Termux) | see repo | Gemini chat-in-terminal pattern | device control rather than chat; provider-agnostic; safety gates |
| [suyogpawate-lab/jarvis](https://github.com/suyogpawate-lab/jarvis) | MIT | memory + shell guardrail concept | smaller surface, stdlib-only, the whole phone via `uiautomator`, not just shell |
| [MarkFrencerLozada/jarvis](https://github.com/MarkFrencerLozada/jarvis) | see repo | plugin layout ideas | single coherent registry instead of loose scripts |

## Deliberate departures

1. **One generic loop, not 200 intents.** Any phrasing works, including things
   nobody wrote a regex for ("put my phone on silent and text Pepper").
2. **Any app, not just API-having apps.** `uiautomator` + `input` means JARVIS
   can operate a banking app or a game, not only Spotify and WhatsApp.
3. **Zero pip dependencies.** Pure stdlib over `urllib`, so `pkg install python
   termux-api` genuinely is enough — the usual Termux install failure point
   (compiling `pyaudio`, `whisper`, `google-generativeai`) cannot happen here.
4. **Local-first.** Ollama is a first-class provider; cloud keys are optional.
5. **Failures are legible.** Every tool returns a message the model can act on
   ("Termux:API is not available. Install the app…"), and the agent budget stops
   the classic eighteen-identical-taps loop.
6. **Testable off-device.** `JARVIS_MOCK=1` provides a fake phone, so CI and
   laptops can exercise the agent, tools and providers.

If you fork one of the above instead, see [FORKING.md](FORKING.md) for a map of
what to port over.

- [Headroom](https://github.com/headroomlabs-ai/headroom) — optional context
  compression (Apache-2.0). If `pip install headroom-ai` is present, JARVIS
  routes message history through `headroom.compress()` before each model call;
  without it the integration is a pass-through and JARVIS remains dependency-free.
- [itskreisler/termux-skill](https://github.com/itskreisler/termux-skill) —
  surveyed its 70+ Termux API command reference when reviewing JARVIS's own
  Termux:API coverage.
