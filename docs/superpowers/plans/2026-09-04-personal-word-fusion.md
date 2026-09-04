# Personal Word Fusion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify static, learned, auxiliary, and Tiger context evidence at the candidate-path level without key-length ownership branches.

**Architecture:** Rime userdb is the durable source of learned counts; Tiger owns the sentence path lattice and consumes static plus learned lexical edges. Native receives O(1) commit deltas immediately and full snapshots during idle reconciliation. The existing reorder filter only deduplicates logical paths and preserves hard Pin/manual overrides; no translator list is globally first by input length.

**Tech Stack:** Rime YAML, Lua 5.4, C++17 native decoder, Python unittest, Lua assertion tests.

---

### Task 1: Export learned static words to Tiger

**Files:**
- Modify: `tests/mohu_personal_lexicon_test.lua`
- Modify: `lua/mohu_personal_lexicon.lua`

- [ ] **Step 1: Write the failing snapshot test**

Change the expected rows so the built-in learned entry `内置` is retained and serialized alongside user-created words:

```lua
local rows = require("mohu_personal_lexicon").collect(memory)
assert(#rows == 3, #rows)
assert(rows[1].text == "内置" and rows[1].commits == 99)
assert(rows[2].text == "晴跟打")
assert(rows[3].text == "比亚迪")
local payload, count = require("mohu_personal_lexicon").serialize(rows)
assert(count == 3)
assert(payload:find("neiz\t内置\t99\n", 1, true))
```

- [ ] **Step 2: Verify the test fails**

Run: `lua tests/mohu_personal_lexicon_test.lua`

Expected: FAIL because `accept_entry` discards `内置` through `is_builtin`.

- [ ] **Step 3: Remove the built-in exclusion**

Keep positive commits, UTF-8, multi-character, code normalization, and duplicate validation, but remove the `is_builtin(...)` rejection from `accept_entry`. The native engine already updates a matching static edge instead of adding a duplicate.

- [ ] **Step 4: Verify snapshot tests pass**

Run: `lua tests/mohu_personal_lexicon_test.lua`

Expected: `personal lexicon tests passed`.

### Task 2: Add immediate native personal-edge deltas

**Files:**
- Modify: `tests/tigerengine_safety_test.cc`
- Modify: `tests/tigerengine_lua_safety_test.cc`
- Modify: `tiger_sentence_native/tigerengine.h`
- Modify: `tiger_sentence_native/tigerengine.cc`
- Modify: `tiger_sentence_native/tigerengine_lua.cc`
- Modify: `tiger_sentence_native/mohu_tiger_sentence.lua`

- [ ] **Step 1: Write the failing delta test**

Add an API test that inserts a learned edge, increases its count twice, and requires
the edge to be visible without calling `set_personal_lexicon`:

```cpp
assert(tiger_engine_adjust_personal(handle, "jmkyfu", "简快符", 1) == 1);
assert(tiger_engine_adjust_personal(handle, "jmkyfu", "简快符", 1) == 1);
assert(tiger_decode_full(handle, "jmkyfu", 0, output, sizeof(output)) >= 1);
assert(std::strstr(output, "简快符") != nullptr);
```

- [ ] **Step 2: Verify the test fails**

Run: `make tigerengine-safety`

Expected: FAIL because the delta API is not defined.

- [ ] **Step 3: Implement O(1) edge adjustment**

Track per-edge commit counts in `Lexicon`, update an existing static edge or add a
personal edge, recompute its monotonic calibrated prior, invalidate the native decode
cache, and expose the operation through the Lua ABI. Missing/old ABI remains optional
and fail-open on the Lua side.

- [ ] **Step 4: Bridge native commits immediately**

After the existing native candidate userdb write succeeds, call the delta API with
the normalized bare code and text. Keep the existing full snapshot refresh for
startup, sync, deletion, and reconciliation.

- [ ] **Step 5: Verify delta tests pass**

Run: `make tigerengine-safety && lua tests/mohu_tiger_two_char_test.lua`

Expected: both commands exit zero.

### Task 3: Unify candidate-path scoring and remove list ownership branches

**Files:**
- Modify: `tests/mohu_reorder_filter_lexicon_test.lua`
- Modify: `lua/mohu_reorder_filter.lua`
- Modify: `lua/mohu_word_order_filter.lua`

