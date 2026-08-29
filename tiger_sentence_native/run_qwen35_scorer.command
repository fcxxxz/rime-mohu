#!/bin/zsh
# User-scoped supervisor for the local Qwen scorer. The supervisor owns one
# Python child at a time and reloads it when mohu_llm/config/model-selection changes.
set -euo pipefail

script_dir="${0:A:h}"
python_bin="${MOHU_QWEN35_PYTHON:-}"
if [[ -z "$python_bin" && -x "$script_dir/.venv/bin/python" ]]; then
  python_bin="$script_dir/.venv/bin/python"
fi
if [[ -z "$python_bin" ]]; then
  python_bin="$(command -v python3 2>/dev/null || true)"
fi
socket_path="${MOHU_QWEN35_SOCKET:-$script_dir/qwen35-reranker.sock}"
selection_path="${MOHU_SCORER_SELECTION_PATH:-$script_dir/../config/model-selection}"
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
shutdown_requested=0

stop_pid() {
  local pid="$1"
  if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then return; fi
  kill -TERM "$pid" 2>/dev/null || true
  for _ in {1..20}; do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.05
  done
  kill -KILL "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
}

stop_child() {
  if [[ -n "$child_pid" ]]; then
    stop_pid "$child_pid"
  fi
  child_pid=""
  child_selection=""
}
defer_signal() { shutdown_requested=1; }
handle_signal() { shutdown_requested=1; stop_child; exit 0; }
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
  # Defer termination while the background job is being created and $! is
  # captured.  A signal in this tiny window must not orphan the scorer.
  shutdown_requested=0
  trap defer_signal INT TERM HUP
  "$python_bin" "$script_dir/qwen35_scorer.py" \
    --model "$model_dir" \
    --socket "$socket_path" \
    --idle-timeout 0 \
    --warmup \
    --expected-sha256 "$expected_sha" &
  child_pid=$!
  child_selection="$selection"
  trap handle_signal INT TERM HUP
  if (( shutdown_requested )); then
    stop_child
    exit 0
  fi
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
