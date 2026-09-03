#!/usr/bin/env python3
"""Generate the two-character word rank asset for four-code char-vs-word ordering.

The authority list (e.g. 二字词表2.0.txt, format ``word<TAB>count``) provides the
base ordering. Mohu's own ``mohu_zrm.base.dict.yaml`` word weights are used only
to fill gaps: words absent from the authority list but whose base weight maps to
an equivalent rank within ``--add-limit`` are inserted with the floor of that
equivalent rank. Authority words keep their original line ranks untouched, so
gap filling can only enable new words and never re-judges existing ones; Mohu
corpus quirks never veto or reorder external judgments, and frequency counts in
the authority file are ignored apart from their implied line order.

Variant forms listed on the right side of ``--variants`` are excluded from
competition entirely (e.g. 其它/惟一/报导 keep only 其他/唯一/报道 competing).
Words owning shortcut codes in the fixed dictionaries still compete.

Output lines are ``word<TAB>rank`` where rank mirrors lua/tiger_rank.txt for
runtime loaders; ranks are not a dense sequence because additions share the
integer floor of their equivalent rank.
"""
from __future__ import annotations

import argparse
import bisect
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def is_two_char_cjk(text: str) -> bool:
    def is_cjk(char: str) -> bool:
        code = ord(char)
        return (
            0x3400 <= code <= 0x4DBF
            or 0x4E00 <= code <= 0x9FFF
            or 0x20000 <= code <= 0x3347F
        )

    return len(text) == 2 and all(is_cjk(char) for char in text)


def load_authority(path: Path) -> list[tuple[str, int]]:
    rows: list[tuple[str, int]] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for line_number, raw in enumerate(handle, 1):
            parts = raw.rstrip("\r\n").split("\t")
            if len(parts) != 2 or not parts[1].isdigit():
                raise ValueError(f"invalid authority row {line_number}: {raw!r}")
            word = parts[0]
            if not is_two_char_cjk(word):
                raise ValueError(f"non two-char CJK word at row {line_number}: {word!r}")
            if word in seen:
                raise ValueError(f"duplicate authority word at row {line_number}: {word}")
            seen.add(word)
            rows.append((word, int(parts[1])))
    if not rows:
        raise ValueError("authority list is empty")
    for previous, current in zip(rows, rows[1:]):
        if current[1] > previous[1]:
            raise ValueError(f"authority frequency increases at word {current[0]}")
    return rows


def load_base_weights(path: Path) -> dict[str, int]:
    weights: dict[str, int] = defaultdict(int)
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            parts = raw.rstrip("\r\n").split("\t")
            if len(parts) < 3 or not is_two_char_cjk(parts[0]):
                continue
            try:
                weight = int(parts[2])
            except ValueError:
                continue
            weights[parts[0]] = max(weights[parts[0]], weight)
    return dict(weights)


def build(authority: list[tuple[str, int]], base: dict[str, int], add_limit: int):
    authority_rank = {word: rank for rank, (word, _) in enumerate(authority, 1)}
    reference = sorted(
        (base.get(word, 0) for word, _ in authority), reverse=True
    )
    negative = [-weight for weight in reference]

    def equivalent(weight: int) -> float | None:
        if weight <= 0:
            return None
        left = bisect.bisect_left(negative, -weight)
        right = bisect.bisect_right(negative, -weight)
        return (left + 1 + right) / 2

    rows = [(rank, 0.0, 0, word) for rank, (word, _) in enumerate(authority, 1)]
    additions = 0
    for word, weight in base.items():
        if word in authority_rank:
            continue
        rank = equivalent(weight)
        if rank is None or rank > add_limit:
            continue
        rows.append((max(1, int(rank)), rank, -weight, word))
        additions += 1
    rows.sort(key=lambda row: (row[0], row[1], row[2], row[3]))
    return rows, additions


def load_variant_exclusions(path: Path) -> set[str]:
    result: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            if raw.startswith("#") or not raw.strip():
                continue
            parts = raw.rstrip("\r\n").split("\t")
            if len(parts) != 2:
                raise ValueError(f"invalid variant row: {raw!r}")
            result.add(parts[1])
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--external",
        type=Path,
        required=True,
        help="authority word list, word<TAB>count per line, ordered by frequency",
    )
    parser.add_argument(
        "--base",
        type=Path,
        default=ROOT / "mohu_zrm.base.dict.yaml",
        help="Mohu base dictionary used for gap filling",
    )
    parser.add_argument(
        "--add-limit",
        type=int,
        default=1000,
        help="maximum equivalent rank for base-only additions",
    )
    parser.add_argument(
        "--variants",
        type=Path,
        default=ROOT / "tools/data/two_char_variant_groups.tsv",
        help="keep<TAB>exclude variant pairs; right side never competes",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "tools/data/two_char_word_rank.txt",
        help="dev rank table; keep out of lua/ so it never enters packages",
    )
    args = parser.parse_args()

    authority = load_authority(args.external)
    base = load_base_weights(args.base)
    rows, additions = build(authority, base, args.add_limit)

    variants = load_variant_exclusions(args.variants)
    authority_set = {word for word, _ in authority}
    dropped_variants = variants & (authority_set | set(base))
    kept = [row for row in rows if row[3] not in variants]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("# generated by tools/build_two_char_word_rank.py; do not edit\n")
        for rank, _, _, word in kept:
            handle.write(f"{word}\t{rank}\n")

    print(f"authority words: {len(authority)}")
    print(f"base-only additions (equiv<= {args.add_limit}): {additions}")
    print(f"variant exclusions in corpus: {len(dropped_variants)}")
    print(f"total words: {len(kept)}")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
