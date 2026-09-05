# Completion-Preserving Latency Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve Mohu syllable/word completion and the full Wanxiang-backed dictionary while bounding per-key candidate work, eliminating the V5 model startup page sweep/redundant mapping, and deferring management-only Memory construction.

**Architecture:** Lua filters use a two-phase bounded-prefetch protocol: finite streams retain the legacy exact transformation, while oversized streams fail open in source order. The native loader owns one primary mapping, validates only metadata/indexes by default, validates touched pages at lookup, and keeps full page validation behind an explicit strict flag. Candidate-management Memory wrappers are created only by actions that need user-dictionary access.

**Tech Stack:** Rime YAML, Lua 5.4/librime-lua, C++17 native engine, Python 3/uv tests, Windows Weasel memory probes.

---

## Chunk 1: Completion-Preserving Candidate Pipeline

### Task 1: Lock completion semantics in schema tests

**Files:**
- Modify: `tests/test_mohu_config.py`
- Create: `tests/mohu_filter_pipeline_test.lua`
- Modify: `Makefile`
- Modify: `mohu_zrm.schema.yaml`
- Modify: `mohu_flypy.schema.yaml`
- Modify: `mohu_zrm_core.schema.yaml`
- Modify: `mohu_flypy_core.schema.yaml`
- Modify: `mohu_zrm_sentence_core.schema.yaml`
- Modify: `mohu_flypy_sentence_core.schema.yaml`

- [ ] **Step 1: Write the failing configuration test**

Add this exact matrix covering every dynamic translator namespace:

```python
COMPLETION_NAMESPACES = {
    "mohu_zrm.schema.yaml": ("smart", "smart_static"),
    "mohu_flypy.schema.yaml": ("smart", "smart_static"),
    "mohu_zrm_core.schema.yaml": ("smart", "smart_static"),
    "mohu_flypy_core.schema.yaml": ("smart", "smart_static"),
    "mohu_zrm_sentence_core.schema.yaml": ("translator", "translator_static"),
    "mohu_flypy_sentence_core.schema.yaml": ("translator", "translator_static"),
}

for path, namespaces in COMPLETION_NAMESPACES.items():
    schema = yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))
    for namespace in namespaces:
        assert schema[namespace]["enable_completion"] is True
        assert schema[namespace]["enable_word_completion"] is True
```

Add negative invariants only for the four public/core schemas (where these nodes exist):
`fixed`, `fixed_legacy`, and `custom_phrase` retain `enable_completion is False`, while
`reverse_tiger` and `reverse_tiger_backtick` retain `enable_completion is True`. For the
two sentence-core files assert only the translator matrix. Assert the two extended
dictionary files still contain their respective `*.wanxiang` import. Update stale comments
from commit `136937a` so they describe retained completion plus bounded filters.

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
uv run python -m unittest tests.test_mohu_config -v
```

Expected: FAIL because commit `136937a` sets main/core smart completion false and sentence-core lacks explicit word-completion settings.

- [ ] **Step 3: Make completion explicit only on smart namespaces**

Set this pair under each matrix namespace without changing fixed/custom/reverse nodes:

```yaml
enable_completion: true
enable_word_completion: true
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the command from Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_mohu_config.py mohu_zrm.schema.yaml mohu_flypy.schema.yaml \
  mohu_zrm_core.schema.yaml mohu_flypy_core.schema.yaml \
  mohu_zrm_sentence_core.schema.yaml mohu_flypy_sentence_core.schema.yaml \
  tests/mohu_filter_pipeline_test.lua Makefile
