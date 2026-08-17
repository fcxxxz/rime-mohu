# QQ Dictionary Adjustment Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a global QQ workflow that stages net Rime dictionary changes, queries effective word frequencies, reconciles external GitHub edits, and publishes validated changes to `fcxxxz/rime-mohu` through an automatically merged pull request and rolling Release.

**Architecture:** `rime-mohu` owns a strict JSON batch format, a pure-Python atomic dictionary applier, and a branch-dispatch workflow. The NAS `qing_memory` plugin owns command parsing, a synchronized dictionary index, SQLite-backed net pending state, GitHub520-aware HTTP access, and the publication state machine. GitHub `main` is authoritative; the bot holds only an optimistic unpublished overlay and deletes published content after the matching Release succeeds.

**Tech Stack:** Python 3.11+, `unittest`, Rime YAML/TSV dictionaries, AstrBot event filters, SQLite WAL, `aiohttp`, GitHub REST API, GitHub Actions, existing `uv`/Make build tooling.

---

## File Map

### `rime-mohu` repository

- Create `tools/qq_dictionary_batch.py`: JSON schema validation, Rime table parsing, effective-frequency lookup, atomic batch application, and CLI.
- Create `tests/test_qq_dictionary_batch.py`: focused unit and repository-fixture tests for all operation kinds.
- Create `.github/workflows/qq-dictionary-batch.yml`: manually dispatched branch worker that applies a payload, regenerates Flypy files, verifies changes, and commits to the bot branch.
- Create `tests/test_qq_dictionary_workflow.py`: static workflow safety and contract checks.
- Modify `Makefile`: add the two focused tests to `make test` without changing existing build targets.

### NAS AstrBot plugin

- Create `/volume1/fcx/server/astrbot/data/plugins/qing_memory/rime_dictionary.py`: command parser, immutable snapshot parser, virtual overlay reducer, export formatter, and shared batch JSON types.
- Create `/volume1/fcx/server/astrbot/data/plugins/qing_memory/rime_dictionary_store.py`: SQLite schema and atomic pending/batch persistence.
- Create `/volume1/fcx/server/astrbot/data/plugins/qing_memory/rime_dictionary_github.py`: GitHub REST client, codeload synchronization, GitHub520 resolver cache, retries, workflow/PR/Release APIs.
- Create `/volume1/fcx/server/astrbot/data/plugins/qing_memory/rime_dictionary_service.py`: command orchestration, periodic sync, reconciliation, publication state machine, and restart recovery.
- Create `/volume1/fcx/server/astrbot/data/plugins/qing_memory/tests/test_rime_dictionary.py`: pure parser/index/reducer tests.
- Create `/volume1/fcx/server/astrbot/data/plugins/qing_memory/tests/test_rime_dictionary_store.py`: SQLite and batch transition tests.
- Create `/volume1/fcx/server/astrbot/data/plugins/qing_memory/tests/test_rime_dictionary_github.py`: mocked network, GitHub520, and REST lifecycle tests.
- Create `/volume1/fcx/server/astrbot/data/plugins/qing_memory/tests/test_rime_dictionary_handler.py`: AstrBot registration, permissions, nickname, and response tests.
- Modify `/volume1/fcx/server/astrbot/data/plugins/qing_memory/plugin_handlers.py`: delegate the direct command to the service.
- Modify `/volume1/fcx/server/astrbot/data/plugins/qing_memory/plugin_core.py`: create/start/stop the service and its tasks.
- Modify `/volume1/fcx/server/astrbot/data/plugins/qing_memory/main.py`: register one high-priority direct handler for the dictionary command family.

## Task 1: Isolate Work And Verify Baselines

**Files:**
- Read: `AGENTS.md`
- Read: `README.md`
- Worktree: `.worktrees/qq-dictionary-adjustment-bot`

- [ ] **Step 1: Create an isolated repository worktree from the current committed HEAD**

Run:

```bash
git worktree add .worktrees/qq-dictionary-adjustment-bot -b codex/qq-dictionary-adjustment-bot HEAD
```

Expected: a clean feature worktree containing design commit `44b40ca`, while the user's unstaged changes in the original worktree remain untouched.

- [ ] **Step 2: Record repository and plugin baselines**

Run:

