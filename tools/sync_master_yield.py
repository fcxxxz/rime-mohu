#!/usr/bin/env python3
"""主表单字让位行序同步：与引擎根词表同一套五类规则。

tools/sync_rootlex_aux.py 已把引擎侧按「无退路单字 | 兼容退路单字 |
双拼词 | 前缀简码单字（让全）| 简词」排序；主表是固定层 codes 流的同码
初始顺序来源（质量 100，高于引擎的 50），必须同步，否则有退路字会被
codes 流压回首选。

主表的结构本身编码了类别：主单字块（码序，先于词块）承载无退路/兼容
退路字；尾部「当前有效的完整单字码」区承载有前缀简码、走让全的字。
因此本工具只做两件事：

1. 同码内，0 类（无简码声明、无兼容打法）单字行不得排在 1 类（rescued）
   或 3 类（前缀简码）之后——违规时把 0 类行移动到首个更高类行之前
   （跨区移动落位主块码序位置）；
2. 同码内 1 类行不得排在 3 类行之后（同为让全语义的次序）。

词行、简码区（一二三码）、无争议的尾区停靠（同码无 1/3 类行）不动；
让全运行时行为（ijrq）不在此重复。飞键镜像区按同样规则在区内修正。

用法：
    uv run python tools/sync_master_yield.py            # 干跑报告
    uv run python tools/sync_master_yield.py --apply    # 写回主表
"""

from __future__ import annotations

import argparse
import re
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
ROW = re.compile(r"^([^\t#]+)\t([a-z]+)$")
CJK = re.compile(r"[\u4e00-\u9fff]")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="写回主表（默认干跑）")
    args = parser.parse_args()

    aux = load_auxiliary_tsv(ROOT / "tools/data/tiger_aux.txt")
    rescued = load_master_compatibility_characters(MASTER, aux)

    lines = MASTER.read_text(encoding="utf-8").splitlines()
    claims: dict[str, set[str]] = defaultdict(set)
    rows_by_code: dict[str, list[int]] = defaultdict(list)
    for i, line in enumerate(lines):
        m = ROW.match(line)
        if not m or not CJK.search(m.group(1)) or len(m.group(1)) != 1:
            continue
        code = m.group(2)
        if 1 <= len(code) <= 3:
            claims[m.group(1)].add(code)
        elif len(code) == 4:
            rows_by_code[code].append(i)

    def klass(text: str, code: str) -> int:
        if any(code.startswith(c) for c in claims.get(text, ())):
            return 3
        if text in rescued:
            return 1
        return 0

    def find_moves(current: list[str]) -> list[tuple[int, int]]:
        found: list[tuple[int, int]] = []
        for code in sorted(rows_by_code):
            chars = [(i, ROW.match(current[i]).group(1)) for i in rows_by_code[code]]
            classes = [klass(t, code) for _, t in chars]
            for j in range(len(chars)):
                if classes[j] != 0:
                    continue
                higher = next((k for k in range(j) if classes[k] in (1, 3)), None)
                if higher is not None:
                    found.append((chars[j][0], chars[higher][0]))
                break
        return found

    first = find_moves(lines)
    print(f"首轮违规: {len(first)}")
    for src, dst in first:
        print(f"  {lines[src]!r}  ->  移至 {lines[dst]!r} 之前")

    if not args.apply:
        print("干跑完成；--apply 写回")
        return
    if not first:
        print("无可移动行")
        return

    # 逐条应用并全量重扫：单轮只动一条，避免批量索引漂移
    total = 0
    while True:
        moves = find_moves(lines)
        if not moves:
            break
        src, dst = moves[0]
        print(f"  move {lines[src]!r} -> before {lines[dst]!r}")
        line = lines.pop(src)
        lines.insert(dst, line)
        total += 1
        rows_by_code = defaultdict(list)
        claims = defaultdict(set)
        for i, cur in enumerate(lines):
            m = ROW.match(cur)
            if not m or not CJK.search(m.group(1)) or len(m.group(1)) != 1:
                continue
            code = m.group(2)
            if 1 <= len(code) <= 3:
                claims[m.group(1)].add(code)
            elif len(code) == 4:
                rows_by_code[code].append(i)

    MASTER.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"共 {total} 次移动收敛；已写回 {MASTER}")


if __name__ == "__main__":
    main()
