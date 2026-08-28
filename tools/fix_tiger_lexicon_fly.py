#!/usr/bin/env python3
"""补齐原生整句码表（mohu_tiger.lexicon.txt）的飞键行。

mohu 方案的 speller algebra（mohu_defs.yaml:/fly）在音节层派生三条飞键：
    wz; -> wk;   xq; -> xo;   qx; -> qo;
派生按规则链式应用（后一条规则能看到前一条的产物），同一规则内全局替换。

原生码表当初只同步了上游自带的高频飞键，且未做闭包，导致例如：
    wk 只有「为」，位/微/未/围... 均缺失；「维修」缺链式组合 wkxo。
用户用飞键打整句（mowktktl）时引擎看不见「位」，只能组出「万为淘汰」。

本脚本按以下规则补齐（与 mohu algebra 语义对齐）：
    单字条目（code 前两字母即音节）：前缀替换，辅码后缀原样保留。
    多字条目：仅当 code 长度 == 2×字数（全音节码，每两字母一个音节，
    且每个音节都在 mohu_zrm.chars.dict.yaml 的音节表中）时，对音节做
    飞键闭包；声母码（如 mwtt、ab）不动。
    已存在的 (code, text) 行不重复生成，rank/freq 列照抄来源行。

用法：
    uv run tools/fix_tiger_lexicon_fly.py            # 应用（写回并备份 .bak）
    uv run tools/fix_tiger_lexicon_fly.py --check    # 只统计不写入
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FLY = {"wz": "wk", "xq": "xo", "qx": "qo"}


def load_syllables(chars_dict: Path) -> set[str]:
    syllables: set[str] = set()
    in_body = False
    for line in chars_dict.read_text(encoding="utf-8").splitlines():
        if line.startswith("..."):
            in_body = True
            continue
        if not in_body or not line.strip() or line.startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) < 2:
            continue
        code = cols[1].split()[0] if cols[1].split() else ""
        syllable = code.split(";")[0]
        if len(syllable) == 2:
            syllables.add(syllable)
    return syllables


def fly_closure(syllables: tuple[str, ...]) -> set[tuple[str, ...]]:
    """链式应用三条飞键规则，同一条规则内全局替换，返回全部变体（不含原码）。"""
    seen = {syllables}
    frontier = [syllables]
    while frontier:
        cur = frontier.pop()
        for src, dst in FLY.items():
            if src in cur:
                nxt = tuple(dst if s == src else s for s in cur)
                if nxt not in seen:
                    seen.add(nxt)
                    frontier.append(nxt)
    return seen - {syllables}


def parse_lexicon(path: Path) -> tuple[list[list[str]], list[str], list[str]]:
    """返回 ([code, text, rank, freq] 行, 原行文本, 注释/空行)。"""
    parsed: list[tuple[list[str], str]] = []
    header_lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            header_lines.append(line)
            continue
        cols = line.split("\t")
        code, text = cols[0], cols[1]
        rank = cols[2] if len(cols) > 2 else "1"
        freq = cols[3] if len(cols) > 3 else "20001"
        parsed.append(([code, text, rank, freq], line))
    return [p[0] for p in parsed], [p[1] for p in parsed], header_lines


def compute_missing(rows: list[list[str]],
                     syllables: set[str]) -> tuple[list[list[str]], dict]:
    """对全部可飞键行计算缺失的飞键变体，返回 (新行, 统计)。"""
    existing = {(r[0], r[1]) for r in rows}
    new_rows: list[list[str]] = []
    stats = {"char": 0, "word": 0, "skip_dup": 0, "anomaly": 0}
    for code, text, rank, freq in rows:
        n = len(text)
        variants: list[str] = []
        if n == 1:
            if len(code) >= 2 and code[:2] in FLY:
                variants.append(FLY[code[:2]] + code[2:])
            else:
                continue
        else:
            if len(code) != 2 * n:
                if len(code) != n:
                    stats["anomaly"] += 1
                continue
            syl = tuple(code[i:i + 2] for i in range(0, len(code), 2))
            if not all(s in syllables for s in syl):
                continue
            variants = ["".join(v) for v in fly_closure(syl)]
            if not variants:
                continue
            stats["word"] += 1
        stats["char"] += 1 if n == 1 else 0
        for v in variants:
            if (v, text) in existing:
                stats["skip_dup"] += 1
                continue
            existing.add((v, text))
            new_rows.append([v, text, rank, freq])
    return new_rows, stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lexicon", type=Path,
                    default=REPO / "tiger_sentence_native/mohu_tiger.lexicon.txt")
    ap.add_argument("--chars-dict", type=Path,
                    default=REPO / "mohu_zrm.chars.dict.yaml")
    ap.add_argument("--check", action="store_true", help="只统计，不写入")
    args = ap.parse_args()

    syllables = load_syllables(args.chars_dict)
    print(f"音节表: {len(syllables)} 个双拼音节")

    rows, raw, header_lines = parse_lexicon(args.lexicon)

    # 校验原文件按 (code, rank) 有序
    keys = [(r[0], int(r[2])) for r in rows]
    if keys != sorted(keys):
        print("警告: 原码表并非严格按 (code, rank) 有序，合并时保持原有相对顺序")

    new_rows, stats = compute_missing(rows, syllables)

    per_prefix: dict[str, int] = {}
    for r in new_rows:
        for src, dst in FLY.items():
            if r[0].startswith(dst) or dst in r[0]:
                per_prefix[dst] = per_prefix.get(dst, 0) + 1
                break

    print(f"原行数: {len(rows)}")
    print(f"单字可飞键行: {stats['char']}，多字全音节可飞键行: {stats['word']}")
    print(f"新增飞键行: {len(new_rows)}（跳过已存在 {stats['skip_dup']}，异常码形 {stats['anomaly']}）")
    for k in sorted(per_prefix):
        print(f"  含 {k} 前缀/组合的新增: {per_prefix[k]}")

    if args.check:
        return 0

    merged = list(zip(rows, raw)) + [(r, "\t".join(r)) for r in new_rows]
    merged.sort(key=lambda p: (p[0][0], int(p[0][2])))  # 稳定排序：原行相对顺序不变
    backup = args.lexicon.with_name(args.lexicon.name + ".bak")
    shutil.copy2(args.lexicon, backup)
    out = [p[1] for p in merged]
    if header_lines:
        out = header_lines + out
    args.lexicon.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"已写入 {args.lexicon}（备份: {backup}，总行数 {len(merged)}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
