#!/usr/bin/env python3
"""custom_aux.py —— 自定义辅助码：用户表合并 + 形码全码推导 + 单字码表提取。

三种输入，同一产出（dict[字, AuxiliaryEntry]）：

1. 辅码表 TSV（--aux）：字<Tab>正常辅码<Tab>[14位]<Tab>[13位]，
   与 tools/data/tiger_aux.txt 同格式；码在前（辅码<Tab>字）也认，
   按列内容识别、不依赖列序。只列要改的字，缺字回退虎码底表。

2. 形码全码表 TSV（--shape-code）：字<Tab>完整编码[<Tab>根数]，
   任意字根形码（五笔/仓颉/郑码/自编）按魔虎取位规则推导辅码：
   正常位 = 主码 1–2 位，14 位兼容 = 1+4 位，13 位兼容 = 1+3 位。
   主码选择沿用虎码规则（源序首个四码，否则最长码首个）；多码字的
   其余编码作为别名候选返回给构建报告。码在前同样支持。

3. 单字成品码表（--char-table）：别的音形方案的整表，形如
   `双拼(2键)+辅码(1–2键)<Tab>字`（码在前）或字在前。前两键双拼
   剥掉、紧随其后的 2 键取为该字辅码；一/二键行（简码、裸音节）
   忽略，三键行（辅码首键渐进）仅做一致性校验。
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

TOOLS_ROOT = Path(__file__).resolve().parents[1]
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

from tiger_aux import (  # noqa: E402
    AuxiliaryEntry,
    load_auxiliary_tsv,
    select_primary_code,
    to_auxiliary_entry,
)

ROOT = TOOLS_ROOT.parent
DEFAULT_BASE_TSV = ROOT / "tools/data/tiger_aux.txt"

_CODE_RANGE = re.compile(r"[a-z]{2,6}")


class AuxError(ValueError):
    """辅码输入不合法；str(e) 直接面向用户。"""


# ---------------------------------------------------------------------------
# 通用解析：列序无关（兼容码在前/字在前）
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r"[a-z]+")
_DIGITS = re.compile(r"[0-9]{1,2}")


def _is_single_han(text: str) -> bool:
    """单个汉字（基本区/扩展A/兼容/扩展B+/々〆〇）。"""
    if len(text) != 1:
        return False
    cp = ord(text)
    return (
        0x3400 <= cp <= 0x4DBF
        or 0x4E00 <= cp <= 0x9FFF
        or 0xF900 <= cp <= 0xFAFF
        or 0x3005 <= cp <= 0x3007
        or cp >= 0x20000
    )


def _read_rows(path: Path) -> list[tuple[int, list[str]]]:
    """读 TSV 数据行：跳过空行与 # 注释；返回 (行号, 分列)。"""
    rows = []
    for line_no, raw in enumerate(
        Path(path).read_text(encoding="utf-8-sig").splitlines(), 1
    ):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        rows.append((line_no, line.split("\t")))
    return rows


def _split_char_and_codes(fields: list[str]) -> tuple[str, list[str]] | None:
    """把一行拆成 (字, 其余非空列)。

    汉字列按内容识别（恰好一列是单个汉字），不依赖列序：
    `字 bm` / `bm 字` / `bm xx yy 字` 都能拆出同一结果。
    没有唯一汉字列时返回 None（由调用方决定报错还是跳过）。
    """
    han_idx = [i for i, field in enumerate(fields) if _is_single_han(field)]
    if len(han_idx) != 1:
        return None
    char = fields[han_idx[0]]
    codes = [field for i, field in enumerate(fields) if i != han_idx[0] and field]
    return char, codes


@dataclass
class ShapeCodeSource:
    """形码全码表解析结果。

    codes: 字 -> 完整编码列表（源序，已去重）
    root_counts: 字 -> 根数（列提供时）
    aliases: 字 -> 非主码编码（推导时挑出主码后剩下的，报告用）
    """

    codes: dict[str, list[str]] = field(default_factory=dict)
    root_counts: dict[str, int] = field(default_factory=dict)
    aliases: dict[str, list[str]] = field(default_factory=dict)


