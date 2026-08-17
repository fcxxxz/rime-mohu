# Split Release Packages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and publish independent Natural Code and Flypy Rime archives whose files and scheme menus contain no references to the other double-pinyin scheme.

**Architecture:** A Python distribution builder copies an explicit shared runtime set plus one `mohu_<scheme>*` set and filters the other scheme from `default.yaml`. Make exposes two concrete targets, while GitHub Actions uploads two build artifacts and replaces the rolling Release's combined ZIP with two scheme-specific ZIP files.

**Tech Stack:** Python 3.12 standard library, GNU Make, shell assertions, GitHub Actions YAML, GitHub CLI

---

### Task 1: Add scheme-specific distribution builds

**Files:**
- Create: `tests/test_split_distribution.py`
- Create: `tools/build_split_dist.py`
- Modify: `Makefile`

- [ ] **Step 1: Write the failing distribution test**

Create a unittest that runs `make dist-zrm dist-flypy`, then checks each output's files and `default.yaml`:

```python
EXPECTED_SCHEMAS = {
    "zrm": ["mohu_zrm", "mohu_zrm_fixed", "mohu_zrm_sentence", "mohu_zrm_aux", "tiger"],
    "flypy": ["mohu_flypy", "mohu_flypy_fixed", "mohu_flypy_sentence", "mohu_flypy_aux", "tiger"],
}

def test_split_distributions_build_with_isolated_schemas(self):
    subprocess.run(["make", "dist-zrm", "dist-flypy"], cwd=ROOT, check=True)
    for scheme, expected in EXPECTED_SCHEMAS.items():
        output = ROOT / f"dist-{scheme}"
        self.assertEqual(expected, schema_ids(output / "default.yaml"))
        self.assertTrue((output / f"mohu_{scheme}.schema.yaml").is_file())
        self.assertTrue((output / "zh-hans-t-essay-bgw.gram").is_file())
        self.assertFalse((output / "zh-hans-t-essay-bgc.gram").exists())
        other = "flypy" if scheme == "zrm" else "zrm"
        self.assertEqual([], list(output.glob(f"mohu_{other}*")))
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
uv run python tests/test_split_distribution.py -v
```

Expected: FAIL because `dist-zrm` and `dist-flypy` do not exist as Make targets.

- [ ] **Step 3: Implement the split distribution builder**

Create `tools/build_split_dist.py` with:

```python
SCHEMES = {"zrm", "flypy"}
COMMON_ROOT_PATHS = (
    "README.md", "LICENSE", "etc", "mohu.yaml", "mohu_defs.yaml",
    "mohu_charset.dict.yaml", "mohu_charset.schema.yaml",
    "mohu_fixed.symbols.dict.yaml", "mohu_pinyin.dict.yaml",
    "mohu_pinyin.schema.yaml", "key_bindings.yaml", "punctuation.yaml",
    "symbols.yaml", "recipe.yaml", "recipes", "squirrel.yaml",
    "tiger.dict.yaml", "tiger.schema.yaml", "zh-hans-t-essay-bgw.gram",
    "Rime皮肤编辑器",
)

def build_distribution(scheme: str, destination: Path) -> None:
    if scheme not in SCHEMES:
        raise ValueError(f"unsupported scheme: {scheme}")
    recreate_destination(destination)
    for relative in COMMON_ROOT_PATHS:
        copy_path(ROOT / relative, destination / relative)
    for source in ROOT.glob(f"mohu_{scheme}*"):
        copy_path(source, destination / source.name)
    copy_runtime_directories(destination)
    write_filtered_default(scheme, destination / "default.yaml")
```

The helper must copy Lua wholesale without `__pycache__`, copy only deployed
OpenCC `.ocd2`/`.json` files plus `mohu_TSPhrases.txt`, reject unsafe output
paths, and remove only schema-list rows whose IDs start with the other scheme.

- [ ] **Step 4: Add Make targets**

Add:

```make
ZRM_DESTDIR ?= $(abspath ./dist-zrm)
FLYPY_DESTDIR ?= $(abspath ./dist-flypy)

dist-zrm: quick
	uv run tools/build_split_dist.py zrm "$(ZRM_DESTDIR)"

dist-flypy: quick
	uv run tools/build_split_dist.py flypy "$(FLYPY_DESTDIR)"
```

Extend `clean` and `.PHONY` for the new directories and targets.

- [ ] **Step 5: Run the test and verify GREEN**

Run:

```bash
uv run python tests/test_split_distribution.py -v
```

Expected: PASS with both distributions built and isolated.

- [ ] **Step 6: Commit the distribution implementation**

```bash
git add -f tests/test_split_distribution.py
git add tools/build_split_dist.py Makefile
git commit -m "feat: 拆分自然码和小鹤发布包"
```

### Task 2: Publish two rolling Release assets

**Files:**
- Create: `tests/test_split_release_workflow.py`
- Modify: `.github/workflows/build.yml`

- [ ] **Step 1: Write the failing workflow test**

Create assertions that require both distribution targets, artifact names,
archive names, both `gh release upload` arguments, and removal of the old asset:

```python
def test_workflow_publishes_only_split_archives(self):
    workflow = WORKFLOW.read_text(encoding="utf-8")
    self.assertIn("make dist-zrm dist-flypy", workflow)
    self.assertIn("rime-mohu-zrm-latest.zip", workflow)
    self.assertIn("rime-mohu-flypy-latest.zip", workflow)
    self.assertIn("gh release delete-asset latest rime-mohu-latest.zip", workflow)
    self.assertNotIn("zip -r ../rime-mohu-latest.zip .", workflow)
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
uv run python tests/test_split_release_workflow.py -v
```

Expected: FAIL because the workflow still builds and uploads one combined archive.

- [ ] **Step 3: Update the build job**

Run `make dist-zrm dist-flypy`, validate representative files and exclusions in
both directories, and upload two artifacts named with scheme, run number, and
attempt. Pull requests build and validate but do not upload artifacts.

- [ ] **Step 4: Update the release job**

Download both artifacts to `dist-zrm` and `dist-flypy`, package:

```bash
(cd dist-zrm && zip -r ../rime-mohu-zrm-latest.zip .)
(cd dist-flypy && zip -r ../rime-mohu-flypy-latest.zip .)
```

Before uploading to an existing Release, run:

```bash
gh release delete-asset latest rime-mohu-latest.zip --yes 2>/dev/null || true
gh release upload latest rime-mohu-zrm-latest.zip rime-mohu-flypy-latest.zip --clobber
```

Create new Releases with both ZIP paths as arguments.

- [ ] **Step 5: Run the workflow test and syntax checks**

Run:

```bash
uv run python tests/test_split_release_workflow.py -v
actionlint .github/workflows/build.yml
```

Expected: both commands exit 0 without diagnostics. If `actionlint` is not
installed, validate YAML parsing and report that limitation explicitly.

- [ ] **Step 6: Commit the workflow implementation**

```bash
git add -f tests/test_split_release_workflow.py
git add .github/workflows/build.yml
git commit -m "ci: 发布自然码和小鹤分包"
```

### Task 3: Verify complete package behavior

**Files:**
- Modify only if verification reveals a scoped defect.

- [ ] **Step 1: Run focused tests from a clean output state**

```bash
find dist-zrm dist-flypy -depth -delete 2>/dev/null || true
uv run python tests/test_split_distribution.py -v
uv run python tests/test_split_release_workflow.py -v
```

Expected: all tests pass.

- [ ] **Step 2: Verify package contents manually**

```bash
find dist-zrm -maxdepth 1 -type f -print | sort
find dist-flypy -maxdepth 1 -type f -print | sort
```

Expected: no opposite-scheme assets and no `zh-hans-t-essay-bgc.gram`.

- [ ] **Step 3: Create both ZIP files and measure sizes**

```bash
task_archive_dir="$(mktemp -d)"
(cd dist-zrm && zip -qr "$task_archive_dir/rime-mohu-zrm-latest.zip" .)
(cd dist-flypy && zip -qr "$task_archive_dir/rime-mohu-flypy-latest.zip" .)
du -h "$task_archive_dir"/*.zip
```

Expected: two independently usable archives materially smaller than the former
combined archive.

- [ ] **Step 4: Run repository checks**

```bash
uv run --with ruff ruff check tools/build_split_dist.py tests/test_split_distribution.py tests/test_split_release_workflow.py
git diff --check
git status --short
```

Expected: no lint or whitespace errors; only intentional generated output
directories may remain untracked or ignored.

- [ ] **Step 5: Review commits and final diff**

```bash
git log --oneline main..HEAD
git diff --stat main...HEAD
git diff main...HEAD -- Makefile tools/build_split_dist.py .github/workflows/build.yml tests
```

Expected: the design commit plus narrowly scoped distribution and CI commits.
