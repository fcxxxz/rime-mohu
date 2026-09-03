#!/usr/bin/env python3
"""Build the tail-auxiliary cross-candidate benchmark.

Differences from ``build_cross_candidate_cases``:

- every target word keeps exactly ``--contexts-per-target`` real corpus
  sentences in which the word occurs after a real prefix (never at the
  sentence start);
- candidate sentences are excluded against the union of the Mohu training
  mixes by normalized exact sentence equality before sampling;
- the input modes are one-key and two-key tail auxiliary codes; Mohu and
  Moran additionally test the two-key form suffixed with ``o`` and ``/``
  (the yield-the-full-code suffix both schemes derive in their spelling
  algebra);
- Yeying and Wanxiang Pro have no such suffix rule in their spelling
  algebra, so they only contribute the two tail modes.

A case is dropped when any scheme cannot encode the target in every required
mode or cannot encode the prefix; dropped counts are recorded per reason in
the manifest.  Prefix candidates that fail to commit at run time stay in the
benchmark as context-unavailable rows.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

from research.lm_sentence_compare.audit_training_overlap import sentence_key
from research.lm_sentence_compare.build_cross_candidate_cases import (
    FLYPY_PRONUNCIATIONS,
    REPO,
    Context,
    Reading,
    SchemeEncoding,
    build_homophone_groups,
    encode_prefix,
    load_code_readings,
    load_frequency_entries,
    load_full_pinyin_readings,
    pick_target_reading,
    sha256_file,
    strict_word_pronunciations,
)
from research.lm_sentence_compare.encode_sentences import load_word_vocabulary
from tools.zrmify import unzrmify1

HERE = Path(__file__).resolve().parent
MIN_CJK_CHARS = 6
MAX_CJK_CHARS = 24
SENTENCE_RE = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fff]+$")
SUFFIX_SCHEMES = ("moran", "mohu_zrm", "mohu_flypy")
SCHEME_MODES: dict[str, tuple[str, ...]] = {
    "moran": ("tail1", "tail2", "tail2o", "tail2s"),
    "yeying": ("tail1", "tail2"),
    "wxpro": ("tail1", "tail2"),
    "mohu_zrm": ("tail1", "tail2", "tail2o", "tail2s"),
    "mohu_flypy": ("tail1", "tail2", "tail2o", "tail2s"),
}
SUFFIX_RULE_NOTE = (
    "YYXXo and YYXX/ are the yield-the-full-code spellings derived by the "
    "Mohu and Moran spelling algebra; Yeying and Wanxiang Pro define no "
    "such suffix, so those schemes stop at the two-key tail form."
)


@dataclass(frozen=True, slots=True)
class Sentence:
    identifier: str
    source: str
    text: str


def _hex(value: str) -> str:
    return value.encode("utf-8").hex()


def _sentence_allowed(text: str, training_hashes: np.ndarray) -> bool:
    if not (
        MIN_CJK_CHARS <= len(text) <= MAX_CJK_CHARS
        and SENTENCE_RE.fullmatch(text)
    ):
        return False
    key = int.from_bytes(sentence_key(text), "big")
    position = min(int(np.searchsorted(training_hashes, np.uint64(key))), len(training_hashes) - 1)
    return training_hashes[position] != np.uint64(key)


def load_sentences(paths: Sequence[Path]) -> tuple[list[Sentence], dict[str, int]]:
    sentences: list[Sentence] = []
    seen: set[str] = set()
    duplicates = 0
    for path in paths:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                text = str(row.get("text", ""))
                identifier = str(row.get("id", f"{path.stem}-{line_number}"))
                source = str(row.get("source", path.stem))
                if not text or text in seen:
                    duplicates += 1
                    continue
                seen.add(text)
                sentences.append(Sentence(identifier, source, text))
    return sentences, {"duplicate_texts": duplicates}


def load_plain_sentences(
    paths: Sequence[Path],
    training_hashes: np.ndarray,
    *,
    cap_per_file: int,
    seen: set[str],
) -> tuple[list[Sentence], dict[str, int]]:
    """Stream one-sentence-per-line files, keeping only training-clean rows.

    The extracted sources are the upstream of the training mixes, but the
    mixes subsampled them; exact-match exclusion against the consumed union
    keeps only sentences the trainer never saw.
    """

    sentences: list[Sentence] = []
    per_file: dict[str, int] = {}
    duplicates = 0
    rejected = 0
    for path in sorted(paths, key=lambda item: item.name):
        kept = 0
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if kept >= cap_per_file:
                    break
                text = line.strip()
                if not text or text in seen:
                    duplicates += 1
                    continue
                if not _sentence_allowed(text, training_hashes):
                    rejected += 1
                    continue
                seen.add(text)
                sentences.append(Sentence(f"{path.stem}-{line_number}", path.stem, text))
                kept += 1
        per_file[path.stem] = kept
    return sentences, {
        "duplicate_or_seen": duplicates,
        "training_or_length_rejected": rejected,
        "per_file_kept": per_file,
    }


def filter_sentences(
    sentences: Sequence[Sentence], training_hashes: np.ndarray
) -> tuple[list[Sentence], dict[str, int]]:
    kept: list[Sentence] = []
    training_overlap = 0
    length_or_charset = 0
    for sentence in sentences:
        if not (
            MIN_CJK_CHARS <= len(sentence.text) <= MAX_CJK_CHARS
            and SENTENCE_RE.fullmatch(sentence.text)
        ):
            length_or_charset += 1
            continue
        key = int.from_bytes(sentence_key(sentence.text), "big")
        position = int(np.searchsorted(training_hashes, np.uint64(key)))
        position = min(position, len(training_hashes) - 1)
        if training_hashes[position] == np.uint64(key):
            training_overlap += 1
            continue
        kept.append(sentence)
    return kept, {
        "training_overlap": training_overlap,
        "length_or_charset": length_or_charset,
    }


def segment_with_set(text: str, wordset: frozenset[str] | set[str], *, max_len: int = 10) -> list[str]:
    """Same longest-forward match as encode_sentences.segment_max_match.

    The shared helper rebuilds a set from the vocabulary on every call, which
    is quadratic in the corpus size; this copy hoists the set out.
    """

    result: list[str] = []
    index = 0
    while index < len(text):
        word = text[index]
        upper = min(max_len, len(text) - index)
        for length in range(upper, 1, -1):
            candidate = text[index : index + length]
            if candidate in wordset:
                word = candidate
                break
        result.append(word)
        index += len(word)
    return result


def build_contexts(
    sentences: Sequence[Sentence],
    eligible_words: set[str],
    vocabulary: set[str],
    *,
    max_word_length: int = 10,
) -> dict[str, list[Context]]:
    contexts: dict[str, list[Context]] = defaultdict(list)
    seen: dict[str, set[str]] = defaultdict(set)
    wordset = frozenset(vocabulary)
    for sentence in sentences:
        words = segment_with_set(sentence.text, wordset, max_len=max_word_length)
        offset = 0
        for word in words:
            if word in eligible_words and offset > 0:
                prefix = sentence.text[:offset]
                if prefix not in seen[word]:
                    contexts[word].append(
                        Context(prefix, sentence.text, sentence.source, sentence.identifier)
                    )
                    seen[word].add(prefix)
            offset += len(word)
    return dict(contexts)


def tail_mode_codes(reading_codes: Sequence[str], reading_aux: Sequence[str]) -> dict[str, str]:
    if len(reading_codes) != 2 or len(reading_aux) != 2:
        raise ValueError("tail modes require two codes")
    first, second = reading_codes
    aux2 = reading_aux[1]
    if len(aux2) < 2:
        raise ValueError("tail2 requires at least two tail auxiliary codes")
    base = {
        "tail1": first + second + aux2[0],
        "tail2": first + second + aux2[:2],
    }
    base["tail2o"] = base["tail2"] + "o"
    base["tail2s"] = base["tail2"] + "/"
    return base


def target_encodable(
    word: str,
    pronunciation: tuple[str, str],
    word_readings: Mapping[str, Sequence[Reading]],
    char_readings: Mapping[str, Sequence[Reading]],
) -> bool:
    target, _ = pick_target_reading(word, pronunciation, word_readings, char_readings)
    if target is None:
        return False
    try:
        tail_mode_codes(target.codes, target.auxiliaries)
    except ValueError:
        return False
    return True


def encode_tail_case(
    word: str,
    pronunciation: tuple[str, str],
    prefix: str,
    word_readings: Mapping[str, object],
    char_readings: Mapping[str, object],
    modes: tuple[str, ...],
) -> SchemeEncoding:
    target, source = pick_target_reading(word, pronunciation, word_readings, char_readings)
    if target is None:
        return SchemeEncoding("", {mode: "" for mode in modes}, "", 0, False, "target_reading")
    try:
        candidates = tail_mode_codes(target.codes, target.auxiliaries)
    except ValueError:
        return SchemeEncoding("", {mode: "" for mode in modes}, "", 0, False, "target_auxiliary_length")
    mode_codes = {mode: candidates[mode] for mode in modes}
    prefix_result = encode_prefix(prefix, char_readings)
    if prefix_result is None:
        return SchemeEncoding("", mode_codes, source, 0, False, "prefix_reading")
    prefix_code, fallback = prefix_result
    return SchemeEncoding(prefix_code, mode_codes, source, fallback, True, "")


def write_inputs(
    output_root: Path,
    cases: Sequence[Mapping[str, object]],
    schemes: Sequence[str],
) -> None:
    input_dir = output_root / "in"
    input_dir.mkdir(parents=True, exist_ok=True)
    for scheme in schemes:
        modes = SCHEME_MODES[scheme]
        fresh_rows: list[str] = []
        after_rows: list[str] = []
        for case in cases:
            encoding = case["encodings"][scheme]
            scheme_modes = encoding["modes"]
            columns = [f"{mode}={scheme_modes[mode]}" for mode in modes]
            b_row = "\t".join(
                [
                    "B",
                    str(case["case_id"]),
                    *columns,
                    _hex(str(case["word"])),
                    "0",
                    str(case["word"]),
                ]
            )
            fresh_rows.append(b_row)
            after_rows.append(
                "\t".join(
                    [
                        "W",
                        str(case["case_id"]),
                        str(encoding["prefix_code"]),
                        _hex(str(case["prefix"])),
                    ]
                )
            )
            after_rows.append(b_row)
        (input_dir / f"{scheme}.fresh.tsv").write_text(
            "\n".join(fresh_rows) + "\n", encoding="utf-8"
        )
        (input_dir / f"{scheme}.afterA.tsv").write_text(
            "\n".join(after_rows) + "\n", encoding="utf-8"
        )


def build_benchmark(
    *,
    frequency_path: Path,
    corpus_paths: Sequence[Path],
    plain_corpus_paths: Sequence[Path],
    training_hashes_path: Path,
    training_audit_path: Path,
    output_root: Path,
    moran_base: Path,
    moran_chars: Path,
    yeying_dict: Path,
    wxpro_words: Path,
    wxpro_chars: Path,
    contexts_per_target: int,
    target_limit: int,
    plain_cap_per_file: int,
) -> dict[str, object]:
    frequency_entries = load_frequency_entries(frequency_path)
    frequency_by_word = {entry.word: entry for entry in frequency_entries}

    canonical = load_code_readings(
        REPO / "mohu_zrm.base.dict.yaml", decode=unzrmify1, text_lengths={2}
    )
    canonical_pronunciations = strict_word_pronunciations(frequency_by_word, canonical)
    groups = build_homophone_groups(frequency_entries, canonical_pronunciations)
    eligible_words = {
        entry.word for entries in groups.values() for entry in entries
    }

    training_hashes = np.load(training_hashes_path)
    audit = json.loads(training_audit_path.read_text(encoding="utf-8"))
    sentences, duplicate_counts = load_sentences(corpus_paths)
    seen = {sentence.text for sentence in sentences}
    plain_sentences, plain_counts = load_plain_sentences(
        plain_corpus_paths, training_hashes, cap_per_file=plain_cap_per_file, seen=seen
    )
    kept_sentences, filter_counts = filter_sentences(sentences, training_hashes)
    kept_sentences.extend(
        sentence for sentence in plain_sentences
    )

    vocabulary = load_word_vocabulary()
    contexts = build_contexts(kept_sentences, eligible_words, vocabulary)

    qualified = [
        entry
        for entry in frequency_entries
        if entry.word in contexts
        and len(contexts[entry.word]) >= contexts_per_target
        and canonical_pronunciations.get(entry.word) in groups
    ]
    if target_limit:
        selected = qualified[:target_limit]
    else:
        selected = qualified
    if not selected:
        raise ValueError(
            f"no target word has {contexts_per_target} clean contexts; "
            f"widest word has {max((len(v) for v in contexts.values()), default=0)}"
        )

    scheme_sources = {
        "moran": (
            load_code_readings(moran_base, decode=unzrmify1, text_lengths={2}),
            load_code_readings(moran_chars, decode=unzrmify1, text_lengths={1}),
        ),
        "yeying": (
            load_code_readings(
                yeying_dict,
                decode=lambda code: FLYPY_PRONUNCIATIONS.get(code, ("",))[0],
                text_lengths={2},
                code_pronunciations=FLYPY_PRONUNCIATIONS,
            ),
            load_code_readings(
                yeying_dict,
                decode=lambda code: FLYPY_PRONUNCIATIONS.get(code, ("",))[0],
                text_lengths={1},
                code_pronunciations=FLYPY_PRONUNCIATIONS,
            ),
        ),
        "wxpro": (
            load_full_pinyin_readings(wxpro_words, text_lengths={2}),
            load_full_pinyin_readings(wxpro_chars, text_lengths={1}),
        ),
        "mohu_zrm": (
            load_code_readings(
                REPO / "mohu_zrm.base.dict.yaml", decode=unzrmify1, text_lengths={2}
            ),
            load_code_readings(
                REPO / "mohu_zrm.chars.dict.yaml", decode=unzrmify1, text_lengths={1}
            ),
        ),
        "mohu_flypy": (
            load_code_readings(
                REPO / "mohu_flypy.base.dict.yaml",
                decode=lambda code: FLYPY_PRONUNCIATIONS.get(code, ("",))[0],
                text_lengths={2},
                code_pronunciations=FLYPY_PRONUNCIATIONS,
            ),
            load_code_readings(
                REPO / "mohu_flypy.chars.dict.yaml",
                decode=lambda code: FLYPY_PRONUNCIATIONS.get(code, ("",))[0],
                text_lengths={1},
                code_pronunciations=FLYPY_PRONUNCIATIONS,
            ),
        ),
    }

    cases: list[dict[str, object]] = []
    dropped: dict[str, int] = defaultdict(int)
    for target_index, entry in enumerate(selected):
        pronunciation = canonical_pronunciations[entry.word]
        competitors = [
            {
                "word": competitor.word,
                "rank": competitor.rank,
                "frequency": competitor.frequency,
            }
            for competitor in groups[pronunciation]
            if competitor.word != entry.word
        ]
        for context_index, context in enumerate(
            contexts[entry.word][:contexts_per_target]
        ):
            encodings = {}
            unavailable_reason = ""
            for scheme, (word_readings, char_readings) in scheme_sources.items():
                encoding = encode_tail_case(
                    entry.word,
                    pronunciation,
                    context.prefix,
                    word_readings,
                    char_readings,
                    SCHEME_MODES[scheme],
                )
                encodings[scheme] = asdict(encoding)
                if not encoding.available:
                    unavailable_reason = f"{scheme}:{encoding.unavailable_reason}"
            if unavailable_reason:
                dropped[unavailable_reason] += 1
                continue
            case_id = f"w{target_index:04d}c{context_index:02d}"
            cases.append(
                {
                    "case_id": case_id,
                    "word": entry.word,
                    "source_rank": entry.rank,
                    "source_frequency": entry.frequency,
                    "pronunciation": list(pronunciation),
                    "homophone_competitors": competitors,
                    "prefix": context.prefix,
                    "sentence": context.sentence,
                    "corpus_source": context.source,
                    "corpus_id": context.corpus_id,
                    "encodings": encodings,
                }
            )

    if not cases:
        raise ValueError("every candidate case was dropped for scheme encoding gaps")

    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    schemes = tuple(scheme_sources)
    write_inputs(root, cases, schemes)
    metadata = {str(case["case_id"]): case for case in cases}
    (root / "meta.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (root / "cases.jsonl").write_text(
        "".join(
            json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n"
            for case in cases
        ),
        encoding="utf-8",
    )

    words_kept = sorted({str(case["word"]) for case in cases})
    target_context_counts: dict[str, int] = defaultdict(int)
    for case in cases:
        target_context_counts[str(case["word"])] += 1
    source_paths = {
        "frequency_list": Path(frequency_path),
        **{f"sentence_corpus_{path.stem}": Path(path) for path in corpus_paths},
        **{f"plain_corpus_{path.name}": Path(path) for path in plain_corpus_paths},
        "training_hashes": Path(training_hashes_path),
        "mohu_zrm_chars": REPO / "mohu_zrm.chars.dict.yaml",
        "mohu_flypy_chars": REPO / "mohu_flypy.chars.dict.yaml",
        "moran_base": Path(moran_base),
        "moran_chars": Path(moran_chars),
        "yeying_dict": Path(yeying_dict),
        "wxpro_words": Path(wxpro_words),
        "wxpro_chars": Path(wxpro_chars),
    }
    manifest: dict[str, object] = {
        "version": 1,
        "first_candidate_rule": "ignore_single_char",
        "selection": {
            "description": (
                "Frequency-ranked unambiguous two-character targets with at least "
                "one other supplied word sharing both complete tone-free pinyin "
                "syllables and at least the requested number of whole-word corpus "
                "occurrences after a real prefix (never at the sentence start), "
                "each keeping exactly that many contexts."
            ),
            "target_limit": target_limit or None,
            "contexts_per_target": contexts_per_target,
            "context_cap_per_target": contexts_per_target,
            "qualified_word_count": len(qualified),
            "target_count": len(words_kept),
            "case_count": len(cases),
            "first_source_rank": selected[0].rank,
            "last_source_rank": selected[-1].rank,
            "context_count_distribution": {
                str(count): sum(value == count for value in target_context_counts.values())
                for count in range(1, contexts_per_target + 1)
            },
            "dropped_cases": dict(sorted(dropped.items())),
        },
        "modes": {scheme: list(SCHEME_MODES[scheme]) for scheme in schemes},
        "suffix_rule_note": SUFFIX_RULE_NOTE,
        "training_exclusion": {
            "criterion": str(audit.get("criterion", "")),
            "training_union_sentences": int(audit.get("training_union_sentences", 0)),
            "mix_files": audit.get("mix_files", []),
            "sentence_pool": {
                "heldout_loaded": len(sentences),
                **duplicate_counts,
                **filter_counts,
                "plain_loaded": len(plain_sentences),
                **plain_counts,
                "kept": len(kept_sentences),
            },
        },
        "schemes": list(schemes),
        "reproduction": [
            "uv run python -m research.lm_sentence_compare.audit_training_overlap "
            "--corpus research/lm_sentence_compare/corpus/testset/frozen_v1.jsonl "
            "--corpus research/lm_sentence_compare/corpus/testset/neutral_v1.jsonl "
            "--corpus research/lm_sentence_compare/corpus/probe/oscar_heldout.jsonl "
            "--corpus research/lm_sentence_compare/corpus/probe/wiki_heldout.jsonl "
            f"--output {root}/train-audit.json --hashes {root}/train-sentence-hashes.npy",
            "uv run python -m research.lm_sentence_compare.build_tail_aux_cases \\",
            f"  --frequency-list {frequency_path} \\",
            f"  --output-root {root} \\",
            f"  --training-hashes {root}/train-sentence-hashes.npy \\",
            f"  --training-audit {root}/train-audit.json \\",
            f"  --contexts-per-target {contexts_per_target}"
            + (f" \\\n  --target-limit {target_limit}" if target_limit else ""),
            "uv run python -m research.lm_sentence_compare.run_cross_candidate \\",
            f"  --root {root} \\",
            "  --model /path/to/mohu-sentence-ngram-v5.bin \\",
            "  --workers 5 --units-per-shard 1200 --max-candidates 5",
            "uv run python -m research.lm_sentence_compare.cross_candidate \\",
            f"  --kua3 {root} \\",
            f"  --json {root}/cross_candidate_report.json \\",
            f"  --markdown {root}/cross_candidate_report.md",
        ],
        "sources": {
            name: {"path": str(path), "sha256": sha256_file(path)}
            for name, path in source_paths.items()
        },
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frequency-list", type=Path, required=True)
    parser.add_argument(
        "--corpus",
        type=Path,
        action="append",
        default=None,
        help="held-out sentence jsonl (repeatable)",
    )
    parser.add_argument("--training-hashes", type=Path, required=True)
    parser.add_argument("--training-audit", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument(
        "--moran-base",
        type=Path,
        default=Path("/Users/fuchuxuan/PycharmProjects/rime-moran/dist/moran.base.dict.yaml"),
    )
    parser.add_argument(
        "--moran-chars",
        type=Path,
        default=Path("/Users/fuchuxuan/PycharmProjects/rime-moran/dist/moran.chars.dict.yaml"),
    )
    parser.add_argument(
        "--yeying-dict",
        type=Path,
        default=Path("/Users/fuchuxuan/Downloads/夜莺rime/yeying.dict.yaml"),
    )
    parser.add_argument(
        "--wxpro-words",
        type=Path,
        default=Path("/tmp/kua-templates/wxpro/dicts/jichu.pro.dict.yaml"),
    )
    parser.add_argument(
        "--wxpro-chars",
        type=Path,
        default=Path("/tmp/kua-templates/wxpro/dicts/zi.pro.dict.yaml"),
    )
    parser.add_argument("--contexts-per-target", type=int, default=20)
    parser.add_argument(
        "--target-limit",
        type=int,
        default=0,
        help="0 keeps every qualifying word",
    )
    parser.add_argument(
        "--plain-corpus",
        type=Path,
        action="append",
        default=None,
        help="one-sentence-per-line corpus file, exact-match excluded against "
        "the training union (repeatable)",
    )
    parser.add_argument("--plain-cap-per-file", type=int, default=30000)
    args = parser.parse_args()
    corpus_paths = args.corpus or [
        HERE / "corpus/testset/frozen_v1.jsonl",
        HERE / "corpus/testset/neutral_v1.jsonl",
        HERE / "corpus/probe/oscar_heldout.jsonl",
        HERE / "corpus/probe/wiki_heldout.jsonl",
    ]
    plain_corpus_paths = args.plain_corpus if args.plain_corpus else sorted(
        path for path in (HERE / "corpus/extracted").glob("*.txt")
        if path.name != "llm_distilled.txt"
    )
    manifest = build_benchmark(
        frequency_path=args.frequency_list,
        corpus_paths=corpus_paths,
        plain_corpus_paths=plain_corpus_paths,
        training_hashes_path=args.training_hashes,
        training_audit_path=args.training_audit,
        output_root=args.output_root,
        moran_base=args.moran_base,
        moran_chars=args.moran_chars,
        yeying_dict=args.yeying_dict,
        wxpro_words=args.wxpro_words,
        wxpro_chars=args.wxpro_chars,
        contexts_per_target=args.contexts_per_target,
        target_limit=args.target_limit,
        plain_cap_per_file=args.plain_cap_per_file,
    )
    print(json.dumps(manifest["selection"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
