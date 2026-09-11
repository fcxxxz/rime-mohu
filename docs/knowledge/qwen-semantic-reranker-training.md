# Qwen Semantic Reranker Training Contract

This document defines the training, evaluation, deployment, and rollback
contract for a Qwen-based semantic candidate reranker. It is a maintained
engineering reference. Dated measurements, individual experiments, and
incident reports belong in `docs/reports/`.

It complements [cross-candidate ordering](cross-candidate-ordering.md), which
owns the current Rime/Tiger candidate pipeline and the V5 contextual-ordering
baseline.

## 1. Objective and boundary

The semantic reranker chooses an order for candidates that Rime has already
produced for one complete composition. It does not generate text, recover a
candidate omitted from the menu, replace code tables, replace native decoding,
or commit text.

Its task is:

> Given the committed context, current code, candidate metadata, and the
> complete candidate menu, rank the existing candidates by likely user intent.

This is not the same as selecting the text with the highest unconstrained
character-level likelihood. A text-only character model can prefer a strong
local collocation while losing the input method structure. For example, it can
mistake the `旺角` substring for evidence that `李火旺角踩着金砖` should outrank
`李火旺脚踩着金砖`, despite the latter preserving a name boundary and the known
phrase `脚踩着`.

The reranker must therefore use code, boundaries, and dictionary evidence as
first-class inputs. It is a conservative additional signal, not the final
owner of candidate validity.

### Non-goals

- Do not let the model emit arbitrary text or an explanation.
- Do not score an incomplete-prefix candidate against candidates that consume
  all current input.
- Do not make a model request alter candidate membership, candidate spans, or
  explicit user ordering.
- Do not make a successful offline language-model benchmark a release claim
  without real Rime candidate replay.
- Do not use a pure neural score, or `weight=1.0` takeover, as a production
  default.

## 2. Current system and migration boundary

**Update 2026-09-10 (later) — in-process deployment, no external service.**
The Unix-socket scorer service form was withdrawn: librime-lua ships no
luasocket (silent fail-open) and requiring a user-started helper process is
unacceptable as product form. C2 now runs inside `libtigerengine.dylib` via an
embedded ONNX Runtime session (`tiger_sentence_native/semantic_infer.h`,
`tiger_semantic_create/score/free` ABI, Lua `semantic_create/score/free`).
Model + char vocab TSV live in `mohu_semantic/`; `libonnxruntime.1.dylib` ships
in `mohu/runtime/` resolved via `@loader_path`. The switch is labeled
魔虎语义关/开; a model-load failure auto-reverts the switch to off, so the
switch staying on is itself the load-success signal. Candidate identity is the
final menu slot order passed to native — upstream weak-table provenance
bindings do not survive librime filter boundaries and that gate was removed.

**Update 2026-09-10 — TinyCharLM removed, C2 is the sole neural_rerank backend.**
The same-method production comparison (identical evaluator, gate, protected-slot
policy, margins, and paired bootstrap over 89,970 real menus) showed TinyCharLM
regresses badly on real menus (-3.13pp probe / -10.24pp tnews; also negative
under 0.5 fusion) while C2 is the only backend with a net gain. The native
char-LM fusion path (`neural_infer.h`, `neural_rerank_policy.h`,
`tiger_engine_set_neural_rerank`, `scores.neural_reranked`, the Lua input gate
and option observer) has been deleted; `lua/mohu_student_gate_filter.lua` and
its test are gone. The shared `neural_rerank` option now drives only
`lua/mohu_semantic_gate_filter.lua` (after `uniquifier`, SCORE2 protocol,
big-endian frames) served by `research/semantic_student/scorer_service.py`
with the C2 ONNX export. The paragraphs below describe the removed TinyCharLM
path and are kept as historical context; `tiger/neural_rerank_model` is no
longer a supported key.

The current optional neural path is TinyCharLM, not Qwen:

```text
Rime translators
  -> native Tiger candidates
  -> lua/mohu_word_order_filter.lua
  -> V5 contextual character scores and ambiguity gate
  -> TinyCharLM causal character likelihood
  -> z-normalized score fusion and stable candidate reorder
```

The native code is in `tiger_sentence_native/tigerengine.cc`; the embedded
model loader and scorer are in `tiger_sentence_native/neural_infer.h`; the Lua
bridge is `tiger_sentence_native/mohu_tiger_sentence.lua`; and candidate
ownership rules are in `lua/mohu_word_order_filter.lua`.

The native `MOHU_NLM` model format is a fixed float32 TinyCharLM ABI. It cannot
load a Qwen checkpoint, GGUF model, MLX directory, ONNX model, or BPE
Tokenizer. A Qwen implementation must use a separately versioned scoring
runtime and protocol. It must not pretend that setting `tiger/neural_rerank_model`
to a Qwen asset is supported.