```bash
git -C .worktrees/qq-dictionary-adjustment-bot status --short
uv run python -m unittest tests.test_flypy_assets tests.test_split_release_workflow -v
ssh -i ~/.ssh/id_ed25519_home_nas root@100.76.114.72 \
  '/var/packages/ContainerManager/target/usr/bin/docker exec astrbot python -m unittest discover -s /AstrBot/data/plugins/qing_memory/tests -p "test_trigger_prefixes.py" -v'
```

Expected: clean worktree and all selected baseline tests pass. Record pre-existing failures before changing code.

- [ ] **Step 3: Fetch only the current plugin files into a disposable staging directory**

Run:

```bash
stage_dir="$(mktemp -d /tmp/qing-memory-rime.XXXXXX)"
scp -i ~/.ssh/id_ed25519_home_nas \
  root@100.76.114.72:/volume1/fcx/server/astrbot/data/plugins/qing_memory/{main.py,plugin_core.py,plugin_handlers.py} \
  "$stage_dir/"
```

Expected: three current production files in a unique `/tmp` directory. Do not create a persistent local plugin copy.

## Task 2: Define And Parse Repository Batches

**Files:**
- Create: `tools/qq_dictionary_batch.py`
- Create: `tests/test_qq_dictionary_batch.py`

- [ ] **Step 1: Write failing schema and parser tests**

Add tests that instantiate the public API exactly as follows:

```python
from tools.qq_dictionary_batch import (
    BatchValidationError,
    DictionaryBatch,
    RimeTable,
    load_batch,
)


def test_load_batch_rejects_control_characters() -> None:
    with self.assertRaisesRegex(BatchValidationError, "control character"):
        load_batch({
            "version": 1,
            "batch_id": "20260817-000001",
            "base_sha": "a" * 40,
            "operations": [{"kind": "word_add", "word": "坏\\n词", "expected": None, "desired": 1}],
        })


def test_rime_table_uses_declared_weight_column() -> None:
    table = RimeTable.parse("""---
name: sample
columns:
  - text
  - weight
  - code
...
打印机\t1763\tda;ua yn;bf ji;eo
""")
    self.assertEqual(table.rows_for("打印机")[0].weight, 1763)
```

The accepted operation kinds are `fixed_add`, `fixed_delete`, `fixed_reorder`, `word_add`, `word_delete`, and `word_frequency`. A `word_frequency` operation contains one word plus `expected` and `desired`; the bot emits two such operations for a swap.
Reject more than 500 operations or a canonical JSON payload larger than 60 KiB
so it remains below GitHub's workflow-dispatch input limit.

- [ ] **Step 2: Run the focused tests and confirm the module is absent**

Run:

```bash
uv run python -m unittest tests.test_qq_dictionary_batch -v
```

Expected: FAIL with `ModuleNotFoundError: tools.qq_dictionary_batch`.

- [ ] **Step 3: Implement immutable batch types and structured Rime header parsing**

Implement these public data types:

```python
ACTIVE_WORD_TABLES = (
    "mohu_zrm.base.dict.yaml",
    "mohu_zrm.words.dict.yaml",
    "mohu_zrm.tencent.dict.yaml",
    "mohu_zrm.computer.dict.yaml",
    "mohu_zrm.moe.dict.yaml",
)

class BatchValidationError(ValueError):
    pass

@dataclass(frozen=True)
class BatchOperation:
    kind: str
    word: str = ""
    code: str = ""
    other_word: str = ""
    expected: int | None = None
    desired: int | None = None
    expected_order: tuple[str, ...] = ()
    desired_order: tuple[str, ...] = ()

@dataclass(frozen=True)
class DictionaryBatch:
    version: int
    batch_id: str
    base_sha: str
    operations: tuple[BatchOperation, ...]

```

Expose the exact callable signatures `RimeTable.parse(text: str) -> RimeTable`,
`RimeTable.rows_for(word: str) -> list[RimeRow]`, `RimeTable.render() -> str`,
and `load_batch(payload: Mapping[str, object]) -> DictionaryBatch`.

Use `yaml.safe_load` for the YAML header, preserve body comments and blank lines byte-for-byte, derive default columns as `text, code, weight`, and reject unknown keys so untrusted payloads cannot silently change meaning.

- [ ] **Step 4: Run parser tests**

Run:

```bash
uv run python -m unittest tests.test_qq_dictionary_batch -v
```

Expected: schema and heterogeneous-column tests pass.

- [ ] **Step 5: Commit the parser foundation**

```bash
git add tools/qq_dictionary_batch.py tests/test_qq_dictionary_batch.py
git commit -m "feat: validate QQ dictionary batches"
```