git commit -m "fix: preserve smart completion"
```

### Task 2: Replace reorder streaming with bounded fail-open

**Files:**
- Modify: `tests/mohu_reorder_filter_lexicon_test.lua`
- Modify: `lua/mohu_reorder_filter.lua`
- Modify: `Makefile`

- [ ] **Step 0: Select the Lua 5.4 test runner**

Use the repository's Lua 5.4 binary after the setup documented in
`tiger_sentence_native/README.md`; do not silently substitute the Weasel host's Lua:

```powershell
$luaBin = $env:MOHU_LUA_BIN
if (-not $luaBin) { throw "set MOHU_LUA_BIN to a Lua 5.4 executable" }
& $luaBin -v
```

Setup commands are explicit: on MSYS2 run `make -C tiger_sentence_native/lua-5.4.6
mingw` and set `MOHU_LUA_BIN` to the resulting `src/lua.exe`; on POSIX run the repository
Lua make target and set it to `src/lua`. A missing runner is a blocked verification
prerequisite, not a passing test. Modify Makefile Lua-test recipes to use
`$(MOHU_LUA_BIN)` (defaulting to `lua`) so CI and local commands use the same binary.

- [ ] **Step 1: Add coroutine-backed failing tests**

Extend the test harness so `_G.yield` calls `coroutine.yield(candidate)`, each candidate
has distinct `type`, `text`, `preedit`, `comment`, `quality`, and object identity, and the
input iterator increments an `advance_count`. Add one test per behavior:

```lua
-- finite stream: output fields and identity equal a pinned HEAD^ fixture
-- pinned + delayed smart: no duplicate
-- native(3/4 chars) + later matching smart: native identity is retained
-- oversized stream: first resume consumes <= budget + 1 candidates
-- fallback: source order/identity retained and `F becomes public indicator
-- exact budget-1, budget, budget+1 (including sentinel behavior)
-- clock before sentinel, clock fallback, iterator throw, and invalid return
-- fixed fallback is not scored or reordered by the downstream word-order filter
```

The pipeline test must compose both filters with a list-backed translation and assert every
candidate's `get_genuine()` type, text, preedit, comment, quality, and object identity; a
fixed candidate normalized from `` `F`` must not reach the scorer.

Use a small configured budget in tests so boundaries are deterministic.

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
& $env:MOHU_LUA_BIN tests/mohu_reorder_filter_lexicon_test.lua
```

Expected: FAIL on duplicate/native-loss cases and on bounded first-yield assertions.

- [ ] **Step 3: Restore the finite legacy state machine**

Remove `streaming`/`enter_streaming`. Copy the exact parent algorithm into an internal
`run_finite(input, env)` path; pin the fixture to `git show acb327d:lua/mohu_reorder_filter.lua`
so later branch changes cannot silently redefine the oracle. Before processing `delay_slot`,
detach it:

```lua
local delayed = ctx.delay_slot
ctx.delay_slot = {}
for _, cand in ipairs(delayed) do
    Top.handle_matching(env, ctx, cand)
end
```

This guarantees a delayed candidate cannot be flushed from two lists.

- [ ] **Step 4: Add the bounded iterator adapter**

Read `mohu/reorder_scan_budget` (default 64) and `mohu/reorder_time_budget_ms` (default 4).
Save one `advance/state`, prefetch at most budget candidates plus one sentinel, and wrap
every call, including the tail drain, in the same `pcall` adapter:

```lua
local ok, cand = pcall(advance, state)
if not ok then
    emit_consumed_buffer_and_stop()
elseif cand == nil then
    return run_finite(list_translation(buffer), env)
elseif over_budget then
    buffer[#buffer + 1] = cand
    emit_passthrough(buffer)
    drain_same_iterator_with_pcall(advance, state)
end
```

