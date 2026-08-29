# 魔虎大模型完整方案包实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 将当前单一 `mohu_tiger_sentence` overlay 重构为 `mohu_llm_zrm` 和 `mohu_llm_flypy` 两个可独立安装的完整大模型方案包，并统一使用 `~/Library/Rime/mohu_llm/` 运行时目录。

**Architecture:** 两个 schema 共享 native engine、Qwen scorer 和模型选择状态，但分别引用自然码/小鹤专属的 Rime 依赖、整句 lexicon 和数据清单。每个发布包包含完整方案文件、对应数据、共享 runtime 和双击安装器；Qwen 权重作为两个独立 Release 资产发布，不进入方案包。

**Tech Stack:** Rime schema YAML、Lua 5.4/librime-lua、C++17 Mach-O dylib、Python/MLX scorer、zsh installer/supervisor、Makefile、GitHub Actions、Python unittest、Lua/C++ safety tests。

---

### Task 1: Generate and validate scheme-specific native data

**Files:**
- Create or modify: `tools/build_mohu_llm_lexicons.py`
- Create: `tiger_sentence_native/data/zrm/mohu_llm_zrm.lexicon.txt`
- Create: `tiger_sentence_native/data/flypy/mohu_llm_flypy.lexicon.txt`
- Modify: `tests/test_tiger_lexicon_fly.py`
- Create: `tests/test_mohu_llm_lexicons.py`

- [ ] **Step 1: Write failing data-boundary tests**

Assert that both lexicons exist, every row has `code`, `text`, `rank`, `freq_rank`, natural-code rows decode with the natural-code converter, flypy rows decode with the flypy converter, and the two files are not byte-identical. Assert that each lexicon contains the same target sentence texts but different double-pinyin spellings where natural and flypy differ.

- [ ] **Step 2: Run the focused data tests and verify the expected failure**

Run `uv run python -m unittest tests.test_mohu_llm_lexicons tests.test_tiger_lexicon_fly -v`. The new tests must fail because the scheme-specific files and generator do not exist.

- [ ] **Step 3: Implement deterministic lexicon generation**

Use the existing tiger ranking/decomposition inputs and `tools/schemagen.py` conversion helpers. Generate one lexicon per double-pinyin scheme, preserving the native row format and stable `(code, rank, text)` ordering. Run the existing fly-key closure for each generated output and ensure no source absolute path is embedded.

- [ ] **Step 4: Verify data invariants and generated output**

Run `uv run python -m unittest tests.test_mohu_llm_lexicons tests.test_tiger_lexicon_fly -v` and inspect row counts, code-length distribution, and representative natural/flypy spellings. Run `git diff --check`.

- [ ] **Step 5: Commit the data task**

Run `git add tools/build_mohu_llm_lexicons.py tiger_sentence_native/data tests/test_mohu_llm_lexicons.py tests/test_tiger_lexicon_fly.py && git commit -m "feat(data): split mohu llm lexicons by scheme"`.

### Task 2: Rename and generalize the shared runtime path

**Files:**
- Modify: `tiger_sentence_native/mohu_tiger_sentence.lua`
- Modify: `tiger_sentence_native/mohu_tiger_reranker.lua`
- Modify: `tiger_sentence_native/mohu_tiger_model_catalog.lua`
- Modify: `tiger_sentence_native/run_qwen35_scorer.command`
- Modify: `tiger_sentence_native/scorer_models.zsh`
- Modify: `tiger_sentence_native/install_qwen35_launch_agent.command`
- Modify: `tiger_sentence_native/switch_qwen_model.command`
- Create: `tiger_sentence_native/mohu_llm_runtime.lua`
- Modify: `tests/mohu_tiger_model_catalog_test.lua`
- Create: `tests/mohu_llm_path_test.lua`

- [ ] **Step 1: Write failing path and identity tests**

Assert that runtime defaults use `mohu_llm/runtime`, shared n-gram uses `mohu_llm/data/sentence-ngram-mobile.bin`, model selection uses `mohu_llm/config/model-selection`, and model directories use `mohu_llm/models`. Assert no production file contains a default `~/Library/Rime/tiger/` path.

- [ ] **Step 2: Run the path tests and verify the expected failure**

Run `lua tests/mohu_llm_path_test.lua && lua tests/mohu_tiger_model_catalog_test.lua`; the new path assertions must fail against the current `tiger/` defaults.

- [ ] **Step 3: Implement one shared path resolver**

