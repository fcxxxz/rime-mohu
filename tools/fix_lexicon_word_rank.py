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
tiger_sentence_native/mohu_tiger.lexicon.txt, two-char rows — whose code is
by construction the word's full double-pinyin spelling — are promoted to the
top of the word tier sorted by corpus frequency descending (yywu: 鹦鹉 1490
above the abbreviation rows, aadb: 阿斗 above 嗷嗷待哺). Rows longer than two
characters are 声母简词 (their full spellings are 8+ keys; the four-code row
is the f47aeb7 quick-abbreviation feature, 成语优先) and **keep their
inherited relative order** — 云雨巫山 stays ahead of 医药卫生, 安堵乐业 ahead
of 澳大利亚. Single-char rows never move (出简让全/字序 own them), rank-99
injected rows never move, the freq column is untouched, and the master table
is untouched. Frequency comes from the generated
tiger_sentence_native/data/zrm/mohu_zrm.lexicon.txt; this file's own word
freq column is a 20001 sentinel. Idempotent; ``make mohu_lexicons``
regenerates both derived lexicons after running.
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
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
        two_char = [index for index in words
                    if len(lines[index].split("\t")[1]) == 2]
        if not two_char:
            continue  # 纯简词组无二字真词，整组保持继承序
        freqs = {
            index: exact.get((code, lines[index].split("\t")[1]),
                             by_text.get(lines[index].split("\t")[1], 0))
            for index in words
        }
        # 二字全码词按词频升到词层顶部；>2 字声母简词保持原相对序。
        head = sorted(two_char, key=lambda index: (-freqs[index], index))
        tail = [index for index in words if index not in set(two_char)]
        order = head + tail
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
        # 词行整行置换到词行槽位：行序、rank 序一致。
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
