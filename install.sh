#!/bin/bash
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
LABELS=(com.solominer.widget-server com.solominer.widget-window)

[ "$(uname -s)" = "Darwin" ] || { echo "This widget needs macOS." >&2; exit 1; }
command -v swiftc >/dev/null || { echo "swiftc not found. Run: xcode-select --install" >&2; exit 1; }
if [ -x /usr/bin/python3 ]; then PYTHON=/usr/bin/python3
else
  PYTHON="$(command -v python3)" || { echo "python3 not found." >&2; exit 1; }
  PYTHON="$("$PYTHON" -c 'import os,sys; print(os.path.realpath(sys.executable))')"
fi
case "$REPO$HOME" in *[\&\|\\]*) echo "Path contains a character this installer cannot handle (& | \\)." >&2; exit 1;; esac

swiftc -O -o "$REPO/widget/miner_widget_window" "$REPO/widget/miner_widget_window.swift"

if [ ! -f "$REPO/widget/miners.json" ]; then
  cp "$REPO/widget/miners.example.json" "$REPO/widget/miners.json"
  echo "Created widget/miners.json - edit it and put your miner's IP address in it."
fi

mkdir -p "$AGENTS"
UID_N="$(id -u)"
for label in "${LABELS[@]}"; do
  plist="$AGENTS/$label.plist"
  sed -e "s|@REPO@|$REPO|g" -e "s|@PYTHON@|$PYTHON|g" "$REPO/launchagents/$label.plist.template" > "$plist"
  launchctl bootout "gui/$UID_N/$label" 2>/dev/null || true
  sleep 1
  ok=0
  for _ in 1 2 3; do
    if launchctl bootstrap "gui/$UID_N" "$plist"; then ok=1; break; fi
    sleep 2
  done
  [ "$ok" = 1 ] || { echo "Could not load $label." >&2; exit 1; }
done

echo "Installed. The widget appears at the top right of your screen."
echo "If it shows 'not answering', allow Local Network access: System Settings > Privacy & Security > Local Network."
