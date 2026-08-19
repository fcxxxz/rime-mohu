#!/bin/zsh
# 注销 Rime 开机同步（移除 LaunchAgent）
DIR="$(cd "$(dirname "$0")" && pwd)"
bash "$DIR/rime_sync.sh" uninstall
echo ""
read -r "unused?按回车关闭..."