## Task 3: Apply Dictionary Changes Atomically

**Files:**
- Modify: `tools/qq_dictionary_batch.py`
- Modify: `tests/test_qq_dictionary_batch.py`

- [ ] **Step 1: Add failing fixed-table tests**

Cover insertion, exact deletion, and optimistic reorder:

```python
def test_fixed_reorder_requires_expected_order(self) -> None:
    repo = self.fixture_repo(fixed_rows=[("甲", "abc"), ("乙", "abc"), ("丙", "abd")])
    batch = self.batch({
        "kind": "fixed_reorder",
        "code": "abc",
        "expected_order": ["甲", "乙"],
        "desired_order": ["乙", "甲"],
    })
    apply_batch(repo, batch)
    self.assertEqual(self.fixed_words(repo, "abc"), ["乙", "甲"])
```

Add a mismatch case asserting no file changes and `BatchConflict("fixed order changed")`.

- [ ] **Step 2: Add failing ordinary-word and frequency tests**

Use fixtures with `打印机=1763` in a `text,code,weight` table and `大妖精` in two tables with missing/zero weights. Assert:

```python
self.assertEqual(index.effective_weight("打印机"), 1763)
self.assertEqual(index.effective_weight("大妖精"), 0)
self.assertIsNone(index.effective_weight("未收录词"))
```

Apply two `word_frequency` operations and assert every exact-text occurrence receives the desired native-column weight. Add tests for supplemental insertion at weight `1` and exact-text deletion across all active phrase tables.

- [ ] **Step 3: Run tests and observe operation failures**

Run:

```bash
uv run python -m unittest tests.test_qq_dictionary_batch -v
```

Expected: FAIL because `DictionaryIndex`, `apply_batch`, and `BatchConflict` do not exist.

- [ ] **Step 4: Implement validate-then-write application**

Add `BatchConflict(RuntimeError)`, `DictionaryIndex.load(root: Path)`,
`DictionaryIndex.effective_weight(word: str)`,
`DictionaryIndex.fixed_order(code: str)`, and
`apply_batch(root: Path, batch: DictionaryBatch) -> tuple[Path, ...]`.

Validate every operation before the first write. Render each changed file to a sibling temporary path, call `os.replace`, and return a sorted tuple of changed paths. A failed operation must leave the working tree byte-identical.

- [ ] **Step 5: Add and test the CLI**

Implement `main(argv: Sequence[str] | None = None) -> int` with required
`--batch`, optional `--root` defaulting to `ROOT`, and optional `--result`.

The result JSON is
`{"batch_id":"20260817-000001","changed_files":["mohu_zrm.words.dict.yaml"]}`
on success. Conflicts print one sanitized line to stderr and exit `2`;
validation errors exit `3`.

Run:

```bash
uv run python -m unittest tests.test_qq_dictionary_batch -v
uv run python tools/qq_dictionary_batch.py --help
```

Expected: all tests pass and help lists `--root`, `--batch`, and `--result`.

- [ ] **Step 6: Commit atomic application**

```bash
git add tools/qq_dictionary_batch.py tests/test_qq_dictionary_batch.py
git commit -m "feat: apply QQ dictionary batches"
```

## Task 4: Add The Branch Worker Workflow

**Files:**
- Create: `.github/workflows/qq-dictionary-batch.yml`
- Create: `tests/test_qq_dictionary_workflow.py`
- Modify: `Makefile`

- [ ] **Step 1: Write the failing workflow contract test**

The test must parse YAML and assert:

```python
self.assertEqual(workflow["on"]["workflow_dispatch"]["inputs"]["payload"]["required"], True)
self.assertEqual(workflow["permissions"], {"contents": "write"})
self.assertIn("python tools/qq_dictionary_batch.py", workflow_text)
self.assertIn("make quick", workflow_text)
self.assertIn("git diff --check", workflow_text)
self.assertNotIn("pull-requests: write", workflow_text)
self.assertNotIn("echo ${{ inputs.payload }}", workflow_text)
```

- [ ] **Step 2: Run the workflow test and verify it fails**

Run:

```bash
uv run python -m unittest tests.test_qq_dictionary_workflow -v
```

Expected: FAIL because the workflow file is absent.

- [ ] **Step 3: Implement the workflow without shell-evaluating user data**

The workflow sequence is:

```yaml
name: Apply QQ dictionary batch
on:
  workflow_dispatch:
    inputs:
      payload:
        description: Base64-encoded validated dictionary batch
        required: true
        type: string
permissions:
  contents: write
jobs:
  apply:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: {ref: "${{ github.ref }}"}
      - run: sudo apt-get update && sudo apt-get install --yes opencc
      - uses: astral-sh/setup-uv@v6
      - run: uv sync --locked
      - name: Decode batch
        env: {BATCH_PAYLOAD: "${{ inputs.payload }}"}
        run: python -c 'import base64,os,pathlib; (pathlib.Path(os.environ["RUNNER_TEMP"]) / "batch.json").write_bytes(base64.b64decode(os.environ["BATCH_PAYLOAD"], validate=True))'
      - run: uv run python tools/qq_dictionary_batch.py --batch "$RUNNER_TEMP/batch.json" --result "$RUNNER_TEMP/result.json"
      - run: make quick
      - run: uv run python -m unittest tests.test_qq_dictionary_batch tests.test_flypy_assets -v
      - run: git diff --check
      - name: Commit generated changes
        run: |
          git config user.name 'Qing Dictionary Bot'
          git config user.email 'qing-dictionary-bot@users.noreply.github.com'
          git add mohu_zrm*.dict.yaml mohu_flypy*.dict.yaml
          git diff --cached --quiet && exit 4
          git commit -m 'dict: apply QQ dictionary batch'
          git push origin HEAD
```

Use a heredoc Python step if GitHub expression quoting makes the one-liner unsafe; never interpolate decoded content into shell source.

- [ ] **Step 4: Add focused tests to `make test` and run checks**

Run:

```bash
uv run python -m unittest tests.test_qq_dictionary_batch tests.test_qq_dictionary_workflow tests.test_flypy_assets -v
```

Expected: PASS.

- [ ] **Step 5: Commit repository automation**

```bash
git add .github/workflows/qq-dictionary-batch.yml tests/test_qq_dictionary_workflow.py Makefile
git commit -m "ci: apply QQ dictionary batches"
```

## Task 5: Build The Bot Command And Snapshot Domain

**Files:**
- Create: `/volume1/fcx/server/astrbot/data/plugins/qing_memory/rime_dictionary.py`
- Create: `/volume1/fcx/server/astrbot/data/plugins/qing_memory/tests/test_rime_dictionary.py`

- [ ] **Step 1: Write failing command parser tests in the disposable staging tree**

Cover these exact results:

```python
self.assertEqual(parse_dictionary_command("加词 sdd 3D打印机").action, "fixed_add")
self.assertEqual(parse_dictionary_command("加词 打印机").action, "word_add")
self.assertEqual(parse_dictionary_command("调频 sdd 3D打印机 速度打").action, "fixed_reorder")
self.assertEqual(parse_dictionary_command("调频 打印机 大妖精").action, "word_frequency")
self.assertEqual(parse_dictionary_command("词频 打印机  大妖精").words, ("打印机", "大妖精"))
self.assertEqual(parse_dictionary_command('词频 "Visual Studio Code" 打印机').words[0], "Visual Studio Code")
```

Also assert code, word length, control character, arity, and twenty-word limits.

- [ ] **Step 2: Write failing snapshot tests**

Create tiny fixture dictionaries with different column orders and assert `DictionarySnapshot.effective_weight`, `fixed_order`, and missing-versus-zero results. The snapshot loader accepts a codeload extraction root and records its 40-character SHA.

- [ ] **Step 3: Run the new tests inside AstrBot and confirm failure**

Run after copying only the test file to a temporary path in the container:

```bash
/var/packages/ContainerManager/target/usr/bin/docker exec astrbot \
  python -m unittest data.plugins.qing_memory.tests.test_rime_dictionary -v
```

Expected: FAIL because `rime_dictionary.py` is absent.

- [ ] **Step 4: Implement the pure domain module**

Implement these stable data types:

```python
@dataclass(frozen=True)
class DictionaryCommand:
    action: str
    code: str = ""
    words: tuple[str, ...] = ()

@dataclass(frozen=True)
class PendingEvent:
    event_id: str
    action: str
    code: str
    words: tuple[str, ...]
    operator_id: str
    operator_name: str
    operator_is_master: bool
    created_at: int
    depends_on: tuple[str, ...]

```

