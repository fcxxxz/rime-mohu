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
    printf 'patch:\\n  schema_list:\\n    - schema: mohu_zrm\\n' \\
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


def load_char_sets(variant: str) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """返回 (字->固顶简码列表（含备用码）, 字->chars 词典四码变体列表)。

    固顶码表中被挤的简码字会带备用码（如 万=mof/wjf、丁=dya/vga），
    全部保留供探测。chars 词典中同字也可能有多条辅助码变体（不同拆分）。
    """
    fixed = parse_fixed(variant)
    chars = parse_chars(variant)
    fixed_all: dict[str, list[str]] = {}
    full: dict[str, list[str]] = {}
    for char in sorted(set(fixed) | set(chars), key=ord):
        short = [c for c in fixed.get(char, []) if len(c) < 4]
        if short:
            seen: list[str] = []
            for code in short:
                if code not in seen:
                    seen.append(code)
            fixed_all[char] = seen
        if chars.get(char):
            seen = []
            for code in chars[char]:
                if code not in seen:
                    seen.append(code)
            full[char] = seen
    return fixed_all, full


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


def replay_keys(method: str) -> str:
    """打法转回放按键（_ -> 空格）。"""
    return method.replace("_", " ")


def ranked_methods_for(
    char: str,
    fixed_codes: list[str],
    full_codes: list[str],
    dump: dict[str, list[str]],
) -> list[str]:
    """按方案打法口径为单字生成降序候选打法列表。

    口径（四码让词：被词占位时补第五键 o 或 /，不用四码数字选重）：
    1. 有简出简：简码（含被挤后的备用简码）引擎第 1 位 -> ``简码_``；
       简码被更常见候选压住 -> ``简码N``（三键处的数字选重，魔然惯例）。
    2. 四码第 1 位 -> ``四码_``。
    3. 四码被词占位 -> 补 ``o`` 全码顶字 -> ``四码o_``；仍不行补 ``/``
       筛单字 -> ``四码/_``（/ 形式只列单字，必为整码上屏）。
    4. 以上都拿不到第 1 位 -> 纯 ``四码o`` 兜底（打全码后菜单自选），
       多为被常用字过滤的繁体/生僻。
    同级取词典序靠前的变体（多音/多拆分中权重最高的读法）。
    """
    ranked: list[tuple[tuple[int, int], int, str]] = []  # (排序键, 统计档, 打法)
    for order, code in enumerate(fixed_codes):
        pos = page_position(dump.get(code, []), char)
        if pos:
            ranked.append(((0 if pos == 1 else 1, order), 0 if pos == 1 else 1, code + suffix_for(pos)))
    for order, code in enumerate(full_codes):
        pos = page_position(dump.get(code, []), char)
        if pos == 1:
            ranked.append(((2, order), 2, code + "_"))
        pos = page_position(dump.get(code + "o", []), char)
        if pos == 1:
            ranked.append(((3, order), 3, code + "o_"))
        pos = page_position(dump.get(code + "/", []), char)
        if pos == 1:
            ranked.append(((4, order), 4, code + "/_"))
    ranked.sort(key=lambda item: item[0])
    return [(grade, method) for _key, grade, method in ranked]


GRADE_NAMES = ["简码", "简码选重", "四码", "全码o", "斜杠"]


