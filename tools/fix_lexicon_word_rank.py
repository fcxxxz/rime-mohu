#!/usr/bin/env python3
"""Align native-lexicon word ranks with corpus frequency inside four-code groups.

The express layer already gives two-char words primary slots and demotes
abbreviation rows (云雨巫山/医药卫生) to secondary, but the native engine knows
no such class: every lexicon row competes by rank, and ranks are the
historical curation order inherited from the unified table. A rank-1 idiom
therefore rides the native>smart merge priority over a far more frequent
word at the same code (yywu: 云雨巫山 rank 1 vs 鹦鹉 freq 1490) — the data
alignment debt recorded with the 0.17.0 note ("词库权重与词秩策展源不一致，
属数据对齐问题").

Rule applied here, and only here: within each four-code group of
tiger_sentence_native/mohu_tiger.lexicon.txt, word rows (len>=2, rank != 99)
are permuted among their own line slots sorted by corpus frequency descending
(ties keep current order), and their rank values are reassigned to the same
sorted slot set — so line order, rank order and frequency order agree.
Single-char rows never move (出简让全/字序 own them), rank-99 injected rows
never move, the freq column is untouched (it does not enter decode scores),
and the master table is untouched (the express layer owns runtime word order
there). Frequency comes from the generated
tiger_sentence_native/data/zrm/mohu_zrm.lexicon.txt (the corpus freq the
engine build writes); this file's own word freq column is a 20001 sentinel.
Idempotent; ``make mohu_lexicons`` regenerates both derived lexicons after
running.
"""
from __future__ import annotations

import argparse
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEXICON = ROOT / "tiger_sentence_native/mohu_tiger.lexicon.txt"
FREQ_SOURCE = ROOT / "tiger_sentence_native/data/zrm/mohu_zrm.lexicon.txt"
CODE_RE = re.compile(r"^[a-z]{4}$")
HAN = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fff]+$")


def load_corpus_freq() -> tuple[dict[tuple[str, str], int], dict[str, int]]:
    exact: dict[tuple[str, str], int] = {}
    by_text: dict[str, int] = {}
    for line in FREQ_SOURCE.read_text(encoding="utf-8").splitlines():
        fields = line.split("\t")
        if len(fields) < 4 or not fields[0]:
            continue
        try:
            freq = int(fields[3])
        except ValueError:
            continue
        exact[(fields[0], fields[1])] = freq
        if freq > by_text.get(fields[1], -1):
            by_text[fields[1]] = freq
    return exact, by_text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    exact, by_text = load_corpus_freq()
    lines = LEXICON.read_text(encoding="utf-8").split("\n")
    groups: dict[str, list[int]] = defaultdict(list)
    for index, line in enumerate(lines):
        fields = line.split("\t")
        if len(fields) < 4 or not CODE_RE.fullmatch(fields[0]):
            continue
        if len(fields[1]) >= 1 and HAN.fullmatch(fields[1]):
            groups[fields[0]].append(index)

    changed = 0
    samples: list[str] = []
    for code, indexes in sorted(groups.items()):
        words = [index for index in indexes
                 if len(lines[index].split("\t")[1]) >= 2
                 and lines[index].split("\t")[2] != "99"]
        if len(words) < 2:
            continue
        freqs = {
            index: exact.get((code, lines[index].split("\t")[1]),
                             by_text.get(lines[index].split("\t")[1], 0))
            for index in words
        }
        order = sorted(words, key=lambda index: (-freqs[index], index))
        if order == words:
            continue
        changed += 1
        if len(samples) < 10:
            samples.append(f"{code}: " + " > ".join(
                f"{lines[index].split(chr(9))[1]}({freqs[index]})"
                for index in order[:4]))
        slots = sorted(int(lines[index].split("\t")[2]) for index in words)
        for slot, source_index in zip(slots, order):
            fields = lines[source_index].split("\t")
            fields[2] = str(slot)
            lines[source_index] = "\t".join(fields)
        # 词行整行置换到词行槽位：行序、rank 序、词频序三者一致。
        reordered = [lines[source_index] for source_index in order]
        for slot_position, source_index in enumerate(words):
            lines[source_index] = reordered[slot_position]
    LEXICON.write_text("\n".join(lines), encoding="utf-8")
    print(f"four-code word groups reordered: {changed}")
    for sample in samples:
        print(f"  {sample}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
