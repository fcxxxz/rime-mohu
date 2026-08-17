import re
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Literal

from tools.flypyify import flypyify1
from tools.zrmify import zrmify

DoublePinyin = Literal["zrm", "flypy"]
EntrySource = Literal["shape", "original", "pinyin_fallback"]

SELECTION_KEYS = "_;'456789"


@dataclass(frozen=True)
class SourceEntry:
    text: str
    code: str
    weight: float
    source: EntrySource = "shape"


@dataclass(frozen=True)
class TableEntry:
    text: str
    code: str
    weight: float
    order: int
    selection_rank: int = 1
    source: EntrySource = "shape"


def load_original_character_entries(
    paths: Iterable[Path],
    chars: Iterable[str] | None = None,
) -> list[SourceEntry]:
    allowed = set(chars) if chars is not None else None
    result: list[SourceEntry] = []
    seen: set[tuple[str, str]] = set()
    for path in paths:
        for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
            parts = raw_line.split("\t")
            if len(parts) < 2:
                continue
            if parts[0].isdigit() and len(parts) >= 3:
                text, code = parts[1].strip(), parts[2].strip().lower()
            else:
                text, code = parts[0].strip(), parts[1].strip().lower()
            value = (text, code)
            if (
                len(text) != 1
                or (allowed is not None and text not in allowed)
                or re.fullmatch(r"[a-z]+", code) is None
                or value in seen
            ):
                continue
            seen.add(value)
            result.append(SourceEntry(text, code, 0.0, "original"))
    return result


def build_source_entries(
    chars: Iterable[str],
    pinyin_table: dict[str, list[tuple[str, float]]],
    auxiliary_codes: dict[str, list[str]],
    *,
    double_pinyin: DoublePinyin = "zrm",
) -> list[SourceEntry]:
    result: list[SourceEntry] = []
    seen: set[tuple[str, str]] = set()
    for char in chars:
        for pinyin, weight in pinyin_table.get(char, []):
            phonetic_code = _to_double_pinyin(pinyin, double_pinyin)
            for auxiliary in auxiliary_codes.get(char, []):
                value = (char, phonetic_code + auxiliary)
                if value in seen:
                    continue
                seen.add(value)
                result.append(SourceEntry(char, value[1], weight))
    return result


def rank_candidates(entries: Iterable[TableEntry]) -> list[TableEntry]:
    rows = list(entries)
    by_code: dict[str, list[TableEntry]] = defaultdict(list)
    for entry in rows:
        by_code[entry.code].append(entry)
    ranked: dict[int, int] = {}
    for code_rows in by_code.values():
        for rank, entry in enumerate(sorted(code_rows, key=_candidate_rank_key), 1):
            ranked[entry.order] = rank
    return [replace(entry, selection_rank=ranked[entry.order]) for entry in rows]


def select_records(
    entries: Iterable[TableEntry],
    chars: Iterable[str],
    frequency_weights: dict[str, float],
) -> list[dict[str, str | float | int]]:
    by_text: dict[str, list[TableEntry]] = defaultdict(list)
    for entry in entries:
        by_text[entry.text].append(entry)
    records: list[dict[str, str | float | int]] = []
    for rank, char in enumerate(chars, 1):
        char_entries = by_text.get(char, [])
        if not char_entries:
            continue
        shape_entries = [entry for entry in char_entries if entry.source != "pinyin_fallback"]
        preferred_entries = shape_entries or char_entries
        short = min(preferred_entries, key=_short_entry_key)
        full = min(
            preferred_entries,
            key=lambda row: (
                -len(row.code),
                _source_tier(row),
                row.selection_rank,
                -row.weight,
                row.order,
            ),
        )
        typing_code = short.code
        if short.selection_rank > 1:
            typing_code += _selection_key(short.selection_rank)
        records.append(
            {
                "rank": rank,
                "char": char,
                "frequency_weight": float(frequency_weights.get(char, 0.0)),
                "short_code": short.code,
                "typing_code": typing_code,
                "full_code": full.code,
                "selection_rank": short.selection_rank,
                "source": short.source,
            }
        )
    return records


def _to_double_pinyin(pinyin: str, schema: DoublePinyin) -> str:
    if schema == "zrm":
        return zrmify(pinyin)
    if schema == "flypy":
        return flypyify1(pinyin)
    raise ValueError(f"unknown double-pinyin schema: {schema}")


def _candidate_rank_key(row: TableEntry) -> tuple[int, float, int]:
    if row.source == "original":
        return (0, 0.0, row.order)
    if row.source == "shape":
        return (1, -row.weight, row.order)
    return (2, -row.weight, row.order)


def _source_tier(entry: TableEntry) -> int:
    if entry.source == "original":
        return 0
    if entry.source == "shape":
        return 1
    return 2


def _short_entry_key(entry: TableEntry) -> tuple[int, int, bool, int, int, float, int]:
    typing_length = len(entry.code) + (1 if entry.selection_rank > 1 else 0)
    return (
        typing_length,
        _source_tier(entry),
        entry.selection_rank > 1,
        entry.selection_rank,
        len(entry.code),
        -entry.weight,
        entry.order,
    )


def _selection_key(rank: int) -> str:
    index = max(0, rank - 1)
    return SELECTION_KEYS[index] if index < len(SELECTION_KEYS) else str(rank)
