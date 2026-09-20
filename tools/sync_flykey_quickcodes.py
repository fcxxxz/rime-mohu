#!/usr/bin/env python3
"""生成 fixed 词典的飞键区块（全量闭包，纯派生数据）。

飞键区块是主区块的派生物：对主区块每个词条按 tools/fly_keys 的逐位
闭包生成变体行。块内按（变体码、主区块出现序）排序，因此同码组会
镜像主区块顺序。主区块和 ``tools/data/mohu_fixed_code_claims.tsv`` 是
唯一的手工数据来源；飞键区块不要手工编辑。

用法::

    uv run python tools/sync_flykey_quickcodes.py [--apply|--check] [--scheme zrm] dict.yaml ...

默认仅打印报告；--apply 写回；--check 在有偏差时以非零退出（供 CI）。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fly_keys import fly_variants, scheme_pairs  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

START = re.compile(r"^#\s*开始飞键\s*(\S+)\s*->\s*(\S+)")
END = re.compile(r"^#\s*结束飞键\s*$")
ENTRY = re.compile(r"^([^\t#]+)\t([a-z]+)(.*)$")

DEFAULT_DICTS = (
    "mohu_zrm_fixed.dict.yaml",
    "mohu_zrm_fixed_legacy.dict.yaml",
)


def check_path(path: Path) -> None:
    resolved = path.resolve()
    if resolved.parent != REPO_ROOT or resolved.suffixes[-2:] != [".dict", ".yaml"]:
        raise SystemExit(f"只允许仓库根目录下的 .dict.yaml: {path}")


def load_lines(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return lines


def find_blocks(lines: list[str]) -> list[dict[str, int | tuple[str, str]]]:
    """Find complete fly blocks and reject an unterminated generated region."""
    blocks: list[dict[str, int | tuple[str, str]]] = []
    index = 0
    while index < len(lines):
        match = START.match(lines[index])
        if not match:
            index += 1
            continue
        end = index + 1
        while end < len(lines) and not END.match(lines[end]):
            end += 1
        if end == len(lines):
            raise ValueError(f"unterminated fly-key block at line {index + 1}")
        blocks.append(
            {
                "start": index,
                "end": end,
                "pair": (match.group(1), match.group(2)),
            }
        )
        index = end + 1
    return blocks


def _entry(line: str) -> tuple[str, str] | None:
    match = ENTRY.match(line)
    if not match:
        return None
    return match.group(1), match.group(2)


def _variant_line(line: str, code: str, variant: str) -> str:
    """Replace only the code token, preserving weight and tab layout."""
    marker = "\t" + code
    position = line.find(marker)
    if position < 0:
        raise ValueError(f"cannot replace code {code!r} in row {line!r}")
    return line[: position + 1] + variant + line[position + 1 + len(code) :]


def build_expected(
    lines: list[str],
    blocks: list[dict[str, int | tuple[str, str]]],
    fly: dict[str, str],
) -> dict[tuple[str, str], list[str]]:
    """Build canonical block rows from non-fly rows.

    The second sort key is the source row's position, not the generated row's
    position. This keeps equal-code groups in the same order as the main table.
    """
    blocked: set[int] = set()
    for block in blocks:
        blocked.update(range(int(block["start"]), int(block["end"]) + 1))

    expected: dict[tuple[str, str], list[tuple[str, int, str]]] = {}
    seen: dict[tuple[str, str], set[tuple[str, str]]] = {}
    main_index: set[tuple[str, str]] = set()
    entries: list[tuple[int, str, str, str]] = []
    for index, line in enumerate(lines):
        if index in blocked:
            continue
        parsed = _entry(line)
        if parsed is None:
            continue
        word, code = parsed
        entries.append((index, word, code, line))
        main_index.add((word, code))

    for source_index, word, code, line in entries:
        for variant, pair in fly_variants(word, code, fly):
            if (word, variant) in main_index:
                continue
            if (word, variant) in seen.setdefault(pair, set()):
                continue
            seen[pair].add((word, variant))
            expected.setdefault(pair, []).append(
                (variant, source_index, _variant_line(line, code, variant))
            )

    ordered: dict[tuple[str, str], list[str]] = {}
    for pair, rows in expected.items():
        rows.sort(key=lambda row: (row[0], row[1]))
        ordered[pair] = [row[2] for row in rows]
    return ordered


def render_blocks(
    expected: dict[tuple[str, str], list[str]],
    fly: dict[str, str],
) -> list[str]:
    """Render every configured block in stable configuration order."""
    output: list[str] = []
    for index, (source, target) in enumerate(fly.items()):
        if index:
            output.append("")
        output.append(f"# 开始飞键 {source} -> {target}")
        output.extend(expected.get((source, target), []))
        output.append("# 结束飞键")
    return output


def rebuild(
    lines: list[str],
    blocks: list[dict[str, int | tuple[str, str]]],
    expected: dict[tuple[str, str], list[str]],
    fly: dict[str, str],
    report: list[str],
) -> list[str]:
    """Replace the complete derived region with canonical configured blocks."""
    generated = render_blocks(expected, fly)
    if blocks:
        first = min(int(block["start"]) for block in blocks)
        removed = sum(int(block["end"]) - int(block["start"]) + 1 for block in blocks)
        old_pairs = [block["pair"] for block in blocks]
        report.append(f"  重建 {len(blocks)} 个飞键块（移除 {removed} 行）")
        unknown = [pair for pair in old_pairs if pair not in fly.items()]
        for pair in unknown:
            report.append(f"  删除旧飞键块 {pair[0]}->{pair[1]}")
        return lines[:first] + generated + lines[max(int(block["end"]) for block in blocks) + 1 :]

    # A source dictionary without any blocks is valid during bootstrap. Append
    # the generated region rather than requiring a hand-written anchor.
    report.append(f"  新增 {len(fly)} 个飞键块")
    return lines + ([""] if lines and lines[-1] else []) + generated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="写回文件（默认仅报告）")
    parser.add_argument("--check", action="store_true", help="有偏差时以非零退出（供 CI）")
    parser.add_argument("--scheme", choices=["zrm", "flypy"], default="zrm")
    parser.add_argument("dicts", nargs="*")
    args = parser.parse_args()

    paths = [Path(path) for path in (args.dicts or DEFAULT_DICTS)]
    fly = scheme_pairs(args.scheme)
    drift = False
    for path in paths:
        check_path(path)
        lines = load_lines(path)
        blocks = find_blocks(lines)
        expected = build_expected(lines, blocks, fly)
        report: list[str] = []
        output = rebuild(lines, blocks, expected, fly, report)
        print(f"== {path.name}")
        for line in report:
            print(line)
        if output != lines:
            drift = True
            print("  状态: 与生成结果不一致" + ("（--apply 写回）" if args.apply else ""))
            if args.apply:
                path.write_text("\n".join(output) + "\n", encoding="utf-8")
        else:
            print("  状态: 一致")
    if args.check and drift:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
