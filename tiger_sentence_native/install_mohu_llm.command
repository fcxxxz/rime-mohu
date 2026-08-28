#!/bin/zsh
# Install the 魔虎大模型 addon without overwriting an existing user patch.
set -euo pipefail

script_dir="${0:A:h}"
rime_dir="${MOHU_RIME_DIR:-$HOME/Library/Rime}"
squirrel_bin="${MOHU_SQUIRREL_BIN:-/Library/Input Methods/Squirrel.app/Contents/MacOS/Squirrel}"

if [[ ! -f "$script_dir/mohu_tiger_sentence.schema.yaml" ||
      ! -d "$script_dir/lua" || ! -d "$script_dir/tiger" ]]; then
  print -u2 "invalid 魔虎大模型 addon directory: $script_dir"
  exit 1
fi

mkdir -p "$rime_dir/lua" "$rime_dir/tiger/models"

copy_atomic() {
  local source="$1" destination="$2" mode="${3:-0644}" temporary
  if [[ ! -f "$source" ]]; then
    print -u2 "addon file is missing: $source"
    exit 1
  fi
  temporary="$(mktemp "$destination.tmp.XXXXXX")"
  install -m "$mode" "$source" "$temporary"
  mv -f "$temporary" "$destination"
}

copy_atomic "$script_dir/mohu_tiger_sentence.schema.yaml" \
  "$rime_dir/mohu_tiger_sentence.schema.yaml"
for filename in \
  mohu_tiger_sentence.lua mohu_tiger_reranker.lua mohu_tiger_reranker_profile.lua \
  mohu_tiger_model_catalog.lua mohu_tiger_model_menu.lua option_sync.lua option_state.lua; do
  copy_atomic "$script_dir/lua/$filename" "$rime_dir/lua/$filename"
done
for filename in \
  qwen35_scorer.py run_qwen35_scorer.command install_qwen35_launch_agent.command \
  scorer_models.zsh switch_qwen_model.command mohu_tiger_reranker_profile.lua \
  mohu_tiger_reranker_profile_qwen3_06b.lua libtigerengine.dylib \
  mohu_tiger.lexicon.txt sentence-ngram-mobile.bin; do
  mode=0644
  [[ "$filename" == *.command || "$filename" == "libtigerengine.dylib" ]] && mode=0755
  copy_atomic "$script_dir/tiger/$filename" "$rime_dir/tiger/$filename" "$mode"
done
for filename in README.md qwen35-0.8b.manifest qwen3-0.6b.manifest; do
  copy_atomic "$script_dir/tiger/models/$filename" "$rime_dir/tiger/models/$filename"
done

if [[ ! -f "$rime_dir/tiger/model-selection" ]]; then
  temporary="$(mktemp "$rime_dir/tiger/model-selection.tmp.XXXXXX")"
  printf '%s\n' qwen35-0.8b > "$temporary"
  mv -f "$temporary" "$rime_dir/tiger/model-selection"
fi

custom="$rime_dir/default.custom.yaml"

schema_registered() {
  grep -Ev '^[[:space:]]*#' "$custom" |
    sed -E 's/[[:space:]]+#.*$//' |
    grep -Eq '(^[[:space:]]*-[[:space:]]*schema:[[:space:]]*mohu_tiger_sentence([[:space:]} ,]|$)|[{,][[:space:]]*schema:[[:space:]]*mohu_tiger_sentence([[:space:]} ,]|$))'
}

patch_inline() {
  local source="$1" destination="$2"
  if grep -Eq '^[[:space:]]*patch:[[:space:]]*\{[[:space:]]*\}[[:space:]]*(#.*)?$' "$source"; then
    sed -E 's|^([[:space:]]*patch:[[:space:]]*)\{[[:space:]]*\}([[:space:]]*(#.*))?$|\1{schema_list/+: [{schema: mohu_tiger_sentence}]}\2|' "$source" > "$destination"
  else
    sed -E 's|^([[:space:]]*patch:[[:space:]]*\{)(.*)\}([[:space:]]*(#.*))?$|\1\2, schema_list/+: [{schema: mohu_tiger_sentence}]}\3|' "$source" > "$destination"
  fi
}

if [[ ! -f "$custom" ]]; then
  temporary="$(mktemp "$custom.tmp.XXXXXX")"
  printf '%s\n' 'patch:' '  schema_list/+:' '    - schema: mohu_tiger_sentence' > "$temporary"
  mv -f "$temporary" "$custom"
elif ! schema_registered; then
  temporary="$(mktemp "$custom.tmp.XXXXXX")"
  if grep -Eq '^[[:space:]]*patch:[[:space:]]*\{.*\}[[:space:]]*(#.*)?$' "$custom"; then
    patch_inline "$custom" "$temporary"
  else
    cp "$custom" "$temporary"
  fi
  if grep -Eq '^patch:[[:space:]]*$' "$custom"; then
    printf '\n  schema_list/+:\n    - schema: mohu_tiger_sentence\n' >> "$temporary"
  elif ! grep -Eq '^[[:space:]]*patch:[[:space:]]*\{.*\}[[:space:]]*(#.*)?$' "$custom"; then
    printf '\npatch:\n  schema_list/+:\n    - schema: mohu_tiger_sentence\n' >> "$temporary"
  fi
  mv -f "$temporary" "$custom"
fi

if [[ -x "$rime_dir/tiger/install_qwen35_launch_agent.command" &&
      -x "$rime_dir/tiger/.venv/bin/python" ]]; then
  "$rime_dir/tiger/install_qwen35_launch_agent.command" >/dev/null
else
  print "模型 scorer 尚未启动：请先安装 Python/MLX 和任一 Qwen 模型。"
fi

if [[ -x "$squirrel_bin" ]]; then
  "$squirrel_bin" --reload
fi
print "魔虎大模型 addon 已安装；可在方案列表选择「魔虎大模型」，输入 /model 选择模型。"
