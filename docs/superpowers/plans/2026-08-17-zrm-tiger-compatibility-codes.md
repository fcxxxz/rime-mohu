# Natural-code Tiger Compatibility Codes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Natural-code compatibility spellings derived independently from Tiger positions three and four, then promote unoccupied compatibility spellings to deterministic multi-short-code fixed rows over the 8,105-character NAS order.

**Architecture:** A focused `tools/tiger_compatibility.py` module derives compatibility auxiliary pairs from every tied longest official Tiger code. `tools/gen_chars.py` emits those pairs only for modern Simplified readings, while `tools/rebuild_fixed_tiger.py` performs a second, Natural-code-only fixed allocation after the existing legacy cascade. The fixed allocator uses a maximum bipartite matching for baseline-unresolved characters, then assigns every remaining free compatibility code by NAS rank.

**Tech Stack:** Python 3.12, `unittest`, Rime YAML dictionaries, `uv`, Make.

---

## File Structure

- Create `tools/tiger_compatibility.py`: pure compatibility-auxiliary derivation from longest Tiger codes.
- Modify `tools/gen_chars.py`: add compatibility spellings to modern Simplified Natural-code rows.
- Modify `tools/rebuild_fixed_tiger.py`: load and validate the 8,105-character NAS order, allocate fixed compatibility rows, and expose collision audit helpers.
- Modify `tests/test_tiger_aux.py`: unit, repository, and collision-count regression tests.
- Modify `Makefile`: declare Tiger compatibility and NAS ranking inputs as generation dependencies.
- Regenerate `mohu_zrm.chars.dict.yaml`: Natural-code smart character rows with compatibility spellings.
- Regenerate `mohu_zrm_tiger_fixed_legacy.dict.yaml`: Natural-code legacy fixed compatibility rows.
- Regenerate `mohu_zrm_fixed_legacy.dict.yaml`: parent fixed dictionary with the same generated rows.
- Verify `mohu_flypy*`, `mohu_zrm_tiger_fixed.dict.yaml`, `mohu_zrm_fixed.dict.yaml`, schema, Lua, and fixed word blocks remain unchanged.

### Task 1: Derive Both Tiger Compatibility Positions

**Files:**
- Create: `tools/tiger_compatibility.py`
- Modify: `tests/test_tiger_aux.py`
- Test: `tests/test_tiger_aux.py`

- [ ] **Step 1: Write failing derivation tests**

Import the wished-for functions and add tests proving positions three and four are independent and stable:

```python
from tools.tiger_compatibility import (
    build_compatibility_auxiliary_map,
    derive_compatibility_auxiliaries,
)

def test_derives_third_and_fourth_tiger_positions(self):
    self.assertEqual(
        derive_compatibility_auxiliaries(["lwxn"]),
        ["lx", "ln"],
    )
    self.assertEqual(
        derive_compatibility_auxiliaries(["lwni"]),
        ["ln", "li"],
    )

def test_deduplicates_equal_positions_without_stopping_early(self):
    self.assertEqual(derive_compatibility_auxiliaries(["lwcc"]), ["lc"])
    self.assertEqual(
        derive_compatibility_auxiliaries(["abcd", "abed"]),
        ["ac", "ad", "ae"],
    )

def test_ignores_tiger_codes_shorter_than_three(self):
    self.assertEqual(derive_compatibility_auxiliaries(["a", "ab"]), [])
```

- [ ] **Step 2: Run the derivation tests and verify RED**

Run:

```bash
uv run python -m unittest \
  tests.test_tiger_aux.TigerCompatibilityUnitTest -v
```

Expected: `ERROR` because `tools.tiger_compatibility` does not exist.

- [ ] **Step 3: Implement the pure derivation module**

Create `tools/tiger_compatibility.py` with stable longest-code selection and position-by-position generation:

