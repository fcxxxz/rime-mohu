# Personal Word Fusion Design

## Problem Essence

The Mohu main schemas use two candidate systems with different responsibilities:

- Rime `smart` owns code-specific dictionary frequency and userdb learning.
- Tiger native owns sentence segmentation and character-level context scoring.

The current length-based handoff sends complete three-character word codes such as
`jmkyfu` through the native sentence path. Native then sees a user-created word as a
small capped boost, while learned static words are omitted from its personal snapshot.
The result is that a word selected with auxiliary code can remain below unrelated
native segmentations.

The design must preserve the measured short-code behavior: character-level native
takeover of four-key pure double-pinyin reduced top-1 accuracy by 7.3 percentage
points, so native must not replace smart for exact word lookup.

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

Priority is contextual rather than one global numeric ordering:

```text
manual Pin / manual order
    > current auxiliary-code match
    > learned personal word, ordered by submission count
    > static dictionary candidate
    > native character segmentation fallback
```

The auxiliary-code relation is ephemeral. It affects the current composition only;
it does not add an extra permanent submission count. Selecting `jmrkyfu` for
`简快符` increments the normalized `jmkyfu + 简快符` userdb entry once.

## Candidate Ownership

### Complete word lookup

When the active segment has at least one smart/auxiliary candidate that consumes the
entire segment as a supported two- or three-character word, the segment is classified
as a complete word lookup. This classification is based on a complete candidate
path, not on raw key length alone; a six-key segment with two full auxiliary-bearing
syllables must not be mistaken for a three-character bare word. In this mode, `smart`
remains the candidate authority. The native translator does not independently
outrank these candidates. Its context scorer may reorder the smart candidates only
when contextual ordering is enabled, and only within the existing candidate set.

### Sentence lookup

For an actual sentence composition, Tiger remains responsible for segmentation and
context scoring. The native lexicon receives an in-memory snapshot of all active
multi-character userdb rows:

- static entries with learned commit counts;
- user-created entries not present in the static dictionary;
- normalized bare double-pinyin codes and positive commit counts.

If a user row matches a static edge, the edge is retained and its learned prior is
updated; no duplicate edge is created. If it is not static, a personal lexical edge
is added. User-created edges may occur inside a native sentence. Existing static
multi-character edge eligibility is unchanged.

## Scoring

Every native edge has three independent contributions:

```text
edge_score = native_context_score
           + learned_prior(commit_count)
           + current_auxiliary_bonus
```

`learned_prior` is monotonic in the positive commit count, starts small, and is
bounded or decayed so one mistaken selection cannot dominate every sentence. Its
coefficient and cap are calibrated against replayed smart rankings rather than
chosen from native character-score magnitudes.

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
        +--> smart: exact complete-word candidates and normal learning
        |
        +--> native: personal lexical edges + learned prior

current composition auxiliary evidence ------------------+
                                                          v
smart candidates / native sentence paths --> current aux bonus --> menu
```

The hot path reads only the in-memory snapshot. It must not call `Memory`, scan a
LevelDB userdb, or synchronously rebuild a snapshot for each key.

## Refresh and Compatibility

- Keep the existing idle refresh state machine and transaction ABI.
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
5. complete three-character word lookup keeps smart/userdb authority, while an
   ambiguous six-key two-character auxiliary path remains native-capable;
6. four-key native takeover remains disabled by default and its baseline accuracy is
   unchanged;
7. old ABI and failed snapshot updates fail open.

The existing native two-character, selected-segment, word-order, and user-model test
suites remain mandatory gates.
