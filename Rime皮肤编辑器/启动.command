#!/bin/zsh
cd "$(dirname "$0")/.." || exit 1

HTML_PATH="$PWD/Rime皮肤编辑器/index.html"

open_static_editor() {
  echo "未找到可直接使用的 Python 3.7+，无法自动启动本地服务。"
  echo "将打开网页版；请使用 Chrome 或 Edge 选择 Rime 配置文件夹。"
  if open -a "Google Chrome" "$HTML_PATH" >/dev/null 2>&1; then
    return
  fi
  if open -a "Microsoft Edge" "$HTML_PATH" >/dev/null 2>&1; then
    return
  fi
  open "$HTML_PATH"
}

python_ok() {
  "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 7) else 1)' >/dev/null 2>&1
}

PYTHON_BIN="$(command -v python3 2>/dev/null || true)"
if [ -n "$PYTHON_BIN" ] && [ "$PYTHON_BIN" != "/usr/bin/python3" ] && python_ok "$PYTHON_BIN"; then
  "$PYTHON_BIN" "Rime皮肤编辑器/local/local_server.py" --root "$PWD"
elif xcode-select -p >/dev/null 2>&1 && [ -x /usr/bin/python3 ] && python_ok /usr/bin/python3; then
  /usr/bin/python3 "Rime皮肤编辑器/local/local_server.py" --root "$PWD"
else
  open_static_editor
  read -r "unused?按回车关闭..."
fi
