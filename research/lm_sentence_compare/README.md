# Mohu sentence-model benchmark

This directory contains the executable benchmark behind
`docs/reports/2026-08-29-sentence-model-benchmark.md`. It compares BGW,
Wanxiang LTS, and the current native Tiger TCSKNM decoder on one fixed natural
double-pinyin corpus.

`research/sentence_benchmark/` is the initial data-contract sketch retained for
history. It is not used by this runner; the authoritative corpus schema, mode
names, parsers, and metrics live here.

## Artifact policy

The repository tracks source, focused tests, report summaries, and a hash
manifest. It intentionally does not track:

- downloaded TNews/LCCC text;
- encoded `cases.jsonl`;
- external Wanxiang grammar and Tiger n-gram model binaries (the BGW grammar
  used here is tracked in the repository);
- staged Rime build output or compiled probe binaries;
- raw candidate and latency TSV files;
- the joined per-case `results.jsonl`.

Reproduction is valid only when the external SHA-256 values match the report
manifest.

## Workflow

Build and encode the fixed corpus:

```bash
uv run python research/lm_sentence_compare/corpus/build_corpus.py
uv run python research/lm_sentence_compare/encode_sentences.py
```

Before staging, obtain the pinned Wanxiang grammar (the 420 MB file is
intentionally ignored by Git) and verify its digest:

```bash
curl -L \
  https://github.com/amzxyz/RIME-LMDG/releases/download/LTS/wanxiang-lts-zh-hans.gram \
  -o research/lm_sentence_compare/wanxiang-lts-zh-hans.gram
shasum -a 256 research/lm_sentence_compare/wanxiang-lts-zh-hans.gram
# expected: 4554bbe1ba683c416e64ab15d65c944743bdad5251285032681f12d24ee87102
```

Prepare the isolated staging tree:

```bash
uv run python research/lm_sentence_compare/prepare_staging.py
```

`prepare_staging.py` only removes `data/` after it has written the
`.mohu-lm-staging-v1` ownership marker. If a pre-existing staging directory is
unmarked, choose a new dedicated directory or remove that directory yourself
after checking its contents; the tool will not recursively delete it.

Run the isolated Rime grammar variants after building
`probes/rime_candidate_dump_squirrel` against the Squirrel librime ABI:

```bash
# Use headers compatible with the installed Squirrel API and link the exact
# librime.1.dylib shipped in Squirrel (not a potentially different Homebrew runtime).
clang++ -std=c++17 -O2 -I/opt/homebrew/include \
  research/lm_sentence_compare/probes/rime_candidate_dump.cc \
  "/Library/Input Methods/Squirrel.app/Contents/Frameworks/librime.1.dylib" \
  -Wl,-rpath,"/Library/Input Methods/Squirrel.app/Contents/Frameworks" \
  -o research/lm_sentence_compare/probes/rime_candidate_dump_squirrel
```

`run_rime_eval.py` repeats this ABI check with `otool -L` and rejects probes
that link an absolute Homebrew/local librime path; a probe linked with
`-L/opt/homebrew/lib -lrime` must not be used with Squirrel's plugins.

The Rime runner also requires the `data/` directory and ownership marker made
by `prepare_staging.py`; it will not write into an arbitrary Rime data tree.

```bash
uv run python research/lm_sentence_compare/run_rime_eval.py \
  --results-dir /path/to/raw-results
```

The runner verifies the Squirrel librime, Lua plugin, and octagram plugin
against the manifest-pinned SHA-256 values by default and checks the probe's
Squirrel ABI. A freshly compiled probe may have a different binary hash; pass
`--probe-sha256 878055e7509f790c6bea8ca6673bc0bce752919bc5ba437092bee18ad211b639`
only when requiring the historical probe byte-for-byte. Use
`--allow-unpinned-runtime` only for deliberate development runs; the run
manifest records whether the runtime bypass was used.

Run Tiger directly, optionally with `--shard i/n`, then merge sharded output:

```bash
uv run python research/lm_sentence_compare/run_tiger_eval.py \
  --top 20 --out /path/to/raw-results
uv run python research/lm_sentence_compare/merge_tiger_shards.py \
  --shards-root /path/to/shards --output-root /path/to/raw-results
```

Generate summaries without rerunning either decoder:

```bash
uv run python -m research.lm_sentence_compare.report_results \
  --cases research/lm_sentence_compare/cases.jsonl \
  --results /path/to/raw-results \
  --out /path/to/report \
  --artifact-manifest /path/to/manifest.json
```

`--artifact-manifest` is optional for a new run directory. When supplied, the
reporter verifies every raw/latency file byte-for-byte before calculating
metrics. For a deliberate development subset, add both
`--allow-noncanonical-cases` and `--skip-artifact-hash-check`.
For the canonical run, use
`docs/reports/2026-08-29-sentence-model-benchmark.manifest.json`; the
`rime-run-manifest.json` and `tiger-run-manifest.json` files are per-run
diagnostics, not substitutes for the artifact hash manifest.

Run the focused test suite:

```bash
uv run python -m unittest discover \
  -s research/lm_sentence_compare/tests -v
```
