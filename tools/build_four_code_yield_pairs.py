#!/usr/bin/env python3
"""Generate the shipped four-code yield pair tables from dev rank data.

Consumes the dev word-rank table (tools/build_two_char_word_rank.py output)
plus real collision probes: per scheme, the four-code-only fixed character
list (``<scheme>_only4_detail.tsv``) and the corresponding smart candidate
streams (``<scheme>_dump.txt``) produced by the engine probe runs. A word
displaces a character when it is the first rank-table word in the stream and
its rank is below 4x the character's Tiger rank.

One table is emitted per scheme family (zrm / flypy), shipped via lua/: every
line is a real collision observed in that scheme, so users edit their own
file without guessing whether a line applies to them. Each line lists a word
and every character it may precede. Deleting a character restores that
character to first choice; deleting a line disables the word entirely.
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RATIO = 4

SCHEMES = {
    "zrm": ("自然码（含 mohu_zrm / mohu_zrm）", "lua/four_code_yield_pairs_zrm.txt"),
    "flypy": ("小鹤（含 mohu_flypy / mohu_flypy）", "lua/four_code_yield_pairs_flypy.txt"),
}


def header(scheme: str) -> list[str]:
    label = SCHEMES[scheme][0]
    return [
        f"# 魔虎四码字词避让表（{label}）",
        "# 词 <TAB> 被顶的字（多个字以空格分隔）；每行均为该方案实际发生的让位。",
        "# 输入四码时，本表中的二字词排在行内所列单字之前，被顶单字仍保次选。",
        "# 编辑后重新部署生效：删字=该字恢复首选；删行=该词不再顶字；增行=新增让位。",
        "# 由 tools/build_four_code_yield_pairs.py 生成，来源与规则见该脚本说明。",
    ]


def load_ranks(path: Path) -> dict[str, int]:
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
            ranks[char] = index
    return ranks


def load_details(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def load_dump(path: Path) -> dict[str, list[str]]:
    dump: dict[str, list[str]] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        parts = raw.rstrip("\r\n").split("\t")
        dump[parts[0]] = [value for value in parts[1:] if value]
    return dump


def collect_pairs(ranks: dict[str, int], details: Path, dump: Path) -> set[tuple[str, str]]:
    streams = load_dump(dump)
    pairs: set[tuple[str, str]] = set()
    for row in load_details(details):
        char_rank = int(row["rank"])
        word = next(
            (text for text in streams.get(row["code"], []) if text in ranks),
            None,
        )
        if word is not None and ranks[word] < RATIO * char_rank:
            pairs.add((word, row["char"]))
    return pairs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ranks",
        type=Path,
        default=ROOT / "tools/data/two_char_word_rank.txt",
    )
    parser.add_argument("--work", type=Path, default=Path("/tmp/mohu-yield"))
    args = parser.parse_args()

    ranks = load_ranks(args.ranks)
    char_ranks = load_char_ranks(ROOT / "lua/tiger_rank.txt")

    for scheme, (_, output_name) in SCHEMES.items():
        pairs = collect_pairs(
            ranks,
            args.work / f"{scheme}_only4_detail.tsv",
            args.work / f"{scheme}_dump.txt",
        )
        grouped: dict[str, list[str]] = defaultdict(list)
        for word, char in pairs:
            grouped[word].append(char)

        lines = []
        for word in sorted(grouped, key=lambda value: (ranks[value], value)):
            chars = sorted(grouped[word], key=lambda value: (char_ranks.get(value, 10**9), value))
            lines.append(f"{word}\t{' '.join(chars)}")

        output = ROOT / output_name
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(header(scheme)) + "\n")
            handle.write("\n".join(lines) + "\n")

        print(f"{scheme}: pairs {len(pairs)} words {len(grouped)} -> {output}")


if __name__ == "__main__":
    main()