Add a small runtime path module that resolves the Rime user directory once and exposes `runtime`, `data`, `models`, `config`, `socket`, and `selection` paths. Update Lua, scorer supervisor, launch agent, and model catalog to consume those paths. Keep protocol names generic (`mohu_llm`) and remove model-specific hard-coded Qwen3.5 filenames from the active path.

- [ ] **Step 4: Run path, supervisor, and scorer regressions**

Run `lua tests/mohu_llm_path_test.lua && uv run python -m unittest tests.test_qwen35_scorer tests.test_qwen_model_supervisor -v`. Verify that the supervisor still switches models and that an unavailable model remains fail-closed.

- [ ] **Step 5: Commit the runtime task**

Run `git add tiger_sentence_native tests/mohu_llm_path_test.lua tests/mohu_tiger_model_catalog_test.lua && git commit -m "refactor(runtime): move mohu llm files under named root"`.

### Task 3: Split complete natural-code and flypy schemas

**Files:**
- Create: `mohu_llm_zrm.schema.yaml`
- Create: `mohu_llm_flypy.schema.yaml`
- Remove: `tiger_sentence_native/mohu_tiger_sentence.schema.yaml`
- Modify: `tiger_sentence_native/mohu_tiger_sentence.lua`
- Modify: `lua/mohu_reorder_filter.lua`
- Create: `tests/mohu_llm_schema_split_test.lua`
- Modify: `tests/test_mohu_tiger_sentence_native.py`
- Modify: `default.yaml`

- [ ] **Step 1: Write failing schema split tests**

Assert that both schemas have the correct display names and IDs, natural-code dependencies/lexicon paths are used only by `mohu_llm_zrm`, flypy dependencies/lexicon paths only by `mohu_llm_flypy`, both contain the complete fixed/smart/native/filter pipeline, and neither contains Octagram or early-commit wiring.

- [ ] **Step 2: Run schema tests and verify the expected failure**

Run `lua tests/mohu_llm_schema_split_test.lua && uv run python -m unittest tests.test_mohu_tiger_sentence_native tests.test_mohu_config -v`; the new IDs and separate paths must fail before implementation.

- [ ] **Step 3: Implement the two schemas from existing complete pipelines**

Copy the complete pipeline structure from the current native schema and the existing `mohu_zrm_sentence`/`mohu_flypy_sentence` schemes. Set each schema's `smart`, `fixed`, `translator_legacy`, reverse lookup, symbol, candidate manager, model menu, and model rerank components to its own scheme. Use a shared Lua translator module with an explicit `scheme`/`lexicon` configuration, not a natural-code default hidden inside flypy.

- [ ] **Step 4: Register exact scheme behavior**

Update `default.yaml` and option sync so the two new IDs are the only LLM schemes. Rename visible options to “模型重排关/模型重排开”, keep early commit absent, and ensure ordinary Mohu schemas retain their existing Octagram behavior. Update reorder filters and candidate type checks for both native IDs.

- [ ] **Step 5: Run schema and native pipeline tests**

Run `lua tests/mohu_llm_schema_split_test.lua`, all existing native Lua tests, `uv run python -m unittest tests.test_mohu_config tests.test_mohu_tiger_sentence_native -v`, and `git diff --check`.

- [ ] **Step 6: Commit the schema task**

Run `git add default.yaml mohu_llm_zrm.schema.yaml mohu_llm_flypy.schema.yaml lua/mohu_reorder_filter.lua lua/option_sync.lua tiger_sentence_native tests && git commit -m "feat(schema): split mohu llm natural and flypy schemes"`.

### Task 4: Build idempotent complete-package installers

**Files:**
- Create: `tiger_sentence_native/install_mohu_llm_zrm.command`
- Create: `tiger_sentence_native/install_mohu_llm_flypy.command`
- Create: `tiger_sentence_native/mohu_llm_zrm.package.json`
- Create: `tiger_sentence_native/mohu_llm_flypy.package.json`
- Modify: `tests/test_mohu_config.py`
- Create: `tests/test_mohu_llm_installers.py`

- [ ] **Step 1: Write failing installer/package tests**

For a temporary Rime directory, assert that installing the natural package copies only natural schema/data and registers only `mohu_llm_zrm`; installing flypy does the analogous operation; installing both leaves both entries exactly once. Include tests for empty, block, inline, comment, and repeated `default.custom.yaml` forms, and assert no `tiger/` path is created.

