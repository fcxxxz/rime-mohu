#!/usr/bin/env python3
"""一次性导入：把简单鹤三字简词按「每槽总量≤3 竞速」规则并入主表。

规则（2026-10-12 与用户确认的 B 方案）：
- 码不照抄简单鹤，用 mohu_zrm.words.dict.yaml 的双拼逐字重新生成声母简码；
- 每个三码位上「现有三字简词 + 新词」按整句词频竞速（词频相同现有优先），
  取前 3 名，同码行序即名次序；
- 被挤出的现有三字简码行删除（整句库仍可达），备份写入 research/；
- 新行插入三字简词块（最后一个飞键块之后、符号码之前），块内按码排序；
- 主块中零星历史三字行：参与竞速，被挤则删，幸存则原地不动；
- 三字声母简码无完整音节结构，不参与飞键派生（与现状一致）。

用法：
    uv run python tools/import_jdhe_3words.py            # 干跑，只出报告
    uv run python tools/import_jdhe_3words.py --apply    # 写回 mohu_zrm.dict.yaml
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
JD = REPO / "research/_downloads/rime-JDhe"
MASTER = REPO / "mohu_zrm.dict.yaml"
WORDS = REPO / "mohu_zrm.words.dict.yaml"
DISPLACED_OUT = REPO / "research/jdhe_3word_displaced_20261012.tsv"

CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0002a6df〇〻]")
DATA = re.compile(r"^([^\t#]+)\t([a-z]+)$")
FLY_END = re.compile(r"^#\s*结束飞键\s*$")
SYMBOL_MARK = "# 符号码"
TOP_N = 3


def iter_dict_rows(path: Path):
    started = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not started:
            if line.strip() == "...":
                started = True
            continue
        m = DATA.match(line)
        if m:
            yield m.group(1), m.group(2)


def load_sentence_data():
    """words.dict -> (三字词声母键重生成函数, 词频表).

    words.dict 行格式为 text<Tab>code<Tab>weight，code 形如
    ``ai;yw qy;ha``（每字「双拼;虎形」，空格分隔），与主表 TSV 不同，
    这里按列宽松解析。
    """
    char_key: dict[str, str] = {}
    word_keys: dict[str, list[str]] = {}
    weight: dict[str, int] = {}
    started = False
    for line in WORDS.read_text(encoding="utf-8").splitlines():
        if not started:
            if line.strip() == "...":
                started = True
            continue
        if not line or line.startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) < 2:
            continue
        text, code = cols[0], cols[1]
        if not CJK.search(text):
            continue
        wt = 0
        if len(cols) >= 3:
            try:
                wt = int(cols[2])
            except ValueError:
                wt = 0
        if len(text) == 1 and ";" in code:
            char_key.setdefault(text, code.split(";")[0][:1])
        elif len(text) == 3 and " " in code:
            parts = code.split()
            if len(parts) == 3 and all(";" in p for p in parts):
                word_keys.setdefault(text, [p.split(";")[0][:1] for p in parts])
                if wt > weight.get(text, 0):
                    weight[text] = wt

    def regen(word: str) -> str | None:
        if word in word_keys:
            return "".join(word_keys[word])
        keys = [char_key.get(ch) for ch in word]
        if all(keys):
            return "".join(keys)
        return None

    return regen, weight


def find_block_bounds(lines: list[str]) -> tuple[int, int]:
    """返回 (最后一个飞键块结束行号, 三字简词块数据起点, 符号码注释行号)."""
    last_fly_end = -1
    for idx, line in enumerate(lines):
        if FLY_END.match(line):
            last_fly_end = idx
        if line.startswith(SYMBOL_MARK):
            return last_fly_end, idx
    raise SystemExit("找不到飞键块或符号码标记")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="写回主表（默认干跑）")
    args = ap.parse_args()

    regen, weight = load_sentence_data()

    # ---- 简单鹤三字词单（只取词形，码丢弃）----
    jdhe_order: list[str] = []
    seen = set()
    for text, code in iter_dict_rows(JD / "cn_dicts/jde/jde.dict.yaml"):
        if len(text) == 3 and len(code) == 3 and CJK.search(text) and text not in seen:
            seen.add(text)
            jdhe_order.append(text)

    # ---- 主表现状 ----
    lines = MASTER.read_text(encoding="utf-8").splitlines()
    last_fly_end, symbol_idx = find_block_bounds(lines)
    existing_rows: dict[int, tuple[str, str]] = {}  # line_idx -> (word, code)
    for idx, line in enumerate(lines[:symbol_idx]):
        m = DATA.match(line)
        if m:
            existing_rows[idx] = (m.group(1), m.group(2))

    def is_3word(word: str) -> bool:
        return len(word) == 3 and CJK.search(word)

    existing_by_word: dict[str, set[str]] = defaultdict(set)
    for word, code in existing_rows.values():
        if is_3word(word) and len(code) == 3:
            existing_by_word[word].add(code)

    # ---- 槽位竞速 ----
    slot: dict[str, list[dict]] = defaultdict(list)
    for idx, (word, code) in existing_rows.items():
        if is_3word(word) and len(code) == 3:
            in_block = last_fly_end < idx < symbol_idx
            slot[code].append(
                {"word": word, "wt": weight.get(word, 0), "new": 0,
                 "idx": idx, "in_block": in_block, "line": lines[idx]}
            )
    new_total = 0
    skipped_dup = skipped_pinyin = 0
    jdhe_rank = {w: i for i, w in enumerate(jdhe_order)}
    for word in jdhe_order:
        code = regen(word)
        if code is None:
            skipped_pinyin += 1
            continue
        if code in existing_by_word.get(word, ()):
            skipped_dup += 1
            continue
        slot[code].append(
            {"word": word, "wt": weight.get(word, 0), "new": 1,
             "idx": jdhe_rank[word], "in_block": None, "line": None}
        )
        new_total += 1

    keep: dict[str, list[dict]] = {}
    displaced: list[dict] = []
    dropped_new = 0
    for code, entries in slot.items():
        entries.sort(key=lambda e: (-e["wt"], e["new"], e["idx"]))
        keep[code] = entries[:TOP_N]
        for e in entries[TOP_N:]:
            if e["new"]:
                dropped_new += 1
            else:
                displaced.append(e)
    displaced_idx = {e["idx"] for e in displaced}

    # ---- 组装三字简词块 ----
    block_data_idx = [idx for idx in existing_rows
                      if last_fly_end < idx < symbol_idx]
    # 块内现有数据行（含零星非三字行，保持原位）
    block_rows: list[tuple[int, str, str]] = [
        (idx, existing_rows[idx][0], existing_rows[idx][1]) for idx in block_data_idx]
    for i in range(1, len(block_rows)):
        assert block_rows[i - 1][2] <= block_rows[i][2], \
            f"三字简词块第 {block_rows[i][0] + 1} 行码序错乱"

    # 同码索引连续性校验（仅对有变化的码）
    code_positions: dict[str, list[int]] = defaultdict(list)
    for pos, (_idx, _w, c) in enumerate(block_rows):
        code_positions[c].append(pos)

    # 需要重排/插入的码
    touched_codes = {c for c, entries in keep.items()
                     if any(e["new"] for e in entries)}
    for c in touched_codes & set(code_positions):
        positions = code_positions[c]
        assert positions == list(range(positions[0],
                                       positions[0] + len(positions))), \
            f"码 {c} 在块内不连续: {positions}"

    # 生成每个有新增码的最终行序列（块内现有幸存行原文 + 新行，按名次混排）
    final_by_code: dict[str, list[str]] = {}
    for code, entries in keep.items():
        if not any(e["new"] for e in entries):
            continue
        merged = []
        for e in entries:
            if e["new"]:
                merged.append(f"{e['word']}\t{code}")
            elif e["in_block"]:
                merged.append(e["line"])
            # 主块历史行物理上不在块内，跳过（名次靠行序近似）
        final_by_code[code] = merged

    # 遍历块数据行，输出新块
    out_block: list[str] = []
    pos = 0
    inserted_codes: set[str] = set()
    while pos < len(block_rows):
        _idx, _w, code = block_rows[pos]
        run_end = pos
        while run_end < len(block_rows) and block_rows[run_end][2] == code:
            run_end += 1
        if code in final_by_code:
            # 同码但非竞速的占位行（如 PDF/PPT 等 ASCII 词条）不参与竞速，保留在前
            competing = {e["word"] for e in slot[code]}
            for p in range(pos, run_end):
                if block_rows[p][1] not in competing:
                    out_block.append(lines[block_rows[p][0]])
            out_block.extend(final_by_code[code])
            inserted_codes.add(code)
        else:
            # 无新增的码：保留幸存原文，剔除被挤行
            out_block.extend(lines[block_rows[p][0]] for p in range(pos, run_end)
                             if block_rows[p][0] not in displaced_idx)
        pos = run_end
    # 新码（块内无现有行）按码序插入：先收齐再整体重插
    fresh_codes = sorted(c for c in final_by_code if c not in inserted_codes)
    if fresh_codes:
        rebuilt: list[str] = []
        fi = 0
        for line in out_block:
            cur = line.split("\t")[1] if "\t" in line else "zzzzz"
            while fi < len(fresh_codes) and fresh_codes[fi] < cur:
                rebuilt.extend(final_by_code[fresh_codes[fi]])
                fi += 1
            rebuilt.append(line)
        while fi < len(fresh_codes):
            rebuilt.extend(final_by_code[fresh_codes[fi]])
            fi += 1
        out_block = rebuilt

    # ---- 全文件重组：删被挤行 + 换块 ----
    displaced_lines = [e["line"] for e in displaced]
    new_lines_out: list[str] = []
    for idx, line in enumerate(lines):
        if idx == block_data_idx[0]:
            new_lines_out.extend(out_block)
        if idx in displaced_idx:
            continue
        if block_data_idx[0] <= idx <= block_data_idx[-1]:
            continue  # 块内旧行（含首尾）已由 out_block 取代
        new_lines_out.append(line)

    kept_new = sum(1 for entries in keep.values() for e in entries if e["new"])
    block_old = len(block_data_idx)
    # 主块历史三字行里被挤的数量
    displaced_main = sum(1 for e in displaced if not e["in_block"])
    displaced_zero_wt = sum(1 for e in displaced if e["wt"] == 0)
    order_changed = sum(
        1 for code in touched_codes & set(code_positions)
        if [e["word"] for e in keep[code] if not e["new"] and e["in_block"]] !=
           [existing_rows[block_rows[p][0]][0] for p in code_positions[code]
            if block_rows[p][0] not in displaced_idx]
    )
    print(f"简单鹤三字词单 {len(jdhe_order):,}：已有同码跳过 {skipped_dup:,}，"
          f"无拼音跳过 {skipped_pinyin}，可竞速新词 {new_total:,}")
    print(f"每槽竞速取前 {TOP_N}：新词入选 {kept_new:,}，新词落选 {dropped_new:,}")
    print(f"现有行被挤删除 {len(displaced):,}（主块历史行 {displaced_main}，"
          f"零词频 {displaced_zero_wt}）")
    print(f"三字简词块：{block_old:,} 行 -> {len(out_block):,} 行；"
          f"重排序码组 {order_changed} 个")
    print(f"主表总行数：{len(lines):,} -> {len(new_lines_out):,}")

    if args.apply:
        MASTER.write_text("\n".join(new_lines_out) + "\n", encoding="utf-8")
        DISPLACED_OUT.write_text(
            "# 被简单鹤合并挤出的三字简码行（词仍可全码/整句输入）；恢复=按码序贴回\n"
            "# word\tcode\tweight\n" +
            "".join(f"{e['word']}\t"
                    f"{existing_rows[e['idx']][1]}\t{e['wt']}\n"
                    for e in sorted(displaced, key=lambda e: (existing_rows[e['idx']][1], e['idx']))),
            encoding="utf-8",
        )
        print(f"已写回 {MASTER}；被挤行备份 {DISPLACED_OUT}")
    else:
        print("干跑完成；加 --apply 写回")


if __name__ == "__main__":
    main()
