#!/usr/bin/env python3
"""Build the natural-code and Flypy native sentence lexicons.

The checked-in Tiger lexicon is the source of sentence/text coverage.  This
tool keeps that coverage identical for both schemes, converting only the
syllable portion that can be identified from the character dictionary.  Fly-key
substitutions are scheme-specific and closed transitively per output; the
single source of truth is ``tools/data/mohu_fly_keys.tsv`` (loaded via
``tools/fly_keys.py``): the natural-code set (wz->wk, xq->xo, qx->qo,
ju->jv, yu->yv) and the Flypy set (xq->xo for xiu, qx->qo for qia, ju->jv,
yu->yv -- note qx is qia in Flypy, not the Natural Code qie).
Source rows that are themselves natural-code fly variants are reverted to
their base codes when building the Flypy output instead of leaking through as
dead codes (the qie rows under qo thus re-emerge as plain qp codes).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import fly_keys
import flypyify
import zrmify

ROOT = TOOLS.parent
# 飞键替换对单一事实源（tools/fly_keys.py），与 mohu_defs.yaml /fly* 同步：
# 自然码 wz->wk、xq->xo、qx->qo、ju->jv、yu->yv；小鹤 qx 在其音系下是
# qia（qie=qp、wei=ww 不设飞键）。此处仅保留别名供本模块历史命名引用。
FLY_ZRM = fly_keys.FLY_ZRM
FLY_FLYPY = fly_keys.FLY_FLYPY
# 仅用于源表（自然码形态）读音简频回溯，源行恒为自然码。
FLY_INVERSE = {target: source for source, target in FLY_ZRM.items()}
# 行结构（含可选第 6 列规范音节头）：源表行恒为 4/5 列，第 6 列只在
# 构建产物中出现。飞键换头变体行（ju→jv、yu→yv、xq→xo、qx→qo、
# wz→wk）指回源读音头，供引擎把同一读音的多码形合并后再归一
# log P(读音|字)——否则同一读音被当成两个读音、总频翻倍，主读音先验
# 被白白罚 ln2（句 ju/jv 均挂 254300 时先验恰为 -0.693）。非变体行为空。
ROW_KEY = tuple[str, str, str, str, str, str]
ROW_OUT = ROW_KEY


def load_rows(path: Path,
              reading_frequencies: dict[tuple[str, str], int] | None = None
              ) -> list[ROW_KEY]:
    rows: list[ROW_KEY] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) < 2 or not fields[0] or not fields[1]:
            raise ValueError(f"invalid lexicon row at {path}:{line_no}")
        rank = fields[2] if len(fields) > 2 and fields[2] else "1"
        freq = fields[3] if len(fields) > 3 and fields[3] else "20001"
        reading = fields[4] if len(fields) > 4 and fields[4] else ""
        canonical = fields[5] if len(fields) > 5 and fields[5] else ""
        try:
            int(rank)
            int(freq)
            if reading:
                int(reading)
        except ValueError as exc:
            raise ValueError(f"invalid rank/freq at {path}:{line_no}") from exc
        if canonical and not re.fullmatch(r"[a-z]{2}", canonical):
            raise ValueError(f"invalid reading canon at {path}:{line_no}: {canonical!r}")
        if not reading and reading_frequencies is not None:
            reading = _reading_frequency(fields[0], fields[1], reading_frequencies)
        rows.append((fields[0], fields[1], rank, freq, reading, canonical))
    return rows


def _reading_frequency(code: str, text: str,
                       frequencies: dict[tuple[str, str], int]) -> str:
    """单字行按 (字, 规范音节) 查读音简频；飞键变体行回溯到原音节。"""
    if len(text) != 1 or len(code) < 2:
        return ""
    syllable = code[:2]
    canonical = FLY_INVERSE.get(syllable, syllable)
    value = frequencies.get((text, canonical))
    if value is None:
        value = frequencies.get((text, syllable))
    return "" if value is None else str(value)


def load_reading_frequencies(path: Path) -> dict[tuple[str, str], int]:
    """读单字词典，返回 (单字, 自然码双拼音节) -> 读音条件简频。

    词典权重列与 chars.txt 的读音简频同源（如「万」mo=1 / wj=1201402），
    同一 (字, 音节) 多辅码行取最大值去重。
    """
    result: dict[tuple[str, str], int] = {}
    in_body = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() == "...":
            in_body = True
            continue
        if not in_body or not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) < 2 or not fields[0] or len(fields[0]) != 1:
            continue
        weight = fields[2].strip() if len(fields) > 2 else ""
        value = 0
        if weight:
            try:
                value = int(weight)
            except ValueError:
                value = int(float(weight))
        for token in fields[1].split():
            syllable = token.split(";", 1)[0][:2]
            if not _is_natural_syllable(syllable):
                continue
            key = (fields[0], syllable)
            if value > result.get(key, -1):
                result[key] = value
    return result


def load_character_syllables(path: Path) -> dict[str, set[str]]:
    """Read natural-code syllables from a Rime character dictionary."""
    result: dict[str, set[str]] = {}
    in_body = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() == "...":
            in_body = True
            continue
        if not in_body or not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) < 2 or not fields[0]:
            continue
        syllables = result.setdefault(fields[0], set())
        for token in fields[1].split():
            code = token.split(";", 1)[0]
            candidate = code[:2]
            if _is_natural_syllable(candidate):
                syllables.add(candidate)
        if not syllables:
            result.pop(fields[0], None)
    return result


def _is_natural_syllable(code: str) -> bool:
    """Accept reversible two-letter Natural Code spellings, excluding auxiliaries."""
    if code == "pp" or not re.fullmatch(r"[a-z]{2}", code):
        return False
    try:
        pinyin = zrmify.unzrmify1(code)
        return zrmify.zrmify1(pinyin) == code
    except (AssertionError, IndexError, ValueError, TypeError):
        return False


def _convert_syllable(code: str) -> str:
    return flypyify.flypyify1(zrmify.unzrmify1(code))


def _canonical_syllables(code: str, text: str,
                         character_syllables: dict[str, set[str]] | None) -> tuple[str, ...] | None:
    if not text or len(code) < 2 * len(text):
        return None
    base = code[: 2 * len(text)]
    if not re.fullmatch(r"[a-z]+", base):
        return None
    syllables = tuple(base[i:i + 2] for i in range(0, len(base), 2))
    if character_syllables is None:
        return syllables
    if len(text) == 1:
        allowed = character_syllables.get(text)
        return syllables if allowed and syllables[0] in allowed else None
    for char, syllable in zip(text, syllables):
        allowed = character_syllables.get(char)
        if not allowed or syllable not in allowed:
            return None
    return syllables


def _convert_code(code: str, text: str,
                 character_syllables: dict[str, set[str]] | None) -> str:
    syllables = _canonical_syllables(code, text, character_syllables)
    if syllables is None:
        return code
    converted = "".join(_convert_syllable(syllable) for syllable in syllables)
    return converted + code[2 * len(text):]


def _fly_closure(code: str, text: str, fly: dict[str, str]) -> set[str]:
    """Return all positional variants for a native full-syllable code."""
    return fly_keys.full_syllable_variants(code, text, fly)


def _reverted_zrm_fly_code(code: str, text: str,
                           character_syllables: dict[str, set[str]] | None,
                           source_index: set[tuple[str, str]]) -> str | None:
    """若源码表某行是自然码飞键变体行，返回还原后的基础码；否则返回 None。

    全音节行：把音节前缀中的飞键目标音节还原为源码后能通过音节校验
    （如 anwk→anwz、wkxo→wzxq，含 qofqo/ 带 o、/ 派生后缀的家族行）。
    简码行（编码长度不足无音节结构）：码首两字母是飞键目标，且还原后
    在源表存在同词行（如 qo 取消→qx 取消）；wc 完成、aw 安慰等真首字母
    简码不满足条件，原样透传。
    """
    if not text or _canonical_syllables(code, text, character_syllables) is not None:
        return None
    if len(code) < 2 * len(text):
        head = code[:2]
        if head in FLY_INVERSE:
            reverted = FLY_INVERSE[head] + code[2:]
            return reverted if (reverted, text) in source_index else None
        return None
    base = code[: 2 * len(text)]
    if not re.fullmatch(r"[a-z]+", base):
        return None
    reverted = "".join(
        FLY_INVERSE.get(base[i:i + 2], base[i:i + 2])
        for i in range(0, len(base), 2)
    ) + code[2 * len(text):]
    return reverted if _canonical_syllables(
        reverted, text, character_syllables) is not None else None


def build_rows(rows: list[ROW_KEY], scheme: str,
               character_syllables: dict[str, set[str]] | None = None) -> list[ROW_OUT]:
    if scheme not in {"zrm", "flypy"}:
        raise ValueError(f"unsupported scheme: {scheme}")
    fly = FLY_ZRM if scheme == "zrm" else FLY_FLYPY
    fly_inverse = {target: source for source, target in fly.items()}
    source_index = {(code, text) for code, text, *_ in rows}

    def emit(code: str, text: str, rank: str, freq: str, reading: str) -> ROW_OUT:
        # 规范头是 (码, 方案) 的确定函数：读音先验只消费单字行，规范头
        # 只对换头变体的单字行有意义。
        canonical = ""
        if len(text) == 1 and len(code) >= 2:
            head = code[:2]
            source_head = fly_inverse.get(head)
            if source_head is not None and source_head != head:
                canonical = source_head
        return (code, text, rank, freq, reading, canonical)

    output: set[ROW_OUT] = set()
    for source_code, text, rank, freq, reading, _source_canonical in rows:
        code = source_code
        if scheme == "flypy":
            reverted = _reverted_zrm_fly_code(
                source_code, text, character_syllables, source_index)
            if reverted is not None:
                code = reverted
        base_code = code if scheme == "zrm" else _convert_code(code, text, character_syllables)
        output.add(emit(base_code, text, rank, freq, reading))
        for variant in _fly_closure(base_code, text, fly):
            output.add(emit(variant, text, rank, freq, reading))
    return sorted(output, key=lambda row: (row[0], int(row[2]), row[1], int(row[3]), row[4]))


def validate_output_path(path: Path, root: Path = ROOT) -> None:
    resolved = path.resolve()
    if resolved.is_absolute() and root.resolve() not in resolved.parents:
        raise ValueError(f"output path must be inside repository: {path}")


def write_rows(path: Path, rows: list[ROW_OUT], root: Path = ROOT) -> None:
    validate_output_path(path, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing5: dict[tuple[str, str, str, str, str], bool] = {}
    existing6: dict[tuple[str, str, str, str, str], bool] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            fields = line.split("\t")
            if len(fields) >= 4 and fields[0] != "# code":
                key = tuple((fields + [""] * 5)[:5])
                existing5[key] = len(fields) >= 5
                existing6[key] = len(fields) >= 6
    rendered = []
    for row in rows:
        fields = list(row[:4])
        # 第 5 列（读音简频）保持既有幂等规则；第 6 列（规范音节头）是
        # (码, 方案) 的确定函数，只在非空时输出，出现即说明该行是换头
        # 变体。规范头非空时第 5 列必须保留占位（空值也是一列）。
        keep5 = bool(row[4]) or bool(row[5]) or existing5.get(tuple(row[:5]), False)
        keep6 = bool(row[5]) or existing6.get(tuple(row[:5]), False)
        if keep5:
            fields.append(row[4])
            if keep6:
                fields.append(row[5])
        rendered.append("\t".join(fields))
    text = "# code\ttext\trank\tfreq_rank\treading_freq\treading_canon\n" + \
        "\n".join(rendered) + "\n"
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path,
                        default=ROOT / "tiger_sentence_native/mohu_tiger.lexicon.txt")
    parser.add_argument("--chars-dict", type=Path, default=ROOT / "mohu_zrm.chars.dict.yaml")
    parser.add_argument("--zrm-output", type=Path,
                        default=ROOT / "tiger_sentence_native/data/zrm/mohu_zrm.lexicon.txt")
    parser.add_argument("--flypy-output", type=Path,
                        default=ROOT / "tiger_sentence_native/data/flypy/mohu_flypy.lexicon.txt")
    args = parser.parse_args()
    reading_frequencies = load_reading_frequencies(args.chars_dict)
    rows = load_rows(args.source, reading_frequencies)
    syllables = load_character_syllables(args.chars_dict)
    zrm_rows = build_rows(rows, "zrm", syllables)
    fly_rows = build_rows(rows, "flypy", syllables)
    write_rows(args.zrm_output, zrm_rows)
    write_rows(args.flypy_output, fly_rows)
    single = sum(1 for row in rows if len(row[1]) == 1)
    matched = sum(1 for row in rows if len(row[1]) == 1 and row[4])
    print(f"source rows: {len(rows)}; zrm rows: {len(zrm_rows)}; flypy rows: {len(fly_rows)}")
    print(f"reading_freq coverage: {matched}/{single} single-char rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