`yield_passthrough` may only normalize an internal `` `F`` comment. Wrap iterator and clock calls with the error semantics in the design; never call `input:iter()` twice.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run the command from Step 0/2. Expected: PASS with no duplicate/loss, exact finite-stream
parity, and bounded `advance_count`.

- [ ] **Step 6: Commit**

```bash
git add tests/mohu_reorder_filter_lexicon_test.lua lua/mohu_reorder_filter.lua
git commit -m "fix(lua): bound reorder candidate lookahead"
```

### Task 3: Make contextual word ordering fail open at its scan budget

**Files:**
- Modify: `tests/mohu_word_order_filter_test.lua`
- Modify: `lua/mohu_word_order_filter.lua`
- Modify: `tiger_sentence_native/mohu_tiger_sentence.lua`
- Modify: `tiger_sentence_native/README.md`

- [ ] **Step 0: Reuse the Lua runner selected in Task 2**

Require `MOHU_LUA_BIN` to point to the Lua 5.4 executable and fail before running if it
is unset. The test must use a coroutine-yielding iterator, not an eager table collector.

- [ ] **Step 1: Write failing budget and lifecycle tests**

Add separate cases proving:

```lua
-- budget hit before a complete block: scorer not acquired/called; source order unchanged
-- the budget-th candidate completes a fixture block with >=2 reorderable slots: scorer runs
-- EOF with 2..N candidates: scorer runs
-- sentinel and same iterator are preserved across coroutine resumes
-- unavailable engine handle never triggers ensure_engine from the filter
-- an initial or mid-scan clock error disables only time budgeting and continues to the count cap
-- an already-expired valid deadline fails open; iterator error logs once and stops
-- fixed `F` normalized by reorder is not treated as a reorderable word downstream
```

Make the mock scorer acquisition count visible and make mock `free()` invalidate the handle.

- [ ] **Step 2: Run and verify RED**

```powershell
& $env:MOHU_LUA_BIN tests/mohu_word_order_filter_test.lua
```

Expected: FAIL because the current branch partially scores on budget exhaustion and acquires the scorer before bounded collection.

- [ ] **Step 3: Implement fail-open collection**

Track whether the loop ended because the block filled, EOF arrived, or a count/time budget fired. On budget, output `prefix + block + tail` untouched. Build slots first, then acquire the already-live scorer only when at least two slots exist.

Change `acquire_char_scorer` and `acquire_word_scorer` to return nil unless `engine_handle` already exists; do not call `ensure_engine` from these accessors.

- [ ] **Step 4: Document the two budget options**

Document `tiger/word_order_scan_budget` and `tiger/word_order_time_budget_ms`, including cooperative timing and count-bound guarantees.

- [ ] **Step 5: Run focused Lua suites and verify GREEN**

```powershell
& $env:MOHU_LUA_BIN tests/mohu_word_order_filter_test.lua
& $env:MOHU_LUA_BIN tests/mohu_reorder_filter_lexicon_test.lua
& $env:MOHU_LUA_BIN tests/mohu_tiger_context_test.lua
```

- [ ] **Step 6: Commit**

```bash
git add tests/mohu_word_order_filter_test.lua lua/mohu_word_order_filter.lua tiger_sentence_native/mohu_tiger_sentence.lua tiger_sentence_native/README.md
git commit -m "fix(lua): fail open on word-order scan budget"
```

## Chunk 2: Native Model Startup

### Task 4: Enforce one-owner model mapping

**Files:**
- Create: `tests/tigerengine_mobile_test.cc`
- Create: `tests/tigerengine_mobile_cases.py`
- Create: `tests/tigerengine_mapping_ownership_test.cc`
- Create: `tests/tigerengine_windows_mapping_test.py`
- Modify: `Makefile`
- Modify: `.gitignore`
- Modify: `.github/workflows/build.yml`
- Modify: `tiger_sentence_native/tigerengine.cc`

- [ ] **Step 1: Add a portable fixture test and a Windows-only mapping probe**

Create `tests/tigerengine_mobile_test.cc` with a wire-layout builder, not a test that
depends on optional real-model environment variables. The valid TCSKNM02 fixture has
`index_stride=16`, one unigram for U+7532, a bigram key `kBOS=2`, a trigram key
`pack2(2,2)`, and lexicon row `a<TAB>甲`; decode raw input `a` is the deterministic
oracle. Add malformed used-page, malformed unused-page, index/page boundary, repeated
  decode-cache, unknown magic, and repeated load-failure cases. Add explicit tiny fixtures
  for TCSKNM01, MHKNM01, and MHCTN01 with deterministic status fields (`format`,
  `word_scorer`, `word_vocab`), decode-first-candidate, and packed-vs-explicit score
  equality assertions. Include a malformed MHCTN character offset/length/header case that
  rejects the whole container with a nonempty error, a malformed optional MHCTN word section
  that downgrades to character-only, and a malformed MHKNM01 successor/page case that still
  rejects at create.

Create `tests/tigerengine_mobile_cases.py` as the cross-platform subprocess parent. It
invokes the fixture executable separately for `valid`, `unused-bad`, `used-bad`, and
`strict-bad` cases, asserting exit code/output; this replaces POSIX-only `fork` for the
new malformed-page tests. `tests/tigerengine_mobile_test.cc` accepts the case argument
and returns success only when the expected create/decode status is observed.

Create `tests/tigerengine_windows_mapping_test.py` as a subprocess test. It requires
`TIGER_ENGINE_DLL`, `TIGER_NGRAM`, and `TIGER_LEXICON`; if any is absent it exits with
status 2 and a clear “probe not run” message, never a false pass. The child loads the DLL
  with ctypes, creates one handle, waits on a pipe, and the parent uses
  `OpenProcess(PROCESS_QUERY_INFORMATION|PROCESS_VM_READ)` plus `VirtualQueryEx` to count
  regions grouped by `AllocationBase`. Use `GetMappedFileNameW` to identify the path,
  pointer-size-aware `MEMORY_BASIC_INFORMATION`, and `QueryWorkingSetEx` for resident
  pages. Filter only the primary model path/size; for MHCTN01 match the container file
  size rather than the character-section length. Explicit scorer/blend mappings are
  reported separately. After the child frees the handle, assert
the primary region count is zero.

Create `tests/tigerengine_mapping_ownership_test.cc` and compile `tigerengine.cc` with
`-DTIGERENGINE_MAPPING_TEST`. Under that macro only, expose a narrow test function that
creates one owner plus two borrowed interior views, destroys the views, verifies the owner
bytes remain readable, then releases the owner and reports exactly one unmap and, on
Windows, exactly one mapping-handle close. Assert borrowed release resets `owned=true`,
then reuse/move the object; this is a deterministic RED case on POSIX as well as Windows.
Unify both platform destructors through `release()` so the counters cover the same path.
The production library must not export this seam.

Add `.tmp/native-tests/` to `.gitignore`. Add a `tigerengine-mobile` Make target for the
portable fixture, a
`tigerengine-mapping` target for a cross-platform borrowed/owner lifecycle probe, and a
`tigerengine-windows-memory` target that runs the Python probe only on Windows when its
three variables are set. Do not add Windows-only code to the existing POSIX
`tigerengine_safety_test.cc`.

The three new recipes must be `.PHONY`, write binaries under ignored
`.tmp/native-tests/`, honor `$(CXX) $(CPPFLAGS) $(CXXFLAGS)`, and have explicit cleanup
and exit behavior. `tigerengine-safety` remains POSIX-only; Windows validation uses the
portable mobile/mapping targets plus the Python probe.

Add a `windows-runtime` workflow step after the existing smoke test: copy `lua54.dll`
beside `libtigerengine.dll`, run `make tigerengine-mobile tigerengine-mapping` under
`msys2 {0}`, export the three probe paths to the downloaded root model/DLL/repository
lexicon, run `python tests/tigerengine_windows_mapping_test.py`, and upload its JSON
manifest. Missing required assets must fail the job, not skip it.

- [ ] **Step 2: Run native safety tests and verify RED**

Run the existing `tigerengine-safety`/`tigerengine-word-score` POSIX targets only in the
documented POSIX build shell; their current fork/`sys/wait.h` harness is not a Windows
target. Run the new `tigerengine-mobile`, `tigerengine-mapping`, and Windows probe in the
MSYS2 MINGW64 shell. The PowerShell command below is only for the ctypes probe. Make
recipes must use `$(CXX)`, `$(CPPFLAGS)`, and `$(CXXFLAGS)` so sanitizer flags are not
silently ignored.

```bash
make tigerengine-safety
make tigerengine-word-score
```

Run the fixture targets too:

```bash
make tigerengine-mobile
make tigerengine-mapping
make tigerengine-word-score
python tests/tigerengine_mobile_cases.py .tmp/native-tests/tigerengine_mobile_test
```

Expected: the new mapping/format assertions fail before implementation; fixture targets
never skip. Optional real word-score tests may skip only when their documented external
assets are absent.

- [ ] **Step 3: Fix `MappedFile` ownership**

Make `release()` unmap only `owned` data on both platforms, close only owned mapping handles, clear all fields, and restore `owned=true`. Make `open()` and `set_view()` release previous state first.

Add `load_mapped(MappedFile&&, label)` helpers. On failure, the target loader releases the
owner and resets all parsed metadata/caches before returning. Add a test that repeats a
failed create in one live process and observes zero primary mappings after each attempt.

- [ ] **Step 4: Dispatch once by magic**

Map the primary file once into `Engine::container`, inspect magic, then either retain it
for MHCTN01 or move it into `KnModel`/`WordModel`. Assert the moved-from container has
`data == nullptr`, `mapping == nullptr`, and zero size immediately after the move;
MHCTN01 model/word views must be borrowed and the container must remain the sole owner.
Load into a fresh temporary model and publish only after all metadata has succeeded; on
any failure reset pointers, flags, counts, path, caches, and mapping ownership before
returning. Preserve character-section rejection and optional word-section downgrade
semantics. Unknown or invalid-container magic must fail directly after the single
inspection, without fallback re-probing. The one-map assertion excludes intentional
`word_scorer_model`, `MH_BLEND`, and container-declared secondary resources.

- [ ] **Step 5: Run focused native tests and verify GREEN**

Run the commands from Step 2 plus `make tigerengine-mobile`, `make tigerengine-mapping`,
and `make tigerengine-word-score`. Expected: all formats preserve behavior; the portable
ownership test proves a borrowed view leaves the owner mapping usable and the owner is
released exactly once. On Windows also run:

```powershell
$env:TIGER_ENGINE_DLL = (Resolve-Path "mohu\runtime\libtigerengine.dll")
$env:TIGER_NGRAM = (Resolve-Path "mohu\model\mohu-sentence-ngram-v5.bin")
$env:TIGER_LEXICON = (Resolve-Path "tiger_sentence_native/data/zrm/mohu_zrm.lexicon.txt")
& (Get-Command python).Source tests/tigerengine_windows_mapping_test.py
```

Expected: one primary mapping while live and zero after free; a failed-create child reports
zero primary mappings before exiting. The ctypes child must preload `lua54.dll` with
`os.add_dll_directory` or an explicit PATH entry and require 64-bit Python.

- [ ] **Step 6: Commit**

```bash
git add tests/tigerengine_mobile_test.cc tests/tigerengine_mapping_ownership_test.cc \
  tests/tigerengine_mobile_cases.py tests/tigerengine_windows_mapping_test.py \
  Makefile .gitignore .github/workflows/build.yml \
  tiger_sentence_native/tigerengine.cc
