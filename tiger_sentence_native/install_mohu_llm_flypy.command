#!/bin/zsh
set -euo pipefail
export MOHU_LLM_SCHEME=flypy
exec "${0:A:h}/install_mohu_llm_scheme.command" "$@"