```python
from collections.abc import Iterable
from pathlib import Path

from tools.tiger_aux import load_tiger_codes, select_longest_codes


def derive_compatibility_auxiliaries(codes: Iterable[str]) -> list[str]:
    result: list[str] = []
    for code in select_longest_codes(codes):
        if len(code) >= 3:
            result.append(code[0] + code[2])
        if len(code) >= 4:
            result.append(code[0] + code[3])
    return list(dict.fromkeys(result))


def build_compatibility_auxiliary_map(path: Path) -> dict[str, list[str]]:
    return {
        char: auxiliaries
        for char, codes in load_tiger_codes(path).items()
        if (auxiliaries := derive_compatibility_auxiliaries(codes))
    }
```

- [ ] **Step 4: Run the derivation tests and verify GREEN**

Run the Task 1 command again. Expected: all compatibility unit tests pass.

- [ ] **Step 5: Commit the derivation unit**

```bash
git add tools/tiger_compatibility.py tests/test_tiger_aux.py
git commit -m "feat: 生成虎码三四位兼容辅码"
```

### Task 2: Emit Modern Simplified Compatibility Spellings

**Files:**
- Modify: `tools/gen_chars.py`
- Modify: `tests/test_tiger_aux.py`
- Modify: `Makefile`
- Test: `tests/test_tiger_aux.py`

- [ ] **Step 1: Write a failing generator test**

Add a subprocess test that generates the Simplified dictionary and checks all required spellings while excluding an unsupported reading fixture through a focused helper assertion:

```python
def test_simplified_character_dictionary_contains_both_compatibility_positions(self):
    result = subprocess.run(
        ["uv", "run", "tools/gen_chars.py", "--simplified"],
        cwd=self.root,
        capture_output=True,
        text=True,
        check=False,
    )
    self.assertEqual(result.returncode, 0, result.stderr)
    rows = set(result.stdout.splitlines())
    self.assertIn("莺\tyy;lx\t25291", rows)
    self.assertIn("莺\tyy;ln\t25291", rows)
    self.assertIn("萤\tyy;lc\t13116", rows)
    self.assertIn("莹\tyy;ln\t106488", rows)
    self.assertIn("莹\tyy;li\t106488", rows)
```

- [ ] **Step 2: Run the generator test and verify RED**

Run:

```bash
uv run python -m unittest \
  tests.test_tiger_aux.FixedDictionaryTest.test_simplified_character_dictionary_contains_both_compatibility_positions -v
```

Expected: `FAIL` because current output contains only `yy;lw` for these characters.

- [ ] **Step 3: Add compatibility spellings to `gen_chars.py`**

Load the compatibility map once and append it only for an exact modern `(char, pinyin)` reading:

```python
from tiger_compatibility import build_compatibility_auxiliary_map

compatibility_aux_table = build_compatibility_auxiliary_map(Path("tiger.dict.yaml"))

for ((char, py), w) in freq_table.items():
    if modern_readings is not None:
        w = simplified_reading_weight(char, py, w, modern_readings)
    sp = zrmify(py)
    auxiliaries = list(aux_table[char])
    if modern_readings is not None and (char, py) in modern_readings:
        auxiliaries.extend(compatibility_aux_table.get(char, []))
    for aux in dict.fromkeys(auxiliaries):
        print(f"{char}\t{sp};{aux}\t{w}")
```

Keep original primary auxiliaries first so existing generated order is unchanged.

- [ ] **Step 4: Declare generator dependencies**

Add `tiger.dict.yaml`, `tools/tiger_compatibility.py`, and `tools/tiger_aux.py` to the `mohu_zrm.chars.dict.yaml` dependency list in `Makefile`.

- [ ] **Step 5: Run focused and existing generator tests**

Run:

```bash
uv run python -m unittest \
  tests.test_tiger_aux.FixedDictionaryTest.test_simplified_character_dictionary_contains_both_compatibility_positions \
  tests.test_tiger_aux.TigerAuxRepositoryTest.test_generators_run_as_direct_scripts -v
```