- [ ] **Step 1: Write the failing path-identity tests**

Require same text/code/span candidates from native and smart to remain one logical
candidate, while different spans (a partial word versus a complete word) remain
distinct paths. Require current auxiliary matches to keep their path evidence.

```lua
local native = candidate("mohu_zrm", "简快符", "jmr ky fu")
local smart = candidate("user_phrase", "简快符", "jm ky fu")
assert(filter.logical_key(native) == filter.logical_key(smart))
```

- [ ] **Step 2: Verify the ownership test fails**

Run: `lua tests/mohu_reorder_filter_lexicon_test.lua`

Expected: FAIL because the filter has no shared logical-path key.

- [ ] **Step 3: Add logical-path identity and merge**

Normalize genuine candidate span, text, and code-bearing preedit. Merge duplicate
smart/native representations into one logical path before ordering; retain the
strongest lexical/user metadata and current auxiliary evidence.

- [ ] **Step 4: Apply one score policy**

Use static lexical prior + monotonic commit prior + current auxiliary evidence +
Tiger context score for every path. Keep Pin/manual order outside the score. Do not
branch on six/seven/eight-key length.

- [ ] **Step 5: Verify reorder tests pass**

Run: `lua tests/mohu_reorder_filter_lexicon_test.lua`

Expected: duplicate paths collapse, partial paths remain distinct, and existing
native-only/long-sentence/auxiliary cases remain unchanged.

### Task 4: Preserve native commit learning through wrappers

**Files:**
- Modify: `tests/mohu_tiger_two_char_test.lua`
- Modify: `tiger_sentence_native/mohu_tiger_sentence.lua`

- [ ] **Step 1: Retain the red-green regression already added**

The test commits a wrapped `mohu_reordered` candidate whose genuine value is the native three-character `简快符`, and requires one normalized userdb update:

```lua
assert(#userdb_writes == 1 and userdb_writes[1].text == "简快符" and
  userdb_writes[1].code == "jm;ra ky;hn fu;rj ")
```

- [ ] **Step 2: Keep safe genuine-candidate unwrapping**

Read `type`, `preedit`, and `text` from `cand:get_genuine()` when available, falling back to the outer candidate if unwrapping fails.

- [ ] **Step 3: Verify native Lua learning tests pass**

Run: `lua tests/mohu_tiger_two_char_test.lua && lua tests/mohu_tiger_sentence_native_test.lua && lua tests/mohu_tiger_selected_segment_test.lua`

Expected: all three suites pass.

### Task 5: Verify behavior, performance boundaries, and documentation

**Files:**
- Modify: `tiger_sentence_native/README.md`
- Modify: `docs/knowledge/cross-candidate-ordering.md`

- [ ] **Step 1: Document ownership and learning semantics**

Document that complete learned two-/three-character bare word paths preserve smart/userdb order, auxiliary selection increments the normalized base userdb entry once, and native sentence snapshots include learned static entries without duplicating static edges.

- [ ] **Step 2: Run focused verification**

Run:

```bash
lua tests/mohu_personal_lexicon_test.lua
lua tests/mohu_reorder_filter_lexicon_test.lua
lua tests/mohu_tiger_two_char_test.lua
lua tests/mohu_tiger_sentence_native_test.lua
lua tests/mohu_tiger_selected_segment_test.lua
lua tests/mohu_contextual_translator_test.lua
uv run python -m unittest tests.test_mohu_tiger_sentence_native tests.test_mohu_config -v
make tigerengine-safety
```

Expected: every command exits zero.

- [ ] **Step 3: Inspect the scoped diff**

Run:

```bash
git diff --check -- \
  lua/mohu_personal_lexicon.lua lua/mohu_reorder_filter.lua \
  tiger_sentence_native/mohu_tiger_sentence.lua \
  mohu_zrm.schema.yaml mohu_flypy.schema.yaml \
  tests/mohu_personal_lexicon_test.lua \
  tests/mohu_reorder_filter_lexicon_test.lua \
  tests/mohu_tiger_two_char_test.lua \
  tests/test_mohu_tiger_sentence_native.py \
  tiger_sentence_native/README.md docs/knowledge/cross-candidate-ordering.md
```

Expected: no whitespace errors in the scoped files.
