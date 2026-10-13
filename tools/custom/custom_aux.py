#!/usr/bin/env python3
"""custom_aux.py —— 自定义辅助码：用户表合并 + 形码全码推导。

两种输入，同一产出（dict[字, AuxiliaryEntry]）：

1. 辅码表 TSV（--aux）：字<Tab>正常辅码<Tab>[14位]<Tab>[13位]，
   与 tools/data/tiger_aux.txt 同格式（直接复用 tiger_aux.load_auxiliary_tsv）。
   只列要改的字，缺字回退虎码底表。

2. 形码全码表 TSV（--shape-code）：字<Tab>完整编码[<Tab>根数]，
   任意字根形码（五笔/仓颉/郑码/自编）按魔虎取位规则推导辅码：
   正常位 = 主码 1–2 位，14 位兼容 = 1+4 位，13 位兼容 = 1+3 位。
   主码选择沿用虎码规则（源序首个四码，否则最长码首个）；多码字的
   其余编码作为别名候选返回给构建报告。
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
    """读用户辅码表 TSV（tiger_aux.txt 格式）；报错信息面向用户。"""
    path = Path(path)
    if not path.is_file():
        raise AuxError(f"找不到辅码表文件：{path}")
    try:
        return load_auxiliary_tsv(path)
    except ValueError as exc:
        raise AuxError(
            f"{path.name} 不是合法辅码表（每行：字<Tab>正常辅码<Tab>[14位]"
            f"<Tab>[13位]，辅码为 1–2 个小写字母）：{exc}"
        ) from None


def load_shape_codes(path: Path) -> ShapeCodeSource:
    """读形码全码表：字<Tab>完整编码[<Tab>根数]；一字多码写多行。"""
    path = Path(path)
    if not path.is_file():
        raise AuxError(f"找不到形码全码表文件：{path}")
    source = ShapeCodeSource()
    bad: list[str] = []
    for line_no, raw in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), 1
    ):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) < 2 or len(fields[0]) != 1:
            bad.append(f"第 {line_no} 行：{line[:30]}")
            continue
        char, code = fields[0], fields[1].strip()
        root: int | None = None
        if len(fields) > 2 and fields[2].strip():
            try:
                root = int(fields[2].strip())
            except ValueError:
                bad.append(f"第 {line_no} 行：根数列不是整数（{fields[2].strip()}）")
                continue
            if root < 1 or root > 99:
                bad.append(f"第 {line_no} 行：根数应在 1–99（{root}）")
                continue
        if not _CODE_RANGE.fullmatch(code):
            if len(code) == 1:
                bad.append(
                    f"第 {line_no} 行：{char} 的编码 {code!r} 只有 1 个字母——"
                    f"正常辅码需要 2 位，请提供更长的编码或改用辅码表"
                )
            else:
                bad.append(f"第 {line_no} 行：{char} 的编码 {code!r} 不合法（2–6 个小写字母）")
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
