#!/bin/zsh
set -euo pipefail
export MOHU_LLM_SCHEME=zrm
exec "${0:A:h}/install_mohu_llm_scheme.command" "$@"
