#!/usr/bin/env python3
"""Build the natural-code and Flypy native sentence lexicons.

The checked-in Tiger lexicon is the source of sentence/text coverage.  This
tool keeps that coverage identical for both schemes, converting only the
syllable portion that can be identified from the character dictionary.  The
three Mohu fly-key substitutions are then closed transitively.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import flypyify
import zrmify

ROOT = TOOLS.parent
FLY = {"wz": "wk", "xq": "xo", "qx": "qo"}
ROW_KEY = tuple[str, str, str, str]


def load_rows(path: Path) -> list[ROW_KEY]:
    rows: list[ROW_KEY] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) < 2 or not fields[0] or not fields[1]:
            raise ValueError(f"invalid lexicon row at {path}:{line_no}")
        rank = fields[2] if len(fields) > 2 and fields[2] else "1"
        freq = fields[3] if len(fields) > 3 and fields[3] else "20001"
        try:
            int(rank)
            int(freq)
        except ValueError as exc:
            raise ValueError(f"invalid rank/freq at {path}:{line_no}") from exc
        rows.append((fields[0], fields[1], rank, freq))
    return rows


def load_character_syllables(path: Path) -> dict[str, set[str]]:
    """Read natural-code syllables from a Rime character dictionary."""
    result: dict[str, set[str]] = {}
    in_body = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() == "...":
            in_body = True
            continue
        if not in_body or not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) < 2 or not fields[0]:
            continue
        syllables = result.setdefault(fields[0], set())
        for token in fields[1].split():
            code = token.split(";", 1)[0]
            candidate = code[:2]
            if _is_natural_syllable(candidate):
                syllables.add(candidate)
        if not syllables:
            result.pop(fields[0], None)
    return result


def _is_natural_syllable(code: str) -> bool:
    """Accept reversible two-letter Natural Code spellings, excluding auxiliaries."""
    if code == "pp" or not re.fullmatch(r"[a-z]{2}", code):
        return False
    try:
        pinyin = zrmify.unzrmify1(code)
        return zrmify.zrmify1(pinyin) == code
    except (AssertionError, IndexError, ValueError, TypeError):
        return False


def _convert_syllable(code: str) -> str:
    return flypyify.flypyify1(zrmify.unzrmify1(code))


def _canonical_syllables(code: str, text: str,
                         character_syllables: dict[str, set[str]] | None) -> tuple[str, ...] | None:
    if not text or len(code) < 2 * len(text):
        return None
    base = code[: 2 * len(text)]
    if not re.fullmatch(r"[a-z]+", base):
        return None
    syllables = tuple(base[i:i + 2] for i in range(0, len(base), 2))
    if character_syllables is None:
        return syllables
    if len(text) == 1:
        allowed = character_syllables.get(text)
        return syllables if allowed and syllables[0] in allowed else None
    for char, syllable in zip(text, syllables):
        allowed = character_syllables.get(char)
        if not allowed or syllable not in allowed:
            return None
    return syllables


def _convert_code(code: str, text: str,
                 character_syllables: dict[str, set[str]] | None) -> str:
    syllables = _canonical_syllables(code, text, character_syllables)
    if syllables is None:
        return code
    converted = "".join(_convert_syllable(syllable) for syllable in syllables)
    return converted + code[2 * len(text):]


def _fly_closure(code: str, text: str) -> set[str]:
    """Return all code variants from the three substitutions."""
    if not text or len(code) < 2 * len(text):
        return set()
    base = code[: 2 * len(text)]
    if not re.fullmatch(r"[a-z]+", base):
        return set()
    syllables = tuple(base[i:i + 2] for i in range(0, len(base), 2))
    seen = {syllables}
    pending = [syllables]
    while pending:
        current = pending.pop()
        for source, target in FLY.items():
            if source not in current:
                continue
            variant = tuple(target if item == source else item for item in current)
            if variant not in seen:
                seen.add(variant)
                pending.append(variant)
    suffix = code[2 * len(text):]
    return {"".join(item) + suffix for item in seen if item != syllables}


def build_rows(rows: list[ROW_KEY], scheme: str,
               character_syllables: dict[str, set[str]] | None = None) -> list[ROW_KEY]:
    if scheme not in {"zrm", "flypy"}:
        raise ValueError(f"unsupported scheme: {scheme}")
    output: set[ROW_KEY] = set()
    for code, text, rank, freq in rows:
        base_code = code if scheme == "zrm" else _convert_code(code, text, character_syllables)
        output.add((base_code, text, rank, freq))
        for variant in _fly_closure(base_code, text):
            output.add((variant, text, rank, freq))
    return sorted(output, key=lambda row: (row[0], int(row[2]), row[1], int(row[3])))


def validate_output_path(path: Path, root: Path = ROOT) -> None:
    resolved = path.resolve()
    if resolved.is_absolute() and root.resolve() not in resolved.parents:
        raise ValueError(f"output path must be inside repository: {path}")


def write_rows(path: Path, rows: list[ROW_KEY], root: Path = ROOT) -> None:
    validate_output_path(path, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "# code\ttext\trank\tfreq_rank\n" + "\n".join("\t".join(row) for row in rows) + "\n"
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path,
                        default=ROOT / "tiger_sentence_native/mohu_tiger.lexicon.txt")
    parser.add_argument("--chars-dict", type=Path, default=ROOT / "mohu_zrm.chars.dict.yaml")
    parser.add_argument("--zrm-output", type=Path,
                        default=ROOT / "tiger_sentence_native/data/zrm/mohu_llm_zrm.lexicon.txt")
    parser.add_argument("--flypy-output", type=Path,
                        default=ROOT / "tiger_sentence_native/data/flypy/mohu_llm_flypy.lexicon.txt")
    args = parser.parse_args()
    rows = load_rows(args.source)
    syllables = load_character_syllables(args.chars_dict)
    zrm_rows = build_rows(rows, "zrm", syllables)
    fly_rows = build_rows(rows, "flypy", syllables)
    write_rows(args.zrm_output, zrm_rows)
    write_rows(args.flypy_output, fly_rows)
    print(f"source rows: {len(rows)}; zrm rows: {len(zrm_rows)}; flypy rows: {len(fly_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
