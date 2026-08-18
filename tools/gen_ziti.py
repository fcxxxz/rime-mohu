#!/usr/bin/env python3
"""生成晴跟打（TypeSunny）用的魔虎字提文件。

打法口径（与方案默认设置一致，候选位置来自真实 librime 引擎）：

1. 有简出简：单字在固顶码表（mohu_*_fixed.dict.yaml 的「生成单字」）中
   拥有 1~3 键简码时，字提只给最短简码，记作 ``简码_``（下划线表示空格上屏）。
2. 四码让词：其余单字的音码+虎码前两码共四键。默认（动词）模式下四码时
   词组/智能组句优先，单字在首页的真实位置决定打法：
   - 首页第 1 位：``四码_``（四码后直接空格）
   - 首页第 2~5 位：``四码N``（四码后按数字 N 选重）
   - 首页之外：补第五键 ``o``（方案预编辑显示 °）强制顶出单字，即全码
     ``四码o``，仍非首选时记 ``四码oN``；再不行退到斜杠筛单字 ``四码/``；
     最终兜底保留纯全码 ``四码o``（多为繁体/生僻，需开全字集）。
3. 有简出全会让全（ijrq）：简码字打全码会让出首位，因此这类字一律给简码。

三步流水线（探针 tools/ziti_probe.c 负责与引擎交互）：

    clang tools/ziti_probe.c -I/opt/homebrew/opt/librime/include \\
        -L/opt/homebrew/opt/librime/lib -lrime \\
        -Wl,-rpath,/opt/homebrew/opt/librime/lib -o /tmp/mohu-ziti/probe
    uv run python tools/gen_ziti.py queries --variant zrm > /tmp/mohu-ziti/q.txt
    /tmp/mohu-ziti/probe <部署目录> mohu_zrm < /tmp/mohu-ziti/q.txt > /tmp/mohu-ziti/dump.txt
    uv run python tools/gen_ziti.py build --variant zrm --dump /tmp/mohu-ziti/dump.txt --out 魔虎.txt
    uv run python tools/gen_ziti.py verify-emit --file 魔虎.txt --count 2000 > /tmp/mohu-ziti/v.txt
    /tmp/mohu-ziti/probe <部署目录> mohu_zrm commit < /tmp/mohu-ziti/v.txt > /tmp/mohu-ziti/vout.txt
    uv run python tools/gen_ziti.py verify-check --file 魔虎.txt --result /tmp/mohu-ziti/vout.txt

部署目录准备（首次，zrm 为例）：

    mkdir -p /tmp/mohu-ziti/deploy
    cp *.yaml *.gram /tmp/mohu-ziti/deploy/ && cp -R lua opencc /tmp/mohu-ziti/deploy/
    printf 'patch:\\n  schema_list:\\n    - schema: mohu_zrm\\n    - schema: mohu_zrm_fixed_legacy\\n' \\
      > /tmp/mohu-ziti/deploy/default.custom.yaml
"""

import argparse
import random
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

from flypyify import flypyify1  # noqa: E402
from zrmify import zrmify1  # noqa: E402

PAGE = 5  # 方案默认 menu/page_size: 5，首页数字键 1~5


def parse_fixed(variant: str) -> dict[str, list[str]]:
    """固顶码表单字 -> 全部编码（含 stem），供「有简出简」取最短简码。"""
    path = REPO / f"mohu_{variant}_fixed.dict.yaml"
    codes: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        m = re.match(r"^([^\t]+)\t([a-z;]+)\t?([a-z]+)?", line)
        if not m:
            continue
        text, code, stem = m.group(1), m.group(2), m.group(3)
        if len(text) != 1 or ";" in code:
            continue  # 词与简快符号不进字提
        row = codes.setdefault(text, [])
        row.append(code)
        if stem:
            row.append(stem)
    return codes


def parse_chars(variant: str) -> dict[str, list[str]]:
    """chars 词典单字 -> 全码列表（yy;xx 去掉分号），按词频降序。"""
    path = REPO / f"mohu_{variant}.chars.dict.yaml"
    codes: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            continue
        m = re.match(r"^(\S+)\t([a-z]+);([a-z]+)\t(\d+)", line)
        if not m:
            continue
        char, yy, xx = m.group(1), m.group(2), m.group(3)
        codes.setdefault(char, []).append(yy + xx)
    return codes