Expose the exact callables `DictionarySnapshot.load(root: Path, sha: str)`,
`DictionarySnapshot.effective_weight(word: str)`,
`DictionarySnapshot.fixed_order(code: str)`,
`parse_dictionary_command(body: str)`, `reduce_pending(snapshot, events)`,
`format_pending_export(overlay)`, and `build_batch_payload(overlay, batch_id)`.

`reduce_pending` removes inverse add/delete events and all later events depending on a canceled transient target, then canonicalizes fixed order and word weights against the base. If a connected adjustment returns fully to base, no event content remains in the overlay.

- [ ] **Step 5: Run pure tests in the container**

Expected: all parser, snapshot, frequency, cancellation, dependent-operation, and export-order tests pass.

## Task 6: Persist Net State And Batch Recovery

**Files:**
- Create: `/volume1/fcx/server/astrbot/data/plugins/qing_memory/rime_dictionary_store.py`
- Create: `/volume1/fcx/server/astrbot/data/plugins/qing_memory/tests/test_rime_dictionary_store.py`

- [ ] **Step 1: Write failing SQLite tests**

Use a temporary database and assert:

```python
store = DictionaryStore(db_path)
store.initialize()
store.replace_pending(events, base_sha="a" * 40)
batch = store.freeze_pending("20260817-000001")
self.assertEqual(batch.state, "preparing")
self.assertEqual(store.list_pending(), ())
self.assertEqual(store.list_frozen_events(batch.batch_id), events)
```

Also cover one active batch globally, rollback on failure, restart loading, pending clear that does not touch a frozen batch, and content-free cleanup summary.

- [ ] **Step 2: Run and verify the missing module failure**

Run in the container:

```bash
python -m unittest data.plugins.qing_memory.tests.test_rime_dictionary_store -v
```

Expected: FAIL with import error.

- [ ] **Step 3: Implement schema creation and explicit transitions**

Create tables `rime_dictionary_state`, `rime_dictionary_events`, `rime_dictionary_batches`, and `rime_dictionary_batch_events`. Use foreign keys, JSON text columns, integer timestamps, and a partial unique index preventing two nonterminal batches.

Expose `DictionaryStore.initialize`, `snapshot_sha`, `set_snapshot_sha`,
`list_pending`, `replace_pending`, `clear_pending`, `freeze_pending`,
`transition_batch`, `active_batch`, and `finish_batch` with the argument and
return types exercised by the tests above. `transition_batch` must compare the
stored state to its `expected` argument inside the same transaction.

After `finish_batch`, delete event content and retain only IDs, counts, URLs, SHA, and timestamps.

- [ ] **Step 4: Run store tests**

Expected: all transaction and recovery tests pass.

## Task 7: Synchronize GitHub And Maintain GitHub520 Fallback

**Files:**
- Create: `/volume1/fcx/server/astrbot/data/plugins/qing_memory/rime_dictionary_github.py`
- Create: `/volume1/fcx/server/astrbot/data/plugins/qing_memory/tests/test_rime_dictionary_github.py`

- [ ] **Step 1: Write failing GitHub520 parser tests**

Assert only lines between `# GitHub520 Host Start` and `# GitHub520 Host End` are accepted, only allowlisted GitHub hosts survive, malformed IPs fail the whole candidate, and a failed health check retains the old cache.

- [ ] **Step 2: Write failing mocked REST tests**

Mock HTTP responses for ETag/304 main polling, codeload extraction with path traversal entries, `403` rate limit reset, `5xx` retry, authentication failure without retry, branch creation, workflow dispatch, workflow lookup by branch, PR creation/checks/merge, `latest` tag verification, and branch deletion.

- [ ] **Step 3: Implement the bounded client**

Create `GitHubError`, `GitHubAuthError`, and `GitHubConflictError`, plus async
`RimeMohuGitHub` methods named `refresh_github520`, `main_sha`,
`download_snapshot`, `create_branch`, `dispatch_batch`,
`wait_for_batch_workflow`, `create_pull_request`,
`wait_for_pull_request_checks`, `squash_merge`, `wait_for_latest_release`, and
`delete_branch`. Their arguments and results must match the mocked calls in the
preceding tests; use immutable `WorkflowResult` and `PullRequest` dataclasses.

Use `aiohttp`, TLS hostname verification, normal DNS first, a custom resolver only on retry, maximum archive byte and file-count limits, and safe tar extraction that rejects absolute and parent paths. Redact authorization headers from all errors.

- [ ] **Step 4: Run network unit tests**

