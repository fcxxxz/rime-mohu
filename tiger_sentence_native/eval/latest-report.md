# Qwen3.5 Native Reranker Validation

Date: 2026-08-27

## Runtime

- Model: `Qwen3.5-0.8B-MLX-4bit`
- Runtime: `mlx-lm==0.31.3`, `mlx==0.32.2`
- Model fingerprint: `8b1fc914a940d611e13ba1880ffdae553deb4504a0a6299256ac19470fc591b8`
- Candidate mode: `complete`
- Normalization: `sum_token_logp`
- Profile: `base_top_k=5`, adaptive `max_top_k=20`,
  `shortlist_confidence_margin=2.0`, `shortlist_score_margin=2.0`,
  `diversity_threshold=0.45`, rank fusion `alpha=4.0`.
- The profile is local opt-in and has not been calibrated on an independent
  labelled corpus. No accuracy gain is claimed from these spot checks.

## Batch and deadline policy

Requests with at most five candidates use an internal five-row kernel shape.
An adaptive request with more than five candidates is padded to twenty rows,
while the wire response still contains only the real candidates. Short
transformer sequences and selected target positions are padded to at least
eight tokens. This keeps the common adaptive shapes comparable within one
request; MLX 4-bit kernels can still vary across larger sequence buckets, so
absolute scores from different requests are not compared.

Lua uses `tiger/rerank_timeout_ms=45` for the five-row path and
`tiger/rerank_full_timeout_ms=140` for requests above five rows. A timeout,
busy scorer, stale model identity, malformed frame, or missing service returns
the native three-gram order without blocking future requests.  In that
  fail-open case the native candidate order is retained and the composition is
  never rewritten by the scorer.

## Spot checks

These checks use the installed native dictionary and the local checkpoint only.
They are regression checks, not a corpus evaluation.

- `najqmzufmekeybyudele`: the native first two rows are
  `那就没什么可犹豫地了` and `那就没什么可犹豫的了`; the complete-candidate
  Qwen score selects `那就没什么可犹豫的了`.
- `yiyjjqkjiuuisbgb`: native and fused top row is
  `一眼就看出是搜狗`.
- `nibuykzljyufnzhkle`: the target
  `你不要再精神内耗了` is present in native top-20. With the common-prefix
  context and the current neural-priority rank fusion, it is expected to be
  the fused top row; verify this again after changing the model or profile.

## Latency

Warm service, uncached local socket requests on Apple Silicon (representative
short Chinese candidates):

| request | typical latency |
| --- | ---: |
| five-row fast path | about 25-30 ms |
| six-to-twenty-row adaptive path | about 55-65 ms |

First use of a new sequence shape can be substantially slower. Longer
committed contexts have measured roughly 100-200 ms full-pool requests and
may therefore fail-open under the 140 ms deadline. The original strict Gate B
(`P50<=8`, `P95<=12`, `P99<=20`) is **FAIL**; neural mode remains opt-in.

## Native safety

The native translator only publishes candidates. Invalid raw input, malformed
native output, or a missing engine fails open to the ordinary translator; no
component rewrites `Context.input` or commits text from an update callback.
Native regression coverage verifies terminal multi-character states are not
reused across extensions, invalid raws clear the frontier, and final EOS
adjustments happen before the top-20 cap.

## Deployment

The user-scoped LaunchAgent `com.fuchuxuan.mohu.qwen35-reranker` is loaded and
running from `~/Library/Rime/tiger/run_qwen35_scorer.command`. The socket is
`~/Library/Rime/tiger/qwen35-reranker.sock` with mode `0600`; its parent is
`0700`. The latest health response reports `ready=true`, `mlx-lm==0.31.3`,
and the fingerprint above. Repository and live Lua/scorer/dylib checksums are
verified during deployment. The final isolated Squirrel probe returns
`一眼就看出是搜狗` for `yiyjjqkjiuuisbgb` and
`你不要再精神内耗了` for `nibuykzljyufnzhkle`; the probe also exercises the
neural switch without touching active Squirrel user databases.
The scorer was allowed to finish between probes so a busy-model fail-open could
not mask the candidate-order result.

## Accuracy gates

Gate A is **NOT RUN**. The checked-in fixture is synthetic smoke data and does
not provide a licensed independent corpus or a valid bootstrap lower bound.
Run `tools/evaluate_tiger_reranker.py` on a supplied corpus before enabling a
profile by default or distributing the model bundle.

Known residuals: a beam-pruned path outside the exposed twenty rows can still
be missed by candidate reranking. Fixed longer MLX sequence shapes reduce score drift, but the
measured 16/32-token paths are roughly 110/209 ms and would violate the input
latency budget, so the production profile keeps the faster dynamic shapes.