The existing path also has important behavior that the Qwen path must preserve:

- Rime filters are synchronous: a model result cannot safely refresh an already
  shown menu later.
- Candidate reorder is stable and only affects eligible slots; punctuation,
  pinned candidates, quick codes, single characters, and explicit overrides
  retain their ownership.
- Invalid or unavailable scoring must fail open to the byte-for-byte native
  candidate order.
- Current measured neural keypath latency and cold-start behavior are not a
  basis for a default-on model. Every Qwen profile must meet its own measured
  latency gates.

## 3. Candidate-menu data contract

One training, evaluation, or inference record represents one real Rime menu.
The record is versioned and must preserve the candidate set exactly as observed;
never rebuild a menu from the target text later.

Required top-level fields:

```json
{
  "format_version": "qwen-semantic-rerank/v1",
  "case_id": "stable-source-id",
  "schema": "mohu_zrm",
  "raw_input": "lihowhjcclvejnvr",
  "preedit": "li ho wh jc cl ve jn vr",
  "committed_context": "...",
  "target_text": "李火旺脚踩着金砖",
  "target_visible": true,
  "oracle_rank": 2,
  "candidates": []
}
```

Every candidate includes, at minimum:

```json
{
  "menu_index": 0,
  "text": "李火旺脚踩着金砖",
  "type": "mohu_zrm",
  "start": 0,
  "end": 16,
  "consumes_current_input": true,
  "preedit": "li ho wh jc cl ve jn vr",
  "native_rank": 2,
  "native_score": -37.1236436,
  "native_score_kind": "static_or_personalized_v5",
  "source_flags": ["native", "full_input"],
  "dictionary_evidence": {
    "known_phrase_spans": [
      {"text": "脚踩着", "start_char": 3, "end_char": 6, "source": "base"}
    ],
    "personal_phrase_spans": [],
    "fixed_phrase_spans": []
  },
  "code_evidence": {
    "syllables": ["li", "ho", "wh", "jc", "cl", "ve", "jn", "vr"],
    "auxiliary_constraints": [],
    "reading_constraints": []
  }
}
```

`native_score` is diagnostic evidence, not a portable calibrated probability.
If an engine version does not expose it, record `null` and a non-null exact
`engine_capabilities` manifest containing `engine_id`, `engine_version`, and a
canonicalized capability list. A record with only finite native scores must
explicitly carry `"engine_capabilities": null` or may attach the same validated
manifest for provenance. Do not silently substitute a score from another run.

Candidate boundaries are mandatory. A candidate whose `end` does not equal the
current raw input length cannot compete as a full-input semantic candidate.
Candidates after an already selected segment are permitted only when their
active raw input end matches the current composition end.

### Structured Qwen input

The scorer receives one canonical serialization of the complete menu, not a
free-form chat prompt. The serialization must contain:

1. protocol version and model profile;
2. schema and committed context;
3. raw input and verified syllable segmentation;
4. ordered candidate records, including spans, type, source flags, native rank,
   code evidence, and phrase-boundary evidence;
5. a request for exactly one score per supplied candidate index.

The model output is machine-readable candidate scores or a permutation of the
existing indexes. It may not contain free text. The protocol validates the
candidate count, every index, score finiteness, output length, UTF-8, and
format version before a result can affect a menu.

### Executable v1 encoding

`tools/qwen_semantic_rerank.py` is the normative executable validator for
`qwen-semantic-rerank/v1`. It is deliberately independent from the legacy
text-identity evaluator in `tools/evaluate_tiger_reranker.py`.

- Strings are NFC-normalized before validation and hashing. Candidate and phrase
  offsets are Unicode code-point offsets in those normalized strings; they are
  never UTF-8 byte offsets or grapheme-cluster offsets.
- The candidate array is the native menu order. `menu_index` must equal the
  array position and `native_rank` must equal `menu_index + 1`; a dump cannot
  rebuild the order from candidate text or native score later.
- Candidate display text is not an identifier and may repeat. Index identity is
  the only identity used in output validation and evaluation.
- `consumes_current_input` must exactly equal `end == len(raw_input)`. The
  scorer may receive only indices that consume all input and are not protected.
  An incomplete or protected candidate remains in the captured menu but cannot
  be silently included in a scoring batch.
- `native_score` is either a finite number or explicit `null`; every other
  protocol score is finite. A menu always carries `engine_capabilities`: it is
  `null` only when every native score is finite, otherwise it is a validated
  `{engine_id, engine_version, capabilities}` manifest. Required evidence arrays
  are present even when empty. Ordered syllables retain their observed order and
  may repeat; set-like flags, constraints, capability lists, and phrase spans
  are canonicalized deterministically.
