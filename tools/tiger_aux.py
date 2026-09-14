from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import NamedTuple, TextIO

TIGER_EQUIVALENTS = {
    "𖿲": "儿",
    "𖿳": "兒",
}

_CODE_PATTERN = re.compile(r"^[a-z]+$")
_CHAIFEN_PATTERN = re.compile(r"^(\S)\t〔(.+?)&nbsp;·&nbsp;[a-z]+〕")


class AuxiliaryEntry(NamedTuple):
    """Per-character auxiliary codes derived from the first longest Tiger code.

    ``normal`` is the primary auxiliary (positions 1-2). ``compat14`` and
    ``compat13`` are lower-priority compatibility plays built from positions
    1-4 (preferred) and 1-3; they may be empty.

    Compat positions only keep 大码 letters: a Tiger full code ends with the
    last root's 小码 when the character has at most three roots (1-2 root
    codes pad to 大大+小, 3-root codes are 大大大+小; 4+ root codes are all
    大码). With a known root count, ``compat14`` is dropped for ≤3-root
    characters and ``compat13`` for ≤2-root characters. Characters without
    decomposition data (extension-block rarities) keep the legacy derivation.
    """

    normal: str
    compat14: str = ""
    compat13: str = ""

    def codes(self) -> list[str]:
        """All playable codes: normal first, then 14 before 13."""
        result = [self.normal]
        for code in (self.compat14, self.compat13):
            if code and code not in result:
                result.append(code)
        return result

    def compat_codes(self) -> list[str]:
        """Compatibility plays only, 14 before 13."""
        return [code for code in (self.compat14, self.compat13) if code]


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


def select_primary_code(codes: Iterable[str]) -> str | None:
    """Return the first four-letter code, else the first of the longest codes.

    Codes arrive in source order (highest weight first), so this follows the
    Tiger table's own priority instead of mixing mirror codes.
    """
    values = list(dict.fromkeys(codes))
    for code in values:
        if len(code) == 4:
            return code
    if not values:
        return None
    longest = max(map(len, values))
    return next(code for code in values if len(code) == longest)


def to_auxiliary_entry(code: str, root_count: int | None = None) -> AuxiliaryEntry:
    if not _CODE_PATTERN.fullmatch(code):
        raise ValueError(f"invalid Tiger code: {code!r}")
    normal = code[:2]
    compat14 = code[0] + code[3] if len(code) >= 4 else ""
    compat13 = code[0] + code[2] if len(code) >= 3 else ""
    if root_count is not None:
        # 辅码兼容位只用大码：≤3 根的全码末位是末根小码（无 14 位），
        # ≤2 根的第 3 位已是末根小码（无 13 位）。
        if root_count <= 3:
            compat14 = ""
        if root_count <= 2:
            compat13 = ""
    if compat14 == normal:
        compat14 = ""
    if compat13 == normal or compat13 == compat14:
        compat13 = ""
    return AuxiliaryEntry(normal, compat14, compat13)


def load_root_counts(path: Path) -> dict[str, int]:
    """Read per-character root counts from tools/data/tiger_chaifen.txt."""
    result: dict[str, int] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        match = _CHAIFEN_PATTERN.match(raw_line)
        if match:
            result[match.group(1)] = len(match.group(2))
    return result


def build_auxiliary_map(
    path: Path,
    required_chars: Iterable[str] = (),
    root_counts: Mapping[str, int] | None = None,
) -> dict[str, AuxiliaryEntry]:
    tiger_codes = load_tiger_codes(path)
    result: dict[str, AuxiliaryEntry] = {}
    for char, codes in tiger_codes.items():
        primary = select_primary_code(codes)
        if primary:
            count = root_counts.get(char) if root_counts is not None else None
            result[char] = to_auxiliary_entry(primary, count)

    for alias, canonical in TIGER_EQUIVALENTS.items():
        if canonical in result:
            result[alias] = result[canonical]

    required = list(dict.fromkeys(required_chars))
    missing = [char for char in required if char not in result]
    if missing:
        raise ValueError(f"missing Tiger codes: {' '.join(missing)}")
    return result


def load_auxiliary_tsv(path: Path) -> dict[str, AuxiliaryEntry]:
    result: dict[str, AuxiliaryEntry] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not raw_line or raw_line.startswith("#"):
            continue
        parts = raw_line.split("\t")
        if len(parts) < 2 or len(parts) > 4 or len(parts[0]) != 1:
            raise ValueError(f"invalid auxiliary row at {path}:{line_number}")
        char = parts[0]
        columns = [parts[1], parts[2] if len(parts) > 2 else "", parts[3] if len(parts) > 3 else ""]
        if not columns[0] or not all(
            _CODE_PATTERN.fullmatch(code) and len(code) <= 2
            for code in columns
            if code
        ):
            raise ValueError(f"invalid auxiliary codes at {path}:{line_number}")
        if char in result:
            raise ValueError(f"duplicate auxiliary character at {path}:{line_number}: {char}")
        result[char] = AuxiliaryEntry(columns[0], columns[1], columns[2])
    return result


def write_auxiliary_tsv(
    mapping: Mapping[str, AuxiliaryEntry],
    output: TextIO,
) -> None:
    for char, entry in mapping.items():
        fields = [char, entry.normal, entry.compat14, entry.compat13]
        while len(fields) > 2 and not fields[-1]:
            fields.pop()
        output.write("\t".join(fields) + "\n")