git commit -m "fix(native): keep one primary model mapping"
```

### Task 5: Make TCSKNM02 page validation lazy and fail closed

**Files:**
- Modify: `tests/tigerengine_mobile_test.cc`
- Modify: `tests/tigerengine_mobile_cases.py`
- Modify: `tests/tigerengine_safety_test.cc`
- Modify: `tests/mohu_word_order_filter_test.lua`
- Modify: `Makefile`
- Modify: `tiger_sentence_native/tigerengine.cc`
- Modify: `tiger_sentence_native/README.md`

- [ ] **Step 1: Write failing lazy/strict validation tests**

Keep all TCSKNM02 lazy-validation fixtures and their wire-layout builder in
`tests/tigerengine_mobile_test.cc`; do not reference anonymous helpers across translation
units. Update the old `bad_successor` assertion in `tests/tigerengine_safety_test.cc` to
set strict mode explicitly, and add a separate default-mode child case. Assert:

```cpp
// header/index/layout corruption: create fails in every mode
// unused malformed page: default create succeeds without walking it
// used malformed page: an isolated child decode returns an error, repeated cache-hit
// decode returns the same error, and the child exits normally without OOB
// MOHU_TIGER_STRICT_VALIDATE=1: create rejects malformed successor spans
// unset, empty, 0, other values: strict traversal remains disabled
```

Include `index_stride < 16`, the exact zero-section matrix (`context_count == index_count
== 0` accepted; one zero and one nonzero rejected), count/index-stride mismatch, a
duplicate-index-key fixture preserving the existing “last page” binary-search behavior,
maximum representable index counts against a tiny file, checked page-offset additions,
out-of-block page offsets, and bi/tri boundary cases. Do not claim an impossible UINT64
multiplication overflow through the public u32-count wire format. Introduce and directly
test an internal `checked_span(offset, count, item_size, limit, &end)` helper under the
mapping-test seam with maximum u32 counts.

Use a cross-platform RAII environment guard (`setenv/unsetenv` on POSIX,
`_putenv_s` restore on Windows); read the strict flag per create call, never cache it
process-wide, and restore the previous value after each case. Assert every malformed-fixture
create failure has a nonempty format/layout error; generic missing-path, lexicon, allocation,
and other create failures only need a nonempty error. Assert that a later valid create clears
stale error text.

- [ ] **Step 2: Run with sanitizers and verify RED**

```bash
make tigerengine-mobile CXXFLAGS="-O1 -g -fsanitize=address,undefined"
ASAN_OPTIONS=detect_leaks=1 ./build/tigerengine_mobile_test
```

The `tigerengine-mobile` recipe must place its output at the documented path and honor the
repository's `CXX`/`CXXFLAGS` variables. On POSIX, run the fixture under ASan/UBSan. On
Windows/MSYS2, run the same fixture with the toolchain's sanitizer support or record an
explicit skip; the ordinary non-sanitized fixture must still run. Run sanitizers again
after the implementation, not only before it.

Expected: current create rejects deep corruption unconditionally and still walks every page.

- [ ] **Step 3: Tighten metadata/index validation**

Validate exact `header_size == 104`, version/file size, checked section arithmetic,
`index_stride >= 16`, the zero-section exception above, exact
`ceil(context_count/stride)` page counts, nondecreasing keys, and page offsets against
`bi_index_off`/`tri_index_off`. Require ordered, nonoverlapping
`header <= unigram <= bi_blocks <= bi_index <= tri_blocks <= tri_index <= file_size`
intervals and `page_offset >= section_start && page_offset + 16 <= section_end`; do not
invent a page-alignment requirement. Audit `find_page`'s index pointer arithmetic as well
as `page_base`, record stepping, and successor scanning. Do not touch block records in
default create.

- [ ] **Step 4: Add explicit invalid lookup results**

Extend context lookup results with a validity state. Cache malformed page ids separately
for bi/tri and make cache hits return the same invalid state; only cache after the index
page id passed bounds validation. Propagate invalid through `KnModel::logp`,
`has_observed_bigram`, isolation scoring, blend fusion, and user-model fusion without ever
turning it into a finite floor probability. Raise a dedicated decode error before
`expand_range` adds any state, clear the in-progress frontier/result and affected logp
caches, and set a log-once error string. The C API must return a negative result rather
than sorting NaN states, serializing NaN, or reporting “output buffer too small”; Lua then
falls back to smart candidates. Tests must cover a populated user layer and a rare-rank
candidate that exercises `isolation_penalty`; always set and restore a tiny `MH_BLEND`
fixture for the blend-fusion case. Update `tiger_decode` and `tiger_decode_full` catch/status
paths to preserve the dedicated invalid-page error, and add C-API assertions for both
functions plus a Lua mock asserting native nil/error falls back to smart candidates.

- [ ] **Step 5: Retain opt-in full validation**

Call the existing full `validate_pages` only when `MOHU_TIGER_STRICT_VALIDATE` equals `1`. Leave MHKNM01 behavior unchanged.

- [ ] **Step 6: Document native fallback semantics**

Update `tiger_sentence_native/README.md` with the exact strict flag values, metadata-only
default load, touched-page invalidation, dedicated decode error, and Lua smart fallback.
State that MHKNM01/optional container word layers retain existing eager validation.

- [ ] **Step 7: Run native safety and functional suites after the fix**

```bash
make tigerengine-safety
make tigerengine-context
make tigerengine-user-model
make tigerengine-reading-prior
make tigerengine-mobile
make tigerengine-mapping
make tigerengine-word-score
```

Expected: PASS; valid model output stays unchanged.

- [ ] **Step 8: Commit**

```bash
git add tests/tigerengine_mobile_test.cc tests/tigerengine_mobile_cases.py \
  tests/tigerengine_safety_test.cc tests/mohu_word_order_filter_test.lua Makefile \
  tiger_sentence_native/tigerengine.cc tiger_sentence_native/README.md
