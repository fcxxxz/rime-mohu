#!/bin/zsh
# 注册 Rime 开机同步（LaunchAgent）
DIR="$(cd "$(dirname "$0")" && pwd)"
bash "$DIR/rime_sync.sh" install
echo ""
read -r "unused?按回车关闭..."