Expected: both tests pass and existing primary rows remain present.

- [ ] **Step 6: Commit smart compatibility spellings**

```bash
git add tools/gen_chars.py Makefile tests/test_tiger_aux.py
git commit -m "feat: 加入自然码兼容输入"
```

### Task 3: Load and Validate the 8,105-character NAS Order

**Files:**
- Modify: `tools/rebuild_fixed_tiger.py`
- Modify: `tests/test_tiger_aux.py`
- Test: `tests/test_tiger_aux.py`

- [ ] **Step 1: Write failing rank validation tests**

Use temporary charset and TSV files to specify exact ordering and error behavior:

```python
def test_loads_compatibility_order_from_race_profile(self):
    with TemporaryDirectory() as directory:
        root = Path(directory)
        charset = root / "chars.txt"
        profile = root / "race.tsv"
        charset.write_text("# group\n甲\n乙\n丙\n", encoding="utf-8")
        profile.write_text(
            "rank\tchar\tfrequency_weight\n1\t乙\t3\n2\t甲\t2\n3\t丙\t1\n",
            encoding="utf-8",
        )
        self.assertEqual(
            rebuild_fixed_tiger.load_compatibility_order(charset, profile),
            ["乙", "甲", "丙"],
        )

def test_rejects_incomplete_compatibility_order(self):
    with TemporaryDirectory() as directory:
        root = Path(directory)
        charset = root / "chars.txt"
        profile = root / "race.tsv"
        charset.write_text("甲\n乙\n丙\n", encoding="utf-8")
        profile.write_text(
            "rank\tchar\tfrequency_weight\n1\t乙\t3\n2\t甲\t2\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "profile does not match charset"):
            rebuild_fixed_tiger.load_compatibility_order(charset, profile)
```

- [ ] **Step 2: Run rank tests and verify RED**

Run the two new tests. Expected: `FAIL` because `load_compatibility_order` is absent.

- [ ] **Step 3: Implement structured TSV and charset loading**

Use `csv.DictReader(delimiter="\t")`, skip comments and blank lines in the charset, reject duplicate ranks or characters, require consecutive ranks from one, and require exact set equality:

```python
def load_compatibility_order(charset_path: Path, profile_path: Path) -> list[str]:
    charset = [
        line.strip()
        for line in charset_path.read_text(encoding="utf-8-sig").splitlines()
        if len(line.strip()) == 1 and not line.startswith("#")
    ]
    rows = list(csv.DictReader(
        profile_path.read_text(encoding="utf-8-sig").splitlines(),
        delimiter="\t",
    ))
    order = [row["char"] for row in rows]
    ranks = [int(row["rank"]) for row in rows]
    if (
        len(charset) != len(set(charset))
        or len(order) != len(set(order))
        or ranks != list(range(1, len(order) + 1))
        or set(order) != set(charset)
    ):
        raise ValueError("compatibility profile does not match charset")
    return order
```

In production, assert the resulting length is `8105`.

- [ ] **Step 4: Run rank tests and verify GREEN**

Run the two new tests again. Expected: both pass.

- [ ] **Step 5: Commit rank validation**

```bash
git add tools/rebuild_fixed_tiger.py tests/test_tiger_aux.py
git commit -m "feat: 校验兼容码字频顺序"
```

### Task 4: Allocate Fixed Compatibility Codes by Maximum Coverage

**Files:**
- Modify: `tools/rebuild_fixed_tiger.py`
- Modify: `tests/test_tiger_aux.py`
- Test: `tests/test_tiger_aux.py`

- [ ] **Step 1: Write failing allocator tests**

Specify the allocator API and cover free, blocked, out-of-scope, maximum-coverage, and multiple-code behavior:

```python
def test_compatibility_allocation_maximizes_unresolved_coverage(self):
    primary = [
        SourceEntry("甲", "aaxx", 30),
        SourceEntry("乙", "bbxx", 20),
    ]
    compatibility = [
        SourceEntry("甲", "ccca", 30),
        SourceEntry("甲", "cccb", 30),
        SourceEntry("乙", "ccca", 20),
    ]
    rows = rebuild_fixed_tiger.allocate_compatibility_codes(
        [], primary, compatibility, ["甲", "乙"]
    )
    self.assertEqual(
        {(row.text, row.code) for row in rows},
        {("乙", "ccca"), ("甲", "cccb")},
    )

def test_compatibility_allocation_keeps_both_free_codes_for_one_character(self):
    compatibility = [
        SourceEntry("莹", "yyln", 100),
        SourceEntry("莹", "yyli", 100),
    ]
    rows = rebuild_fixed_tiger.allocate_compatibility_codes(
        [], [SourceEntry("莹", "yylw", 100)], compatibility, ["莹"]
    )
    self.assertEqual(
        {(row.text, row.code) for row in rows},
        {("莹", "yyln"), ("莹", "yyli")},
    )
```

Add two more focused cases: a base row owned by another visible character blocks the code, while the same base owner outside `visible_order` does not block it.

```python
def test_visible_fixed_owner_blocks_compatibility_promotion(self):
    base = [TableEntry("乙", "ccca", 20, 0)]
    rows = rebuild_fixed_tiger.allocate_compatibility_codes(
        base,
        [SourceEntry("甲", "aaxx", 30)],
        [SourceEntry("甲", "ccca", 30)],
        ["甲", "乙"],
    )
    self.assertNotIn(("甲", "ccca"), {(row.text, row.code) for row in rows})

def test_out_of_scope_fixed_owner_does_not_block_promotion(self):
    base = [TableEntry("乙", "ccca", 20, 0)]
    rows = rebuild_fixed_tiger.allocate_compatibility_codes(
        base,
        [SourceEntry("甲", "aaxx", 30)],
        [SourceEntry("甲", "ccca", 30)],
        ["甲"],
    )
    self.assertIn(("甲", "ccca"), {(row.text, row.code) for row in rows})
```

- [ ] **Step 2: Run allocator tests and verify RED**

Run all new `allocate_compatibility_codes` tests. Expected: `FAIL` because the function is absent.

- [ ] **Step 3: Implement baseline coverage and occupancy**

Index base rows by character and exact code. A visible character is baseline-unresolved when none of its base fixed codes prefixes any of its primary full codes. Block a compatibility code only when its exact base owners intersect the visible set and include a different character. Use this signature:

```python
def allocate_compatibility_codes(
    base_rows: list[TableEntry],
    primary_entries: list[SourceEntry] | list[TableEntry],
    compatibility_entries: list[SourceEntry] | list[TableEntry],
    visible_order: list[str],
) -> list[TableEntry]:
    """Return base rows plus fixed Natural-code compatibility rows."""
```

- [ ] **Step 4: Implement deterministic maximum matching**

Build edges from baseline-unresolved characters to unblocked compatibility codes. Process characters in high-to-low NAS order using an augmenting-path matcher; an already matched high-priority character may move to another edge but must remain matched when a lower-priority character is considered. This maximizes cardinality while preserving the highest possible ranked matched set.

- [ ] **Step 5: Allocate every remaining free compatibility code**

After rescue matching, traverse free compatibility codes in lexical order and assign each to its highest NAS-ranked visible candidate. Append rows after all base rows, preserve the source reading weight, and call `rank_candidates` over the combined result. Do not stop after a character receives its first code.

- [ ] **Step 6: Run allocator tests and verify GREEN**

Run all focused allocator tests. Expected: free and out-of-scope slots promote, visible occupied slots do not, maximum coverage succeeds, and `莹` keeps two fixed rows.

- [ ] **Step 7: Commit fixed allocation**