git commit -m "perf(native): validate model pages on demand"
```

## Chunk 3: Management-Only Memory

### Task 6: Lazily create candidate-manager Memory

**Files:**
- Modify: `tests/mohu_candidate_manager_test.lua`
- Modify: `lua/mohu_candidate_manager.lua`

- [ ] **Step 1: Write failing lifecycle tests**

Mock `Memory`, LevelDb, pin store, config, commit/update notifiers, and context. Assert
the explicit state machine `unattempted -> live|failed`; processor/translator init performs
zero constructions and still stores namespace; navigation/pin routes stay at zero;
`h` query, `u` query, and h/u delete construct once per env; a failed h/u query returns
the exact prompt `〔无法连接用户词典〕`, does not label records as builtin, and performs
no writes; explicit `refresh_memory` clears attempted and retries; `fini` disconnects and
clears object, attempted flag, and namespace.

- [ ] **Step 2: Run and verify RED**

```powershell
& $env:MOHU_LUA_BIN tests/mohu_candidate_manager_test.lua
```

- Expected RED: constructor count is nonzero at init, or a failed query is retried/misclassified.
- [ ] **Step 3: Implement `ensure_memory`**

Always store the configured namespace during init, add a per-env attempted flag, and replace
the current unconditional `refresh_memory` calls on every h/u query with one-shot
`ensure_memory()` calls. Keep each component's wrapper separate. On failure, show exactly
`〔无法连接用户词典〕` for actions requiring Memory and stop before classification or writes.

- [ ] **Step 4: Run and verify GREEN**

Run the command from Step 2. Expected: PASS, including refresh retry and fini cleanup.

- [ ] **Step 5: Commit**

```bash
git add tests/mohu_candidate_manager_test.lua lua/mohu_candidate_manager.lua
git commit -m "perf(lua): defer candidate-manager memory"
```

### Task 7: Lazily create candidate-override Memory

**Files:**
- Modify: `tests/mohu_candidate_override_test.lua`
- Modify: `lua/mohu_candidate_override.lua`

- [ ] **Step 1: Write failing action/lifecycle tests**

Split actions by candidate/state: builtin hidden-record restore in management mode must not
construct Memory; `user_phrase` permanent delete must construct it; normal learning Ctrl+0
must construct it, while management Ctrl+0 only clears override data and must not. A failed
constructor returns `〔无法连接用户词典〕` before any soft-delete/write, and refresh/fini
reset object, attempted flag, and namespace. Use distinct mocks for hidden builtin and
user-created candidates so the existing `user_created_entry`-before-restore ordering is
covered.

- [ ] **Step 2: Run and verify RED**

```powershell
& $env:MOHU_LUA_BIN tests/mohu_candidate_override_test.lua
```

- Expected RED: init constructs Memory or failed user-word actions fall through to a hide.
- [ ] **Step 3: Implement lazy `ensure_override_memory`**

Store namespace/attempted state during configure, branch on management/candidate type before
calling `user_created_entry`, invoke `ensure_override_memory()` only before learned-weight or
user-created checks, preserve explicit refresh after mutation, and release only an actually-
created wrapper.

- [ ] **Step 4: Run and verify GREEN**

Run the command from Step 2. Expected: PASS for builtin restore, user delete, both Ctrl+0
paths, failure prompts, refresh retry, and fini cleanup.

- [ ] **Step 5: Commit**

```bash
git add tests/mohu_candidate_override_test.lua lua/mohu_candidate_override.lua
git commit -m "perf(lua): defer candidate-override memory"
```

## Chunk 4: Integration and Measured Verification

### Task 8: Run generated-data and full regression checks

**Files:**
- Create: `tools/benchmark_tiger_windows.py`
- Create: `tools/benchmark_rime_latency.py`
- Create: `docs/reports/2026-09-05-completion-preserving-latency.md`
- Modify: `tests/test_mohu_config.py` (if deployment-output assertions are not already covered)
- Modify: `.github/workflows/build.yml`
- Modify: `tiger_sentence_native/README.md`
- Modify: `docs/knowledge/cross-candidate-ordering.md`
- Modify only if required by generated output: files reported by `make all`

- [ ] **Step 1: Regenerate and validate flat distributions**

```bash
make all
make dist-zrm dist-flypy
uv run python -m unittest tests.test_mohu_config -v
uv run python -m unittest tests.test_split_distribution -v
```

Inspect `dist-zrm` and `dist-flypy` (ignored output) directly: assert all six completion
namespaces in each source/package matrix are true and every `mohu_*.extended.dict.yaml`
still imports its `*.wanxiang` table. If an installed `rime_deployer` is available, set
`RIME_DEPLOYER` explicitly and build an isolated temporary user directory, then inspect
the six deployed `build/*.schema.yaml` files; otherwise record deployment as skipped, not
passed. Stop and investigate if `make all` produces large unrelated rewrites.

- [ ] **Step 2: Run the repository suite with baseline accounting**

```bash
make test
```

Run focused changed-file tests first and preserve the exit status. Run `make test` in the
supported build shell; if it reproduces the known pre-existing missing research-artifact
failures, record the exact baseline failures and do not call the full suite green. The
changed Lua/native fixture targets must pass independently. Run
`make tigerengine-mobile tigerengine-mapping tigerengine-word-score` explicitly even if
the umbrella target omits them.

- [ ] **Step 3: Add and run the completion-preserving benchmark**

Implement `tools/benchmark_rime_latency.py` by parameterizing the existing temporary
Weasel/Rime ctypes harness: `--rime-dll`, `--shared-data-dir`, `--user-dir`, `--schema`,
`--key-list`, `--runs`, and `--output-csv`. The script must reset/create an isolated user
directory per run, run maintenance, select the schema, process the exact key list, and
write raw per-key rows. It must print the source commit, schema SHA-256, model SHA-256,
Rime/Weasel versions, page size, userdb reset policy, and percentile definition.

Run it for three sessions with completion enabled and compare candidate snapshots with a
tracked parity helper in the Lua tests. Confirm deployed schemas keep both completion flags
true. Write machine-readable rows plus a human summary to
`docs/reports/2026-09-05-completion-preserving-latency.md`.

- [ ] **Step 4: Measure native startup in isolated processes**

`tools/benchmark_tiger_windows.py` accepts `--dll`, `--model`, `--tiny-model`,
`--lexicon`, `--tiny-lexicon`, `--runtime-dir`, `--runs`, and `--output`. It launches a
fresh 64-bit child per sample, uses QPC for create/free wall time, records
`PROCESS_MEMORY_COUNTERS_EX`, `QueryWorkingSetEx` resident pages, and
`VirtualQueryEx`/`GetMappedFileNameW` primary mappings. It emits one JSON object per
child and a summary manifest with tool versions and SHA-256 hashes. Measure these paired
configurations after one warm-up each:

```text
real model + one-line lexicon: create wall time, page faults, WS delta
tiny model + real lexicon:     isolate lexicon fixed cost
real model + real lexicon:     create wall time, page faults, WS/private delta
VirtualQueryEx:                primary-model mapping count and resident pages
after free:                    mapping count and WS
```

Acceptance follows the design document: model-only create <=50 ms, page faults <=2,000, model WS delta <30 MB, one primary mapping, completion-on P99 <50 ms, and finite-stream output parity.

- [ ] **Step 5: Add the Windows CI fixture gate**

In `.github/workflows/build.yml`, after the existing Windows DLL/model smoke build and
before runtime-closure upload, run the portable `tigerengine-mobile`,
`tigerengine-mapping`, and `tigerengine-windows-memory` targets in `msys2 {0}`. Copy
`lua54.dll` beside the DLL, export
`TIGER_ENGINE_DLL=$PWD/libtigerengine.dll`, `TIGER_NGRAM=$PWD/mohu-sentence-ngram-v5.bin`,
and `TIGER_LEXICON=$PWD/tiger_sentence_native/data/zrm/mohu_zrm.lexicon.txt`, then run
`python tests/tigerengine_windows_mapping_test.py`. Upload its JSON manifest as a CI
artifact; fail on missing model/DLL rather than silently skipping. Keep POSIX-only safety
targets in their existing shell.

The workflow step uses the already-built root `libtigerengine.dll`, downloaded root
`mohu-sentence-ngram-v5.bin`, repository lexicon, and root `lua54.dll`; it sets `PATH=$PWD`
before the Python call, runs with `shell: msys2 {0}`, uploads
`native-memory-manifest.json`, and fails if the probe exits 2 for missing inputs.

- [ ] **Step 6: Update durable documentation with measured results**

Update `tiger_sentence_native/README.md` and `docs/knowledge/cross-candidate-ordering.md` with the strict validation flag, bounded-filter fallback, retained completion semantics, benchmark method, and measured numbers. Do not claim unmeasured Windows/TSF behavior.

- [ ] **Step 7: Commit generated, workflow, report, and documentation changes**

```bash
git add tools/benchmark_tiger_windows.py tools/benchmark_rime_latency.py \
  docs/reports/2026-09-05-completion-preserving-latency.md \
  tests/test_mohu_config.py .github/workflows/build.yml \
  tiger_sentence_native/README.md docs/knowledge/cross-candidate-ordering.md
git commit -m "docs: record completion-preserving latency results"
```

- [ ] **Step 8: Request final code review**

Review the complete range from `origin/main` to HEAD for candidate correctness, model safety, cleanup paths, missing tests, and unrelated generated churn. Resolve every Critical/Important finding before handoff.
