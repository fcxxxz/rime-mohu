# QQ Dictionary Adjustment Bot Design

## Goal

Add a global QQ command workflow to the existing `qing_memory` AstrBot plugin
for staging, reviewing, publishing, and reconciling temporary dictionary
adjustments for `fcxxxz/rime-mohu`.

QQ users may stage additions, deletions, fixed-code candidate reordering, and
ordinary-word frequency swaps. The bot master reviews the net pending state,
publishes it through a pull request, and lets the repository's existing GitHub
Actions workflow produce the rolling Release. GitHub `main` is always the
authoritative dictionary state; the bot stores only unpublished overlays.

## Scope

The feature covers:

- QQ group and private messages using the existing `q ` trigger;
- one adjustment pool shared by all QQ conversations;
- Natural Code source dictionaries and their generated Flypy counterparts;
- synchronization with changes pushed to GitHub outside the bot;
- automated pull request creation, validation, merge, Release monitoring, and
  cleanup;
- scheduled GitHub520 host-map refresh as a network fallback.

It does not change Rime's runtime candidate pipeline, deploy a dictionary
directly to end-user devices, or grant QQ group administrators global publish
authority.

## Identity And Permissions

The global administrator is the existing bot master, QQ `810439322`.

- Any QQ user may add, delete, reorder, query frequency, and swap frequency.
- Only the bot master may run `q 清空词库` and `q 发布词库`.
- QQ group administrators do not gain global clear or publish permission.
- The operator nickname is captured when an adjustment is made. It is used in
  exports and pull request notes only, never as part of a Rime dictionary
  entry.
- QQ IDs remain local and are not included in the public pull request payload.

## Commands

| Command | Meaning |
|---|---|
| `q 加词 sdd 3D打印机` | Add `3D打印机` at the end of fixed-code group `sdd`. |
| `q 加词 打印机` | Add `打印机` to the ordinary supplemental dictionary with weight `1`. |
| `q 删词 sdd 3D打印机` | Delete only `3D打印机` under fixed code `sdd`. |
| `q 删词 打印机` | Delete exact-text phrase entries from active ordinary dictionaries. |
| `q 调频 sdd 3D打印机 速度打` | Swap the two candidates' positions under `sdd`. |
| `q 调频 打印机 大妖精` | Swap the two words' effective numeric weights. |
| `q 词频 打印机 大妖精` | Return the current effective weight of one or more words. |
| `q 导出词库` | Export current net pending changes and conflicts. |
| `q 清空词库` | Clear all pending changes; master only. |
| `q 发布词库` | Publish the current pending batch; master only. |

Parsing rules:

- Codes are normalized to lowercase and must match `[a-z]{1,16}`.
- Words must be non-empty, contain no tabs, newlines, or control characters,
  and contain at most 64 Unicode code points.
- Repeated whitespace is ignored. Double quotes allow an item containing
  spaces, for example `q 词频 "Visual Studio Code" 打印机`.
- A frequency query accepts at most 20 words.
- Fixed-code reordering requires exactly one code and two distinct words.
- Ordinary frequency swapping requires exactly two distinct words.

## Dictionary Semantics

### Fixed-Code Changes

Fixed-code changes target `mohu_zrm_fixed.dict.yaml`, whose `sort: original`
order defines candidate order. A new candidate is appended to an existing code
group or inserted as a new code group in code-sorted position. Deletion removes
only the exact `(code, text)` row. Reordering swaps the positions of two
existing candidates in the same code group.

The repository's generation path produces the corresponding Flypy table. The
repository-side application script must use the project's own code conversion
logic rather than duplicate it in AstrBot.

### Ordinary Word Changes

New ordinary words are added to `mohu_zrm.words.dict.yaml` with weight `1`, in
the repository-prescribed supplemental-word block. Exact-text deletion applies
to active phrase dictionaries imported by `mohu_zrm.extended`, excluding the
single-character table.

The application script reads each dictionary's declared `columns` instead of
assuming one TSV layout. This is required because the repository uses both
`text, code, weight` and `text, weight, code`, and some sources omit explicit
weights.