Expected: mocked tests pass without contacting GitHub.

## Task 8: Implement Reconciliation And Publication Service

**Files:**
- Create: `/volume1/fcx/server/astrbot/data/plugins/qing_memory/rime_dictionary_service.py`
- Modify: `/volume1/fcx/server/astrbot/data/plugins/qing_memory/tests/test_rime_dictionary.py`
- Modify: `/volume1/fcx/server/astrbot/data/plugins/qing_memory/tests/test_rime_dictionary_github.py`

- [ ] **Step 1: Write failing service command tests**

Test `加词`, inverse `删词`, fixed reorder, `词频`, ordinary swap, export grouping, clear permission, publish permission, and commands arriving while a frozen batch exists. Assert returned messages are plain text and include no internal function names.

- [ ] **Step 2: Write failing external reconciliation tests**

Cover all three outcomes: upstream already desired removes pending content, unchanged precondition rebases to new SHA, and divergent upstream marks a visible conflict that blocks publication.
Add an open-PR case where `main` moves: the service must discard and rebuild
only its batch branch from the new SHA before merge, leaving user branches and
manual commits untouched.

- [ ] **Step 3: Write failing publication lifecycle tests**

Use async fakes and assert the exact transitions:

```python
self.assertEqual(states, ["preparing", "applying", "reviewing", "merged", "published"])
github.create_branch.assert_awaited_once()
github.dispatch_batch.assert_awaited_once()
github.squash_merge.assert_awaited_once()
github.wait_for_latest_release.assert_awaited_once_with(merge_sha)
self.assertEqual(store.list_frozen_events(batch_id), ())
```

Add restart-at-each-state cases and a merge-success/Release-failure case that never reapplies dictionary operations.

- [ ] **Step 4: Implement the service**

Create `RimeDictionaryService.start()`, async `stop()`, async
`handle(event, body)`, async `sync(force=False)`, async `publish(event)`, and
async `resume_publication()`. `handle` returns plain text for normal replies
and a temporary `Path` for long exports; all other methods return only the
typed values asserted by their focused tests.

Start a five-minute SHA loop and six-hour GitHub520 loop. Store tasks in a set, consume task exceptions through the plugin's existing background-task pattern, and use one `asyncio.Lock` for sync plus one for publication. Snapshot replacement and SQLite reconciliation are ordered so a crash sees either the old complete state or the new complete state.

- [ ] **Step 5: Run all service tests**

Expected: command, reducer, sync, failure, and restart tests pass.

## Task 9: Wire The Direct AstrBot Command

**Files:**
- Modify: `/volume1/fcx/server/astrbot/data/plugins/qing_memory/plugin_core.py`
- Modify: `/volume1/fcx/server/astrbot/data/plugins/qing_memory/plugin_handlers.py`
- Modify: `/volume1/fcx/server/astrbot/data/plugins/qing_memory/main.py`
- Create: `/volume1/fcx/server/astrbot/data/plugins/qing_memory/tests/test_rime_dictionary_handler.py`

- [ ] **Step 1: Write a failing registration test**

Find `handle_rime_dictionary_command` in AstrBot's registry and assert one `ALL` message handler at priority `9997`. Pipeline cases must activate for `q 加词`, `q 词频`, and private `q 导出词库`, but not bare group text.

- [ ] **Step 2: Write failing handler tests**

Use a fake event with `get_sender_id`, `get_sender_name`, `plain_result`, and `stop_event`. Assert nickname snapshots, master-only clear/publish, service delegation, event stopping, and text-file export delegation.

- [ ] **Step 3: Add lifecycle wiring**

In `QingMemoryCoreMixin.__init__`:

```python
self._rime_dictionary_service = RimeDictionaryService(
    db_path=self.db_path,
    plugin_dir=self.plugin_dir,
    master_qq_id=MASTER_QQ_ID,
)
self._rime_dictionary_service.start()
```

In `terminate`, await `self._rime_dictionary_service.stop()` before returning.

In `QingMemoryHandlersMixin`:

```python
async def handle_rime_dictionary_command(self, event: AstrMessageEvent):
    body = _normalize_content(_qing_trigger_body(getattr(event, "message_str", "") or ""))
    result = await self._rime_dictionary_service.handle(event, body)
    if result is not None:
        event.stop_event()
        yield event.plain_result(str(result))
```

Register the main-class delegate beside other direct commands at priority `9997`.

