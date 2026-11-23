#!/data/data/com.termux/files/usr/bin/bash
# Termux:Widget launcher.
#   cp scripts/widget.sh ~/.shortcuts/JARVIS
#   chmod +x ~/.shortcuts/JARVIS
# Tap the widget: type an instruction, JARVIS answers by voice + notification.
set -uo pipefail
APP_DIR="${JARVIS_HOME:-$HOME/jarvis}"
cd "$APP_DIR" || { termux-toast "jarvis: $APP_DIR not found"; exit 1; }

if [ "$#" -gt 0 ]; then
  PROMPT="$*"
else
  PROMPT=$(termux-dialog text -t "JARVIS" -i "what shall I do?" | sed -n 's/.*"text": *"\(.*\)".*/\1/p')
fi
[ -z "${PROMPT:-}" ] && exit 0

termux-toast -g middle "JARVIS: working"
termux-wake-lock 2>/dev/null || true

ANSWER=$(python3 jarvis.py --no-voice "$PROMPT" 2>&1)
termux-wake-unlock 2>/dev/null || true

termux-notification --title "JARVIS" --content "$ANSWER" --id 41 2>/dev/null || true
termux-tts-speak "$(printf '%s' "$ANSWER" | tr '\n' ' ' | cut -c1-400)" 2>/dev/null || true