```bash
git add tools/rebuild_fixed_tiger.py tests/test_tiger_aux.py
git commit -m "feat: 分配自然码兼容简码"
```

### Task 5: Wire Natural-code Generation and Lock Repository Results

**Files:**
- Modify: `tools/rebuild_fixed_tiger.py`
- Modify: `tests/test_tiger_aux.py`
- Modify: `Makefile`
- Test: `tests/test_tiger_aux.py`

- [ ] **Step 1: Write failing repository acceptance tests**

Add separate smart-dictionary and fixed-table assertions. Every third/fourth spelling must remain inputtable, but a shared fixed code has one visible owner:

```python
self.assertIn(("莺", "yy;lx"), zrm_smart_pairs)
self.assertIn(("莺", "yy;ln"), zrm_smart_pairs)
self.assertIn(("萤", "yy;lc"), zrm_smart_pairs)
self.assertIn(("莹", "yy;ln"), zrm_smart_pairs)
self.assertIn(("莹", "yy;li"), zrm_smart_pairs)

self.assertIn(("蕉", "jcl"), zrm_legacy_pairs)
self.assertIn(("藠", "jclu"), zrm_legacy_pairs)
self.assertIn(("莺", "yylx"), zrm_legacy_pairs)
self.assertIn(("萤", "yylc"), zrm_legacy_pairs)
self.assertIn(("莹", "yyln"), zrm_legacy_pairs)
self.assertIn(("萦", "yyli"), zrm_legacy_pairs)
self.assertNotIn(("莺", "yyln"), zrm_legacy_pairs)
self.assertNotIn(("莹", "yyli"), zrm_legacy_pairs)
```

Also snapshot the four unique/Flypy generated tables in memory and assert the generator leaves them byte-for-byte unchanged.

- [ ] **Step 2: Run acceptance tests and verify RED**

Expected: compatibility row assertions fail against current committed dictionaries.

- [ ] **Step 3: Wire compatibility inputs only into the Natural-code legacy build**

In `main()`, load the compatibility auxiliary map and validated NAS order. Build compatibility `SourceEntry` rows from the modern reading table and pass them only to the Natural-code legacy allocation. Leave Flypy and both unique allocations on their current inputs.

- [ ] **Step 4: Add fixed-generation dependencies**

Add `tiger.dict.yaml`, `tools/data/simp_chars.txt`, `research/tiger_aux/output/race_profile.tsv`, and `tools/tiger_compatibility.py` to the `fixed_tiger` dependency list in `Makefile`.

- [ ] **Step 5: Run allocator and acceptance tests**

Run:

```bash
uv run python -m unittest tests.test_tiger_aux.FixedDictionaryTest -v
```

Expected: all focused allocation and repository behavior tests pass after generated files are rebuilt in Task 6.

- [ ] **Step 6: Commit Natural-code wiring**

```bash
git add tools/rebuild_fixed_tiger.py Makefile tests/test_tiger_aux.py
git commit -m "feat: 接入自然码兼容码生成"
```

### Task 6: Add Collision Regression Audit and Regenerate Outputs

**Files:**
- Modify: `tools/rebuild_fixed_tiger.py`
- Modify: `tests/test_tiger_aux.py`
- Regenerate: `mohu_zrm.chars.dict.yaml`
- Regenerate: `mohu_zrm_tiger_fixed_legacy.dict.yaml`
- Regenerate: `mohu_zrm_fixed_legacy.dict.yaml`

- [ ] **Step 1: Write failing collision-summary tests**

Add a pure audit helper that reproduces the agreed full-code collision scope. For every modern primary path, suppress a character already covered by a shorter matching legacy code. Keep the exact four-key fixed owner in its original full-code group, and remove a loser from that group only when it gains a fixed compatibility code. Ignore collisions among historical one/two-key multi-short rows because this audit measures remaining full-code collisions after 出简让全. Assert the production baselines and compatibility results:

