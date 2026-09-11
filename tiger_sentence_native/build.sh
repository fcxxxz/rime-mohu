#!/bin/zsh
# 构建 libtigerengine.dylib（需要 Lua 5.4 头文件，见 README.md）
# 魔虎语义依赖同目录 libonnxruntime.1.dylib（@loader_path 解析）。
set -euo pipefail
cd "$(dirname "$0")"
export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-14.0}"
clang++ -O2 -std=c++17 -dynamiclib tigerengine.cc tigerengine_lua.cc \
  -I. -undefined dynamic_lookup \
  -I"${ONNXRUNTIME_HOME:-/opt/homebrew/opt/onnxruntime}/include/onnxruntime" \
  -L. -lonnxruntime.1 -Wl,-rpath,@loader_path \
  -framework Accelerate \
  -o libtigerengine.dylib
# Squirrel is a hardened runtime; linker-only ad-hoc signatures are rejected
# when Lua loads this library from the user data directory.
codesign --force --sign - libtigerengine.dylib >/dev/null
echo "built libtigerengine.dylib"