def load_chaifen() -> dict[str, str]:
    """opencc/mohu_chaifen.txt: 字〔拆分 · 虎码〕 -> 字 -> 拆分。"""
    table: dict[str, str] = {}
    path = REPO / "opencc" / "mohu_chaifen.txt"
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.rstrip("\n").split("\t")
        if len(parts) != 2:
            continue
        zi, chai = parts
        m = re.match(r"^〔?(.*?)\s*·", chai)
        if m:
            table[zi] = m.group(1).replace("〔", "")
    return table


def load_readings() -> dict[str, list[str]]:
    """tools/data/chars.txt: 字 拼音 繁頻 簡頻 -> 字 -> 拼音列表。"""
    table: dict[str, list[str]] = {}
    path = REPO / "tools" / "data" / "chars.txt"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 2 and len(parts[0]) == 1:
            table.setdefault(parts[0], []).append(parts[1])
    return table


def load_char_sets(variant: str) -> tuple[dict[str, str], dict[str, str], dict[str, list[str]]]:
    """返回 (简码字->最短简码, 全码字->首选四码, chars 词典原表)。"""
    fixed = parse_fixed(variant)
    chars = parse_chars(variant)
    quick: dict[str, str] = {}
    full: dict[str, str] = {}
    for char in sorted(set(fixed) | set(chars), key=ord):
        short = [c for c in fixed.get(char, []) if len(c) < 4]
        if short:
            quick[char] = min(short, key=len)
        elif chars.get(char):
            full[char] = chars[char][0]
    return quick, full, chars


def page_position(candidates: list[str], char: str) -> int:
    """字符在首页候选中的 1 起始位置，找不到返回 0。"""
    for index, text in enumerate(candidates[:PAGE], start=1):
        if text == char:
            return index
    return 0


def suffix_for(position: int) -> str:
    return "_" if position == 1 else str(position)


def parse_dump(path: str) -> dict[str, list[str]]:
    dump: dict[str, list[str]] = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            parts = line.rstrip("\n").split("\t")
            if parts and parts[0]:
                dump[parts[0]] = parts[1:]
    return dump


def method_for(char: str, code: str, dump: dict[str, list[str]], stats: dict[str, int]) -> str | None:
    """按 四码 -> 全码o -> 斜杠 的优先级确定打法，找不到返回 None。"""
    pos = page_position(dump.get(code, []), char)
    if pos:
        stats["四码选重" if pos > 1 else "四码"] += 1
        return code + suffix_for(pos)
    pos = page_position(dump.get(code + "o", []), char)
    if pos:
        stats["全码o选重" if pos > 1 else "全码o"] += 1
        return code + "o" + suffix_for(pos)
    pos = page_position(dump.get(code + "/", []), char)
    if pos:
        stats["斜杠"] += 1
        return code + "/" + suffix_for(pos)
    stats["兜底"] += 1
    return code + "o"


def reading_sp(char: str, quick: dict[str, str], chars: dict[str, list[str]]) -> str:
    """取与打法对应的双拼两键（简码或首选全码的前缀）。"""
    if char in quick:
        code = quick[char]
        return code[:2] if len(code) >= 2 else code
    if chars.get(char):
        return chars[char][0][:2]
    return ""


def pick_reading(candidates: list[str], sp: str, to_sp) -> str:
    if not candidates:
        return ""
    if sp:
        for reading in candidates:
            try:
                if to_sp(reading) == sp:
                    return reading
            except Exception:
                continue
    return candidates[0]