```python
self.assertEqual(before[1500], (0, 0))
self.assertEqual(before[3500], (31, 44))
self.assertEqual(before[6000], (134, 188))
self.assertEqual(before[8105], (262, 354))
self.assertEqual(after[1500], (0, 0))
self.assertEqual(after[3500], (1, 1))
self.assertEqual(after[6000], (5, 5))
self.assertEqual(after[8105], (11, 11))
```

The helper must report `8088` codeable rows at the 8105 threshold and list the exact eleven residual groups from the design.

- [ ] **Step 2: Run collision tests and verify RED**

Expected: `FAIL` because collision audit support and fixed compatibility rows are absent.

- [ ] **Step 3: Implement the pure collision audit helper**

Keep the audit independent from Rime runtime state. Use modern primary source entries, baseline fixed rows, compatibility fixed rows, and the NAS visible order. For each original full code, deduplicate characters in NAS order, keep groups with at least two characters, and count all characters after the first as non-first entries. Return structured groups so tests can assert exact residual characters and blocked compatibility codes:

```python
def audit_full_code_collisions(
    primary_entries: list[SourceEntry] | list[TableEntry],
    baseline_rows: list[TableEntry],
    compatibility_rows: list[TableEntry],
    visible_order: list[str],
    thresholds: tuple[int, ...],
) -> dict[int, CollisionAudit]:
    """Audit original full-code groups after shorter and compatibility exits."""
```

- [ ] **Step 4: Run `make quick` and inspect the generated diff**

The README requires `make quick` before deploying from the main branch. Run:

```bash
make quick
```

Expected: successful generation. Stop if unrelated dictionaries, word blocks, Flypy outputs, schema, or Lua files receive broad changes.

- [ ] **Step 5: Verify generated examples and collision counts**

Run the new collision tests and the full `tests.test_tiger_aux` module. Expected: `0/1/5/11` final groups and exact compatibility examples.

- [ ] **Step 6: Check deterministic generation**

Run:

```bash
uv run tools/rebuild_fixed_tiger.py --check
git diff --check
```

Expected: no stale generated files and no whitespace errors.

- [ ] **Step 7: Commit generated outputs and regression audit**

```bash
git add tools/rebuild_fixed_tiger.py tests/test_tiger_aux.py \
  mohu_zrm.chars.dict.yaml \
  mohu_zrm_tiger_fixed_legacy.dict.yaml \
  mohu_zrm_fixed_legacy.dict.yaml
git commit -m "dict: 生成自然码虎码兼容码"
```

### Task 7: Full Verification

**Files:**
- Verify all changed files and generated outputs.

- [ ] **Step 1: Run focused Python tests**

```bash
uv run python -m unittest tests.test_tiger_aux -v
```

Expected: all tests pass.

- [ ] **Step 2: Run Python lint**

```bash
uv run --with ruff ruff check \
  tools/tiger_compatibility.py \
  tools/gen_chars.py \
  tools/rebuild_fixed_tiger.py \
  tests/test_tiger_aux.py
```

Expected: no lint errors.

- [ ] **Step 3: Run complete generation and project tests**

```bash
make all
make test
```

Expected: both commands pass. If an environment dependency such as Mira, Lua, OpenCC, or Node is unavailable, record the exact failing command and continue with all independently runnable suites.

- [ ] **Step 4: Inspect final scope**

Confirm with `git diff --stat` and targeted diffs that only Natural-code compatibility inputs and Natural-code legacy fixed generated character blocks changed. Confirm Flypy, unique fixed rows, fixed word blocks, schema, and Lua are byte-for-byte unchanged.

- [ ] **Step 5: Commit verification fixes only when files changed**

Inspect `git status --short`. If verification required a tracked correction, stage only the named corrected files and commit them with `fix: 完善自然码兼容码校验`. If the worktree has no new correction, do not create a commit.
