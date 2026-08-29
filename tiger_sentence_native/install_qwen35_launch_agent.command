#!/bin/zsh
# Install the user-scoped launchd job for the local Qwen3.5 scorer.
set -euo pipefail

script_dir="${0:A:h}"
launcher="$script_dir/run_qwen35_scorer.command"
python_bin="${MOHU_QWEN35_PYTHON:-}"
label="com.fuchuxuan.mohu.qwen35-reranker"
plist="$HOME/Library/LaunchAgents/$label.plist"
log_dir="$script_dir/logs"

if [[ ! -x "$launcher" ]]; then
  print -u2 "scorer launcher is missing or not executable: $launcher"
  exit 1
fi
if [[ -z "$python_bin" && -x "$script_dir/.venv/bin/python" ]]; then
  python_bin="$script_dir/.venv/bin/python"
fi
if [[ -z "$python_bin" ]]; then
  python_bin="$(command -v python3 2>/dev/null || true)"
fi
if [[ -z "$python_bin" || ! -x "$python_bin" ]]; then
  print -u2 "scorer Python runtime is missing; install Python 3 or set MOHU_QWEN35_PYTHON"
  exit 1
fi
if ! "$python_bin" -c 'import mlx_lm' >/dev/null 2>&1; then
  print -u2 "mlx_lm is unavailable in $python_bin; install mlx-lm (for example: uv pip install mlx-lm) or set MOHU_QWEN35_PYTHON"
  exit 1
fi
# The supervisor validates the current selection and retries unavailable models
# in place, so installation remains possible before checkpoints are present.
source "$script_dir/scorer_models.zsh"

mkdir -p "$HOME/Library/LaunchAgents" "$log_dir"
chmod 700 "$log_dir"

xml_escape() {
  local value="$1"
  value="${value//&/&amp;}"
  value="${value//</&lt;}"
  value="${value//>/&gt;}"
  value="${value//\"/&quot;}"
  value="${value//\'/&apos;}"
  print -r -- "$value"
}

script_dir_xml="$(xml_escape "$script_dir")"
launcher_xml="$(xml_escape "$launcher")"
home_xml="$(xml_escape "$HOME")"
log_dir_xml="$(xml_escape "$log_dir")"
python_bin_xml="$(xml_escape "$python_bin")"

cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array><string>$launcher_xml</string></array>
  <key>WorkingDirectory</key><string>$script_dir_xml</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key><string>$home_xml</string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>MOHU_QWEN35_PYTHON</key><string>$python_bin_xml</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$log_dir_xml/scorer.stdout.log</string>
  <key>StandardErrorPath</key><string>$log_dir_xml/scorer.stderr.log</string>
</dict>
</plist>
EOF
chmod 600 "$plist"

uid="$(id -u)"
launchctl bootout "gui/$uid" "$plist" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$uid" "$plist"
launchctl kickstart -k "gui/$uid/$label"
print "installed and started $label"
