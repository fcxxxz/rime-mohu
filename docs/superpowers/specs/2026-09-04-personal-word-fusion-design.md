# Personal Word Fusion Design

## Problem Essence

Mohu currently has two independent candidate systems:

- Rime `smart` knows code-specific dictionary frequency and userdb learning.
- Tiger native knows sentence segmentation and character-level context scoring.

They are currently merged as ordered candidate lists. Their scores have different
units, candidate paths consume different spans, and one engine can hide a learned
word from the other. A raw key-length handoff (for example, six keys to `smart` and
seven keys to Tiger) is not a semantic boundary because the same input can be a word,
a partial word, or an auxiliary reinterpretation.

The design must combine code constraints, lexical priors, user history, auxiliary
evidence, and context at the candidate-path level. The measured 4-key native
takeover regression (82.0% to 74.7%) remains a hard validation gate for the unified
scoring model, not a reason to add another length branch.

## Success Criteria

1. A complete word-code lookup uses Rime userdb learning as its source of truth.
2. A word selected with auxiliary code receives current-input priority, while its
   long-term learning count is the same normalized worddb entry as a bare-code
   selection.
3. In native sentences, personal words participate as lexical edges with a
   monotonic submission-count prior instead of a fixed, arbitrary boost.
4. A one-time accidental selection does not permanently override strong static or
   contextual evidence; repeated selections can eventually win same-code conflicts.
5. Four-key pure double-pinyin accuracy and existing sentence-context gains do not
   regress.
6. Keypress latency does not perform userdb I/O or a full userdb scan.

## Priority Semantics

Priority is evaluated per candidate path rather than per translator list:

```text
manual Pin / manual order
    > current auxiliary-code match
    > user-history prior (monotonic with commits)
    > static lexical prior
    > Tiger contextual score / character fallback
```

The auxiliary-code relation is ephemeral. It affects the current composition only;
it does not add an extra permanent submission count. Selecting `jmrkyfu` for
`简快符` increments the normalized `jmkyfu + 简快符` userdb entry once.

## Candidate-Path Ownership

Both engines may contribute evidence for every composition. Tiger owns the path
lattice for sentence-capable inputs, while smart remains the durable source of
lexical and user-history facts. There is no length-based ownership switch.

The native lexicon receives an in-memory snapshot of all active multi-character
userdb rows:

- static entries with learned commit counts;
- user-created entries not present in the static dictionary;
- normalized bare double-pinyin codes and positive commit counts;
- whether the row matches a static edge, so matching edges are updated rather than
  duplicated.

If a user row matches a static edge, the edge is retained and its learned prior is
updated; no duplicate edge is created. If it is not static, a personal lexical edge
is added. Both kinds of edge may occur inside a native sentence. Candidate identity
is `(input span, normalized code path, text)` so smart/native duplicates collapse to
one logical path before ordering.

## Scoring

Every native edge has three independent contributions:

```text
edge_score = native_context_score
           + learned_prior(commit_count)
           + current_auxiliary_bonus
```

`learned_prior` is monotonic in the positive commit count, starts small, and is
bounded or decayed so one mistaken selection cannot dominate every sentence. Its
coefficient is calibrated against replayed smart rankings and native path gaps,
never chosen by comparing unrelated raw score units.

`current_auxiliary_bonus` is present only when the edge's character/code path
matches an explicitly typed auxiliary key. Lua derives this evidence from the
candidate path (or the auxiliary resolver) before native scoring; it is not inferred
from the candidate text alone. It implements the old `aux_priority` behavior for the
current menu and is never persisted as extra frequency.

Manual Pin and candidate-override records remain outside this score and keep their
existing hard-priority behavior.

## Data Flow

```text
Rime Memory (all active multi-character rows)
        |
        | idle/commit-boundary snapshot, <=5 ms slices
        v
Lua normalized rows: bare code, text, commits, origin (static/user-created)
        |
        +--> smart: lexical/user-history facts and normal learning
        |
        +--> native: one path lattice with personal lexical edges

current composition auxiliary evidence ------------------+
                                                          v
smart candidates / native sentence paths --> current aux bonus --> menu
```

The hot path reads only the in-memory snapshot. It must not call `Memory`, scan a
LevelDB userdb, or synchronously rebuild a snapshot for each key.

## Refresh and Compatibility

- Keep the existing idle refresh state machine and transaction ABI.
- Add an O(1) native personal-edge delta API used at commit time; the full snapshot
  remains the reconciliation path for sync, deletion, and restart.
- Remove the personal-snapshot filter that discards static-but-learned entries;
  retain the existing multi-character and positive-count validation. Mark whether
  each row is already present in the static dictionary so native can update an
  existing edge rather than add a duplicate.
- Preserve the current row cap of 4,096 for the main schemas unless benchmarks
  demonstrate a safe increase.
- If the native ABI or snapshot update fails, fail open to the existing smart/native
  candidate path without consuming input.
- Keep `decode_context_takeover: false` for four-key pure double-pinyin.
- Auxiliary matching must use genuine candidates after any `ShadowCandidate`
  wrapper, matching the commit-learning fix already covered by the native Lua test.

## Performance Budget

Acceptance measurements must compare cold and warm p50/p95 latency for four-, five-,
six-, and seven-key inputs, plus snapshot refresh cost. The implementation must
preserve these boundaries:

- no userdb I/O in the keypress path;
- snapshot scanning in bounded idle slices;
- candidate fusion linear in the visible candidate count;
- personal rows that match static edges must not duplicate lexical edges;
- native beam width remains 200 unless a benchmark justifies a change.

The before/after comparison must report both absolute latency and regression. A
candidate-path change fails review if warm p95 grows by more than 10% and 0.5 ms
over the existing baseline, or if snapshot refresh performs work on a non-empty
composition.

## Verification

The implementation must add or update focused tests for:

1. static learned rows are exported to the native snapshot with their origin;
2. user-created rows remain available as native sentence edges;
3. commit counts produce monotonic personal ranking;
4. auxiliary matches receive current-input priority without extra persisted count;
5. word and sentence paths use the same scoring model; no six-/seven-/eight-key
   ownership rule is allowed;
6. four-key native takeover remains disabled by default and its baseline accuracy is
   unchanged;
7. old ABI and failed snapshot updates fail open.

The existing native two-character, selected-segment, word-order, and user-model test
suites remain mandatory gates.
