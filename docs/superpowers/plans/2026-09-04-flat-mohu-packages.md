# Flat Mohu Packages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current ordinary/LLM split Release output with two flat, scheme-specific Mohu packages and an independently downloadable versioned V5 model asset.

**Architecture:** The public schema IDs become `mohu_zrm` and `mohu_flypy`; the old ordinary schemas are renamed to compile-only internal IDs so the public schemas do not collide. Runtime files move under `mohu/`, lexicons under `mohu/data/<scheme>/`, and versioned sentence models under `mohu/model/`. Lua selects the highest numeric version from `mohu-sentence-ngram-vN.bin`, including decimal versions such as `v5.10`.

**Tech Stack:** Rime YAML, Lua 5.4, Python unittest, Makefile, GitHub Actions, zip/unzip validation.

---

### Task 1: Add failing model-version resolver tests

**Files:**
- Create: `tests/mohu_model_version_test.lua`
- Modify: `tests/mohu_llm_path_test.lua` (rename assertions to `mohu/` paths)

- [ ] **Step 1: Write the failing test**

Create a temporary `mohu/model/` directory containing `mohu-sentence-ngram-v5.bin`, `mohu-sentence-ngram-v5.2.bin`, `mohu-sentence-ngram-v5.10.bin`, `mohu-sentence-ngram-v6.bin`, and invalid names. Assert the resolver returns `v6`, then remove it and assert `v5.10` beats `v5.2`; assert no match returns nil.

- [ ] **Step 2: Run the focused test and verify the expected failure**

Run `lua tests/mohu_model_version_test.lua`.
Expected: fail because `mohu_runtime.resolve_model` does not exist.

### Task 2: Implement runtime model selection and internal path rename

**Files:**
- Rename: `tiger_sentence_native/mohu_llm_runtime.lua` -> `tiger_sentence_native/mohu_runtime.lua`
- Modify: `tiger_sentence_native/mohu_tiger_sentence.lua`
- Modify: `tiger_sentence_native/mohu_tiger_reranker.lua`
- Modify: `tiger_sentence_native/mohu_tiger_model_catalog.lua`
- Modify: `tiger_sentence_native/mohu_tiger_model_menu.lua`
- Modify: `tiger_sentence_native/run_qwen35_scorer.command`
- Modify: `tiger_sentence_native/scorer_models.zsh`
- Modify: `tiger_sentence_native/switch_qwen_model.command`
- Modify: all Lua/Python/C++ tests that assert `mohu_llm` paths or candidate IDs

- [ ] **Step 1: Implement the minimal resolver**

Add `resolve_model(options)` to `mohu_runtime.lua`. It scans only the model directory, accepts the filename pattern `^mohu%-sentence%-ngram%-v([0-9]+(?:%.[0-9]+)*)%.bin$`, compares each dot-separated numeric component numerically, and returns the full path of the highest version. Ignore invalid files and return nil when the directory is unavailable.

- [ ] **Step 2: Wire the resolver into native initialization**

Change `ensure_engine` so an empty/configured model path resolves through the versioned model directory; preserve explicit absolute model paths for tests and advanced overrides. Keep native load failures fail-open.

- [ ] **Step 3: Rename runtime paths and public identities**

Move runtime root to `mohu/`, model directory to `mohu/model/`, and update Lua module imports, schema candidate types, filter allowlists, option synchronization, scorer paths, and test fixtures. Rename the public schemas/files to `mohu_zrm` and `mohu_flypy`; rename old ordinary compile-only schema IDs/files to non-conflicting internal names and update dependencies.

- [ ] **Step 4: Run focused runtime tests**

Run `lua tests/mohu_model_version_test.lua`, `lua tests/mohu_llm_path_test.lua`, the native sentence tests, and `uv run python -m unittest tests.test_mohu_tiger_sentence_native tests.test_mohu_config -v`. Expected: all pass with no `mohu_llm` production paths remaining.

### Task 3: Build flat scheme packages without model or installer

**Files:**
- Modify: `tools/build_split_dist.py`
- Modify: `Makefile`
- Modify: `tests/test_split_distribution.py`
- Create: `tests/test_flat_distribution.py`

- [ ] **Step 1: Write failing package-layout tests**

Assert each flat output has `default.yaml` and exactly one public schema at its root, has no `base/`, installer, `mohu_llm`, or model file, contains only the selected scheme lexicon, and contains `mohu/model/` only as needed for documentation. Assert all schema references point to the renamed internal/public IDs and `mohu/` paths.

- [ ] **Step 2: Run the focused package test and verify the expected failure**

Run `uv run python -m unittest tests.test_flat_distribution -v`.
Expected: fail because the flat targets and renamed layout do not exist.

- [ ] **Step 3: Implement flat Makefile targets and builder changes**

Build each scheme directly into a clean destination root, copy the selected scheme's compile-only files and shared Lua/OpenCC resources, write a `default.yaml` containing only that public schema, and omit all install scripts, package manifests, Qwen manifests, and V5 files. Ensure destination cleanup is bounded and safe.

- [ ] **Step 4: Build the standalone versioned model asset**

Add a target that stages `mohu-sentence-ngram-v5.bin` as `mohu/model/mohu-sentence-ngram-v5.bin` for zip creation without changing the scheme package outputs.

- [ ] **Step 5: Run package tests**

Run `uv run python -m unittest tests.test_flat_distribution tests.test_split_distribution -v` and inspect `unzip -Z1` output for root layout, traversal safety, and absence of model/install files.

### Task 4: Replace GitHub Release assets and verify end to end

**Files:**
- Modify: `.github/workflows/build.yml`
- Modify: `tests/test_split_release_workflow.py`
- Modify: `README.md` and `tiger_sentence_native/README.md` only for the new download/drop paths

- [ ] **Step 1: Write failing workflow assertions**

Assert the workflow builds and uploads only `rime-mohu-zrm-latest.zip`, `rime-mohu-flypy-latest.zip`, and `mohu-sentence-ngram-v5.bin`; assert it no longer uploads ordinary/LLM package names or installer assets.

- [ ] **Step 2: Run the workflow test and verify the expected failure**

Run `uv run python -m unittest tests.test_split_release_workflow -v`.
Expected: fail against the current four-package workflow.

- [ ] **Step 3: Update workflow packaging**

Remove ordinary and installer package jobs from the Release path, zip the flat scheme directories, stage the standalone V5 model asset, restore only required executable bits for retained project tools, and validate every archive's root paths and file boundaries.

- [ ] **Step 4: Run the complete focused verification matrix**

Run `uv run python -m unittest tests.test_flat_distribution tests.test_split_distribution tests.test_split_release_workflow tests.test_mohu_config tests.test_mohu_tiger_sentence_native -v`, the Lua runtime/native tests, `make lint-python`, and `git diff --check`. Inspect `git diff` to ensure existing unrelated user changes remain intact.

- [ ] **Step 5: Commit implementation changes**

Use the repository's release prefix: `ci: publish flat mohu scheme packages`.
