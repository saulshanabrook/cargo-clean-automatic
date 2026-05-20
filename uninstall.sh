#!/bin/zsh
set -eu

label="com.saulshanabrook.cargo-clean-automatic"
plist="$HOME/Library/LaunchAgents/$label.plist"

launchctl bootout "gui/$(id -u)" "$plist" >/dev/null 2>&1 || true
launchctl unload "$plist" >/dev/null 2>&1 || true

if [[ -f "$plist" ]]; then
  rm "$plist"
fi

echo "Uninstalled $label"
