"""Voice: text-to-speech, speech-to-text and a wake-word loop, via Termux:API."""

from __future__ import annotations

import json
import re
import time

from . import animations, shims, ui
from .config import Config


def clean_for_speech(text: str) -> str:
    """Strip markdown, code and emoji so TTS does not read punctuation aloud."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_#>|]+", " ", text)
    text = re.sub(r"https?://\S+", "link", text)
    text = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


class Voice:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.enabled = cfg.voice_output and shims.has("termux-tts-speak")
        self.last_spoken = 0.0

    # ------------------------------------------------------------------ output
    def speak(self, text: str, blocking: bool = True) -> bool:
        if not self.enabled:
            return False
        phrase = clean_for_speech(text)
        if not phrase:
            return False
        if len(phrase) > 700:
            cut = phrase[:700].rsplit(". ", 1)[0]
            phrase = (cut or phrase[:700]) + "."
        args = ["-r", "1.0"]
        if self.cfg.tts_engine:
            args = ["-e", self.cfg.tts_engine, *args]
        args.append(phrase)
        if blocking:
            rc, _, err = shims.run(["termux-tts-speak", *args], timeout=180)
            self.last_spoken = time.time()
            if rc != 0 and err:
                ui.warn(f"TTS failed: {err.strip()}")
            return rc == 0
        import subprocess

        subprocess.Popen(["termux-tts-speak", *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.last_spoken = time.time()
        return True

    def engines(self) -> list[str]:
        ok, data, _ = shims.termux_json("termux-tts-engines")
        if ok and isinstance(data, list):
            return [e.get("name", "") for e in data if e.get("name")]
        return []

    # ------------------------------------------------------------------- input
    def listen(self, language: str = "en-US", prompt: str = "", animate: bool = True) -> str:
        """One utterance. termux-speech-to-text shows its own listening dialog."""
        if shims.has("termux-toast") and prompt:
            shims.run(["termux-toast", "-g", "middle", prompt[:120]])
        force = None if getattr(self.cfg, "animations", True) else False
        haptics = bool(getattr(self.cfg, "haptics", True))
        listen_ctx = animations.Listening(prompt or "listening", force=force, haptics=haptics) if animate else None
        if listen_ctx is not None:
            listen_ctx.__enter__()
        try:
            ok, data, err = shims.termux_json("termux-speech-to-text", "-l", language, timeout=90)
        finally:
            if listen_ctx is not None:
                listen_ctx.__exit__(None, None, None)
        if not ok:
            ui.warn(f"speech-to-text failed: {err}")
            return ""
        if isinstance(data, dict):
            return str(data.get("text", "")).strip()
        if isinstance(data, str):
            return data.strip()
        return ""

    # -------------------------------------------------------------- wake word
    def wait_for_wake_word(self, language: str = "en-US") -> str:
        """Block until the wake word is heard; return the request that followed it."""
        word = self.cfg.wake_word.lower().strip()
        ui.info(f'listening for the wake word "{word}" — say "{word}, what is my battery level"')
        while True:
            heard = self.listen(language).lower()
            if not heard:
                time.sleep(0.5)
                continue
            if word in heard:
                request = heard.split(word, 1)[1].strip(" ,.:;!?")
                return request or ""
            ui.info(f'heard "{heard[:60]}" — not addressed to me')

    def converse(self, agent, language: str = "en-US", gate=None) -> None:
        """Hands-free loop. Nothing happens unless JARVIS is addressed by name."""
        from .gate import WakeGate

        gate = gate or WakeGate(self.cfg.wake_word, True, self.cfg.awake_window)
        ui.ok(f'Voice mode — say "{self.cfg.wake_word}, …". Ctrl-C to stop.')
        while True:
            try:
                heard = self.listen(language)
            except KeyboardInterrupt:
                self.speak("Standing by.")
                return
            if not heard:
                continue
            decision = gate.check(heard)
            if not decision.addressed:
                continue
            animations.wake_flash(
                self.cfg.wake_word,
                force=None if getattr(self.cfg, "animations", True) else False,
            )
            if decision.reason == "sleep":
                ui.info("standing by")
                self.speak("Standing by.")
                continue
            request = decision.request
            if not request:
                self.speak(f"Yes, {self.cfg.address}?")
                gate.open()
                continue
            ui.jarvis(f"{ui.dim('(heard)')} {request}")
            try:
                answer = agent.ask(request)
            except KeyboardInterrupt:
                self.speak("Very well.")
                return
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                ui.warn(str(exc))
                self.speak("I ran into a problem with that.")
                continue
            print(f"{ui.gold('JARVIS')} {ui.dim('›')} {answer}")
            self.speak(answer)
            gate.open()


def stt_available() -> bool:
    return shims.has("termux-speech-to-text")


def describe_devices() -> dict:
    return {
        "tts": "ok" if shims.has("termux-tts-speak") else "missing",
        "stt": "ok" if stt_available() else "missing",
        "toast": "ok" if shims.has("termux-toast") else "missing",
    }