- [ ] **Step 2: Run installer tests and verify the expected failure**

Run `uv run python -m unittest tests.test_mohu_llm_installers -v`; it must fail because the package manifests and installers do not exist.

- [ ] **Step 3: Implement package manifests and installers**

Each manifest declares schema ID, package name, data directory, runtime files, required model manifests, and executable files. Each installer validates its manifest, atomically copies files under `mohu_llm/`, merges schema registration without duplicate YAML keys, and reloads Squirrel. It never removes user files and never silently installs the other scheme.

- [ ] **Step 4: Run installer integration tests**

Run `uv run python -m unittest tests.test_mohu_llm_installers tests.test_mohu_config -v`, `zsh -n tiger_sentence_native/install_mohu_llm_*.command`, and parse every generated temporary `default.custom.yaml` with PyYAML.

- [ ] **Step 5: Commit the installer task**

Run `git add tiger_sentence_native/install_mohu_llm_*.command tiger_sentence_native/*.package.json tests && git commit -m "feat(install): add complete mohu llm scheme packages"`.

### Task 5: Replace Makefile and GitHub packaging targets

**Files:**
- Modify: `Makefile`
- Modify: `.github/workflows/build.yml`
- Modify: `tiger_sentence_native/README.md`
- Modify: `tiger_sentence_native/models/README.md`
- Create: `tests/test_mohu_llm_distribution.py`

- [ ] **Step 1: Write failing distribution tests**

Assert that `make mohu-llm-zrm-dist` and `make mohu-llm-flypy-dist` produce complete packages with their schema, scheme data, shared runtime, installer, and no other LLM schema. Assert that a package has no absolute-path zip entries and that runtime-only and scheme packages have explicit boundaries.

- [ ] **Step 2: Run distribution tests and verify the expected failure**

Run `uv run python -m unittest tests.test_mohu_llm_distribution -v`; it must fail because the new targets and package manifests are absent.

- [ ] **Step 3: Implement Makefile package targets**

Add destination-controlled targets for runtime, natural-code, and flypy packages. Copy shared runtime once per package, copy only the scheme-specific lexicon/data, retain executable bits with `install -m 0755`, verify dylib signatures, and fail if any Qwen weight file enters a scheme package.

- [ ] **Step 4: Update GitHub Actions and Release assets**

Keep the standard zrm/flypy jobs unchanged. Add a macOS arm64 job that builds both complete LLM scheme packages from the pinned n-gram source, validates package boundaries and root entries, uploads both artifacts, and adds both zips to `latest`. Keep Qwen3.5/Qwen3 weight assets separate and persistent.

- [ ] **Step 5: Run clean package builds**

Run both new Makefile targets with temporary destinations, inspect their complete file lists, run `unzip -Z1` on generated archives, and run `uv run python -m unittest tests.test_mohu_llm_distribution tests.test_mohu_config -v`.

- [ ] **Step 6: Commit the packaging task**

Run `git add Makefile .github/workflows/build.yml tiger_sentence_native/README.md tiger_sentence_native/models/README.md tests && git commit -m "ci: publish complete mohu llm scheme packages"`.

### Task 6: Deploy, verify, and adversarially review

**Files:**
- Modify: `/Users/fuchuxuan/Library/Rime/` user deployment only
- Test: all new schema, installer, package, native, and scorer suites

- [ ] **Step 1: Run the complete focused verification matrix**

Run all new tests, existing native/Qwen tests, C++ safety tests, `make dist`, both LLM package targets, `zsh -n`, YAML parsing, and `git diff --check`. Record unrelated baseline failures separately.

- [ ] **Step 2: Deploy one scheme package at a time**

Install the natural and flypy packages into isolated temporary Rime directories first. Then deploy the selected local scheme using the new installer, atomically replace the signed dylib, reload Squirrel, and verify the build output contains exactly the selected schema and paths under `mohu_llm/`.

- [ ] **Step 3: Verify runtime behavior**

With Qwen3-0.6B and Qwen3.5-0.8B available, switch `/model` and verify the shared selection works in both schemas. Probe the two known long sentences in natural and flypy input, confirm model rerank and fail-open behavior, and verify no early commit or accidental code remains in candidates.

- [ ] **Step 4: Dispatch final adversarial review**

Review the complete diff against the design spec, package manifests, schema list, path inventory, and Release assets. Fix every Critical/Important finding, rerun affected tests, and only then report completion.