- Every object is closed: unknown fields at the menu, candidate, evidence,
  phrase-span, request, response, score, evaluation-row, and gate levels are
  rejected rather than silently discarded before checksum or replay validation.
- Canonical bytes are UTF-8 JSON with NFC strings, sorted object keys, compact
  separators, and no NaN/Infinity. `protocol_checksum` is SHA-256 over those
  canonical menu bytes. Requests bind a model profile, checksum, menu, and the
  exact eligible index sequence.
- A response has the same format version, bound profile, and checksum, then
  exactly one complete `scores` array or `permutation`. Both are a full
  permutation of every eligible index. Scores sort descending and equal scores
  retain native index order. Missing, duplicate, unknown, protected, or
  ineligible indexes invalidate the whole response.
- A response describes only eligible slots. Applying it rewrites those slots in
  place; all other slots retain their original index and position. A closed
  gate, fail-open result, or protection veto retains the full native index
  sequence even if a diagnostic model response exists.

The companion `tools/evaluate_qwen_semantic_reranker.py` validates replay rows
against this protocol and rejects membership changes, index duplication,
unknown fields, profile/checksum mismatches, and any final order inconsistent
with an applied response. The evaluator requires a trusted `--model-profile`
argument; the untrusted row declaration must match it before any response is
validated. A valid response may be retained as diagnostic data when the gate is
closed, a scoring failure selected fail-open, or protection vetoes application;
each of those rows must retain the native full-menu index sequence and does not
count as a semantic reversal. It reports candidate-membership parity as a
release metric.

### Public-Rime observation boundary

`research/lm_sentence_compare/probes/rime_semantic_menu_dump.cc` and
`tools/qwen_semantic_menu_observation.py` define the separately versioned
`qwen-semantic-menu-observation/v1` capture format. This is deliberately an
**observation** format, not a training record. The public librime C API exposes
only composition-level `preedit`, length/caret/selection state and each
candidate's ordered `text` and `comment`. It does not expose candidate `type`,
raw-input spans, candidate preedit, source/protection flags, native score,
verified syllables, reading/auxiliary constraints, phrase evidence, or committed
context.

The observation format has a header, one composition row, ordered zero-based
candidate rows, and an end row. It preserves repeated candidate text by
`menu_index`; text is never deduplicated. Its parser rejects blank records,
truncated or incomplete menus, non-contiguous indexes, bad UTF-8, format drift,
and malformed counters.

### Controlled metadata-bridge acceptance boundary

The public observation probe remains the only usable exporter until a controlled
Lua bridge satisfies every item below. This boundary exists because the current
Mohu Lua pipeline can inspect more fields than `RimeCandidate`, but does not yet
carry a complete, index-bound semantic record through the final menu.

Current direct observations are deliberately narrow:

- a Lua filter can read a candidate's outer and genuine `type`, `preedit`, and
  Rime `start`/`end` values;
- the native translator has transient decoded `score`, `confidence`, `max_rank`,
  `personal`, path-map, and segmented-code data before it constructs a Rime
  candidate;
- the final display pipeline can wrap a candidate in `ShadowCandidate`, change
  its outer type/comment, apply user overrides, and deduplicate it.

Those facts do **not** make a training record. In particular, Rime segment
positions are offsets in the original UTF-8 `Context.input`; they are not the
NFC Unicode-code-point offsets required by this protocol. The translator also
currently discards its decoded score when it assigns display `quality`, and
non-native final candidates have no corresponding native decode item. A bridge
must never treat display quality, a changed comment, candidate text, or raw-code
length as a substitute for one of these values.

A future `qwen-semantic-rime-lua-bridge/v1` is admissible only when all of the
following are implemented and tested in an isolated staging schema:

1. It observes the post-`uniquifier` menu without adding, removing, rewriting,
   committing, or reordering candidates. Its `menu_index` and `native_rank` are
   assigned only at that final observation point.
2. It binds an immutable opaque provenance record to every final candidate before
   a later filter can wrap it. The final observer must resolve that record through
   the genuine candidate, reject an ambiguous/missing/duplicate binding, and
   reject a menu when a transformed or synthetic candidate has no authoritative
   producer record.
3. It converts each Rime byte boundary to an NFC Unicode-code-point offset using
   a tested UTF-8 boundary map for the exact original raw input. Any invalid UTF-8,
   boundary inside a code point, normalization length change, or mismatch with
   the final candidate's active span rejects the entire menu. An ASCII-only test
   does not prove this conversion.
4. Native translator records retain their decoded score and exact score kind from
   the same `decode()` invocation. Other candidates either expose an authoritative
   score from their own producer or carry `native_score: null` with the exact
   capability manifest that proves no score is available. A rank, display
   `quality`, or score from a separate decode is not acceptable provenance.
