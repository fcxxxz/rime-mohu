# Personal Word Fusion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore gradual userdb learning and auxiliary-selected word priority across the smart and Tiger native candidate paths.

**Architecture:** Rime `smart` remains authoritative when a learned two- or three-character word consumes the complete active input. Tiger remains authoritative for sentence decoding and receives every active multi-character userdb row, including learned static entries. Candidate ownership changes at the existing reorder filter; userdb scanning remains confined to the existing idle snapshot refresh.

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

### Task 2: Restore userdb access for long smart queries

**Files:**
- Modify: `tests/test_mohu_tiger_sentence_native.py`
- Modify: `mohu_zrm.schema.yaml`
- Modify: `mohu_flypy.schema.yaml`

- [ ] **Step 1: Write the failing schema test**

For both public schemas, assert that `smart_static` uses the same user dictionary as `smart` and does not disable userdb:

```python
self.assertEqual(schema["smart"]["user_dict"], schema["smart_static"]["user_dict"])
self.assertNotEqual("", schema["smart_static"]["user_dict"])
self.assertNotEqual(False, schema["smart_static"].get("enable_user_dict", True))
```

- [ ] **Step 2: Verify the test fails**

Run: `uv run python -m unittest tests.test_mohu_tiger_sentence_native -v`

Expected: FAIL because both `smart_static` sections currently set an empty user dictionary and `enable_user_dict: false`.

- [ ] **Step 3: Restore the shared userdb namespace**

Set `smart_static/user_dict` to `mohu_zrm_tiger_prefix2` and `mohu_flypy_tiger_prefix2`, respectively, and remove `enable_user_dict: false`.

- [ ] **Step 4: Verify schema tests pass**

Run: `uv run python -m unittest tests.test_mohu_tiger_sentence_native -v`

Expected: all schema tests pass.

### Task 3: Let learned complete words keep smart ordering

**Files:**
- Modify: `tests/mohu_reorder_filter_lexicon_test.lua`
- Modify: `lua/mohu_reorder_filter.lua`

- [ ] **Step 1: Write failing ownership tests**

Extend the test candidate with optional `entry`, `start`, and `_end` fields. Add a complete learned three-character word case:

```lua
local learned = candidate("user_phrase", "简快符", "jm ky fu")
learned.entry = { commit_count = 8 }
local output = run({
  candidate("mohu_zrm", "渐伏", "jmky fu"),
  candidate("mohu_zrm", "见快符", "jm ky fu"),
  learned,
  candidate("sentence", "监会符", "jm ky fu"),
}, "jmkyfu")
assert(output[1].text == "简快符")
```

Also retain the existing two-character auxiliary regression so `yh jcbt` remains native-first when no bare complete learned word exists.

- [ ] **Step 2: Verify the ownership test fails**

Run: `lua tests/mohu_reorder_filter_lexicon_test.lua`

Expected: FAIL with native `渐伏` before learned `简快符`.

- [ ] **Step 3: Detect complete learned smart words**

Add a helper that unwraps the genuine candidate and returns true only when:

```lua
type is phrase or user_phrase
text length is 2 or 3
preedit has exactly one two-letter token per character
canonical preedit equals the complete active input
candidate is user_phrase or entry.commit_count > 0
```

This deliberately excludes native auxiliary paths such as `jmky fu` and `yh jcbt`.

- [ ] **Step 4: Move native output behind smart for that composition**

Record `ctx.smart_word_authoritative` while collecting non-native candidates. In `Top.flush`, preserve fixed/Pin output first. When the flag is true, yield smart candidates in their existing order before eligible native candidates; otherwise retain the existing native-first order. `uniquifier` keeps the first text when smart/native duplicate.

- [ ] **Step 5: Verify reorder tests pass**

Run: `lua tests/mohu_reorder_filter_lexicon_test.lua`

Expected: learned complete-word case is smart-first; existing native-only, long-sentence, and two-character auxiliary cases remain unchanged.

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