- [ ] **Step 4: Run handler and regression tests**

Run in the container:

```bash
python -m unittest \
  data.plugins.qing_memory.tests.test_rime_dictionary \
  data.plugins.qing_memory.tests.test_rime_dictionary_store \
  data.plugins.qing_memory.tests.test_rime_dictionary_github \
  data.plugins.qing_memory.tests.test_rime_dictionary_handler \
  data.plugins.qing_memory.tests.test_trigger_prefixes -v
```

Expected: PASS.

## Task 10: Integrate, Review, And Deploy

**Files:**
- Repository changes from Tasks 2-4
- NAS plugin files from Tasks 5-9
- Secret mount configuration, path determined from the existing compose file without printing its contents

- [ ] **Step 1: Run repository validation in the isolated worktree**

Run:

```bash
uv run python -m unittest tests.test_qq_dictionary_batch tests.test_qq_dictionary_workflow tests.test_flypy_assets tests.test_split_release_workflow -v
make quick
git diff --check
```

Inspect generated diffs. Stop if `make quick` changes unrelated data beyond known generated counterparts.

- [ ] **Step 2: Commit repository-side implementation**

```bash
git add tools/qq_dictionary_batch.py tests/test_qq_dictionary_batch.py \
  .github/workflows/qq-dictionary-batch.yml tests/test_qq_dictionary_workflow.py Makefile
git commit -m "feat: support QQ dictionary publishing"
```

- [ ] **Step 3: Back up and deploy only changed plugin files**

Create a timestamped backup directory under the plugin's existing backup area, then `scp` the new and surgically patched files from the disposable staging directory. Do not overwrite unrelated plugin files or `memory.db`.

- [ ] **Step 4: Compile and run tests inside the live container before restart**

Run:

```bash
/var/packages/ContainerManager/target/usr/bin/docker exec astrbot \
  python -m py_compile \
  /AstrBot/data/plugins/qing_memory/rime_dictionary.py \
  /AstrBot/data/plugins/qing_memory/rime_dictionary_store.py \
  /AstrBot/data/plugins/qing_memory/rime_dictionary_github.py \
  /AstrBot/data/plugins/qing_memory/rime_dictionary_service.py \
  /AstrBot/data/plugins/qing_memory/plugin_core.py \
  /AstrBot/data/plugins/qing_memory/plugin_handlers.py \
  /AstrBot/data/plugins/qing_memory/main.py
```

Then run the test command from Task 9. Expected: all compile and tests pass.

- [ ] **Step 5: Configure the token without exposing it**

Resolve the existing compose file and add a read-only bind mount to `/run/secrets/rime_mohu_github_token`. Verify only presence, ownership, and mode; never print the value. If no token exists yet, deploy read-only query/staging behavior and report publication as pending credential configuration.

- [ ] **Step 6: Restart and inspect logs**

Run:

```bash
ssh -i ~/.ssh/id_ed25519_home_nas root@100.76.114.72 \
  '/var/packages/ContainerManager/target/usr/bin/docker restart astrbot'
ssh -i ~/.ssh/id_ed25519_home_nas root@100.76.114.72 \
  '/var/packages/ContainerManager/target/usr/bin/docker logs --tail 180 astrbot 2>&1 | grep -E "AstrBot started|rime_dictionary|Traceback|ERROR|Exception"'
```

Expected: AstrBot starts, dictionary sync initializes, and no new traceback appears.

- [ ] **Step 7: Perform non-destructive smoke tests**

Use QQ to verify `q 词频 打印机 大妖精`, empty `q 导出词库`, a temporary add followed by inverse delete, and master/ordinary-user permission responses. Do not run `q 发布词库` until the repository workflow is present on GitHub and the token is configured.

- [ ] **Step 8: Verify GitHub publication with an intentional real word change**

After master confirmation of the staged word, run `q 发布词库`. Verify branch creation, workflow success, PR checks, squash merge, `latest` tag movement, bot branch deletion, empty pending export, and content-free batch summary.

## Final Verification

- [ ] Repository focused tests pass.
- [ ] `make quick` produces only expected generated changes.
- [ ] Plugin modules compile in the container.
- [ ] Plugin focused and trigger regression tests pass.
- [ ] AstrBot restarts without new errors.
- [ ] Read-only frequency and net-cancellation smoke tests work in QQ.
- [ ] External `main` changes reconcile without overwrite.
- [ ] One authorized publication completes through Release and removes temporary content.
