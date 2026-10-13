#!/usr/bin/env python3
"""Restore char readings dropped by the 2026-10-08 code-table unification.

e660742 collapsed each surviving char to its exported shortest-code set, so
some polyphone chars lost every row of an alternate reading (削's xiāo kept
only the migrated full-code tail, others lost the syllable entirely). This
tool reads the retired ``mohu_zrm.chars.dict.yaml`` from git history and
re-adds, for chars still present in the main table, the missing full codes
(``syllable;aux`` rows) of readings that have no row at any code length.

Restored rows land in the char-order full-code region at the end of the
table (where the unification itself put migrated full codes), so they never
leapfrog curated rows. Only readings whose syllable is a live double-pinyin
prefix in today's table are restored; radical/stroke fallback pseudo
syllables from the old table are reported but skipped. Idempotent.
"""
from __future__ import annotations

import argparse
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OLD_REF = "e660742^"
OLD_CHARS = "mohu_zrm.chars.dict.yaml"


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


def parse_table_text(text: str) -> list[tuple[str, str, int]]:
    out: list[tuple[str, str, int]] = []
    in_table = False
    for raw in text.splitlines():
        if raw == "...":
            in_table = True
            continue
        if not in_table or not raw or raw.startswith("#"):
            continue
        parts = raw.split("\t")
        if len(parts) >= 2 and parts[0] and parts[1]:
            weight = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 0
            out.append((parts[0], parts[1], weight))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dict", type=Path, default=ROOT / "mohu_zrm.dict.yaml")
    parser.add_argument("--old-chars", type=Path,
                        help="retired chars table file (default: git show " + OLD_REF + ")")
    parser.add_argument("--include-variants", action="store_true",
                        help="also restore dropped compat-aux spellings of surviving "
                             "readings (xcyp for 削 alongside xcyv); two-letter "
                             "short-code rows are never restored")
    parser.add_argument("--apply", action="store_true",
                        help="rewrite the source table (default: dry-run report)")
    args = parser.parse_args()

    lines, rows = load_table(args.dict)
    present = {r.text for r in rows if len(r.text) == 1}
    cur_syl: dict[str, set[str]] = defaultdict(set)
    cur_codes: dict[str, set[str]] = defaultdict(set)
    live_syllables: set[str] = set()
    for r in rows:
        if len(r.text) == 1 and len(r.code) >= 3:
            cur_syl[r.text].add(r.code[:2])
            cur_codes[r.text].add(r.code)
            live_syllables.add(r.code[:2])

    if args.old_chars:
        old_text = args.old_chars.read_text(encoding="utf-8")
    else:
        old_text = subprocess.run(
            ["git", "-C", str(ROOT), "show", f"{OLD_REF}:{OLD_CHARS}"],
            check=True, capture_output=True, text=True).stdout
    old_readings: dict[str, set[str]] = defaultdict(set)
    old_codes: dict[str, set[str]] = defaultdict(set)
    for text, code, _weight in parse_table_text(old_text):
        if len(text) != 1 or ";" not in code:
            continue
        syl, aux = code.split(";", 1)
        if len(syl) == 2:
            # the old table parked alternate readings at weight 0 (骑 ji;nm,
            # 粥 yu;bp): weight does not decide restorability — a live
            # syllable prefix does
            old_readings[text].add(syl)
            old_codes[text].add(syl + aux)

    lost: dict[str, set[str]] = {}
    skipped_pseudo: dict[str, set[str]] = {}
    for ch in present:
        miss = old_readings.get(ch, set()) - cur_syl.get(ch, set())
        if not miss:
            continue
        live = {s for s in miss if s in live_syllables}
        if live:
            lost[ch] = live
        if miss - live:
            skipped_pseudo[ch] = miss - live

    restores: list[tuple[str, str]] = []
    variant_restores: list[tuple[str, str]] = []
    for ch, syls in sorted(lost.items()):
        for code in sorted(old_codes.get(ch, ())):
            if code[:2] in syls and code not in cur_codes[ch] and len(code) >= 3:
                restores.append((ch, code))
    if args.include_variants:
        for ch in sorted(present):
            for code in sorted(old_codes.get(ch, ())):
                if (len(code) >= 3 and code not in cur_codes[ch]
                        and code[:2] not in lost.get(ch, set())
                        and code[:2] in cur_syl.get(ch, set())):
                    variant_restores.append((ch, code))

    print(f"chars with a lost live reading: {len(lost)}; rows to restore: {len(restores)}")
    for ch, syls in sorted(lost.items())[:20]:
        print(f"  {ch}: {sorted(syls)}")
    if skipped_pseudo:
        print(f"chars with only pseudo-syllable losses (skipped): {len(skipped_pseudo)}")
    if args.include_variants:
        print(f"compat-aux variant rows to restore: {len(variant_restores)} "
              f"({len({c for c, _ in variant_restores})} chars)")
        for ch, code in variant_restores[:10]:
            print(f"  {ch}: {code}")
    restores += variant_restores

    if not args.apply or not restores:
        print("dry-run only; pass --apply to rewrite the table")
        return

    # The char-order full-code region is the contiguous run of single-char
    # rows at the end of the file. Anchor after the char's last row there;
    # otherwise insert at its codepoint position among the region's rows.
    tail_rows: list[Row] = []
    for r in reversed(rows):
        if len(r.text) != 1:
            break
        tail_rows.append(r)
    tail_rows.reverse()
    tail_first_line = tail_rows[0].line_no if tail_rows else len(lines) + 1
    last_line_of_char: dict[str, int] = {}
    for r in tail_rows:
        last_line_of_char[r.text] = max(last_line_of_char.get(r.text, 0), r.line_no)
    insert_after: dict[int, list[str]] = defaultdict(list)
    for ch, code in restores:
        payload = f"{ch}\t{code}"
        anchor = last_line_of_char.get(ch)
        if anchor is None:
            anchor = next(
                (r.line_no - 1 for r in tail_rows if r.text > ch),
                tail_rows[-1].line_no if tail_rows else len(lines))
        insert_after[anchor].append(payload)
    rewritten: list[str] = []
    for no, raw in enumerate(lines, 1):
        rewritten.append(raw)
        for payload in insert_after.get(no, ()):
            rewritten.append(payload)
    args.dict.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
    print(f"restored {len(restores)} rows in {args.dict}")


if __name__ == "__main__":
    main()
