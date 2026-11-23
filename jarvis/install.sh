#!/data/data/com.termux/files/usr/bin/bash
# J.A.R.V.I.S. installer for Termux.
#   bash install.sh              install everything + guided setup
#   bash install.sh --no-setup   install dependencies only
#   bash install.sh --voice      also install optional local wake-word extras
set -euo pipefail

BOLD=$'\033[1m'; GOLD=$'\033[33m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'; OFF=$'\033[0m'
say()  { printf '%s\n' "${GOLD}▸${OFF} $*"; }
ok()   { printf '%s\n' "${GREEN}✓${OFF} $*"; }
warn() { printf '%s\n' "${RED}!${OFF} $*"; }
have() { command -v "$1" >/dev/null 2>&1; }

SETUP=1
VOICE_EXTRAS=0
for arg in "$@"; do
  case "$arg" in
    --no-setup) SETUP=0 ;;
    --voice) VOICE_EXTRAS=1 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) warn "unknown option $arg"; exit 2 ;;
  esac
done

printf '\n%s\n' "${BOLD}  J.A.R.V.I.S. — installer${OFF}"
printf '%s\n\n' "${DIM}  Just A Rather Very Intelligent System${OFF}"

if [ ! -d /data/data/com.termux ]; then
  warn "This does not look like Termux. The installer expects Termux on Android."
  printf '%s\n' "  On a desktop you can still run:  python3 jarvis.py --mock"
  exit 1
fi

say "Updating package index"
pkg update -y >/dev/null 2>&1 || warn "pkg update had warnings; continuing"

say "Installing system packages"
pkg install -y python termux-api termux-tools coreutils grep sed findutils >/dev/null
ok "python $(python3 -V 2>&1 | awk '{print $2}') + termux-api"

# Optional but genuinely useful
for pkgname in curl git jq; do
  have "$pkgname" || pkg install -y "$pkgname" >/dev/null 2>&1 || true
done

if [ "$VOICE_EXTRAS" = "1" ]; then
  say "Installing optional voice extras (may take a while)"
  pkg install -y ffmpeg sox >/dev/null 2>&1 || warn "ffmpeg/sox unavailable; cloud-free wake word stays disabled"
fi

say "Checking Termux:API bridge"
if ! have termux-battery-status; then
  warn "termux-battery-status missing — install the Termux:API app from F-Droid, then re-run."
fi
if termux-battery-status >/dev/null 2>&1; then
  ok "Termux:API responding"
  termux-toast -g middle "JARVIS installed" >/dev/null 2>&1 || true
else
  warn "Termux:API app not responding. Install 'Termux:API' (same source as Termux), then:"
  printf '   %s\n' "termux-battery-status"
fi

chmod +x jarvis.py scripts/*.sh scripts/service/run 2>/dev/null || true
mkdir -p data logs

if [ ! -f config.json ]; then
  cp config.example.json config.json 2>/dev/null || python3 - <<'PY'
import json, pathlib
pathlib.Path("config.json").write_text(json.dumps({
    "provider": {"name": "ollama", "model": "llama3.1", "base_url": "", "api_key": "",
                 "temperature": 0.5, "max_tokens": 1024, "tool_mode": "auto"},
    "user": "sir", "voice": False, "voice_output": True, "wake_word": "jarvis",
    "max_steps": 12, "confirm_danger": True, "history_turns": 12,
}, indent=2) + "\n")
PY
  chmod 600 config.json
  ok "config.json created"
fi

say "Checking the AI backend"
python3 - <<'PY'
import json, os, urllib.request
cfg = json.load(open("config.json"))
p = cfg.get("provider", {})
name, model = p.get("name", "ollama"), p.get("model", "llama3.1")
base = p.get("base_url") or {
    "ollama": "http://127.0.0.1:11434/v1",
    "llamacpp": "http://127.0.0.1:8080/v1",
    "lmstudio": "http://127.0.0.1:1234/v1",
}.get(name)
if name in ("ollama", "llamacpp", "lmstudio"):
    try:
        urllib.request.urlopen(base.replace("/v1", "") + "/v1/models", timeout=2)
        print(f"  local model server up at {base}")
    except Exception:
        print(f"  no local server at {base} yet")
        if name == "ollama":
            print("  to use a free local model:  pkg install ollama && ollama serve &")
            print("                              ollama pull llama3.1")
        print("  or pick a cloud provider:   python jarvis.py --provider groq --model llama-3.3-70b-versatile")
else:
    env = {"openai": "OPENAI_API_KEY", "groq": "GROQ_API_KEY", "openrouter": "OPENROUTER_API_KEY",
           "anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY", "deepseek": "DEEPSEEK_API_KEY",
           "mistral": "MISTRAL_API_KEY", "xai": "XAI_API_KEY"}.get(name, "")
    if p.get("api_key") or (env and os.environ.get(env)):
        print(f"  {name} key present")
    else:
        print(f"  no key for {name}. set it with:  export {env}=...")
PY

if [ "$SETUP" = "1" ]; then
  say "Guided setup"
  python3 jarvis.py --setup || warn "setup skipped; edit config.json by hand"
fi

say "Running self-test"
if python3 jarvis.py --selftest; then
  ok "all subsystems nominal"
else
  warn "self-test reported problems (see above)"
fi

cat <<EOF

${BOLD}  Ready.${OFF} Commands:

    python jarvis.py                      talk to JARVIS
    python jarvis.py --voice              hands-free ("jarvis, open spotify")
    python jarvis.py "jarvis, battery"    one-shot (wake word required)
    python jarvis.py --start              background service, survives closing Termux
    python jarvis.py --status             service state
    python jarvis.py --diag               health check
    python jarvis.py --tools              see what I can do (83 tools)

${BOLD}  Keep it running without Termux open${OFF}
    pkg install termux-services
    bash scripts/service-setup.sh
    mkdir -p ~/.termux/boot && cp scripts/boot.sh ~/.termux/boot/50-jarvis

${DIM}  Termux:API app permissions needed for the full experience:${OFF}
    Microphone, Camera, SMS, Phone, Contacts, Location, Storage
${DIM}  Grant them in Android Settings > Apps > Termux:API > Permissions.
  Set Termux battery usage to "Unrestricted" or Android will kill voice mode.${OFF}

EOF
