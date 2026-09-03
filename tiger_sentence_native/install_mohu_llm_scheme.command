#!/bin/zsh
# Shared installer for the scheme-specific 魔虎大模型 packages.
set -euo pipefail

script_dir="${0:A:h}"
scheme="${MOHU_LLM_SCHEME:-}"
if [[ "$scheme" != "zrm" && "$scheme" != "flypy" ]]; then
  print -u2 "MOHU_LLM_SCHEME must be zrm or flypy"
  exit 2
fi

rime_dir="${MOHU_RIME_DIR:-$HOME/Library/Rime}"
squirrel_bin="${MOHU_SQUIRREL_BIN:-/Library/Input Methods/Squirrel.app/Contents/MacOS/Squirrel}"
manifest="$script_dir/package.json"
[[ -f "$manifest" ]] || manifest="$script_dir/mohu_llm_${scheme}.package.json"
schema_id="mohu_llm_${scheme}"
schema_file="mohu_llm_${scheme}.schema.yaml"

[[ -f "$manifest" ]] || { print -u2 "missing package manifest: $manifest"; exit 1; }
[[ -f "$script_dir/$schema_file" ]] || { print -u2 "missing package schema: $script_dir/$schema_file"; exit 1; }
[[ -d "$script_dir/base" ]] || { print -u2 "missing package base directory"; exit 1; }
[[ -f "$script_dir/base/default.yaml" ]] || { print -u2 "missing base default.yaml"; exit 1; }
[[ -f "$script_dir/base/mohu_${scheme}.schema.yaml" ]] || { print -u2 "missing base scheme: mohu_${scheme}.schema.yaml"; exit 1; }
[[ -d "$script_dir/lua" ]] || { print -u2 "missing package lua directory"; exit 1; }
[[ -d "$script_dir/runtime" ]] || { print -u2 "missing package runtime directory"; exit 1; }
[[ -d "$script_dir/data/$scheme" ]] || { print -u2 "missing package data directory: data/$scheme"; exit 1; }

# Validate the small JSON manifest when a Python runtime is available.  The
# scorer packages already use Python, but installation remains possible on a
# clean machine where Python is not yet installed.
if command -v python3 >/dev/null 2>&1; then
  python3 - "$manifest" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    payload = json.load(stream)
if not isinstance(payload, dict):
    raise SystemExit("manifest must be a JSON object")
PY
fi
grep -Fq '"package_type": "mohu_llm"' "$manifest" || { print -u2 "invalid package type"; exit 1; }
grep -Fq "\"scheme\": \"$scheme\"" "$manifest" || { print -u2 "manifest scheme mismatch"; exit 1; }
grep -Fq "\"schema_id\": \"$schema_id\"" "$manifest" || { print -u2 "manifest schema mismatch"; exit 1; }
grep -Fq "\"schema\": \"$schema_file\"" "$manifest" || { print -u2 "manifest schema file mismatch"; exit 1; }
grep -Fq '"base_dir": "base"' "$manifest" || { print -u2 "manifest base directory mismatch"; exit 1; }
grep -Fq "\"data_dir\": \"data/$scheme\"" "$manifest" || { print -u2 "manifest data directory mismatch"; exit 1; }
grep -Fq '"runtime_dir": "runtime"' "$manifest" || { print -u2 "manifest runtime directory mismatch"; exit 1; }

required_lua=(
  mohu_llm_runtime.lua mohu_sentence.lua mohu_tiger_sentence.lua mohu_personal_lexicon.lua mohu_tiger_reranker.lua
  mohu_tiger_model_catalog.lua mohu_tiger_model_menu.lua option_sync.lua option_state.lua
)
required_runtime=(
  libtigerengine.dylib qwen35_scorer.py run_qwen35_scorer.command
  install_qwen35_launch_agent.command scorer_models.zsh switch_qwen_model.command
  mohu_tiger_reranker_profile.lua mohu_tiger_reranker_profile_qwen3_06b.lua
)
required_data=(data/sentence-ngram-mobile.bin)
manifest_declares() {
  local filename="$1"
  grep -Fq "\"$filename\"" "$manifest"
}
for filename in "${required_lua[@]}"; do
  manifest_declares "$filename" || { print -u2 "manifest does not declare Lua file: $filename"; exit 1; }
  [[ -f "$script_dir/lua/$filename" ]] || { print -u2 "missing Lua file: $filename"; exit 1; }
