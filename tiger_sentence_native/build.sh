#!/bin/zsh
# 构建 libtigerengine.dylib（需要 Lua 5.4 头文件，见 README.md）
# 神经重排依赖 Accelerate 框架（cblas_sgemm，macOS 自带，零外部依赖）
set -euo pipefail
cd "$(dirname "$0")"
export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-11.0}"
clang++ -O2 -std=c++17 -dynamiclib tigerengine.cc tigerengine_lua.cc \
  -I. -undefined dynamic_lookup \
  -framework Accelerate \
  -o libtigerengine.dylib
# Squirrel is a hardened runtime; linker-only ad-hoc signatures are rejected
# when Lua loads this library from the user data directory.
codesign --force --sign - libtigerengine.dylib >/dev/null
echo "built libtigerengine.dylib"
