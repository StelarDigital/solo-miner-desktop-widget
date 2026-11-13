#!/bin/bash
set -euo pipefail

AGENTS="$HOME/Library/LaunchAgents"
UID_N="$(id -u)"
for label in com.solominer.widget-server com.solominer.widget-window; do
  launchctl bootout "gui/$UID_N/$label" 2>/dev/null || true
  # removes only the plist file install.sh wrote for this label
  rm -f "$AGENTS/$label.plist"
done

echo "Uninstalled. Your widget/miners.json and this folder were left in place."