### Effective Frequency

The effective frequency of a word is the greatest numeric weight among all
active exact-text ordinary dictionary rows. A missing weight is `0`. A word
with no active row is reported as `未收录`, which is distinct from weight `0`.

`q 词频` evaluates GitHub's latest indexed dictionary plus the complete
pending overlay. A pending addition therefore reports `1`, a pending deletion
reports `未收录`, and a pending frequency swap reports the exchanged values
immediately.

To make a swap observable after compilation when a word exists in multiple
import tables, publication writes the exchanged effective value to every
active exact-text row for that word using the row's native weight column. A
swap between equal weights is a no-op and is not recorded.

## Net Pending State

The export is a materialized net difference from the indexed GitHub base, not
an append-only command history.

- Adding and then deleting the same absent target cancels both changes.
- Deleting and then restoring the same existing target cancels both changes.
- Repeating a reorder or frequency swap until the original state is restored
  removes the adjustment.
- Cancellation is global even when the inverse commands came from different QQ
  users.
- If a cancellation invalidates a dependent reorder or frequency swap, the bot
  recomputes the complete overlay and removes the invalid dependent change.
- A command that produces no net change replies that the prior temporary
  adjustment was canceled.

The reducer retains the nickname of the command that produced each remaining
net difference. Canceled content is removed from storage. Clearing removes only
the mutable pending overlay; it never changes a frozen publishing batch.

## Export Format

`q 导出词库` includes additions, deletions, fixed-code order changes,
frequency changes, and synchronization conflicts. Master changes appear first;
member changes follow. Each section is chronological, and every line ends with
the captured QQ nickname.

Short exports are plain QQ text. Long exports are sent as a UTF-8 text file.
The actual dictionary value never contains the nickname suffix.

## Storage And State Machine

The existing `memory.db` gains tables for:

- the current GitHub dictionary snapshot SHA and refresh metadata;
- net pending adjustments and their optimistic preconditions;
- publish batches and their workflow, pull request, merge, and Release state;
- conflict descriptions that are safe to show in QQ.

Only one global batch may publish at a time. Publishing atomically freezes the
current net adjustments into a batch. Commands received while it publishes
create the next pending overlay.

Batch states are:

1. `preparing`: refresh and validate against the latest `main`;
2. `applying`: repository workflow is applying the batch on a bot branch;
3. `reviewing`: pull request exists and checks are running;
4. `merged`: pull request merged, Release not yet confirmed;
5. `published`: Release points at the merged commit;
6. `failed`: a retryable or actionable failure occurred.

Plugin startup resumes any nonterminal batch by querying GitHub. It never
re-applies a batch already present in `main`.

## GitHub Synchronization

GitHub `main` is the sole durable source of truth. The bot polls the head SHA
every five minutes, refreshes immediately when its cache is stale, and always
performs a forced refresh before publishing.

On a new SHA, the bot downloads an immutable codeload archive for that commit,
parses the relevant dictionary files into a new index, and swaps the cache
atomically only after validation. It then replays pending net adjustments:

- if upstream already has the desired state, the pending change is deleted;
- if the precondition still matches, the change is rebased onto the new SHA;
- if upstream differs from both expected and desired state, the change becomes
  a conflict and cannot publish.

Frequency swaps store both original and desired weights. Fixed reorders store
the original and desired relative order. This prevents a manual upstream edit
from being blindly swapped a second time.

If `main` changes while a bot pull request is open, the bot refreshes and
re-applies the frozen batch on the latest `main` before merge. It updates only
its uniquely named batch branch and never force-updates a user branch.

## GitHub Publication

Each batch uses a branch named `bot/qq-dictionary-<batch-id>`.

1. The bot creates the branch from the validated `main` SHA through the GitHub
   Git Database API.
2. It dispatches a dedicated repository workflow on that branch with a
   base64-encoded, strictly validated JSON batch. The payload contains display
   nicknames but no QQ IDs or GitHub token.
3. The workflow decodes JSON without shell evaluation, runs a pure Python
   repository script, regenerates Flypy assets through existing project tools,
   runs focused tests, and commits the resulting dictionary changes.
