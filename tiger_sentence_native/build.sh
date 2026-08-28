#!/bin/zsh
# 构建 libtigerengine.dylib（需要 Lua 5.4 头文件，见 README.md）
set -euo pipefail
cd "$(dirname "$0")"
export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-11.0}"
clang++ -O2 -std=c++17 -dynamiclib tigerengine.cc tigerengine_lua.cc \
  -I. -undefined dynamic_lookup -o libtigerengine.dylib
# Squirrel is a hardened runtime; linker-only ad-hoc signatures are rejected
# when Lua loads this library from the user data directory.
codesign --force --sign - libtigerengine.dylib >/dev/null
echo "built libtigerengine.dylib"
