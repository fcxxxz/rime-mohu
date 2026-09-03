#!/usr/bin/env python3
"""Build a deterministic high-frequency homophone cross-candidate benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from pypinyin import Style, pinyin

from tools.flypyify import flypyify1
from tools.zrmify import ALL_PINYIN, unzrmify1, zrmify1

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
MODES = ("pure", "head", "tail", "both")
CJK_RE = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fff]{2}$")
CODE_RE = re.compile(r"^[a-z]{2}$")


@dataclass(frozen=True, slots=True)
class FrequencyEntry:
    word: str
    rank: int
    frequency: int


@dataclass(frozen=True, slots=True)
class Reading:
    pronunciation: tuple[str, ...]
    codes: tuple[str, ...]
    auxiliaries: tuple[str, ...]
    weight: int
    order: int


@dataclass(frozen=True, slots=True)
class Context:
    prefix: str
    sentence: str
    source: str
    corpus_id: str


@dataclass(frozen=True, slots=True)
class SchemeEncoding:
    prefix_code: str
    modes: Mapping[str, str]
    target_source: str
    prefix_fallback_readings: int
    available: bool
    unavailable_reason: str


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_frequency_entries(path: str | Path) -> list[FrequencyEntry]:
    entries: list[FrequencyEntry] = []
    seen: set[str] = set()
    previous: int | None = None
    for rank, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        fields = line.split()
        if len(fields) != 2 or not CJK_RE.fullmatch(fields[0]):
            raise ValueError(f"invalid frequency row {rank}: {line!r}")
        word, raw_frequency = fields
        if word in seen or not raw_frequency.isdigit():
            raise ValueError(f"invalid or duplicate frequency row {rank}: {line!r}")
        frequency = int(raw_frequency)
        if previous is not None and frequency > previous:
            raise ValueError(f"frequency order increases at row {rank}")
        entries.append(FrequencyEntry(word, rank, frequency))
        seen.add(word)
        previous = frequency
    if not entries:
        raise ValueError(f"empty frequency list: {path}")
    return entries


def _weight(value: str) -> int:
    return int(value) if value.strip().lstrip("-").isdigit() else 0


def _rime_rows(path: str | Path) -> Iterable[tuple[str, str, int, int]]:
    started = False
    order = 0
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not started:
            if line.strip() == "...":
                started = True
            continue
        if not line or line.startswith("#") or "\t" not in line:
            continue
        fields = line.split("\t")
        if len(fields) < 2:
            continue
        yield fields[0], fields[1], _weight(fields[2]) if len(fields) > 2 else 0, order
        order += 1


def load_code_readings(
    path: str | Path,
    *,
    decode: Callable[[str], str],
    text_lengths: set[int] = {1, 2},
    code_pronunciations: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, list[Reading]]:
    output: dict[str, list[Reading]] = defaultdict(list)
    for text, raw_code, weight, order in _rime_rows(path):
        if len(text) not in text_lengths:
            continue
        tokens = raw_code.split()
        if len(tokens) != len(text) or any(";" not in token for token in tokens):
            continue
        codes: list[str] = []
        auxiliaries: list[str] = []
        pronunciation: list[str] = []
        try:
            target_pinyin = [
                plain_pinyin(str(item[0]))
                for item in pinyin(text, style=Style.NORMAL, heteronym=False, errors="ignore")
            ]
            for index, token in enumerate(tokens):
                code, auxiliary = token.split(";", 1)
                auxiliary = auxiliary.split(",", 1)[0]
                if not CODE_RE.fullmatch(code) or not auxiliary or not auxiliary.isalpha():
                    raise ValueError
                codes.append(code)
                auxiliaries.append(auxiliary)
                if code_pronunciations is None:
                    pronunciation.append(decode(code))
                else:
                    options = tuple(code_pronunciations.get(code, ()))
                    target = target_pinyin[index] if index < len(target_pinyin) else ""
                    pronunciation.append(target if target in options else (options[0] if options else ""))
            if any(not syllable for syllable in pronunciation):
                raise ValueError
        except (IndexError, ValueError):
            continue
        reading = Reading(
            tuple(pronunciation), tuple(codes), tuple(auxiliaries), weight, order
        )
        if reading not in output[text]:
            output[text].append(reading)
    for readings in output.values():
        readings.sort(key=lambda item: (-item.weight, item.order))
    return dict(output)


def plain_pinyin(value: str) -> str:
    value = value.translate(str.maketrans({"ǖ": "v", "ǘ": "v", "ǚ": "v", "ǜ": "v", "ü": "v"}))
    decomposed = unicodedata.normalize("NFD", value)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return plain.replace("Ü", "v")


def build_code_pronunciations(encode: Callable[[str], str]) -> dict[str, tuple[str, ...]]:
    output: dict[str, list[str]] = defaultdict(list)
    for syllable in dict.fromkeys(ALL_PINYIN):
        try:
            code = encode(syllable)
        except (AssertionError, ValueError):
            continue
        if syllable not in output[code]:
            output[code].append(syllable)
    return {code: tuple(values) for code, values in output.items()}


FLYPY_PRONUNCIATIONS = build_code_pronunciations(flypyify1)


def load_full_pinyin_readings(
    path: str | Path,
    *,
    text_lengths: set[int] = {1, 2},
) -> dict[str, list[Reading]]:
    output: dict[str, list[Reading]] = defaultdict(list)
    for text, raw_code, weight, order in _rime_rows(path):
        if len(text) not in text_lengths:
            continue
        tokens = raw_code.split()
        if len(tokens) != len(text) or any(";" not in token for token in tokens):
            continue
        codes: list[str] = []
        auxiliaries: list[str] = []
        pronunciation: list[str] = []
        try:
            for token in tokens:
                syllable, auxiliary = token.split(";", 1)
                auxiliary = auxiliary.split(",", 1)[0]
                plain = plain_pinyin(syllable)
                code = zrmify1(plain)
                if not CODE_RE.fullmatch(code) or not auxiliary or not auxiliary.isalpha():
                    raise ValueError
                pronunciation.append(plain)
                codes.append(code)
                auxiliaries.append(auxiliary)
        except (AssertionError, IndexError, ValueError):
            continue
        reading = Reading(
            tuple(pronunciation), tuple(codes), tuple(auxiliaries), weight, order
        )
        if reading not in output[text]:
            output[text].append(reading)
    for readings in output.values():
        readings.sort(key=lambda item: (-item.weight, item.order))
    return dict(output)


def merge_reading_maps(*maps: Mapping[str, Sequence[Reading]]) -> dict[str, list[Reading]]:
    merged: dict[str, list[Reading]] = defaultdict(list)
    for mapping in maps:
        for text, readings in mapping.items():
            for reading in readings:
                if reading not in merged[text]:
                    merged[text].append(reading)
    for readings in merged.values():
        readings.sort(key=lambda item: (-item.weight, item.order))
    return dict(merged)


def strict_word_pronunciations(
    words: Iterable[str], readings: Mapping[str, Sequence[Reading]]
) -> dict[str, tuple[str, str]]:
    output: dict[str, tuple[str, str]] = {}
    for word in words:
        pronunciations = {reading.pronunciation for reading in readings.get(word, ())}
        if len(pronunciations) == 1:
            pronunciation = next(iter(pronunciations))
            if len(pronunciation) == 2:
                output[word] = (pronunciation[0], pronunciation[1])
    return output


def build_homophone_groups(
    frequency_entries: Sequence[FrequencyEntry],
    pronunciations: Mapping[str, tuple[str, str]],
) -> dict[tuple[str, str], list[FrequencyEntry]]:
    groups: dict[tuple[str, str], list[FrequencyEntry]] = defaultdict(list)
    for entry in frequency_entries:
        if entry.word in pronunciations:
            groups[pronunciations[entry.word]].append(entry)
    return {
        pronunciation: entries
        for pronunciation, entries in groups.items()
        if len(entries) >= 2
    }


def load_contexts(
    path: str | Path,
    eligible_words: set[str],
) -> dict[str, list[Context]]:
    contexts: dict[str, list[Context]] = defaultdict(list)
    seen: dict[str, set[str]] = defaultdict(set)
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            text = str(row["text"])
            words = str(row["words"]).split("|")
            if "" in words or "".join(words) != text:
                raise ValueError(f"invalid segmentation at {path}:{line_number}")
            offset = 0
            for word in words:
                if word in eligible_words and offset > 0:
                    prefix = text[:offset]
                    if prefix not in seen[word]:
                        contexts[word].append(
                            Context(
                                prefix,
                                text,
                                str(row.get("source", "")),
                                str(row.get("id", "")),
                            )
                        )
                        seen[word].add(prefix)
                offset += len(word)
    return dict(contexts)


def select_targets(
    frequency_entries: Sequence[FrequencyEntry],
    pronunciations: Mapping[str, tuple[str, str]],
    homophone_groups: Mapping[tuple[str, str], Sequence[FrequencyEntry]],
    contexts: Mapping[str, Sequence[Context]],
    *,
    target_limit: int,
) -> list[FrequencyEntry]:
    selected = [
        entry
        for entry in frequency_entries
        if entry.word in contexts
        and pronunciations.get(entry.word) in homophone_groups
    ]
    if len(selected) < target_limit:
        raise ValueError(
            f"only {len(selected)} eligible targets; requested {target_limit}"
        )
    return selected[:target_limit]


def pick_target_reading(
    word: str,
    pronunciation: tuple[str, str],
    word_readings: Mapping[str, Sequence[Reading]],
    char_readings: Mapping[str, Sequence[Reading]],
) -> tuple[Reading | None, str]:
    direct = [
        reading
        for reading in word_readings.get(word, ())
        if reading.pronunciation == pronunciation
    ]
    if direct:
        return direct[0], "word_dictionary"
    selected: list[Reading] = []
    for char, syllable in zip(word, pronunciation):
        options = [
            reading
            for reading in char_readings.get(char, ())
            if reading.pronunciation == (syllable,)
        ]
        if not options:
            return None, ""
        selected.append(options[0])
    return Reading(
        pronunciation,
        tuple(item.codes[0] for item in selected),
        tuple(item.auxiliaries[0] for item in selected),
        sum(item.weight for item in selected),
        min(item.order for item in selected),
    ), "character_fallback"


def encode_prefix(
    text: str,
    char_readings: Mapping[str, Sequence[Reading]],
) -> tuple[str, int] | None:
    raw = pinyin(text, style=Style.NORMAL, heteronym=False, errors="ignore")
    if len(raw) != len(text) or any(not item for item in raw):
        return None
    codes: list[str] = []
    fallback_count = 0
    for char, values in zip(text, raw):
        options = list(char_readings.get(char, ()))
        if not options:
            return None
        target = plain_pinyin(str(values[0]))
        chosen = next(
            (reading for reading in options if reading.pronunciation == (target,)),
            None,
        )
        if chosen is None:
            chosen = options[0]
            fallback_count += 1
        codes.append(chosen.codes[0])
    return "".join(codes), fallback_count


def four_modes(reading: Reading) -> dict[str, str]:
    if len(reading.codes) != 2 or any(not aux for aux in reading.auxiliaries):
        raise ValueError("four modes require two codes and two auxiliary codes")
    first, second = reading.codes
    head_aux, tail_aux = reading.auxiliaries[0][0], reading.auxiliaries[1][0]
    return {
        "pure": first + second,
        "head": first + head_aux + second,
        "tail": first + second + tail_aux,
        "both": first + head_aux + second + tail_aux,
    }


def encode_case(
    word: str,
    pronunciation: tuple[str, str],
    prefix: str,
    word_readings: Mapping[str, Sequence[Reading]],
    char_readings: Mapping[str, Sequence[Reading]],
) -> SchemeEncoding:
    target, source = pick_target_reading(
        word, pronunciation, word_readings, char_readings
    )
    if target is None:
        return SchemeEncoding("", {mode: "" for mode in MODES}, "", 0, False, "target_reading")
    prefix_result = encode_prefix(prefix, char_readings)
    if prefix_result is None:
        return SchemeEncoding(
            "", four_modes(target), source, 0, False, "prefix_reading"
        )
    prefix_code, fallback = prefix_result
    return SchemeEncoding(prefix_code, four_modes(target), source, fallback, True, "")


def _hex(value: str) -> str:
    return value.encode("utf-8").hex()


def write_inputs(
    output_root: str | Path,
    cases: Sequence[Mapping[str, object]],
    schemes: Sequence[str],
) -> None:
    root = Path(output_root)
    input_dir = root / "in"
    input_dir.mkdir(parents=True, exist_ok=True)
    for scheme in schemes:
        fresh_rows: list[str] = []
        after_rows: list[str] = []
        for case in cases:
            encoding = case["encodings"][scheme]
            modes = encoding["modes"]
            b_row = "\t".join(
                [
                    "B",
                    str(case["case_id"]),
                    *(str(modes[mode]) for mode in MODES),
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
    frequency_path: str | Path,
    cases_path: str | Path,
    output_root: str | Path,
    moran_base: str | Path,
    moran_chars: str | Path,
    yeying_dict: str | Path,
    wxpro_words: str | Path,
    wxpro_chars: str | Path,
    target_limit: int = 1000,
    context_cap: int = 4,
) -> dict[str, object]:
    frequency_entries = load_frequency_entries(frequency_path)
    frequency_by_word = {entry.word: entry for entry in frequency_entries}

    canonical = load_code_readings(
        REPO / "mohu_zrm.base.dict.yaml", decode=unzrmify1, text_lengths={2}
    )
    canonical_pronunciations = strict_word_pronunciations(
        frequency_by_word, canonical
    )
    groups = build_homophone_groups(frequency_entries, canonical_pronunciations)
    eligible_words = {
        entry.word for entries in groups.values() for entry in entries
    }
    contexts = load_contexts(cases_path, eligible_words)
    selected = select_targets(
        frequency_entries,
        canonical_pronunciations,
        groups,
        contexts,
        target_limit=target_limit,
    )

    scheme_sources: dict[str, tuple[dict[str, list[Reading]], dict[str, list[Reading]]]] = {
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
        for context_index, context in enumerate(contexts[entry.word][:context_cap]):
            case_id = f"w{target_index:04d}c{context_index}"
            encodings = {
                scheme: asdict(
                    encode_case(
                        entry.word,
                        pronunciation,
                        context.prefix,
                        word_readings,
                        char_readings,
                    )
                )
                for scheme, (word_readings, char_readings) in scheme_sources.items()
            }
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

    source_paths = {
        "frequency_list": Path(frequency_path),
        "sentence_cases": Path(cases_path),
        "mohu_zrm_base": REPO / "mohu_zrm.base.dict.yaml",
        "mohu_zrm_chars": REPO / "mohu_zrm.chars.dict.yaml",
        "mohu_flypy_base": REPO / "mohu_flypy.base.dict.yaml",
        "mohu_flypy_chars": REPO / "mohu_flypy.chars.dict.yaml",
        "moran_base": Path(moran_base),
        "moran_chars": Path(moran_chars),
        "yeying_dict": Path(yeying_dict),
        "wxpro_words": Path(wxpro_words),
        "wxpro_chars": Path(wxpro_chars),
    }
    unavailable = {
        scheme: sum(
            not bool(case["encodings"][scheme]["available"])
            for case in cases
        )
        for scheme in schemes
    }
    target_context_counts = defaultdict(int)
    for case in cases:
        target_context_counts[str(case["word"])] += 1
    manifest: dict[str, object] = {
        "version": 1,
        "selection": {
            "description": (
                "First frequency-ranked unambiguous two-character targets with at least "
                "one other supplied word sharing both complete tone-free pinyin syllables "
                "and at least one whole-word corpus occurrence after a real prefix."
            ),
            "target_limit": target_limit,
            "context_cap_per_target": context_cap,
            "target_count": len(selected),
            "case_count": len(cases),
            "first_source_rank": selected[0].rank,
            "last_source_rank": selected[-1].rank,
            "context_count_distribution": {
                str(count): sum(value == count for value in target_context_counts.values())
                for count in range(1, context_cap + 1)
            },
        },
        "schemes": list(schemes),
        "unavailable_cases": unavailable,
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
    parser.add_argument("--cases", type=Path, default=HERE / "cases.jsonl")
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
    parser.add_argument("--target-limit", type=int, default=1000)
    parser.add_argument("--context-cap", type=int, default=4)
    args = parser.parse_args()
    manifest = build_benchmark(
        frequency_path=args.frequency_list,
        cases_path=args.cases,
        output_root=args.output_root,
        moran_base=args.moran_base,
        moran_chars=args.moran_chars,
        yeying_dict=args.yeying_dict,
        wxpro_words=args.wxpro_words,
        wxpro_chars=args.wxpro_chars,
        target_limit=args.target_limit,
        context_cap=args.context_cap,
    )
    print(json.dumps(manifest["selection"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