def load_user_aux(path: Path) -> dict[str, AuxiliaryEntry]:
    """读用户辅码表 TSV（tiger_aux.txt 格式，码在前也认）。

    校验语义与 tiger_aux.load_auxiliary_tsv 一致：2–4 列、辅码为
    1–2 个小写字母、重复字报错；列序由内容识别。
    """
    path = Path(path)
    if not path.is_file():
        raise AuxError(f"找不到辅码表文件：{path}")
    result: dict[str, AuxiliaryEntry] = {}
    try:
        for line_no, fields in _read_rows(path):
            parsed = _split_char_and_codes(fields)
            if parsed is None or not parsed[1]:
                raise ValueError(
                    f"第 {line_no} 行：{'/'.join(fields)} ——"
                    f"需要恰好一列单个汉字 + 一列辅码"
                )
            char, codes = parsed
            if len(codes) > 3:
                raise ValueError(f"第 {line_no} 行：列数超过 4（{len(codes) + 1}）")
            normal, compat14, compat13 = (codes + ["", "", ""])[:3]
            for code in (normal, compat14, compat13):
                if code and (not _TOKEN.fullmatch(code) or len(code) > 2):
                    raise ValueError(
                        f"第 {line_no} 行：辅码 {code!r} 不是 1–2 个小写字母"
                    )
            if not normal:
                raise ValueError(f"第 {line_no} 行：缺少正常辅码列")
            if char in result:
                raise ValueError(f"第 {line_no} 行：字 {char} 重复出现")
            result[char] = AuxiliaryEntry(normal, compat14, compat13)
    except ValueError as exc:
        raise AuxError(
            f"{path.name} 不是合法辅码表（每行：字<Tab>正常辅码<Tab>[14位]"
            f"<Tab>[13位]，码在前也可以，辅码为 1–2 个小写字母）：{exc}"
        ) from None
    if not result:
        raise AuxError(f"{path.name} 没有任何有效数据行")
    return result


def load_shape_codes(path: Path) -> ShapeCodeSource:
    """读形码全码表：字<Tab>完整编码[<Tab>根数]，码在前也认；一字多码写多行。"""
    path = Path(path)
    if not path.is_file():
        raise AuxError(f"找不到形码全码表文件：{path}")
    source = ShapeCodeSource()
    bad: list[str] = []
    for line_no, fields in _read_rows(path):
        parsed = _split_char_and_codes(fields)
        if parsed is None:
            bad.append(f"第 {line_no} 行：{'/'.join(f[:12] for f in fields)} —— 找不到唯一的汉字列")
            continue
        char, rest = parsed
        code = next((f for f in rest if _CODE_RANGE.fullmatch(f)), None)
        if code is None:
            first = rest[0] if rest else ""
            if _TOKEN.fullmatch(first) and len(first) == 1:
                bad.append(
                    f"第 {line_no} 行：{char} 的编码 {first!r} 只有 1 个字母——"
                    f"正常辅码需要 2 位，请提供更长的编码或改用辅码表"
                )
            else:
                bad.append(f"第 {line_no} 行：{char} 的编码 {first!r} 不合法（2–6 个小写字母）")
            continue
        root_field = next((f for f in rest if _DIGITS.fullmatch(f)), None)
        leftovers = [f for f in rest if f is not code and f is not root_field]
        if leftovers:
            bad.append(
                f"第 {line_no} 行：{char} 的行内有多余列（{'/'.join(leftovers)}）——"
                f"应为 编码[<Tab>根数] 两列"
            )
            continue
        root: int | None = None
        if root_field is not None:
            root = int(root_field)
            if root < 1 or root > 99:
                bad.append(f"第 {line_no} 行：根数应在 1–99（{root}）")
                continue
        codes = source.codes.setdefault(char, [])
        if code not in codes:
            codes.append(code)
        if root is not None:
            known = source.root_counts.get(char)
            if known is not None and known != root:
                raise AuxError(
                    f"{path.name}: 字 {char} 的根数列前后不一致（{known} vs {root}）"
                )
            source.root_counts[char] = root
    if bad:
        raise AuxError(f"{path.name} 有 {len(bad)} 行无法解析：\n" + "\n".join(bad[:10]))
    if not source.codes:
        raise AuxError(f"{path.name} 没有任何有效数据行")
    return source


