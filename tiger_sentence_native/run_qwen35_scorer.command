#!/bin/zsh
# User-scoped supervisor for the local Qwen scorer. The supervisor owns one
# Python child at a time and reloads it when tiger/model-selection changes.
set -euo pipefail

script_dir="${0:A:h}"
python_bin="${MOHU_QWEN35_PYTHON:-$script_dir/.venv/bin/python}"
socket_path="${MOHU_QWEN35_SOCKET:-$script_dir/qwen35-reranker.sock}"
selection_path="${MOHU_SCORER_SELECTION_PATH:-$script_dir/model-selection}"
poll_interval="${MOHU_SCORER_POLL_INTERVAL:-2}"

if [[ ! "$poll_interval" == <->(|.<->) || "$poll_interval" == 0 ]]; then
  poll_interval=2
fi
if (( poll_interval < 0.05 )); then poll_interval=0.05; fi
if (( poll_interval > 60 )); then poll_interval=60; fi

source "$script_dir/scorer_models.zsh"
default_model="${SCORER_DEFAULT_MODEL:-qwen35-0.8b}"
child_pid=""
child_selection=""

stop_child() {
  if [[ -n "$child_pid" ]] && kill -0 "$child_pid" 2>/dev/null; then
    kill -TERM "$child_pid" 2>/dev/null || true
    for _ in {1..20}; do
      kill -0 "$child_pid" 2>/dev/null || break
      sleep 0.05
    done
    kill -KILL "$child_pid" 2>/dev/null || true
  fi
  if [[ -n "$child_pid" ]]; then wait "$child_pid" 2>/dev/null || true; fi
  child_pid=""
  child_selection=""
}
handle_signal() { stop_child; exit 0; }
trap stop_child EXIT
trap handle_signal INT TERM HUP

read_selection() {
  local value
  if [[ ! -e "$selection_path" ]]; then
    print -r -- "$default_model"
    return 0
  fi
  if [[ ! -f "$selection_path" || ! -r "$selection_path" ]]; then return 1; fi
  value="$(<"$selection_path")"
  value="${value//$'\n'/}"
  value="${value//$'\r'/}"
  value="${value//[[:space:]]/}"
  [[ -n "$value" ]] || return 1
  print -r -- "$value"
}

start_child() {
  local selection="$1" model_dir expected_sha
  model_dir="$script_dir/${SCORER_MODEL_DIR[$selection]:-}"
  expected_sha="${SCORER_MODEL_SHA[$selection]:-}"
  if [[ -z "${SCORER_MODEL_DIR[$selection]:-}" || -z "$expected_sha" ]]; then
    print -u2 "unknown scorer model selection: $selection"
    return 1
  fi
  if [[ ! -x "$python_bin" ]]; then
    print -u2 "Qwen scorer Python runtime is missing: $python_bin"
    return 1
  fi
  if [[ ! -d "$model_dir" ]]; then
    print -u2 "scorer model directory is missing: $model_dir"
    return 1
  fi
  print -u2 "starting scorer model $selection ($model_dir)"
  "$python_bin" "$script_dir/qwen35_scorer.py" \
    --model "$model_dir" \
    --socket "$socket_path" \
    --idle-timeout 0 \
    --warmup \
    --expected-sha256 "$expected_sha" &
  child_pid=$!
  child_selection="$selection"
}

while true; do
  selection="$(read_selection 2>/dev/null || true)"
  if [[ -z "$selection" ]]; then
    [[ -n "$child_pid" ]] && stop_child
    sleep "$poll_interval"
    continue
  fi
  if [[ "$selection" != "$child_selection" ]]; then
    [[ -n "$child_pid" ]] && stop_child
    start_child "$selection" || true
  elif [[ -n "$child_pid" ]] && ! kill -0 "$child_pid" 2>/dev/null; then
    wait "$child_pid" 2>/dev/null || true
    child_pid=""
    start_child "$selection" || true
  fi
  sleep "$poll_interval"
done