5. Candidate source flags, protection flags, verified syllables, auxiliary and
   reading constraints, and each phrase-evidence span come from the owning
   translator/filter or an explicitly versioned dictionary lookup. Missing
   mandatory provenance rejects the menu; it is not represented as inferred empty
   evidence. Pinned, quick-code, incomplete, override, and management candidates
   remain present and protected in the captured menu.
6. Committed context is exported only from an explicitly authorized isolated
   replay source. Production diagnostics contain only counts, timing buckets,
   profile IDs, hashes, and error classes; they never log typed candidate or
   context text.
7. The bridge writes to a caller-provided, owned staging path with no shell
   interpolation, no network operation, no live Rime user directory, and no
   persistent production capture. Its parser validates the completed output with
   `tools/qwen_semantic_rerank.py` before the record can be teacher-labeled.

The implementation therefore begins with a producer-facing metadata ABI and
fixture tests for mapping survival through `ShadowCandidate`, override, and
`uniquifier`; it does not begin by widening the public C++ observation format or
by launching Qwen teacher scoring.

`lua/mohu_semantic_meta.lua` is the first implemented piece of that ABI: a
one-time shallow-copied candidate binding, ordinary outermost-first lookup, and
strict `resolve_menu()` collection for a future final observer. The latter
requires exactly one binding along each final candidate's full genuine chain,
rejects binding reuse across final slots, and preserves duplicate display text
only when the slots have distinct bindings. It also provides genuine-chain depth
and cycle guards, checked UTF-8 byte-boundary to Unicode-code-point span
conversion, and an ASCII-only exportable-raw-input gate (Lua can prove NFC
invariance for ASCII raw inputs only; anything else fails closed).
`tests/mohu_semantic_meta_test.lua` covers these invariants, and a Lua-to-Python
seam check feeds ABI-produced spans into `validate_menu_record`.
`tiger_sentence_native/mohu_tiger_sentence.lua` now binds a
`mohu-native-decode/v1` record at native candidate construction. It preserves
only values from that exact decode response: score, confidence, max rank,
personal-edge status, segmented code, and path-map, alongside separate original
`Context.input` bytes and the normalized decoder code stream. It does not treat
display `quality` as a score or make this data exportable by itself. Survival of
bindings through the deployed `ShadowCandidate`, override, and `uniquifier`
chain still requires the isolated-staging observer and is not yet claimed.

### Producer coverage inventory

The two public schemas share the same candidate-producing and final display
pipeline. The only material schema-specific producer input is the Tiger scheme
and candidate type. This inventory is a release blocker for a future staging
bridge, not a permit to export a native-only subset of a menu.

| Producer group | Current authoritative facts | Missing protocol facts | Required disposition before export |
| --- | --- | --- | --- |
| Native Tiger sentence translator | Same-decode score, score kind, confidence, max rank, personal-edge state, segmented code, path map, original raw-byte input (`mohu-native-decode/v1` binding at construction) | Explicit protection, verified protocol syllables and reading constraints, phrase/fixed dictionary spans, final-menu survival | Complete its producer record and prove final-chain survival. |
| Fixed/smart lexical translators | `mohu-lexical/v1` binding at the express-translator query boundary: source (`fixed`/`smart`), translator variant (`fixed_primary`/`fixed_alternate`/`smart`/`smart_static`), outer and genuine type, dynamic type, query-time preedit, honest score-unavailability | Verified syllables, entry codes, dictionary/phrase evidence, explicit protection policy, final-menu survival | Fill code and dictionary evidence via explicitly versioned lookups; prove final-chain survival. |
| User phrases, pinned candidates, pin tips, and capture status | `mohu-pin/v1` / `mohu-pin-ui/v1` bindings at construction: pin origin, phrase, commit count, protected state, score unavailability | Final-menu survival; spans observed only at the final menu | Retain every slot as protected and prove final-chain survival. |
| Symbol hints, date/time, number, Unicode, and management candidates | `mohu-symbol-hint/v1` / `mohu-temporal/v1` / `mohu-number/v1` / `mohu-unicode/v1` / `mohu-manager/v1` bindings: always-protected synthetic records with producer facts (symbol code, code point, conversion kind, record index) | Final-menu survival only | Prove final-chain survival; these slots stay protected and never enter scoring. |
| Built-in punctuation, reverse lookup, and custom phrases | Final Rime type/span may be observable | No Lua-visible producer boundary; no producer-owned mode, protection, readings, or evidence bindings | Bind at the earliest Lua filter with an explicit versioned type map, or reject any menu containing them. |
| Candidate override and display wrappers | Genuine-chain traversal is covered for hint `ShadowCandidate` fixtures | Override visibility/protection is not immutable producer evidence; comments and outer type are presentation | Preserve original binding through wrappers and reject ambiguous, missing, or reused provenance. |
| Built-in simplifiers and `uniquifier` | Final display membership/order only | Any reliable display-to-producer inference | Observe strictly after them; assign indexes there and resolve provenance only through the genuine chain. |

