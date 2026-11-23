#!/data/data/com.termux/files/usr/bin/bash
# Install JARVIS as a supervised runit service so it survives closing Termux,
# reboots and crashes.
#
#   bash scripts/service-setup.sh            install + enable + start
#   bash scripts/service-setup.sh --remove   uninstall
set -euo pipefail

SERVICE_DIR="${PREFIX:-/data/data/com.termux/files/usr}/var/service"
NAME="jarvis"
APP_DIR="${JARVIS_HOME:-$HOME/jarvis}"

if [ "${1:-}" = "--remove" ]; then
  sv down "$NAME" 2>/dev/null || true
  rm -f "$SERVICE_DIR/$NAME/run"
  rmdir "$SERVICE_DIR/$NAME" 2>/dev/null || true
  termux-wake-unlock 2>/dev/null || true
  echo "removed $NAME service"
  exit 0
fi

if ! command -v sv >/dev/null 2>&1; then
  echo "termux-services is not installed. Run:"
  echo "  pkg install termux-services"
  echo "  # then restart Termux once so runit starts"
  exit 1
fi

mkdir -p "$SERVICE_DIR/$NAME"
install -m 700 "$APP_DIR/scripts/service/run" "$SERVICE_DIR/$NAME/run"
sed -i "s#cd \"\$HOME/jarvis\"#cd \"$APP_DIR\"#" "$SERVICE_DIR/$NAME/run" 2>/dev/null || true

termux-wake-lock 2>/dev/null || true
sv-enable "$NAME" >/dev/null 2>&1 || ln -sf "$SERVICE_DIR/$NAME" "$SERVICE_DIR/../service/enabled/$NAME" 2>/dev/null || true
sv up "$NAME" 2>/dev/null || true

sleep 1
if sv status "$NAME" 2>/dev/null | grep -q "run:"; then
  echo "JARVIS is supervised by runit (auto-restarts, survives Termux closing)."
  echo "  sv status $NAME     # state"
  echo "  sv restart $NAME    # after editing its code"
  echo "  sv down $NAME       # stop"
else
  echo "service installed but not confirmed running; check: sv status $NAME"
fi
