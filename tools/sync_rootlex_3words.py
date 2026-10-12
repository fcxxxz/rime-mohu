#!/usr/bin/env python3
"""把主表三字简词合并结果同步进原生引擎根词表。

背景：tiger_sentence_native/mohu_tiger.lexicon.txt 是整句引擎的源词表，
其 3 码位的「词行」镜像主表三字简词（已实测 HEAD 上 5,875 组词集全对齐）。
import_jdhe_3words.py 改了主表后，必须把受影响码组的词行重写为新词序，
否则引擎词表/排序特征仍用旧行序。

同步规则（每组）：
- 组 = 主表该码全部行（按主表文件序：字在前、三字块词在后）+ 根词表独有行殿后；
- 字与根词表独有行保留原 freq/reading/canonical 列，新词 freq 用 20001 占位
  （构建期由 build_mohu_lexicons 按整句词频富化）；
- rank 全组重排 1..N。

用法：
    uv run python tools/sync_rootlex_3words.py            # 干跑
    uv run python tools/sync_rootlex_3words.py --apply    # 写回根词表
"""

from __future__ import annotations

import argparse
import re
import subprocess
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MASTER = REPO / "mohu_zrm.dict.yaml"
ROOTLEX = REPO / "tiger_sentence_native/mohu_tiger.lexicon.txt"

ROW = re.compile(r"^([a-z;/]+)\t(.+)$")
MASTER_ROW = re.compile(r"^([^\t#]+)\t([a-z]+)$")
CJK3 = re.compile(r"[\u4e00-\u9fff]")


def load_master(path: Path) -> dict[str, list[str]]:
    """code -> 词字序列（主表文件序，含单字行）。"""
    out: dict[str, list[str]] = defaultdict(list)
    for line in path.read_text(encoding="utf-8").splitlines():
        m = MASTER_ROW.match(line)
        if m and CJK3.search(m.group(1)):
            out[m.group(2)].append(m.group(1))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    head_text = subprocess.run(
        ["git", "show", "HEAD:mohu_zrm.dict.yaml"],
        capture_output=True, text=True, check=True,
    ).stdout
    head_master = load_master_from_text(head_text)
    new_master = load_master(MASTER)
    # 只关心三码位且词序有变化的码组
    changed = {c for c in set(head_master) | set(new_master)
               if len(c) == 3 and head_master.get(c) != new_master.get(c)}
    print(f"主表三码位词序变化码组: {len(changed):,}")

    lines = ROOTLEX.read_text(encoding="utf-8").splitlines()
    # 根词表按码连续分组
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
    # 码序单调性校验
    codes_seq = [lines[g[0]].split("\t")[0] for g in groups]
    assert codes_seq == sorted(codes_seq), "根词表码序非单调，需人工检查"

    out_lines: list[str] = []
    rebuilt_groups = 0
    change_bodies: dict[str, list[str]] = {}   # 码 -> 重建后的组行
    fresh_bodies: dict[str, list[str]] = {}    # 全新码 -> 组行
    for code in sorted(changed):
        new_seq = new_master.get(code, [])
        if not new_seq:
            print(f"  警告: {code} 新主表无行，跳过（不应发生）")
            continue
        g = code_of.get(code)
        if g is None:
            fresh_bodies[code] = [f"{code}\t{w}\t{i + 1}\t20001"
                                  for i, w in enumerate(new_seq)]
            rebuilt_groups += 1
            continue
        # 重建组：主表行(文件序) + 根词表独有行(原顺序)
        old_rows = {lines[i].split("\t")[1]: lines[i] for i in g}
        old_set = set(head_master.get(code, []))
        new_set = set(new_seq)
        # 独有行 = 既不在旧主表（被挤词要删）也不在新主表的行（如全读音字）
        kept = [lines[i] for i in g
                if lines[i].split("\t")[1] not in old_set
                and lines[i].split("\t")[1] not in new_set]
        body = []
        for w in new_seq:
            if w in old_rows:
                parts = old_rows[w].split("\t")
                body.append("\t".join([code, w, "0", *parts[3:]]))
            else:
                body.append(f"{code}\t{w}\t0\t20001")
        body += kept
        body = ["\t".join([*row.split("\t")[:2], str(r + 1), *row.split("\t")[3:]])
                for r, row in enumerate(body)]
        change_bodies[code] = body
        rebuilt_groups += 1

    # 单遍合并输出：按原组顺序走，新码组插进其码序位置（无行号漂移）
    fresh_sorted = sorted(fresh_bodies)
    fi = 0
    for g in groups:
        code = lines[g[0]].split("\t")[0]
        while fi < len(fresh_sorted) and fresh_sorted[fi] < code:
            out_lines.extend(fresh_bodies[fresh_sorted[fi]])
            fi += 1
        if code in change_bodies:
            out_lines.extend(change_bodies[code])
        else:
            out_lines.extend(lines[i] for i in g)
    while fi < len(fresh_sorted):
        out_lines.extend(fresh_bodies[fresh_sorted[fi]])
        fi += 1

    # 非数据行（如有）原样保底追加不应发生——根词表纯数据行
    out_codes = [l.split("\t")[0] for l in out_lines if "\t" in l]
    assert out_codes == sorted(out_codes), "输出码序非单调，需人工检查"

    diff = sum(1 for a, b in zip(lines, out_lines) if a != b) + abs(len(lines) - len(out_lines))
    print(f"根词表: {len(lines):,} -> {len(out_lines):,} 行，改写码组 {rebuilt_groups:,}")
    if args.apply:
        ROOTLEX.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        print(f"已写回 {ROOTLEX}")
    else:
        print("干跑完成；加 --apply 写回")


def load_master_from_text(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for line in text.splitlines():
        m = MASTER_ROW.match(line)
        if m and CJK3.search(m.group(1)):
            out[m.group(2)].append(m.group(1))
    return out


if __name__ == "__main__":
    main()