done
for filename in "${required_runtime[@]}"; do
  manifest_declares "$filename" || { print -u2 "manifest does not declare runtime file: $filename"; exit 1; }
  [[ -f "$script_dir/runtime/$filename" ]] || { print -u2 "missing runtime file: $filename"; exit 1; }
done
for filename in run_qwen35_scorer.command install_qwen35_launch_agent.command switch_qwen_model.command; do
  [[ -x "$script_dir/runtime/$filename" ]] || { print -u2 "runtime file is not executable: $filename"; exit 1; }
done
for filename in "${required_data[@]}"; do
  manifest_declares "$filename" || { print -u2 "manifest does not declare data file: $filename"; exit 1; }
  [[ -f "$script_dir/$filename" ]] || { print -u2 "missing data file: $filename"; exit 1; }
done
lexicon="$script_dir/data/$scheme/mohu_llm_${scheme}.lexicon.txt"
manifest_declares "data/$scheme/mohu_llm_${scheme}.lexicon.txt" || { print -u2 "manifest does not declare lexicon"; exit 1; }
[[ -s "$lexicon" ]] || { print -u2 "missing or empty lexicon: $lexicon"; exit 1; }
for manifest_name in qwen35-0.8b.manifest qwen3-0.6b.manifest; do
  [[ -s "$script_dir/models/$manifest_name" ]] || {
    print -u2 "package model manifest is missing: $manifest_name"
    exit 1
  }
done

mkdir -p "$rime_dir/lua" "$rime_dir/mohu_llm/runtime" "$rime_dir/mohu_llm/data/$scheme" \
  "$rime_dir/mohu_llm/models" "$rime_dir/mohu_llm/config"

copy_atomic() {
  local source="$1" destination="$2" mode="${3:-0644}" temporary
  [[ -f "$source" ]] || { print -u2 "package file is missing: $source"; exit 1; }
  temporary="$(mktemp "${destination}.tmp.XXXXXX")"
  install -m "$mode" "$source" "$temporary"
  mv -f "$temporary" "$destination"
}

is_user_maintained() {
  local relative="$1"
  case "$relative" in
    default.yaml|mohu.yaml|mohu_${scheme}_custom_phrases.txt|mohu_${scheme}.extended.dict.yaml|lua/mohu_processor.lua|lua/four_code_yield_pairs_${scheme}.txt) return 0 ;;
  esac
  return 1
}

