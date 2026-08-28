#!/bin/zsh
# Install the user-scoped launchd job for the local Qwen3.5 scorer.
set -euo pipefail

script_dir="${0:A:h}"
launcher="$script_dir/run_qwen35_scorer.command"
label="com.fuchuxuan.mohu.qwen35-reranker"
plist="$HOME/Library/LaunchAgents/$label.plist"
log_dir="$script_dir/logs"

if [[ ! -x "$launcher" ]]; then
  print -u2 "scorer launcher is missing or not executable: $launcher"
  exit 1
fi
if [[ ! -x "$script_dir/.venv/bin/python" ]]; then
  print -u2 "scorer Python runtime is missing: $script_dir/.venv/bin/python"
  exit 1
fi
# The supervisor validates the current selection and retries unavailable models
# in place, so installation remains possible before checkpoints are present.
source "$script_dir/scorer_models.zsh"

mkdir -p "$HOME/Library/LaunchAgents" "$log_dir"
chmod 700 "$log_dir"

cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array><string>$launcher</string></array>
  <key>WorkingDirectory</key><string>$script_dir</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key><string>$HOME</string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$log_dir/scorer.stdout.log</string>
  <key>StandardErrorPath</key><string>$log_dir/scorer.stderr.log</string>
</dict>
</plist>
EOF
chmod 600 "$plist"

uid="$(id -u)"
launchctl bootout "gui/$uid" "$plist" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$uid" "$plist"
launchctl kickstart -k "gui/$uid/$label"
print "installed and started $label"
