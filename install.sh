#!/bin/zsh
set -eu

label="com.saulshanabrook.cargo-clean-automatic"
repo_dir="$(cd "$(dirname "$0")" && pwd)"
runner="$repo_dir/bin/cargo-clean-automatic"
plist="$HOME/Library/LaunchAgents/$label.plist"
log_dir="$HOME/Library/Logs/cargo-clean-automatic"

root="${CARGO_CLEAN_ROOT:-$HOME}"
weekday="${CARGO_CLEAN_WEEKDAY:-1}"
hour="${CARGO_CLEAN_HOUR:-10}"
minute="${CARGO_CLEAN_MINUTE:-0}"

if [[ ! -x "$runner" ]]; then
  chmod +x "$runner"
fi

mkdir -p "$HOME/Library/LaunchAgents" "$log_dir"

cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$label</string>

  <key>ProgramArguments</key>
  <array>
    <string>$runner</string>
    <string>--root</string>
    <string>$root</string>
  </array>

  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key>
    <integer>$weekday</integer>
    <key>Hour</key>
    <integer>$hour</integer>
    <key>Minute</key>
    <integer>$minute</integer>
  </dict>

  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>

  <key>StandardOutPath</key>
  <string>$log_dir/launchd.out.log</string>

  <key>StandardErrorPath</key>
  <string>$log_dir/launchd.err.log</string>
</dict>
</plist>
EOF

if launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1; then
  launchctl bootout "gui/$(id -u)" "$plist" >/dev/null 2>&1 || true
fi

if ! launchctl bootstrap "gui/$(id -u)" "$plist" >/dev/null 2>&1; then
  launchctl unload "$plist" >/dev/null 2>&1 || true
  launchctl load -w "$plist"
fi

echo "Installed $label"
echo "Plist: $plist"
echo "Schedule: weekday=$weekday hour=$hour minute=$minute"
echo "Root: $root"
echo "Logs: $log_dir"
echo ""
echo "To test without deleting anything:"
echo "  $runner --root \"$root\" --dry-run --no-popup"
echo ""
echo "To run once now:"
echo "  launchctl kickstart -k gui/$(id -u)/$label"
