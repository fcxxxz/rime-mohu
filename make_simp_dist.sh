#!/bin/bash

set -euo pipefail
set -x

# 魔虎源码与发布产物均为简体，不再执行二次繁简转换。
rm -rf dist
make dist
uv run python -m unittest tests.test_mohu_config -v
