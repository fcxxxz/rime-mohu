#!/usr/bin/env python3
"""三码位「字先词后」重排：被顶字不再沉到声母简词之后。

背景（2026-10-23 用户判例：无重版 次/吧 三四选）：
三码位（双拼+首辅 / 声母简词同码空间）的引擎菜单序 = 根词表同码组
rank 列。ddc4fbc（10-12 简单鹤三字简词导入）后组序为「主表行在前、
根词表独有阶梯行殿后」，导致未占三简位的高频字（吧 146 / 次 152 /
啊 55 / 把 76 …，字频前 500 共 203 字）一律排到低词频声母简词
（保安队 45 / 存储为 0 等）之后——与四码位已定稿的「被顶字保次选」
（bdc5dcc）方向相反。

用户拍板规则（镜像四码定稿）：
- owner 字（组内现行 rank 最小的字行，即主表三简/简码行）钉首；
- 其余阶梯字按字频名次升序紧随（20001 占位殿后，同频保原序）；
- 声母简词（≥2 字行）按竞速序（现行相对序）整体殿后；
- 纯字 / 纯词码组不动；`XXX/` 三码筛镜像行由 build_mohu_lexicons
  从基础行同 rank 派生，根词表无需单独处理。

用法：
    uv run python tools/fix_lexicon_3key_char_first.py            # 干跑
    uv run python tools/fix_lexicon_3key_char_first.py --apply    # 写回根词表
"""

from __future__ import annotations

import argparse
import re
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROOTLEX = REPO / "tiger_sentence_native/mohu_tiger.lexicon.txt"

ROW = re.compile(r"^([a-z;/]+)\t(.+)$")
PLACEHOLDER_FREQ = 20001


def is_char(text: str) -> bool:
    return len(text) == 1


def char_freq(row: tuple[int, str, list[str]]) -> int:
    _rank, _text, fields = row
    try:
        freq = int(fields[3]) if len(fields) > 3 and fields[3] else PLACEHOLDER_FREQ
    except ValueError:
        freq = PLACEHOLDER_FREQ
    return freq if 0 < freq < PLACEHOLDER_FREQ else 10**9


def rebuild_group(rows: list[tuple[int, str, list[str]]]) -> list[str] | None:
    """rows: [(当前rank, text, 列字段)]，返回重排后的行列表；无需变化返回 None。"""
    chars = [r for r in rows if is_char(r[1])]
    words = [r for r in rows if not is_char(r[1])]
    if not chars or not words:
        return None
    # owner = 现行 rank 最小的字行（主表三简/简码行）钉首；其余阶梯字按
    # 字频名次升序（占位/异常频殿后，同频保现行序）。
    owner = min(chars, key=lambda r: r[0])
    ladder = sorted((r for r in chars if r is not owner),
                    key=lambda r: (char_freq(r), r[0]))
    new_order = [owner, *ladder, *words]
    if [r[1] for r in new_order] == [r[1] for r in rows]:
        return None
    out = []
    for new_rank, (_old_rank, text, fields) in enumerate(new_order, 1):
        out.append("\t".join([fields[0], text, str(new_rank), *fields[3:]]))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="写回根词表（默认干跑）")
    args = ap.parse_args()

    lines = ROOTLEX.read_text(encoding="utf-8").splitlines()
    groups: list[list[int]] = []
    for i, line in enumerate(lines):
        m = ROW.match(line)
        if not m:
            continue
        if groups and lines[groups[-1][0]].split("\t")[0] == m.group(1):
            groups[-1].append(i)
        else:
            groups.append([i])
    code_of = {lines[g[0]].split("\t")[0]: g for g in groups}
    codes_seq = list(code_of)
    assert codes_seq == sorted(codes_seq), "根词表码序非单调，需人工检查"

    base_groups = {
        code: g for code, g in code_of.items()
        if len(code) == 3 and code.isalpha()
    }

    rebuilt: dict[str, list[str]] = {}
    for code, g in base_groups.items():
        rows = []
        for i in g:
            fields = lines[i].split("\t")
            rank = int(fields[2]) if len(fields) > 2 and fields[2] else 1
            rows.append((rank, fields[1], fields))
        out = rebuild_group(rows)
        if out is not None:
            rebuilt[code] = out

    out_lines: list[str] = []
    changed_groups = 0
    for g in groups:
        code = lines[g[0]].split("\t")[0]
        if code in rebuilt:
            out_lines.extend(rebuilt[code])
            changed_groups += 1
        else:
            out_lines.extend(lines[i] for i in g)
    out_codes = [l.split("\t")[0] for l in out_lines if "\t" in l]
    assert out_codes == sorted(out_codes), "输出码序非单调，需人工检查"

    mixed = sum(
        1 for code, g in base_groups.items()
        if any(is_char(lines[i].split("\t")[1]) for i in g)
        and any(not is_char(lines[i].split("\t")[1]) for i in g)
    )
    print(f"三码基础组 {len(base_groups):,}（字词混合 {mixed:,}）；"
          f"重排 {len(rebuilt):,} 组")
    show = [c for c in ("bad", "ciw", "aad", "bau", "biv", "bms") if c in rebuilt]
    for code in show:
        print(f"  {code}: " + " | ".join(
            f"{l.split(chr(9))[1]}({l.split(chr(9))[2]})" for l in rebuilt[code]))

    if args.apply:
        ROOTLEX.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        print(f"已写回 {ROOTLEX}")
    else:
        print("干跑完成；加 --apply 写回")


if __name__ == "__main__":
    main()
