#!/bin/zsh
# Switch the local reranker scorer between registered checkpoints.
#
#   switch_qwen_model.command qwen35-0.8b   # Qwen3.5-0.8B-MLX-4bit (default)
#   switch_qwen_model.command qwen3-0.6b    # Qwen3-0.6B-4bit
#
# The switch is fail-closed: the target checkpoint's content fingerprint is
# verified against scorer_models.zsh before anything changes.  The Rime Lua
# profile is swapped in the same step so the client-side model_sha256 always
# matches the served model; after switching, redeploy Squirrel once so the
# Lua module reloads the new profile.
set -euo pipefail

script_dir="${0:A:h}"
source "$script_dir/scorer_models.zsh"

current_file="$script_dir/${SCORER_SELECTION_FILE:-model-selection}"
current=""
[[ -f "$current_file" ]] && current="$(<"$current_file")"
current="${current//[[:space:]]/}"
[[ -z "$current" ]] && current="${SCORER_DEFAULT_MODEL:-qwen35-0.8b}"

selection="${1:-}"
if [[ -z "$selection" ]]; then
  print "active model: $current"
  print "usage: ${0:t} [${(k)SCORER_MODEL_DIR}]"
  exit 2
fi
if [[ -z "${SCORER_MODEL_DIR[$selection]:-}" ]]; then
  print -u2 "unknown selection: $selection (expected one of: ${(k)SCORER_MODEL_DIR})"
  exit 1
fi
if [[ "$selection" == "$current" ]]; then
  print "already on $selection; nothing to do"
  exit 0
fi

model_dir="$script_dir/${SCORER_MODEL_DIR[$selection]}"
expected_sha="${SCORER_MODEL_SHA[$selection]}"
python_bin="${MOHU_QWEN35_PYTHON:-$script_dir/.venv/bin/python}"

if [[ ! -d "$model_dir" ]]; then
  print -u2 "model directory is missing: $model_dir"
  exit 1
fi
if [[ ! -x "$python_bin" ]]; then
  print -u2 "scorer Python runtime is missing: $python_bin"
  exit 1
fi

# Verify the checkpoint fingerprint before touching the live service.
actual_sha="$(cd "$script_dir" && "$python_bin" - "$model_dir" <<'EOF'
import sys
import qwen35_scorer

print(qwen35_scorer.compute_model_fingerprint(sys.argv[1]))
EOF
)"
if [[ "$actual_sha" != "$expected_sha" ]]; then
  print -u2 "model fingerprint mismatch for $selection"
  print -u2 "expected $expected_sha"
  print -u2 "actual   $actual_sha"
  exit 1
fi

profile_source="$script_dir/${SCORER_MODEL_PROFILE[$selection]}"
lua_dir="$HOME/Library/Rime/lua"
if [[ ! -f "$profile_source" ]]; then
  print -u2 "profile variant is missing: $profile_source"
  exit 1
fi

printf '%s\n' "$selection" > "${current_file}.tmp"
mv "${current_file}.tmp" "$current_file"

mkdir -p "$lua_dir"
cp "$profile_source" "$lua_dir/mohu_tiger_reranker_profile.lua"

label="com.fuchuxuan.mohu.qwen35-reranker"
uid="$(id -u)"
launchctl bootout "gui/$uid/$label" >/dev/null 2>&1 || true
launchctl kickstart -k "gui/$uid/$label" 2>/dev/null || \
  launchctl bootstrap "gui/$uid" "$HOME/Library/LaunchAgents/$label.plist"

print "switched scorer to $selection (${SCORER_MODEL_DIR[$selection]})"
print "profile deployed to $lua_dir/mohu_tiger_reranker_profile.lua"
print "now redeploy Squirrel (输入法菜单 → 重新部署) to reload the profile"
