#!/usr/bin/env python3
"""把单字辅码兼容形态物化进原生引擎根词表，并落地正常码位的让位排序。

背景：chars 词典为每个声明兼容打法（rescued）的字生成 13/14 位辅码行
（`sp;xy`），固定层经拼写运算自动派生全部输入形态；但整句引擎消费的
根词表只物化过正常辅码行，兼容打法在引擎里整类不存在。本工具补齐：

规则一（兼容物化）：rescued 字的兼容形态 `spXY`、`spXY/`、`spXYo`
从 chars 词典同步进根词表，仅限引擎已收录的字。freq 沿用该字正常形态
行的取值。纯兼容码组内排序：真双拼词（二字词且逐字双拼恰为该码）在
前，兼容字按 freq 其后，其余（四码简词等）殿后——与「简词是首选后的
补充候选」一致。兼容码若撞上他字正常码，兼容字作为访客并入单字区末
尾，交给规则二统一让位（正常码语义优先）。

规则二（让位排序）：正常辅码全码组内，无兼容退路的单字排在有退路
单字之前，词条跟在单字之后，各类内部保持原相对序（稳定划分）。
后缀组（`/`、`o`）镜像主组单字次序。

用法：
    uv run python tools/sync_rootlex_aux.py            # 干跑报告
    uv run python tools/sync_rootlex_aux.py --apply    # 写回根词表
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from tiger_aux import (  # noqa: E402
    load_auxiliary_tsv,
    load_master_compatibility_characters,
)

ROOT = TOOLS.parent
MASTER = ROOT / "mohu_zrm.dict.yaml"
CHARS = ROOT / "tools/data/lexicon_sources/zrm/mohu_zrm.chars.dict.yaml"
WORDS = ROOT / "mohu_zrm.words.dict.yaml"
ROOTLEX = ROOT / "tiger_sentence_native/mohu_tiger.lexicon.txt"


def load_chars(path: Path, aux: dict) -> tuple[dict[str, list[tuple[str, str]]], dict[str, set[str]], dict[str, int]]:
    """返回 (char -> [(sp, compat_aux)], char -> 双拼音节集合, char -> 读音频次)。"""
    compat_rows: dict[str, list[tuple[str, str]]] = defaultdict(list)
    syllables: dict[str, set[str]] = defaultdict(set)
    reading_weight: dict[str, int] = {}
    normal_of = {ch: entry[0] for ch, entry in aux.items()}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.startswith("#") or not raw.strip():
            continue
        fields = raw.split("\t")
        if len(fields) < 3 or len(fields[0]) != 1 or ";" not in fields[1]:
            continue
        char, sp, aux_code = fields[0], *fields[1].split(";")
        syllables[char].add(sp)
        weight = int(fields[2]) if fields[2].isdigit() else 0
        reading_weight[char] = max(reading_weight.get(char, 0), weight)
        if normal_of.get(char) and aux_code != normal_of[char]:
            compat_rows[char].append((sp, aux_code))
    return compat_rows, syllables, reading_weight


def load_word_weights(path: Path) -> dict[str, int]:
    """词 -> 最大词频（words 词典末列；同词多行取最大）。"""
    weights: dict[str, int] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.startswith("#") or not raw.strip():
            continue
        fields = raw.split("\t")
        if len(fields) < 3 or len(fields[0]) < 2:
            continue
        weight = int(fields[-1]) if fields[-1].isdigit() else 0
        if weight > weights.get(fields[0], 0):
            weights[fields[0]] = weight
    return weights


def load_short_claims(path: Path) -> dict[str, set[str]]:
    """主表显式简码声明：字 -> 一二三码集合（如 嘶 sid、进 jn、咦 yid）。"""
    claims: dict[str, set[str]] = defaultdict(set)
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.startswith("#") or not raw.strip():
            continue
        fields = raw.split("\t")
        if len(fields) < 2 or len(fields[0]) != 1:
            continue
        code = fields[1]
        if 1 <= len(code) <= 3 and code.isalpha() and code.islower():
            claims[fields[0]].add(code)
    return claims


def parse_lexicon(path: Path) -> list[list[list[str]]]:
    """根词表按码连续分组，返回行列表的列表（每行按制表符切分）。"""
    groups: list[list[list[str]]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        fields = raw.split("\t")
        if len(fields) != 4:
            raise ValueError(f"unexpected lexicon row: {raw!r}")
        if groups and groups[-1][0][0] == fields[0]:
            groups[-1].append(fields)
        else:
            groups.append([fields])
    codes = [g[0][0] for g in groups]
    if codes != sorted(codes):
        raise ValueError("根词表码序非单调，需人工检查")
    return groups


def is_shuangpin_word(text: str, code: str, syllables: dict[str, set[str]]) -> bool:
    """二字词且逐字双拼拼接恰为该码 → 真双拼词（非四码简词）。"""
    if len(text) != 2:
        return False
    return any(a + b == code
               for a in sorted(syllables.get(text[0], ()))
               for b in sorted(syllables.get(text[1], ())))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="写回根词表（默认干跑）")
    parser.add_argument("--check", action="store_true",
                        help="有未同步差异时以非零码退出（供 make 检查）")
    args = parser.parse_args()

    aux = load_auxiliary_tsv(ROOT / "tools/data/tiger_aux.txt")
    rescued = load_master_compatibility_characters(MASTER, aux)
    compat_rows, syllables, reading_weight = load_chars(CHARS, aux)
    short_claims = load_short_claims(MASTER)
    word_weights = load_word_weights(WORDS)

    groups = parse_lexicon(ROOTLEX)
    by_code: dict[str, list[list[str]]] = {g[0][0]: [list(r) for r in g] for g in groups}
    existing_codes = set(by_code)

    # 字 -> 引擎 freq（兼容行沿用正常形态行取值）与正常 4 码映射
    char_freq: dict[str, int] = {}
    normal_code_of: dict[str, str] = {}
    normal_codes: set[str] = set()
    for ch, entry in aux.items():
        for sp in sorted(syllables.get(ch, ())):
            code = sp + entry[0]
            normal_codes.add(code)
            normal_code_of.setdefault(ch, code)
            for row in by_code.get(code, ()):
                if row[1] == ch:
                    char_freq[ch] = int(row[3])
                    break
    char_freq = {ch: f for ch, f in char_freq.items() if f}

    added: list[tuple[str, str, int]] = []       # (code, char, freq)
    skipped_no_freq: list[str] = []              # 引擎未收录的 rescued 字
    reordered: dict[str, tuple[str, str]] = {}   # code -> (before, after)

    def note_reorder(code: str, rows: list[list[str]], new: list[list[str]]) -> None:
        if [r[1] for r in new] != [r[1] for r in rows]:
            reordered[code] = (" ".join(r[1] for r in rows), " ".join(r[1] for r in new))

    # ---- 规则一：兼容物化 ----
    target: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for ch in sorted(rescued):
        entry = aux.get(ch)
        freq = char_freq.get(ch)
        if not entry:
            continue
        if not freq:
            skipped_no_freq.append(ch)
            continue
        for sp, compat_aux in compat_rows.get(ch, ()):
            target[sp + compat_aux].append((ch, freq))

    for code in sorted(target):
        rows = by_code.setdefault(code, [])
        for ch, freq in target[code]:
            if any(r[1] == ch for r in rows):
                continue
            row = [code, ch, "0", str(freq)]
            if code in normal_codes:
                # 撞他字正常码：访客并入单字区末尾，规则二统一让位
                chars_at = [i for i, r in enumerate(rows) if len(r[1]) == 1]
                pos = chars_at[-1] + 1 if chars_at else 0
                rows.insert(pos, row)
            else:
                words = [r for r in rows if is_shuangpin_word(r[1], code, syllables)]
                chars = [r for r in rows if len(r[1]) == 1] + [row]
                chars.sort(key=lambda r: -int(r[3]))
                others = [r for r in rows
                          if r not in words and len(r[1]) != 1]
                rows[:] = words + chars + others
            added.append((code, ch, freq))

    # ---- 规则二：让位排序（正常码主组 + 后缀组镜像）----
    # v9 最小移动版：
    # - 0 类（无兼容退路、无前缀简码）单字移到组顶——它们只有这个码；
    # - 1 类正主（本码=正常辅码全码、有兼容退路、无前缀简码）按权重插进
    #   词列：排到权重小于自身读音频次的词之后、大于它的词之前
    #   （嗣 18110 压 死敌 864，怩 2372 让 你好 44153）；
    # - 3 类（前缀简码在让全）与全部词/简词原位不动；
    # - 访客已由规则一放在既有单字行之后，不再参与移动。
    def has_prefix_claim(ch: str, code: str) -> bool:
        return any(code.startswith(c) and len(c) < len(code)
                   for c in short_claims.get(ch, ()))

    for code in list(by_code):
        if len(code) != 4 or code.endswith(("/", "o")) or code not in normal_codes:
            continue
        rows = by_code[code]

        def is_class0(row: list[str]) -> bool:
            text = row[1]
            return (len(text) == 1 and text not in rescued
                    and not has_prefix_claim(text, code))

        def is_owner1(row: list[str]) -> bool:
            text = row[1]
            return (len(text) == 1 and text in rescued
                    and not has_prefix_claim(text, code)
                    and normal_code_of.get(text) == code)

        top = [r for r in rows if is_class0(r)]
        rest = [r for r in rows if not is_class0(r)]
        # 1 类正主：仅当有词频低于自身读音频次的词挡在前面时上移到该词前
        # （嗣 18110 越过 死敌 864；无词或无更轻词时原位不动，保证幂等）
        moved = True
        while moved:
            moved = False
            for i, row in enumerate(rest):
                if not is_owner1(row):
                    continue
                weight = reading_weight.get(row[1], 0)
                for j in range(i):
                    other = rest[j]
                    if len(other[1]) != 1 and word_weights.get(other[1], 0) < weight:
                        rest.insert(j, rest.pop(i))
                        moved = True
                        break
                if moved:
                    break
        new = top + rest
        note_reorder(code, rows, new)
        by_code[code] = new
        order = [r[1] for r in new if len(r[1]) == 1]
        for suffix in ("/", "o"):
            srows = by_code.get(code + suffix)
            if not srows:
                continue
            snew = sorted(srows,
                          key=lambda r: order.index(r[1]) if len(r[1]) == 1
                          and r[1] in order else len(order))
            note_reorder(code + suffix, srows, snew)
            by_code[code + suffix] = snew

    # ---- 汇总输出 ----
    fresh = [c for c in by_code if c not in existing_codes]
    print(f"兼容行新增: {len(added)} 条（{len(fresh)} 个新码组，含后缀形态）")
    for code, ch, freq in added[:12]:
        print(f"  + {code}\t{ch}\t{freq}")
    if skipped_no_freq:
        print(f"引擎未收录而跳过的 rescued 字: {len(skipped_no_freq)}（"
              f"{' '.join(skipped_no_freq[:10])} …）")
    print(f"让位/插入排序调整码组: {len(reordered)}")
    report = Path("/tmp/sync_rootlex_aux_report.tsv")
    report.write_text(
        "code\tbefore\tafter\n" +
        "\n".join(f"{c}\t{b}\t{a}" for c, (b, a) in sorted(reordered.items())),
        encoding="utf-8")
    print(f"全量清单: {report}")
    for code, (before, after) in list(reordered.items())[:24]:
        print(f"  {code}: {before}  ->  {after}")

    if not args.apply:
        if args.check and (added or reordered):
            print("根词表与兼容/让位规则不同步：需要补行或调整序（--apply）")
            raise SystemExit(1)
        print("干跑完成；--apply 写回")
        return

    out_lines: list[str] = []
    emitted: set[str] = set()

    def emit(code: str) -> None:
        out_lines.extend(
            "\t".join([r[0], r[1], str(i), r[3]])
            for i, r in enumerate(by_code[code], 1))
        emitted.add(code)

    for g in groups:
        code = g[0][0]
        for c in sorted(fresh):
            if c < code and c not in emitted:
                emit(c)
        emit(code)
    for c in sorted(fresh):
        if c not in emitted:
            emit(c)

    codes_out = [line.split("\t")[0] for line in out_lines]
    if codes_out != sorted(codes_out):
        raise ValueError("输出码序非单调，中止")
    ROOTLEX.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    print(f"已写回 {ROOTLEX}（{len(out_lines):,} 行）")


if __name__ == "__main__":
    main()
