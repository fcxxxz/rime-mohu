from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import TextIO

TIGER_EQUIVALENTS = {
    "𖿲": "儿",
    "𖿳": "兒",
}

_CODE_PATTERN = re.compile(r"^[a-z]+$")


def load_tiger_codes(path: Path) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    in_body = False
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw_line.strip()
        if line == "...":
            in_body = True
            continue
        if not in_body or not line or line.startswith("#"):
            continue
        parts = raw_line.split("\t")
        if len(parts) < 2 or len(parts[0].strip()) != 1:
            continue
        char = parts[0].strip()
        code = parts[1].strip()
        if not _CODE_PATTERN.fullmatch(code):
            raise ValueError(f"invalid Tiger code at {path}:{line_number}: {code!r}")
        codes = result.setdefault(char, [])
        if code not in codes:
            codes.append(code)
    return result


def select_longest_codes(codes: Iterable[str]) -> list[str]:
    values = list(dict.fromkeys(codes))
    if not values:
        return []
    longest = max(map(len, values))
    return [code for code in values if len(code) == longest]


def to_prefix2(code: str) -> str:
    if not _CODE_PATTERN.fullmatch(code):
        raise ValueError(f"invalid Tiger code: {code!r}")
    return code[:2]


def build_auxiliary_map(
    path: Path,
    required_chars: Iterable[str] = (),
) -> dict[str, list[str]]:
    tiger_codes = load_tiger_codes(path)
    result: dict[str, list[str]] = {}
    for char, codes in tiger_codes.items():
        prefixes = list(dict.fromkeys(to_prefix2(code) for code in select_longest_codes(codes)))
        if prefixes:
            result[char] = prefixes

    for alias, canonical in TIGER_EQUIVALENTS.items():
        if canonical in result:
            result[alias] = list(result[canonical])

    required = list(dict.fromkeys(required_chars))
    missing = [char for char in required if char not in result]
    if missing:
        raise ValueError(f"missing Tiger codes: {' '.join(missing)}")
    return result


def load_auxiliary_tsv(path: Path) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not raw_line or raw_line.startswith("#"):
            continue
        parts = raw_line.split("\t")
        if len(parts) != 2 or len(parts[0]) != 1:
            raise ValueError(f"invalid auxiliary row at {path}:{line_number}")
        char = parts[0]
        codes = parts[1].split()
        if not codes or any(not _CODE_PATTERN.fullmatch(code) or len(code) > 2 for code in codes):
            raise ValueError(f"invalid auxiliary codes at {path}:{line_number}")
        if char in result:
            raise ValueError(f"duplicate auxiliary character at {path}:{line_number}: {char}")
        result[char] = list(dict.fromkeys(codes))
    return result


def write_auxiliary_tsv(
    mapping: Mapping[str, Sequence[str]],
    output: TextIO,
) -> None:
    for char, codes in mapping.items():
        output.write(f"{char}\t{' '.join(codes)}\n")