@dataclass
class CodedCharStats:
    """--char-table 提取统计（构建报告用）。"""

    rows: int = 0                    # 有效数据行（含被忽略的短码行）
    short_rows: int = 0              # ≤2 键行：简码/裸音节，不参与提取
    graded_rows: int = 0             # 3 键行：辅码首键渐进，仅校验
    graded_mismatch: int = 0         # 3 键行首键与已提取辅码不一致
    long_code_rows: int = 0          # >4 键行：辅码超出 2 键，截断取前 2
    skipped_non_han: int = 0         # 假名/符号/拉丁等非汉字行
    skipped_multi_code: int = 0      # 同行多码列，无法确认取哪个
    multi_aux_chars: int = 0         # 一字多辅码，取首现、其余进 aliases
    in_table_words: int = 0          # 码表内多字词条行（字词混排表）
    unknown_prefixes: set[str] = field(default_factory=set)  # ≥3 键行前缀不在识别布局内
    prefix_total: int = 0            # ≥3 键行数（命中率分母）


@dataclass
class CodedCharTable:
    """单字成品码表提取结果。"""

    aux: dict[str, AuxiliaryEntry] = field(default_factory=dict)
    aliases: dict[str, list[str]] = field(default_factory=dict)
    stats: CodedCharStats = field(default_factory=CodedCharStats)
    prefixes: set[str] = field(default_factory=set)  # ≥3 键行前 2 键集合（布局识别用）
    order: dict[tuple[str, str], int] = field(default_factory=dict)  # (字, 码/读音) -> 原表行序
    shortcodes: list[tuple[str, str]] = field(default_factory=list)  # 1/2/3 键行 (字, 码)，原表序
    words: list[str] = field(default_factory=list)  # 码表内多字词条（字词混排表），去重保序


_TABLE_CODE = re.compile(r"[a-z]{1,8}")


def load_coded_chars(path: Path) -> CodedCharTable:
    """读单字成品码表：前 2 键双拼 + 其后 2 键辅码，剥出 字→辅码。

    码在前/字在前均可（列序按内容识别）。非汉字行（假名、符号、
    拉丁字母等）跳过并计数——那是符号区，不参与辅码转换。
    """
    path = Path(path)
    if not path.is_file():
        raise AuxError(f"找不到单字码表文件：{path}")
    table = CodedCharTable()
    stats = table.stats
    rows = []
    seen_words: set[str] = set()
    for fields in (fields for _, fields in _read_rows(path)):
        parsed = _split_char_and_codes(fields)
        if parsed is None:
            # 字词混排表：无单字列但有 ≥2 字的纯汉字列 → 词条行
            word_cols = [
                f for f in fields
                if f and len(f) >= 2 and all(_is_single_han(ch) for ch in f)
            ]
            if word_cols:
                stats.in_table_words += 1
                if word_cols[0] not in seen_words:
                    seen_words.add(word_cols[0])
                    table.words.append(word_cols[0])
                continue
            stats.skipped_non_han += 1
            continue
        char, codes = parsed
        if len(codes) != 1 or not _TABLE_CODE.fullmatch(codes[0]):
            stats.skipped_multi_code += 1
            continue
        rows.append((char, codes[0]))
        stats.rows += 1
    # 第一遍：≥4 键行提取辅码（3 键渐进行按码序通常在前，先提全量才能校验）；
    # 1/2/3 键行同时收集为简码（原表序，供 --keep-shortcodes 物化）
    # 先记精确码位，再补同读音的回退序；其他读音的首现位置不能抢占本码位。
    for index, (char, code) in enumerate(rows):
        table.order.setdefault((char, code), index)
    for index, (char, code) in enumerate(rows):
        if len(code) >= 2:
            table.order.setdefault((char, code[:2]), index)
    for char, code in rows:
        if len(code) <= 2:
            stats.short_rows += 1
            table.shortcodes.append((char, code))
            continue
        stats.prefix_total += 1
        table.prefixes.add(code[:2])
        if len(code) == 3:
            table.shortcodes.append((char, code))
            continue
        if len(code) > 4:
            stats.long_code_rows += 1
        aux_code = code[2:4]
        if char in table.aux:
            if table.aux[char].normal != aux_code:
                stats.multi_aux_chars += 1
                table.aliases.setdefault(char, []).append(aux_code)
            continue
        table.aux[char] = AuxiliaryEntry(aux_code, "", "")
    # 第二遍：3 键行（辅码首键渐进）与已提取辅码做一致性校验
    for char, code in rows:
        if len(code) != 3:
            continue
        stats.graded_rows += 1
        known = table.aux.get(char)
        if known is not None and known.normal[0] != code[2]:
            stats.graded_mismatch += 1
    if not table.aux:
        raise AuxError(
            f"{path.name} 里没有提取到任何辅码（需要 ≥4 键的"
            f"「双拼(2键)+辅码」行）。若这是一张纯音码表，它不含辅码信息，"
            f"换双拼请用 --sp 布局文件，换辅码请提供辅码表或形码全码表。"
        )
    return table