All bindings above are inert: `mohu_semantic_meta` loads optionally, every
bind is failure-isolated, rebinding a candidate already carrying a record is
ignored, and candidate generation is unchanged when the module is absent. The
producer suites (`tests/mohu_semantic_producer_test.lua` plus the symbol, pin,
manager, and express extensions) assert both the bound record and wrapper
resolution, but they are not yet final-menu survival proof.

A bridge must fail the **entire menu** when any final slot lacks a complete
producer binding. It must not drop unsupported slots, export only native slots,
or fill source flags, protection, score, syllables, spans, phrase evidence,
reading constraints, or auxiliary constraints from candidate text, comments,
display quality, final rank, raw-code length, or heuristic preedit parsing.

The final observation point is immediately after `uniquifier` in an isolated
staging schema only. No public schema may mount the observer until every
configured producer group has a tested complete binding and the staging bridge
has proved its owned-path, no-network, no-live-user-directory, parser-validation
requirements.

## 4. Supervision and model objective

### 4.1 Source of truth

Labels come from user-intent ground truth attached to genuine Rime candidate
replays. The target is useful only when it is present in the captured candidate
menu. Report menu Oracle coverage separately; a reranker cannot repair a target
that is absent.

Synthetic homophone and phrase cases are valuable regression probes, but cannot
be the only training or quality corpus. They must be labelled as synthetic and
must not replace naturally occurring candidate distributions.

### 4.2 Training objective

The first Qwen implementation is a small Qwen-family discriminative ranker,
fine-tuned with LoRA or QLoRA. It is not merely a teacher used to distil another
text-only character model.

Use a menu-level listwise ranking loss as the primary objective. Add pairwise
hard-negative loss for target-versus-confusable alternatives. Hard-negative
sampling must overrepresent failures where text-only likelihood opposes input
method evidence, including:

- person/place/product names adjacent to common collocations;
- known dictionary phrases versus locally fluent non-phrases;
- alternative readings of the same character;
- auxiliary-code constrained alternatives;
- native top-1 correct cases that an older neural model reverses;
- full-input candidates versus incomplete-prefix candidates.

A model run records loss weights, negative-sampling rules, sequence limits,
normalization, template checksum, seed, optimizer, learning-rate schedule,
effective batch size, epochs, checkpoint-selection rule, and software/hardware
environment. Do not compare runs whose protocol serialization differs without
calling out the mismatch.

### 4.3 Model profile

A profile is a checked-in manifest, not an informal model name. It pins:

- base-model repository and immutable revision;
- tokenizer repository, revision, and hash;
- prompt/serialization format version and checksum;
- adapter and merged-weight hashes;
- quantization method and runtime version;
- candidate, token, request-size, and context limits;
- supported operating systems and accelerator backends.

The document deliberately does not choose a default Qwen checkpoint. A model
name becomes a default only after passing the release gates in this document.

## 5. Data lineage, privacy, and leakage control

Each corpus and derived candidate dataset has a manifest containing source URL
or repository revision, license, retrieval date, SHA-256, normalization rules,
filtering rules, document identifier, and split assignment. Record whether data
may contain personal text. Do not ingest private user contexts or menus without
explicit consent and a separately approved retention policy.

Apply all of the following before training:

1. Normalize text with a versioned implementation.
2. Deduplicate at document and normalized-sentence level.
3. Split by source document before extracting candidate menus; never split
   adjacent sentences from one source document across train and test.
4. Freeze development and test manifests before selecting model profiles or
   fusion parameters.
5. Audit normalized exact overlap between all training text and every held-out
   test sentence. Publish counts, exclusions, and hashes.
6. Keep raw candidate/context artifacts outside Git when privacy or size
   requires it, but commit machine-readable aggregate manifests and hashes.

The existing isolated replay and corpus infrastructure under
`research/lm_sentence_compare/` is the preferred starting point. Reuse its
frozen test corpus, source hashing, isolated Rime workspace rule, and paired
comparison methodology. Use `tools/evaluate_tiger_reranker.py` for candidate
permutation validation, Oracle coverage, correction/regression counts, and
paired-bootstrap reporting.

### Fixed regressions

Preserve the following as holdout regression cases, not casually recycled as
training examples:

- `xtji`: a rare-reading path must not promote `学期` over the code-compatible
  intended output through text-only plausibility.