4. After workflow success, the bot creates a pull request through its own
   token. This allows the existing `pull_request` workflow to run normally.
5. The bot waits for all required checks, then performs a squash merge through
   the API.
6. The existing push workflow builds both distributions and updates the
   `latest` Release.
7. The bot confirms that the `latest` tag points at the merged commit, refreshes
   its index, deletes the batch's temporary rows and bot branch, and reports
   success.

After cleanup, the database retains only a content-free batch summary containing
the batch ID, pull request, commit, item count, and timestamps. Published words,
codes, QQ IDs, and nicknames are not retained in that summary.

If merge succeeds but Release fails, the batch remains in `merged` state. Its
dictionary operations are not submitted again. Cleanup waits for the Release
to succeed or for the master to resolve the failure.

## GitHub Credentials

Use a fine-grained token restricted to `fcxxxz/rime-mohu` with only the
Contents, Actions, and Pull requests permissions needed by this workflow. The
token is supplied to the container as a read-only secret file. It is never
stored in source control, SQLite, an Actions input, an exception message, or a
log line.

Staging and querying continue to work when the token is missing. Publication
returns a configuration error without exposing the expected secret path or
value to non-master users.

## GitHub520 Fallback

The bot fetches `https://raw.hellogithub.com/hosts` at startup and every six
hours. It accepts only the marked GitHub520 block, valid IP addresses, and an
explicit allowlist of GitHub hostnames. A candidate mapping becomes active only
after a TLS-verified health probe using the original hostname for SNI and
certificate validation.

Normal DNS is always attempted first. The cached mapping is used only after a
retryable connection failure. A failed refresh retains the last known-good
mapping; it never edits NAS or container `/etc/hosts`. Cache writes are atomic,
and stale mappings expire after seven days.

## Error Handling

- Syntax and validation errors return a short actionable QQ message and do not
  write state.
- Duplicate additions, missing deletion targets, missing reorder candidates,
  and equal-weight swaps are rejected as no-ops.
- GitHub rate limits, timeouts, and `5xx` responses use bounded exponential
  backoff. Authentication and permission failures do not retry indefinitely.
- A batch with any conflict is atomic: no partial pull request is created.
- Workflow or check failure keeps the frozen batch and reports its stage and
  GitHub URL to the master.
- All bot-facing output is plain QQ text.

## Validation

AstrBot-side tests cover:

- all command forms, quoting, validation, and permissions;
- multi-word frequency lookup and missing-versus-zero behavior;
- heterogeneous dictionary column parsing and duplicate effective weights;
- net cancellation across users and dependent-operation recomputation;
- external SHA reconciliation and optimistic conflicts;
- SQLite migration, batch transitions, and restart recovery;
- mocked GitHub retries, branch/workflow/PR/Release polling, and secret
  redaction;
- GitHub520 parsing, validation, fallback, and last-known-good behavior.

Repository-side tests cover:

- fixed-code insertion, exact deletion, code sorting, and candidate swaps;
- ordinary additions and exact-text deletion across active phrase tables;
- effective frequency lookup and swaps across duplicate source rows;
- native weight-column preservation;
- generated Natural Code and Flypy consistency;
- malformed JSON, path injection, control characters, and batch limits;
- the dedicated workflow's permissions and command sequence.

Before deployment, run focused unit tests, the repository generation checks,
and the existing relevant `rime-mohu` build suite. Compile the deployed AstrBot
plugin inside the container, restart AstrBot, inspect startup logs, and perform
read-only QQ smoke tests before attempting a real publication.

## Acceptance Criteria

- All documented commands work in QQ group and private contexts.
- Export contains only net pending changes, with master entries first and
  nickname suffixes.
- `q 词频` returns effective numeric values from synchronized plus pending
  state.
- External GitHub changes are detected and reconciled without being
  overwritten.
- Only the bot master can clear or publish.
- A successful publication creates and validates a pull request, merges it,
  updates the rolling Release, and removes the corresponding temporary state.
- Failures preserve recoverable state and never expose credentials.