@dataclass
class WordTableSource:
    """码表配套词表解析结果（--word-table）。"""

    words: list[str] = field(default_factory=list)  # 词形（≥2 字全汉字，去重保序）
    skipped: int = 0                                  # 没有词列的行（杂行/单字行）


def load_word_table(path: Path) -> WordTableSource:
    """读码表配套词表：每行 码<Tab>词 或 词<Tab>码。

    只取词形（≥2 个连续汉字的列），打法由派生方案按自身规则重新
    生成——与单字的处理一致（辅码来自码表，打法按本方案）。
    """
    path = Path(path)
    if not path.is_file():
        raise AuxError(f"找不到词表文件：{path}")
    source = WordTableSource()
    seen: set[str] = set()
    for _, fields in _read_rows(path):
        candidates = [
            f for f in fields
            if len(f) >= 2 and all(_is_single_han(ch) for ch in f)
        ]
        if not candidates:
            source.skipped += 1
            continue
        word = candidates[0]
        if word not in seen:
            seen.add(word)
            source.words.append(word)
    if not source.words:
        raise AuxError(
            f"{path.name} 里没有提取到任何多字词条（每行需要一列 ≥2 个汉字的词）。\n"
            f"若你的码表是纯单字表、词条在单独的词表文件里，请把那个文件传进来；\n"
            f"或把「词库来源」改回「使用魔虎词库」。"
        )
    return source


def derive_aux_from_shape_codes(
    source: ShapeCodeSource,
) -> dict[str, AuxiliaryEntry]:
    """按魔虎取位规则从形码全码推导辅码。

    默认不剪枝：通用形码每个码位都是字根，13/14 位都合法。虎码式的
    「大小码」体系（≤3 根丢 14、≤2 根丢 13）需要根数信息——由根数列
    或 synth_root_counts_by_length（--tiger-style-prune）提供。
    """
    result: dict[str, AuxiliaryEntry] = {}
    for char, codes in source.codes.items():
        primary = select_primary_code(codes)
        if primary is None:
            continue
        result[char] = to_auxiliary_entry(primary, source.root_counts.get(char))
        rest = [code for code in codes if code != primary]
        if rest:
            source.aliases[char] = rest
    return result


def synth_root_counts_by_length(source: ShapeCodeSource) -> None:
    """--tiger-style-prune 路径：无根数列时按码长合成根数。"""
    for char, codes in source.codes.items():
        if char in source.root_counts:
            continue
        primary = select_primary_code(codes)
        if primary is None:
            continue
        source.root_counts[char] = {2: 1, 3: 2}.get(len(primary), 4)


@dataclass
class MergeOutcome:
    """合并结果与统计（构建报告用）。"""

    table: dict[str, AuxiliaryEntry]
    user_chars: int = 0                 # 用户显式覆盖的字数
    fallback_chars: int = 0             # 引擎字集内回退虎码的字数
    fallback_samples: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # 引擎字集内无任何辅码的字


def merge_aux(
    user: dict[str, AuxiliaryEntry],
    required_chars: list[str],
    base: dict[str, AuxiliaryEntry] | None = None,
) -> MergeOutcome:
    """用户辅码覆盖虎码底表；统计引擎字集的覆盖情况。

    required_chars 是引擎字集（chars.txt 简频>0 的字）；缺字回退虎码，
    虎码也缺的列入 missing（正常应为空——虎码表覆盖全字集）。
    """
    if base is None:
        base = load_auxiliary_tsv(DEFAULT_BASE_TSV)
    merged = dict(base)
    for char, entry in user.items():
        merged[char] = entry
    outcome = MergeOutcome(table=merged)
    for char in required_chars:
        if char in user:
            outcome.user_chars += 1
        elif char in base:
            outcome.fallback_chars += 1
            if len(outcome.fallback_samples) < 12:
                outcome.fallback_samples.append(char)
        else:
            outcome.missing.append(char)
    return outcome


def zrmdb_lines(table: dict[str, AuxiliaryEntry]) -> str:
    """按 lua/zrmdb.txt 格式渲染（字<Tab>码1 码2 …）。"""
    lines = [
        f"{char}\t{' '.join(entry.codes())}"
        for char, entry in table.items()
    ]
    return "\n".join(lines) + "\n"