def iter_output_lines(path: str):
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        char, rest = line.split("\t", 1)
        method = rest.split(" · ", 1)[0]
        yield char, method


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    queries = commands.add_parser("queries", help="输出待探测编码（四码/全码o/斜杠/简码）")
    queries.add_argument("--variant", choices=["zrm", "flypy"], required=True)

    build = commands.add_parser("build", help="由引擎探测结果组装字提文件")
    build.add_argument("--variant", choices=["zrm", "flypy"], required=True)
    build.add_argument("--dump", required=True, help="probe 输出的候选 dump")
    build.add_argument("--out", required=True, help="输出字提 txt 路径")

    verify_emit = commands.add_parser("verify-emit", help="抽样输出打法回放按键（_ 转空格）")
    verify_emit.add_argument("--file", required=True)
    verify_emit.add_argument("--count", type=int, default=2000)
    verify_emit.add_argument("--seed", type=int, default=20260818)

    verify_check = commands.add_parser("verify-check", help="比对回放上屏结果")
    verify_check.add_argument("--file", required=True)
    verify_check.add_argument("--result", required=True, help="probe commit 模式输出")

    args = parser.parse_args()

    if args.command == "queries":
        quick, full, _ = load_char_sets(args.variant)
        codes = set()
        for code in full.values():
            codes.update((code, code + "o", code + "/"))
        codes.update(quick.values())
        for code in sorted(codes):
            print(code)
        print(f"# {len(quick)} 简码字 + {len(full)} 全码字，共 {len(codes)} 条查询", file=sys.stderr)
        return

    if args.command == "build":
        variant = args.variant
        quick, full, chars = load_char_sets(variant)
        dump = parse_dump(args.dump)
        chaifen = load_chaifen()
        readings = load_readings()
        to_sp = zrmify1 if variant == "zrm" else flypyify1

        stats = {"简码": 0, "四码": 0, "四码选重": 0, "全码o": 0, "全码o选重": 0, "斜杠": 0, "兜底": 0}
        methods: dict[str, str] = {}
        for char in quick:
            # 简码理论上固顶首选；引擎结果缺席时（如繁体被字符集过滤）仍给简码
            pos = page_position(dump.get(quick[char], []), char)
            if pos != 1 and dump.get(quick[char]):
                print(f"warning: 简码 {quick[char]} 下 {char} 非首页首选", file=sys.stderr)
            methods[char] = quick[char] + "_"
            stats["简码"] += 1
        for char, code in full.items():
            method = method_for(char, code, dump, stats)
            if method is None:
                continue
            methods[char] = method

        lines = []
        for char in sorted(methods, key=ord):
            chai = chaifen.get(char) or char
            reading = pick_reading(readings.get(char, []), reading_sp(char, quick, chars), to_sp)
            lines.append(f"{char}\t{methods[char]} · {chai} · {reading}" if reading else f"{char}\t{methods[char]} · {chai}")

        header = [
            "# 魔虎字提（%s 组），由 rime-mohu tools/gen_ziti.py 生成" % ("自然码" if variant == "zrm" else "小鹤"),
            "# 打法后缀：_ 空格上屏；数字为四码/全码后的选重键；o 为全码顶字键（预编辑显示 °）；/ 为筛单字斜杠",
            "",
        ]
        Path(args.out).write_text("\n".join(header + lines) + "\n", encoding="utf-8")
        total = len(lines)
        print(f"{args.out}: {total} 字")
        for key, value in stats.items():
            print(f"  {key}: {value} ({value * 100 // max(total, 1)}%)")
        return

    if args.command == "verify-emit":
        # 只输出按键行（探针会把整行都当按键）；期望字由 verify-check 用相同种子重采样
        entries = list(iter_output_lines(args.file))
        rng = random.Random(args.seed)  # 固定种子：emit 与 check 必须抽到同一样本
        sample = rng.sample(entries, min(args.count, len(entries)))
        for _char, method in sample:
            print(method.replace("_", " "))
        return

    if args.command == "verify-check":
        entries = list(iter_output_lines(args.file))
        rng = random.Random(args.seed)
        sample = rng.sample(entries, min(args.count, len(entries)))
        failures = []
        checked = 0
        with open(args.result, encoding="utf-8") as handle:
            result_lines = [line.rstrip("\n").split("\t") for line in handle]
        for (char, method), parts in zip(sample, result_lines):
            if len(parts) != 2:
                continue
            checked += 1
            got = parts[1]
            if got != char:
                failures.append(f"{char!r} {method!r} -> {got!r}")
        print(f"verify: {checked - len(failures)}/{checked} 通过")
        for line in failures[:20]:
            print("  FAIL " + line)
        if failures:
            sys.exit(1)


if __name__ == "__main__":
    main()
