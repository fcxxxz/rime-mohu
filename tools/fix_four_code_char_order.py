#!/usr/bin/env python3
"""Order four-code char rows below leading two-char words in the main table.

Reapplies, as main-table row order, the criterion the retired four-code
avoidance tables used (tools/build_four_code_yield_pairs.py, deleted in
4ece197): at a four-code collision a two-char word displaces a single char
when the best-ranked word's rank is below 4x the char's Tiger rank. That
keeps top-frequency characters (说/家/只…) unbeatable, matching the menus
users had before the 2026-10-10 unification (bixu 必须→庳, yjlm 演练→芫).

Only rows of the natural-code source table move: a displaced char row is
relocated directly after its beating word's row (旧避让表语义「被顶单字仍保
次选」), so 布丁/不定 stay below 卟 at budy. Three-code slots (简单鹤 merge:
字不动、词竞速) and the char-order full-code tail region are not touched.
Idempotent: codes already ordered are skipped. Flypy is derived from this
table by ``make dict``.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RATIO = 4


@dataclass
class Row:
    line_no: int
    text: str
    code: str


def load_table(path: Path) -> tuple[list[str], list[Row]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    rows: list[Row] = []
    in_table = False
    for no, raw in enumerate(lines, 1):
        if raw == "...":
            in_table = True
            continue
        if not in_table or not raw or raw.startswith("#"):
            continue
        parts = raw.split("\t")
        if len(parts) >= 2 and parts[0] and parts[1]:
            rows.append(Row(no, parts[0], parts[1]))
    return lines, rows


def load_word_ranks(path: Path) -> dict[str, int]:
    ranks: dict[str, int] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.startswith("#"):
            continue
        word, rank = raw.split("\t")
        ranks[word] = int(rank)
    return ranks


def load_char_ranks(path: Path) -> dict[str, int]:
    ranks: dict[str, int] = {}
    for index, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        char = raw.split("\t")[0].strip()
        if char:
            ranks.setdefault(char, index)
    return ranks


def plan_moves(rows: list[Row], word_rank: dict[str, int],
               char_rank: dict[str, int]) -> list[tuple[Row, str, str, int, int]]:
    """Return (char_row, best_word, best_word_rank, char_rank_) moves.

    A displaced char belongs directly after its beating word's row (旧避让表
    语义：被顶单字保次选), never below the rest of the code's words. Rows in
    the char-order full-code tail region are fallback spellings and never move.
    """
    by_code: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        by_code[row.code].append(row)

    # the char-order full-code region is the contiguous single-char run at EOF
    tail_first = len(rows) + 1
    for row in reversed(rows):
        if len(row.text) != 1:
            break
        tail_first = row.line_no

    moves: list[tuple[Row, str, str, int, int]] = []
    for code, items in by_code.items():
        if len(code) != 4:
            continue
        chars = [r for r in items if len(r.text) == 1]
        words = [r for r in items if len(r.text) >= 2]
        if not chars or not words:
            continue
        ranked = [(r.line_no, r.text) for r in words if r.text in word_rank]
        if not ranked:
            continue
        # 旧避让表语义：流中第一条入秩词决定让位（补丁 顶 卟，布丁/不定不动）。
        # 离线下以行序第一条入秩词近似流序。
        beater_row = min(ranked)[1]
        beater_rank = word_rank[beater_row]
        beaters = [r for r in words if r.text == beater_row]
        beater_last = max(r.line_no for r in beaters)
        beating = [r for r in chars
                   if r.line_no < tail_first
                   and beater_rank < RATIO * char_rank.get(r.text, len(char_rank) + 1)]
        if not beating:
            continue
        # already a contiguous block right after the beater? then nothing to do
        beat_lines = sorted(r.line_no for r in beating)
        in_block = {r.line_no for r in beating}
        ok = beat_lines[0] > beater_last and all(
            line in in_block or line in {b.line_no for b in beaters}
            for line in range(beater_last + 1, beat_lines[-1] + 1))
        if ok:
            continue
        for char_row in beating:
            moves.append((char_row, beater_row, code, beater_rank,
                          char_rank.get(char_row.text, len(char_rank) + 1)))
    return moves


def apply_moves(lines: list[str], rows: list[Row],
                moves: list[tuple[Row, str, str, int, int]]) -> int:
    """Rewrite ``lines`` in place; return the number of relocated rows."""
    if not moves:
        return 0
    # Anchor directly after the beating word's last row: 被顶单字保次选.
    beater_last_by_code: dict[tuple[str, str], int] = {}
    char_rows_by_code: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        if len(row.text) >= 2:
            key = (row.code, row.text)
            beater_last_by_code[key] = max(beater_last_by_code.get(key, 0),
                                           row.line_no)
        else:
            char_rows_by_code[row.code].append(row)
    word_of_code: dict[str, str] = {}
    for row, word, code, _, _ in moves:
        word_of_code[code] = word
    extracted = {m[0].line_no for m in moves}
    insert_after: dict[int, list[str]] = defaultdict(list)
    for row, word, code, _, _ in moves:
        anchor = beater_last_by_code.get((code, word))
        insert_after[anchor].append(f"{row.text}\t{row.code}")
    rewritten: list[str] = []
    for no, raw in enumerate(lines, 1):
        if no in extracted:
            continue
        rewritten.append(raw)
        for payload in insert_after.get(no, ()):
            rewritten.append(payload)
    lines[:] = rewritten
    return len(moves)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dict", type=Path, default=ROOT / "mohu_zrm.dict.yaml")
    parser.add_argument("--apply", action="store_true",
                        help="rewrite the source table (default: dry-run report)")
    args = parser.parse_args()

    lines, rows = load_table(args.dict)
    word_rank = load_word_ranks(ROOT / "tools/data/two_char_word_rank.txt")
    char_rank = load_char_ranks(ROOT / "lua/tiger_rank.txt")

    moves = plan_moves(rows, word_rank, char_rank)
    codes = {m[2] for m in moves}
    print(f"four-code codes to reorder: {len(codes)}; char rows to move: {len(moves)}")
    for row, word, code, wr, cr in moves[:20]:
        print(f"  {code}: {row.text} (rank {cr}) -> below {word} (rank {wr})")
    if len(moves) > 20:
        print(f"  ... and {len(moves) - 20} more")
    if not args.apply:
        print("dry-run only; pass --apply to rewrite the table")
        return
    moved = apply_moves(lines, rows, moves)
    args.dict.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"relocated {moved} char rows in {args.dict}")


if __name__ == "__main__":
    main()
