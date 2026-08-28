"""Command-line benchmark for the local Qwen3.5 MLX scorer.

Example (Apple Silicon):

    uv run --with mlx-lm==0.31.3 python tiger_sentence_native/qwen35_bench.py \
      --model /path/to/Qwen3.5-0.8B-MLX-4bit --runs 100 --warmup 5

Measurements use ``time.perf_counter_ns`` (monotonic wall time), not CPU time.
The scorer cache is disabled so each sample includes one real batched forward
pass.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

# Support both ``python -m tiger_sentence_native.qwen35_bench`` and direct
# invocation by the documented file path.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tiger_sentence_native.qwen35_scorer import (
    DEFAULT_MODEL,
    MAX_REQUEST_CANDIDATES,
    MLXScorer,
    benchmark_scorer,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="benchmark Qwen3.5 MLX candidate scoring")
    parser.add_argument(
        "--model",
        default=os.environ.get("MOHU_QWEN35_MODEL", DEFAULT_MODEL),
        help="local MLX checkpoint or Hugging Face repo",
    )
    parser.add_argument("--revision", default=os.environ.get("MOHU_QWEN35_REVISION"))
    parser.add_argument("--context", default="")
    parser.add_argument(
        "--candidate",
        dest="candidates",
        action="append",
        default=None,
        help="candidate text; may be repeated (default: three smoke candidates)",
    )
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--runs", type=int, default=100)
    parser.add_argument("--max-context-tokens", type=int, default=256)
    parser.add_argument("--max-candidate-tokens", type=int, default=128)
    parser.add_argument("--no-gpu", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    candidates = tuple(args.candidates or ("你好", "世界", "中国"))
    if not 1 <= len(candidates) <= MAX_REQUEST_CANDIDATES:
        _parser().error(
            f"--candidate must be supplied between 1 and {MAX_REQUEST_CANDIDATES} times"
        )
    scorer = MLXScorer(
        args.model,
        revision=args.revision,
        max_context_tokens=args.max_context_tokens,
        max_candidate_tokens=args.max_candidate_tokens,
        cache_size=0,
        use_gpu=not args.no_gpu,
    )
    result = benchmark_scorer(
        scorer,
        context=args.context,
        candidates=candidates,
        warmup=args.warmup,
        runs=args.runs,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
