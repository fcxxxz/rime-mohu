#!/bin/zsh
# 魔虎字提：逐行隔离回放校验。
# 每行都用全新用户词典状态运行探针，避免上屏学习与上下文流动互相污染。
#
# 用法: zsh tools/ziti_replay.sh <probe二进制> <部署目录> <方案id> <输入文件> <输出文件>
set -euo pipefail

probe=$1
deploy=$2
schema=$3
input=$4
output=$5

: > "$output"
while IFS= read -r line; do
  rm -rf "$deploy"/*.userdb "$deploy"/sync 2>/dev/null || true
  printf '%s\n' "$line" | "$probe" "$deploy" "$schema" commit 2>/dev/null >> "$output"
done < "$input"