- `gyjciu`: partial `拐角` must not displace full-input `拐角处`.
- `hklipu`: partial `好礼` must not displace full-input `好离谱`.
- `李火旺脚踩着金砖`: a name boundary plus `脚踩着` phrase evidence must not be
  reversed to a locally tempting `旺角` path.

For each fixed probe, create structurally similar held-out cases from separate
sources. Passing memorized strings is not evidence that the model learned the
underlying constraint.

## 6. Fusion, gating, and reversal protection

The native result carries information the semantic model does not own:

- code-table eligibility and candidate spans;
- reading and auxiliary-code constraints;
- fixed, base, and personal dictionary evidence;
- native V5 score and user-model effects;
- explicit pin and override behavior.

A Qwen score is an additional, menu-relative feature. It is not an authority to
discard these signals.

### Preconditions

The reranker may run only when all of these are true:

- the user enabled the option and a complete, verified model profile is loaded;
- the composition has sufficient verified complete syllables;
- the candidate menu contains at least two distinct eligible full-input texts;
- all participating records have valid spans and protocol features;
- the target scope is a bounded shortlist and no protected candidate ownership
  rule is violated;
- the native static ambiguity gate, calibrated on development data, allows
  semantic scoring.

The implementation scores all eligible candidates in the participating batch.
It must not let a malformed, OOV, or unsupported candidate shrink the batch and
silently change which menu slots compete.

### Fusion and stable order

Calibrate fusion and reversal thresholds only on the frozen development set.
The deployed formula may combine normalized native and semantic scores, but it
must preserve explicit native evidence and include a nonzero native floor. A
semantic-only `weight=1.0` takeover is prohibited.

A first-candidate reversal requires all of the following:

1. the semantic margin exceeds a calibrated reversal threshold;
2. the fused advantage exceeds a calibrated native-protection threshold;
3. no phrase, name, reading, auxiliary, pin, or override protection vetoes the
   reversal;
4. the result is a valid full permutation of the existing eligible candidate
   slots.

Stable sort determines ties. Only the participating slots are rewritten; all
other candidates retain their exact original positions. Parameters are chosen
on development data and frozen before held-out evaluation.

### Fail-open invariant

Preserve the original candidate sequence byte for byte when any of these occurs:

- model unavailable, profile mismatch, checksum mismatch, startup failure, or
  model reload in progress;
- deadline exceeded, transport error, malformed response, duplicate/missing
  index, non-finite score, unsupported UTF-8, or response-version mismatch;
- empty candidate, token/context/candidate-count overflow, missing required
  code or phrase feature, or invalid candidate span;
- a closed ambiguity gate, flat semantic scores, or lack of a permitted
  first-candidate reversal.

No exception path may reorder a partial result.

## 7. Local scorer service contract

Use a local, isolated scorer service as the initial Qwen runtime. In-process
Qwen would require a new native tokenizer, quantized Qwen loader, likelihood
backend, memory lifecycle, and cross-platform inference stack; it cannot reuse
the TinyCharLM ABI and would enlarge the input-method process failure surface.

The service performs bounded batched scoring only. It does not listen on a
network interface, download models, generate text, or persist candidate/context
content. Diagnostics contain counts, timing buckets, profile IDs, hashes, and
error classes rather than typed user text.

On Unix, use a per-user private directory with mode `0700` and a Unix socket
with mode `0600`. On Windows, use a named pipe with equivalent user-only ACLs;
do not assume Unix sockets exist. The protocol specifies maximum frame size,
maximum candidates, maximum context/candidate tokens, one hard synchronous
deadline, request IDs, backpressure, service restart behavior, and model-switch
semantics.

Model startup is lazy but cannot block a key indefinitely. The client has a
small hard deadline and immediately fails open. The release package must contain
the scorer, model profile, tokenizer, all runtime dependencies, platform
launch/supervision behavior, integrity manifests, and rollback procedure. A
schema option without this package closure is unsupported.

A distilled student may later replace Qwen for latency, but only after it is
trained and evaluated against the same structured candidate-menu contract,
fixed regressions, release corpus, and fail-open semantics. Distillation must
not discard raw-code, boundary, source, or dictionary evidence.

### Client integration precedent and semantic path decision

`lua/mohu_student_gate_filter.lua` (experimental, default-off, not wired into
the public schemas) proves the client architecture end to end: luasocket
Unix-socket calls from librime-lua with injected module paths, a native
ambiguity gate (top-2 z-normalized V5 char-score margin), a hard per-request
timeout budget, byte-exact passthrough on any failure, a bounded LRU cache
invalidated on context change, and protected-slot exclusion (punct, pinned,
single-char, quick-code/pin indicators).

`lua/mohu_semantic_gate_filter.lua` reuses exactly this skeleton and is now
wired into both public schemas immediately after `uniquifier`, sharing the
existing `neural_rerank` (大模型) switch per the maintainer's 2026-09-10
decision. Differences from the char-level filter:

1. It speaks `mohu-semantic-gate/v1` SCORE2 line protocol (per-candidate
   V5 context char scores) to the isolated scorer service
   (`research/semantic_student/scorer_service.py`).
2. Runtime gating is the shared `neural_rerank` option plus
   `mohu/semantic_rerank/enable` (default true); config lives under
   `mohu/semantic_rerank/*` in `mohu.yaml` with a user-dir-relative socket
   default (`mohu_semantic/semantic.sock`).
3. Every window candidate must resolve producer provenance via
   `mohu_semantic_meta`; any failure fails open the entire menu.
4. First-choice reversal additionally requires the semantic z margin;
   every other outcome preserves the native order. Service absent/timeout/
   malformed response byte-exact passthrough — the switch can be on with no
   service installed at zero menu change (mira parity test covers both
   switch states).

## 8. Evaluation and release gates

Evaluate the semantic reranker as a paired menu-ordering change. Do not compare
raw Qwen likelihood across unrelated menus as a quality metric.

### Required evaluation layers

1. Unit and protocol tests: canonical serialization, feature extraction, output
   validation, permutation checks, timeout behavior, malformed responses, and
   byte-for-byte fail-open behavior.
2. Model tests: deterministic score/reorder results for a pinned profile and
   compatibility tests for model, tokenizer, runtime, and quantization hashes.
3. Native/Lua integration tests: protected candidates, complete-span eligibility,
   option toggles, personal words, override order, and gate-open/gate-closed
   behavior.
4. Isolated real-Rime replay: fresh sessions, no live user directory or model
   paths, captured candidate menus, and exact final-menu comparison.
5. Formal held-out quality benchmark: training-disjoint data, preserved menus,
   Oracle coverage, native-on versus Qwen-on paired results, and fixed-seed
   paired bootstrap confidence intervals.
6. Performance and reliability benchmark: model load, first inference, warm
   scorer-only time, full key-to-final-menu time, UI rendering time separately,
   gate-open rate, memory, timeouts, and restart behavior.

### Mandatory reporting

Report, overall and by relevant slices:

- case count and candidate Oracle coverage;
- baseline, reranked, and Oracle Top-1;
- corrections, regressions, and net change;
- paired-bootstrap confidence interval for the Top-1 delta;
- context length, raw-code length, candidate count, candidate type, phrase/name,
  auxiliary-code, reading-ambiguity, and personal-word slices;
- Qwen gate-open rate, reversal rate, protected-veto rate, and fail-open rate;
- warm and cold P50/P95/P99/max latency, memory, and model startup cost.

A release candidate must have all of the following:

- a held-out net quality gain whose lower confidence bound is positive;
- a predeclared regression-rate cap, both overall and for mandatory slices;
- zero regressions in the fixed regression suite;
- no loss of candidate Oracle coverage or candidate-membership parity;
- native/Rime replay parity for baseline behavior when the option is disabled;
- bounded and measured latency, memory, startup, timeout, and restart behavior
  on every supported platform;
- privacy, package-closure, model-integrity, licensing, and rollback review.

Default remains disabled until a profile passes every gate. A positive result on
a curated probe, an untracked teacher score dump, or a latency-only benchmark
cannot promote the option.

## 9. Required release artifacts

Every released profile includes:

- model profile and schema/config profile;
- base model, adapter/merged model, tokenizer, quantization, runtime, and
  protocol hashes;
- source/license notice and supported-platform list;
- corpus, candidate-pool, split, training, teacher, and evaluation manifests;
- code revision, templates, hyperparameters, seeds, and environment lock;
- held-out quality report and raw-artifact hash manifest;
- latency/reliability report and package-closure manifest;
- fixed-regression results;
- verified rollback steps that remove or disable only the semantic service and
  restore the native candidate sequence.

## 10. Implementation sequence

1. Define and test the versioned candidate-menu exporter and canonical feature
   serialization.
2. Build labelled, real-Rime replay datasets with source-level splits, manifests,
   Oracle accounting, and leakage audits.
3. Fine-tune candidate-menu Qwen profiles and select fusion/gate thresholds only
   on development data.
4. Implement the local service and strict fail-open client against the protocol.
5. Add isolated Rime integration, regression, package, and platform tests.
6. Run the held-out quality and latency release suite; publish a dated report.
7. Ship an opt-in profile only after the release gates pass; retain an immediate
   configuration-only rollback path.

### Current implementation status (2026-09-09, replanned after baseline audit)

**Root-cause traceback (all confirmed against repo data):**

