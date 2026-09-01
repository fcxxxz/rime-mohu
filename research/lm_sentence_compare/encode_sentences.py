#!/usr/bin/env python3
"""Encode the fixed corpus as Mohu natural-code sentence input streams.

The four modes intentionally mirror the current scheme's YY/YYX algebra:
plain double-pinyin, sparse word-leading auxiliary keys, one auxiliary key per
word, and one auxiliary key per character.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable, Mapping, Sequence

# A direct ``python path/to/encode_sentences.py`` invocation sets sys.path to
# the script directory rather than the repository root.
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pypinyin import Style, pinyin

from tools import zrmify

HERE = Path(__file__).resolve().parent
CORPUS = HERE / "corpus"
MODES = ("pure", "sparse", "word1", "char1")

_CJK_RE = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fff]+$")


def load_rime_dict(path: str | Path) -> list[tuple[str, str, str]]:
    entries: list[tuple[str, str, str]] = []
    started = False
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not started:
            if line.strip() == "...":
                started = True
            continue
        if not line or line.startswith("#") or "\t" not in line:
            continue
        parts = line.split("\t")
        if len(parts) < 2 or not parts[0]:
            continue
        entries.append((parts[0], parts[1], parts[2] if len(parts) > 2 else ""))
    return entries


def load_charset(path: str | Path = REPO / "mohu_charset.dict.yaml") -> set[str]:
    return {
        text
        for text, _code, _weight in load_rime_dict(path)
        if len(text) == 1 and _CJK_RE.fullmatch(text)
    }


def load_char_readings(path: str | Path = REPO / "mohu_zrm.chars.dict.yaml") -> dict[str, list[dict[str, object]]]:
    """Return char -> stable, weight-ordered single-syllable YY/aux rows."""

    readings: dict[str, list[dict[str, object]]] = {}
    for text, code, weight in load_rime_dict(path):
        if len(text) != 1 or " " in code or ";" not in code:
            continue
        yy, aux = code.split(";", 1)
        if len(yy) != 2 or not aux or not aux.isalpha():
            continue
        numeric_weight = int(weight) if weight.strip().lstrip("-").isdigit() else 0
        rows = readings.setdefault(text, [])
        if not any(row["yy"] == yy for row in rows):
            rows.append({"yy": yy, "aux": aux, "weight": numeric_weight})
    for rows in readings.values():
        rows.sort(key=lambda row: (-int(row["weight"]), str(row["yy"]), str(row["aux"])))
    return readings


def load_word_vocabulary(paths: Sequence[str | Path] = (
    REPO / "mohu_zrm.words.dict.yaml",
    REPO / "mohu_zrm_fixed.dict.yaml",
)) -> set[str]:
    vocabulary: set[str] = set()
    for path in paths:
        for text, _code, _weight in load_rime_dict(path):
            if len(text) >= 2 and _CJK_RE.fullmatch(text):
                vocabulary.add(text)
    return vocabulary


def segment_max_match(text: str, vocabulary: Iterable[str], *, max_len: int = 10) -> list[str]:
    """Deterministic longest-forward matching with one-character fallback."""

    words = set(vocabulary)
    result: list[str] = []
    index = 0
    while index < len(text):
        word = text[index]
        upper = min(max_len, len(text) - index)
        # Checking at most ``max_len`` slices avoids scanning the full phrase
        # vocabulary for every character (the corpus has 20k rows).
        for length in range(upper, 1, -1):
            candidate = text[index : index + length]
            if candidate in words:
                word = candidate
                break
        result.append(word)
        index += len(word)
    return result


def _normalize_pinyin(value: str) -> str:
    return value.replace("ü", "v").replace("ǖ", "v").replace("ǘ", "v").replace("ǚ", "v").replace("ǜ", "v")


def _pick_readings(text: str, readings: Mapping[str, Sequence[Mapping[str, object]]]) -> tuple[list[tuple[str, str]], int] | None:
    # pypinyin performs phrase-level disambiguation when fed the entire text.
    raw = pinyin(text, style=Style.NORMAL, heteronym=False, errors="ignore")
    if len(raw) != len(text) or any(not item for item in raw):
        return None
    result: list[tuple[str, str]] = []
    fallback = 0
    for char, values in zip(text, raw):
        options = list(readings.get(char, ()))
        if not options:
            return None
        py = _normalize_pinyin(str(values[0]))
        try:
            target_yy = zrmify.zrmify1(py)
        except (AssertionError, ValueError):
            target_yy = ""
        chosen = next((row for row in options if str(row["yy"]) == target_yy), None)
        if chosen is None:
            chosen = options[0]
            fallback += 1
        result.append((str(chosen["yy"]), str(chosen["aux"])))
    return result, fallback


def encode_text(
    text: str,
    readings: Mapping[str, Sequence[Mapping[str, object]]],
    charset: set[str],
    vocabulary: Iterable[str],
) -> dict[str, object] | None:
    if not text or any(char not in charset for char in text):
        return None
    picked = _pick_readings(text, readings)
    if picked is None:
        return None
    syllables, fallback = picked
    words = segment_max_match(text, vocabulary)
    word_starts: list[int] = []
    cursor = 0
    for word in words:
        word_starts.append(cursor)
        cursor += len(word)
    positions = {
        "pure": set(),
        "sparse": {index for word_index, index in enumerate(word_starts) if word_index % 4 == 0},
        "word1": set(word_starts),
        "char1": set(range(len(syllables))),
    }
    modes = {
        mode: "".join(yy + (aux[:1] if index in positions[mode] else "") for index, (yy, aux) in enumerate(syllables))
        for mode in MODES
    }
    aux_counts = {mode: sum(index in positions[mode] for index in range(len(syllables))) for mode in MODES}
    return {
        "text": text,
        "words": words,
        "modes": modes,
        "aux_counts": aux_counts,
        "aux_ratios": {mode: aux_counts[mode] / len(syllables) for mode in MODES},
        "fallback_readings": fallback,
    }


def encode_cases(src: str | Path, dst: str | Path, *, repo: Path = REPO) -> dict[str, int]:
    readings = load_char_readings(repo / "mohu_zrm.chars.dict.yaml")
    # The runner explicitly enables ``extended_charset`` so the benchmark
    # does not discard valid news/daily characters merely because they are
    # outside the 8105 common-character filter.  Keep ``load_charset`` public
    # for callers that want the common-set audit.
    charset = set(readings)
    vocabulary = load_word_vocabulary((repo / "mohu_zrm.words.dict.yaml", repo / "mohu_zrm_fixed.dict.yaml"))
    stats: Counter[str] = Counter()
    destination = Path(dst)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Path(src).open(encoding="utf-8") as inp, destination.open("w", encoding="utf-8", newline="\n") as out:
        for line in inp:
            if not line.strip():
                continue
            stats["input"] += 1
            row = json.loads(line)
            encoded = encode_text(str(row["text"]), readings, charset, vocabulary)
            if encoded is None:
                stats["dropped"] += 1
                continue
            output = {**row, **encoded}
            out.write(json.dumps(output, ensure_ascii=False, sort_keys=True) + "\n")
            stats["encoded"] += 1
            stats["fallback_reading_chars"] += int(encoded["fallback_readings"])
    return dict(stats)


def main() -> None:
    parser = argparse.ArgumentParser(description="Encode benchmark sentences")
    parser.add_argument("--src", type=Path, default=CORPUS / "sentences.jsonl")
    parser.add_argument("--dst", type=Path, default=HERE / "cases.jsonl")
    args = parser.parse_args()
    stats = encode_cases(args.src, args.dst)
    print(json.dumps(stats, ensure_ascii=False, sort_keys=True), file=sys.stderr)


if __name__ == "__main__":
    main()
