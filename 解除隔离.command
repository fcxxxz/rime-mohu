#!/bin/zsh
set -u

if [[ "$(uname -s)" != "Darwin" ]]; then
  print "此脚本仅用于 macOS；Windows 无需执行。"
  exit 0
fi

script_dir="${0:A:h}"
target="$script_dir/mohu"
if [[ ! -d "$target" && -d "$HOME/Library/Rime/mohu" ]]; then
  target="$HOME/Library/Rime/mohu"
fi

if [[ ! -d "$target" ]]; then
  print -u2 "未找到 mohu 目录，请把脚本放在方案包根目录或 Rime 根目录后重试。"
  exit 1
fi

if ! command -v xattr >/dev/null 2>&1; then
  print -u2 "系统没有 xattr，无法解除 macOS 下载隔离。"
  exit 1
fi

xattr -dr com.apple.quarantine "$target" 2>/dev/null || true
print "已解除 $target 下文件的 macOS 下载隔离；现在可以重新部署 Rime。"