1. **Wrong evaluation baseline.** All v1 numbers compare against the *raw
   `tiger_decode` output order*, not the production menu. Production order
   is decided by the express translator (fixed/smart interleaving,
   `four_code_yield_pairs` displacement, IJRQ deferral, quick-code
   placement, quality values) followed by the filter chain. Verified
   example: `yuyt` ranks 鱼书雁帖 first in raw decode, but 鱼书雁帖 is a
   `mohu_zrm_fixed` four-code entry and production already serves the smart
   预约 first — no model needed. The +8pp is a decoder-layer delta only.
2. **Wrong training distribution.** The student was trained on raw-decode
   menus (native candidates only, decode scores). Production menus mix
   fixed/smart/user candidates with different score semantics and order.
3. **Train/deploy task mismatch.** Trained as full-menu listwise ranking;
   deployed (gate filter) as gated top-K reorder with margin and protection.
   The 0.9462 measurement is not the deployed behavior's quality.
4. **Context mismatch.** Training context is clean corpus prefixes;
   production `commit_history` is assembled from sequential user commits.
5. **Showcase bias.** Margin-sorted example selection over-represented
   token-length artifacts and polyphonic-char path cases (纤 qian/xian
   makes 一样纤细 a non-homophone competitor at word level).
6. **Session-state leakage in the first pipeline-capture attempt.** The
   initial probe reused one Rime session across cases while smart userdb and
   Tiger user-model learning were enabled. Those captures are invalid and
   discarded — this includes the previously quoted production top1 `0.9309`,
   old-student `0.8701`, and local files named `pipeline-probe*.jsonl`; none
   may be used as baselines or training data. The corrected capture
   creates/destroys one session per case,
   applies benchmark-only patches (`smart/enable_user_dict=false`,
   `custom_phrase/enable_user_dict=false`, `tiger/user_model=false`), and
   was verified Mac/Windows with 100/100 exact context/menu/comment parity.
7. **Text-identity alignment violation.** The first pairer joined public
   production observations to raw-decode scores by display text, contrary
   to the protocol (duplicate text is valid and text is not identity). That
   join is removed: public observations carry explicit unknown score/source
   evidence. Real decoder scores can enter production training only through
   a future candidate-object provenance observer.
8. **Duplicate held-out cases.** tnews had 58,305 rows but 53,173 unique
   case IDs; 5,132 exact duplicate rows (8.8%) made the old bootstrap CI
   falsely narrow. New production tnews capture is de-duplicated by case ID;
   old tnews CI is void. Train/dev/probe IDs are unique.

**Replanned sequence (production truth first):**

- **A. Full-pipeline menu capture.** Isolated Rime sessions running the real
  schema: commit the context sentence prefix key-by-key, type the target
  code, capture the post-`uniquifier` menu (public observation probe:
  text/comment/order) **plus** the same-code raw decode (scores, segmented
  code) aligned by candidate text. Each record then carries the production
  order (baseline) and decoder evidence (features). Built-in
  punct/reverse/custom-phrase provenance must be bound or marked protected
  for the export to close.
- **B. Correct baseline and delta measurement.** Baseline = production menu
  order. The student is evaluated *in its deployed shape*: gate-selected
  ambiguity windows only, protected slots frozen, margin-suppressed first
  flips; report corrections/regressions **relative to production order**,
  plus byte-for-byte parity of the disabled path (mira test staged).
- **C. Retrain to the deployed task.** Features: decode scores for native
  candidates only, explicit unknown markers for fixed/smart (never
  constants); task = reorder the eligible window of a production-shaped
  menu; labels from corpus ground truth; context built from simulated
  sequential commits.
- **D. Preregistered gates (per layer, no more scope mixing).** Production
  layer: correction:regression ≥ 3:1, regression rate ≤ 0.5%, paired
  bootstrap CI lower bound > 0. Decoder layer (ablation only): compare with
  v1 numbers. End-to-end: disabled-path byte parity, latency budget. Qwen
  zero-shot stays as a reference measurement on the same records.

Every future quality number must state its measurement layer (decoder /
pipeline / end-to-end). Prior native-replay numbers are re-scoped as
decoder-layer ablation records in the v1 launch report.

## References

- [Cross-candidate ordering](cross-candidate-ordering.md)
- [Native Tiger README](../../tiger_sentence_native/README.md)
- [Tiny character scorer report](../reports/2026-09-07-tiny-char-scorer.md)
- [Neural rerank repair report](../reports/2026-09-08-neural-rerank-fix.md)
- [Neural rerank optimization report](../reports/2026-09-08-neural-rerank-optimization.md)
- `tools/evaluate_tiger_reranker.py`
- `research/lm_sentence_compare/README.md`
- `tiger_sentence_native/QWEN_RERANKER_PLAN.md` and
  `tiger_sentence_native/QWEN35_SCORER.md` are historical references only;
  neither describes a maintained Qwen runtime in this checkout.