def method_for(
    char: str,
    fixed_codes: list[str],
    full_codes: list[str],
    dump: dict[str, list[str]],
    stats: dict[str, int],
    bad_keys: set[str],
) -> str:
    for grade, method in ranked_methods_for(char, fixed_codes, full_codes, dump):
        if replay_keys(method) not in bad_keys:
            stats[GRADE_NAMES[grade]] += 1
            return method
    # 兜底：引擎探测不可见（繁体/生僻）或所有形式都拿不到第 1 位。
    # 有简码的仍按「有简出简」给最短简码（简码固顶不受常用字过滤影响，
    # 已被全量回放证实）；简码也回放失败时退纯全码（打全码后菜单自选）。
    stats["兜底"] += 1
    if fixed_codes:
        for code in sorted(fixed_codes, key=len):
            if replay_keys(code + "_") not in bad_keys:
                return code + "_"
    if full_codes:
        return full_codes[0] + "o"
    return min(fixed_codes, key=len) if fixed_codes else ""


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
    build.add_argument("--bad-keys", help="回放失败的按键串文件（每行一个），用于数字打法降级")

    digits = commands.add_parser("digits-emit", help="输出全部含数字选重的打法按键，供全量回放")
    digits.add_argument("--file", required=True)

    verify_emit = commands.add_parser("verify-emit", help="抽样输出打法回放按键（_ 转空格）")
    verify_emit.add_argument("--file", required=True)
    verify_emit.add_argument("--count", type=int, default=2000)
    verify_emit.add_argument("--seed", type=int, default=20260818)
    verify_emit.add_argument("--only-real", action="store_true", help="只抽可回放验证的打法（排除兜底全码）")

    verify_check = commands.add_parser("verify-check", help="比对回放上屏结果")
    verify_check.add_argument("--file", required=True)
    verify_check.add_argument("--result", required=True, help="probe commit 模式输出")
    verify_check.add_argument("--count", type=int, default=2000)
    verify_check.add_argument("--seed", type=int, default=20260818)
    verify_check.add_argument("--only-real", action="store_true", help="与 verify-emit 的抽样口径一致")

    args = parser.parse_args()

    if args.command == "queries":
        fixed_all, full = load_char_sets(args.variant)
        codes = set()
        for variants in full.values():
            for code in variants:
                codes.update((code, code + "o", code + "/"))
        for variants in fixed_all.values():
            codes.update(variants)
        for code in sorted(codes):
            print(code)
        print(f"# {len(fixed_all)} 简码字 + {len(full)} 全码字，共 {len(codes)} 条查询", file=sys.stderr)
        return

    if args.command == "digits-emit":
        for _char, method in iter_output_lines(args.file):
            if method[-1:].isdigit():
                print(replay_keys(method))
        return

    if args.command == "build":
        variant = args.variant
        fixed_all, full = load_char_sets(variant)
        dump = parse_dump(args.dump)
        chaifen = load_chaifen()
        readings = load_readings()
        to_sp = zrmify1 if variant == "zrm" else flypyify1
        bad_keys: set[str] = set()
        if args.bad_keys:
            bad_keys = {
                line.rstrip("\n")
                for line in Path(args.bad_keys).read_text(encoding="utf-8").splitlines()
                if line.strip()
            }

        stats = {"简码": 0, "简码选重": 0, "四码": 0, "全码o": 0, "斜杠": 0, "兜底": 0}
        methods: dict[str, str] = {}
        for char in sorted(set(fixed_all) | set(full), key=ord):
            methods[char] = method_for(
                char, fixed_all.get(char, []), full.get(char, []), dump, stats, bad_keys)

        # 有简出简：引擎看不到的简码字（如繁体被常用字过滤）也按设计给最短简码
        for char, codes in fixed_all.items():
            if char not in methods or not methods[char]:
                methods[char] = min(codes, key=len) + "_"

        lines = []
        for char in sorted(methods, key=ord):
            chai = chaifen.get(char) or char
            codes = fixed_all.get(char) or full.get(char) or [""]
            reading = pick_reading(readings.get(char, []), codes[0][:2], to_sp)
            lines.append(f"{char}\t{methods[char]} · {chai} · {reading}" if reading else f"{char}\t{methods[char]} · {chai}")

        header = [
            "# 魔虎字提（%s 组），由 rime-mohu tools/gen_ziti.py 生成" % ("自然码" if variant == "zrm" else "小鹤"),
            "# 打法后缀：_ 空格上屏；o 为全码顶字键（预编辑显示 °）；/ 为筛单字斜杠；数字仅为简码处的选重键",
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
        if args.only_real:
            entries = [(c, m) for c, m in entries if not (m.endswith("o") and len(m) == 5)]
        rng = random.Random(args.seed)  # 固定种子：emit 与 check 必须抽到同一样本
        sample = rng.sample(entries, min(args.count, len(entries)))
        for _char, method in sample:
            print(method.replace("_", " "))
        return

    if args.command == "verify-check":
        entries = list(iter_output_lines(args.file))
        if args.only_real:
            entries = [(c, m) for c, m in entries if not (m.endswith("o") and len(m) == 5)]
        rng = random.Random(args.seed)
        sample = rng.sample(entries, min(args.count, len(entries)))
        failures = []
        checked = 0
        with open(args.result, encoding="utf-8") as handle:
            result_lines = [line.rstrip("\n").split("\t") for line in handle]
        for (char, method), parts in zip(sample, result_lines):
            if len(parts) != 2:
                continue
            # 兜底类（纯四码+o，5 键无后缀）多为常用字过滤隐藏的繁体/生僻，
            # 默认模式下引擎不可见，无法回放验证，跳过。
            if method.endswith("o") and len(method) == 5:
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