copy_tree() {
  local source_dir="$1" destination_dir="$2" preserve_user_files="${3:-0}" relative_prefix="${4:-}" source relative mode
  while IFS= read -r -d '' source; do
    relative="${source#$source_dir/}"
    [[ "$relative" == */__pycache__/* || "$relative" == *.pyc ]] && continue
    if [[ "$preserve_user_files" == 1 && -f "$destination_dir/$relative" ]] && \
      is_user_maintained "$relative_prefix$relative"; then
      continue
    fi
    mode=0644
    [[ "$relative" == *.command ]] && mode=0755
    mkdir -p "$destination_dir/${relative:h}"
    copy_atomic "$source" "$destination_dir/$relative" "$mode"
  done < <(find "$source_dir" -type f -print0)
}

copy_tree "$script_dir/base" "$rime_dir" 1
copy_atomic "$script_dir/$schema_file" "$rime_dir/$schema_file"
copy_tree "$script_dir/lua" "$rime_dir/lua" 1 "lua/"
copy_tree "$script_dir/runtime" "$rime_dir/mohu_llm/runtime"
copy_tree "$script_dir/data/$scheme" "$rime_dir/mohu_llm/data/$scheme"
for filename in "${required_data[@]}"; do
  copy_atomic "$script_dir/$filename" "$rime_dir/mohu_llm/$filename"
done

# Manifests are shared metadata; copying them is harmless and lets /model
# discover whichever Qwen checkpoints the user has installed.
if [[ -d "$script_dir/models" ]]; then
  copy_tree "$script_dir/models" "$rime_dir/mohu_llm/models"
fi

selection="$rime_dir/mohu_llm/config/model-selection"
if [[ ! -f "$selection" ]]; then
  temporary="$(mktemp "${selection}.tmp.XXXXXX")"
  print -r -- qwen35-0.8b > "$temporary"
  mv -f "$temporary" "$selection"
fi

custom="$rime_dir/default.custom.yaml"
schema_registered() {
  [[ -f "$custom" ]] || return 1
  grep -Ev '^[[:space:]]*#' "$custom" | sed -E 's/[[:space:]]+#.*$//' | \
    grep -Eq "(^[[:space:]]*-[[:space:]]*schema:[[:space:]]*$schema_id([[:space:]} ,]|$)|[{,][[:space:]]*schema:[[:space:]]*$schema_id([[:space:]} ,]|$))"
}

append_block() {
  local destination="$1"
  printf '\npatch:\n  schema_list/+:\n    - schema: %s\n' "$schema_id" >> "$destination"
}

if [[ ! -f "$custom" ]]; then
  temporary="$(mktemp "${custom}.tmp.XXXXXX")"
  printf 'patch:\n  schema_list/+:\n    - schema: %s\n' "$schema_id" > "$temporary"
  mv -f "$temporary" "$custom"
elif ! schema_registered; then
  temporary="$(mktemp "${custom}.tmp.XXXXXX")"
  if grep -Eq 'schema_list/\+:[[:space:]]*\[[[:space:]]*\]' "$custom"; then
    sed -E "s|(schema_list/\+:[[:space:]]*)\[[[:space:]]*\]|\1[{schema: $schema_id}]|" "$custom" > "$temporary"
  elif grep -Eq 'schema_list/\+:[[:space:]]*\[' "$custom"; then
    sed -E "s|(schema_list/\+:[[:space:]]*\[[^]]*)\]|\1, {schema: $schema_id}]|" "$custom" > "$temporary"
  elif grep -Eq '^[[:space:]]*patch:[[:space:]]*\{.*\}[[:space:]]*(#.*)?$' "$custom"; then
    if grep -Eq '^[[:space:]]*patch:[[:space:]]*\{[[:space:]]*\}[[:space:]]*(#.*)?$' "$custom"; then
      sed -E "s|^([[:space:]]*patch:[[:space:]]*)\{[[:space:]]*\}([[:space:]]*(#.*))?$|\1{schema_list/+: [{schema: $schema_id}]}\2|" "$custom" > "$temporary"
    else
      sed -E "s|^([[:space:]]*patch:[[:space:]]*\{)(.*)\}([[:space:]]*(#.*))?$|\1\2, schema_list/+: [{schema: $schema_id}]\}\3|" "$custom" > "$temporary"
    fi
  else
    cp "$custom" "$temporary"
    if grep -Eq '^patch:[[:space:]]*(#.*)?$' "$custom"; then
      if grep -Eq '^[[:space:]]+schema_list/\+:' "$custom"; then
        awk -v id="$schema_id" '
          function indent(line) { match(line, /^[[:space:]]*/); return RLENGTH }
          /^[[:space:]]*schema_list\/\+:/ { in_list = 1; list_indent = indent($0) }
          in_list && !added && $0 !~ /^[[:space:]]*schema_list\/\+:/ && $0 !~ /^[[:space:]]*#/ && $0 !~ /^[[:space:]]*$/ && indent($0) <= list_indent { print "    - schema: " id; added = 1; in_list = 0 }
          { print }
          END { if (in_list && !added) print "    - schema: " id }
        ' "$custom" > "$temporary"
      else
        awk -v id="$schema_id" '
          !added && /^patch:[[:space:]]*(#.*)?$/ { print; print "  schema_list/+:"; print "    - schema: " id; added = 1; next }
          { print }
        ' "$custom" > "$temporary"
      fi
    else
      append_block "$temporary"
    fi
  fi
  mv -f "$temporary" "$custom"
fi

if [[ "${MOHU_SKIP_SCORER_INSTALL:-0}" != 1 && -x "$rime_dir/mohu_llm/runtime/install_qwen35_launch_agent.command" ]]; then
  "$rime_dir/mohu_llm/runtime/install_qwen35_launch_agent.command" >/dev/null || \
    print "模型 scorer 尚未启动：请先安装 Python/MLX 和任一 Qwen 模型。"
fi
if [[ -x "$squirrel_bin" ]]; then
  "$squirrel_bin" --reload >/dev/null 2>&1 || true
fi
print "魔虎大模型·$scheme 已安装；可在方案列表选择对应方案。"
