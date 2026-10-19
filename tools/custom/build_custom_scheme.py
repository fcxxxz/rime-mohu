#!/usr/bin/env python3
r"""build_custom_scheme.py —— 一条命令构建自定义双拼/辅助码的魔虎派生方案。

用法（三种输入可单用或组合）：

    # 换双拼（辅码保持虎码）
    uv run tools/custom/build_custom_scheme.py --sp 我的布局.yaml
    # 只换辅码（双拼保持自然码）：直接给辅码表（码在前也认）
    uv run tools/custom/build_custom_scheme.py --aux 我的辅码.tsv
    # 只换辅码：扔一张形码全码表，按魔虎取位规则推导（12 正常 + 13/14 兼容；码在前也认）
    uv run tools/custom/build_custom_scheme.py --shape-code 我的形码.tsv
    # 别家的「双拼+辅码」单字成品码表（码在前/字在前均可）：剥双拼提辅码，
    # 双拼布局自动识别（未识别出时可用 --sp 指定）
    uv run tools/custom/build_custom_scheme.py --char-table 我的码表.txt

    # 组合 + 方案名 + 顺手打 zip
    uv run tools/custom/build_custom_scheme.py --sp 我的布局.yaml \
        --shape-code 我的形码.tsv --name myzrm --zip

    # 导出虎码辅码全量模板（想整体替换辅码时从这里起步）
    uv run tools/custom/build_custom_scheme.py --emit-aux-template

产物：custom-dist/mohu_<name>/（可直接放进 Rime 用户目录，与已装的
魔虎 zrm/flypy 共存）+ 构建报告.txt。

布局文件格式见 tools/custom/examples/；辅码/形码表格式见同目录 sample。
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

CUSTOM_DIR = Path(__file__).resolve().parent
TOOLS_ROOT = CUSTOM_DIR.parent
ROOT = TOOLS_ROOT.parent
for path in (str(TOOLS_ROOT), str(CUSTOM_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from custom_aux import (  # noqa: E402
    AuxError,
    CodedCharTable,
    MergeOutcome,
    ShapeCodeSource,
    derive_aux_from_shape_codes,
    load_coded_chars,
    load_shape_codes,
    load_user_aux,
    merge_aux,
    synth_root_counts_by_length,
    zrmdb_lines,
)
from sp_layout import (  # noqa: E402
    LayoutError,
    RESERVED_NAMES,
    READING_NORMALIZATION,
    SpLayout,
    all_pinyins,
    load_layout,
    load_layout_by_name,
)

import zrmify  # noqa: E402
from build_flypy_assets import replace_dictionary_name  # noqa: E402
from build_mohu_lexicons import (  # noqa: E402
    build_rows,
    convert_two_char_short_code,
    load_character_syllables,
    load_inject_words,
    load_reading_frequencies,
    load_rows,
    write_rows,
)
from build_sentence_dictionary import merge_dictionary, source_paths  # noqa: E402
from build_split_dist import (  # noqa: E402
    COMMON_ROOT_PATHS,
    copy_path,
    recreate_destination,
    remove_schema_name,
)
from gen_chars import generate_chars  # noqa: E402
from gen_word2_codes import extract_codes, write_asset  # noqa: E402
from long_word_completion import bare, master_long_word_rows  # noqa: E402
from tiger_aux import AuxiliaryEntry, load_auxiliary_tsv  # noqa: E402
from utils import freq_simp_table  # noqa: E402

LAYOUTS_DIR = CUSTOM_DIR / "layouts"
TIGER_AUX_TSV = ROOT / "tools/data/tiger_aux.txt"
ZRM_CHARS_DICT = ROOT / "tools/data/lexicon_sources/zrm/mohu_zrm.chars.dict.yaml"
TIGER_LEXICON = ROOT / "tiger_sentence_native/mohu_tiger.lexicon.txt"
SOURCE_SUFFIXES = ("chars", "base", "words", "tencent", "moe", "classics", "wanxiang")
FLY_BLOCK_START = re.compile(r"^#\s*开始飞键")
FLY_BLOCK_END = re.compile(r"^#\s*结束飞键")
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*$")


class BuildError(ValueError):
    """构建失败；str(e) 直接面向用户。"""


@dataclass
class BuildStats:
    master_rows: int = 0
    master_unconverted: list[str] = field(default_factory=list)
    master_pushed_back: list[str] = field(default_factory=list)
    unique_pushed: Counter = field(default_factory=Counter)  # 简码无重：按码长计数
    source_rows: Counter = field(default_factory=Counter)
    source_anomalies: list[str] = field(default_factory=list)
    long_word_rows: int = 0
    lexicon_rows: int = 0
    chars_rows: int = 0


# ---------------------------------------------------------------------------
# 输入解析
# ---------------------------------------------------------------------------

def resolve_layout(value: str) -> SpLayout:
    candidate = Path(value)
    if candidate.suffix in (".yaml", ".yml") or candidate.exists():
        try:
            return load_layout(candidate)
        except LayoutError as exc:
            raise BuildError(str(exc)) from None
    try:
        return load_layout_by_name(value)
    except LayoutError as exc:
        raise BuildError(str(exc)) from None


LAYOUT_PRESETS = ("zrm", "flypy", "jiajia")


def detect_layout_for_table(table: CodedCharTable) -> tuple[str, float]:
    """对 ≥3 键行的前 2 键算各内置布局的音节命中率，取最高。

    只用于剥前缀的归属判断与报告；辅码提取本身不依赖前缀合法性
    （一律取第 3–4 键），识别错了也不破坏数据，只影响提示。
    """
    best_name, best_ratio = "", 0.0
    if not table.prefixes:
        return best_name, best_ratio
    for preset in LAYOUT_PRESETS:
        layout = load_layout_by_name(preset)
        syllables = {layout.to_code(py) for py in all_pinyins()}
        hits = sum(1 for prefix in table.prefixes if prefix in syllables)
        ratio = hits / len(table.prefixes)
        if ratio > best_ratio:
            best_name, best_ratio = preset, ratio
    return best_name, best_ratio


def load_engine_charset() -> list[str]:
    """引擎字集：tools/data/chars.txt 简频>0 的字（去重保序）。"""
    seen: dict[str, None] = {}
    for line in (ROOT / "tools/data/chars.txt").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) < 4 or len(fields[0]) != 1:
            continue
        try:
            simp = int(fields[3])
        except ValueError:
            continue
        if simp > 0:
            seen.setdefault(fields[0], None)
    return list(seen)


# ---------------------------------------------------------------------------
# 转换核心
# ---------------------------------------------------------------------------

def make_converter(layout: SpLayout):
    def converter(zrm_syllable: str) -> str:
        if zrm_syllable == "pp":
            return zrm_syllable
        return layout.zrm_to_code(zrm_syllable)

    return converter


def remap_tail2(old: str, char: str, base: dict, merged: dict) -> str:
    b, m = base.get(char), merged.get(char)
    if b and m:
        if old == b.normal:
            return m.normal
        if b.compat14 and old == b.compat14:
            return m.compat14 or m.normal
        if b.compat13 and old == b.compat13:
            return m.compat13 or m.normal
    return old  # 无理尾：保留（布局无关的私人打法），报告计入


def remap_tail1(old: str, char: str, base: dict, merged: dict) -> str:
    b, m = base.get(char), merged.get(char)
    if b and m and old == b.normal[:1]:
        return m.normal[:1]
    return old


def convert_master_code(
    word: str, code: str, converter, base_aux: dict, merged_aux: dict,
    character_syllables: dict | None = None,
) -> tuple[str, bool]:
    """主码表单行转换；返回 (新码, 是否已识别)。

    分支语义与 tools/build_flypy_assets.convert_table_code 对齐（o 引导
    符号区只豁免单字、二字/三字词按声声韵结构转换），并扩展：词末辅码
    与单字辅码尾按辅码表重映射（默认虎码时为恒等），尾缀 o// 全码标记
    先剥离后回贴（二字及以上才视作标记——单字三码的尾字母是首辅码）。
    未识别形态原样保留并计入报告。
    """
    if not re.fullmatch(r"[a-z;/]+", code) or ";" in code:
        return code, True  # 符号/特殊区：与双拼无关
    work, marker = code, ""
    if len(word) >= 2 and len(code) == 2 * len(word) + 1 and code[-1] in "o/":
        body = code[:-1]
        try:
            probe = "".join(converter(body[i:i + 2]) for i in range(0, len(body), 2))
            if probe:
                marker, work = code[-1], body
        except (LayoutError, ValueError, IndexError):
            pass
    w, n = len(word), len(work)
    converted = True
    if w == 1:
        if n == 1 or work[0] == "o":
            pass  # 声母简码 / o 引导符号区
        elif n == 2:
            work = converter(work)
        elif n == 3:
            work = converter(work[:2]) + remap_tail1(work[2], word, base_aux, merged_aux)
        elif n == 4:
            work = converter(work[:2]) + remap_tail2(work[2:], word, base_aux, merged_aux)
        else:
            converted = False
    elif w == 2:
        if n <= 2:
            pass  # 声声简码
        elif n == 3:
            # 二字词三码按词内读音判型（双拼+声母 / 声母+双拼），与
            # build_flypy_assets.convert_table_code 同口径；判不了走旧兜底。
            typed = convert_two_char_short_code(
                work, word, character_syllables, converter)
            if typed is not None:
                work = typed
            else:
                work = converter(work[:2]) + work[2]
        elif n == 4:
            work = converter(work[:2]) + converter(work[2:])
        elif n == 5:
            work = (converter(work[:2]) + converter(work[2:4])
                    + remap_tail1(work[4], word[1], base_aux, merged_aux))
        else:
            converted = False
    elif w == 3:
        if n <= 3:
            pass
        elif n == 4:
            work = work[:2] + converter(work[2:])
        elif n == 5:
            work = (work[:2] + converter(work[2:4])
                    + remap_tail1(work[4], word[2], base_aux, merged_aux))
        else:
            converted = False
    else:
        if n <= 4:
            pass  # 多字词声母码（声声声韵/声声声声）
        else:
            converted = False
    return work + marker, converted


def convert_master(text: str, scheme: str, converter, base_aux, merged_aux, stats: BuildStats,
                   character_syllables: dict | None = None,
                   unique_shortcode: bool = False,
                   char_order: dict[str, int] | None = None) -> str:
    text = replace_dictionary_name(text, f"mohu_{scheme}")
    lines = []
    in_body = False
    fly_drop = False
    header_rewrite = {
        "# 【主码表｜": f"# 【{scheme} 派生码表｜自动生成，请勿直接编辑】\n"
                       f"# 来源：mohu_zrm.dict.yaml；构建：uv run tools/custom/build_custom_scheme.py。\n"
                       f"# 编码由自然码主码表按布局 {scheme} 转换；同码顺序保持。\n",
    }
    remapped_short: set[tuple[str, str]] = set()
    for raw in text.splitlines(keepends=True):
        for prefix, replacement in header_rewrite.items():
            if raw.startswith(prefix):
                raw = replacement
        if raw.startswith("# 小鹤码表与发布用整句词库"):
            raw = "# 修改请编辑自然码主码表后重新构建本方案。\n"
        elif raw.startswith("# 维护后运行：make check-code-table"):
            raw = "# 自然码源文件维护后重新运行本构建。\n"
        if FLY_BLOCK_START.match(raw):
            fly_drop = True
            continue
        if FLY_BLOCK_END.match(raw):
            fly_drop = False
            continue
        if fly_drop:
            continue
        if raw.strip() == "...":
            in_body = True
        if in_body and not raw.startswith("#") and "\t" in raw:
            fields = raw.rstrip("\n").split("\t")
            if len(fields) >= 2 and re.fullmatch(r"[a-z;/]+", fields[1]):
                stats.master_rows += 1
                original = fields[1]
                try:
                    new_code, converted = convert_master_code(
                        fields[0], original, converter, base_aux, merged_aux,
                        character_syllables)
                except (LayoutError, ValueError, IndexError):
                    new_code, converted = original, False
                fields[1] = new_code
                if not converted:
                    stats.master_unconverted.append(fields[0] + "\t" + new_code)
                if len(fields[0]) == 1 and len(new_code) == 3 and new_code != original:
                    remapped_short.add((fields[0], new_code))
                raw = "\t".join(fields) + ("\n" if raw.endswith("\n") else "")
        lines.append(raw)

    if char_order:
        lines = _reorder_rows_by_source_order(lines, char_order)
    return "".join(_dedupe_shortcode_rows(
        lines, remapped_short, stats, unique_shortcode))


def _reorder_rows_by_source_order(
    lines: list[str], char_order: dict[str, int],
) -> list[str]:
    """把同 code 单字行的内容按原码表字序重新分配到原行位置。

    主表行序继承自然码源（虎码辅码序），且转换辅码后行的物理位置不动
    ——同一新 code 的字行会分散在主表各区（三码区、兼容物化区等），
    同码候选序与三简撞位的保留者因此都是虎码序而非源码表序。此函数
    收集全文件同 code 的单字行，把行内容（含权重列）按 char_order
    排序后依次填回该组原有的行位置集合：位置结构零变化（不破坏主表
    分区约定），仅组内内容置换。源表没有的字排组尾（稳定，多个保持
    原相对序）；词行与非数据行不动。
    """
    def parse(raw: str):
        fields = raw.rstrip("\n").split("\t")
        if (len(fields) >= 2 and len(fields[0]) == 1
                and re.fullmatch(r"[a-z;/]+", fields[1])):
            return fields[0]
        return None

    groups: dict[str, list[int]] = {}
    for idx, raw in enumerate(lines):
        char = parse(raw)
        if char is not None:
            groups.setdefault(lines[idx].rstrip("\n").split("\t")[1], []).append(idx)
    new_lines = list(lines)
    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        contents = sorted(
            (lines[i] for i in idxs),
            key=lambda raw: char_order.get(parse(raw) or "", 1 << 30),
        )
        for idx, content in zip(idxs, contents):
            new_lines[idx] = content
    return new_lines


def _dedupe_shortcode_rows(
    lines: list[str], remapped_short: set[tuple[str, str]],
    stats: BuildStats, unique_shortcode: bool,
) -> list[str]:
    """简码位整理：处理主表行列表里的单字短码行。

    默认（三码设计不变量）：一个三码码位只有一个简码字，撞位的字往
    后推（删行，字仍可由全码/辅码渐进打出）。换辅码后重映射可能把
    多个三简行推到同一码位；按主表行序保留最前的行，其余删除并计
    入报告。例外：撞码双方都是未被改写的原生行（zrm 既有的同码特
    例，如 bjn/lix）时保持原状不动。

    --unique-shortcode（简码无重）：一/二/三简全部去重，原生同码特例
    也推；被推字必须还有更长的码行，否则保留该行兜底（否则字会从
    码表消失）。
    """
    # 单字行的最长码（未删全集上统计），作为删行保底依据
    char_max_len: dict[str, int] = {}
    for raw in lines:
        fields = raw.rstrip("\n").split("\t")
        if (len(fields) >= 2 and len(fields[0]) == 1
                and re.fullmatch(r"[a-z;/]+", fields[1])):
            char_max_len[fields[0]] = max(char_max_len.get(fields[0], 0), len(fields[1]))
    seen_short: dict[str, bool] = {}  # 码位 -> 首占行是否被改写过
    resolved: list[str] = []
    for raw in lines:
        fields = raw.rstrip("\n").split("\t")
        if not (len(fields) >= 2 and len(fields[0]) == 1
                and re.fullmatch(r"[a-z;/]+", fields[1])):
            resolved.append(raw)
            continue
        code = fields[1]
        n = len(code)
        if unique_shortcode and n <= 3:
            if code in seen_short and char_max_len.get(fields[0], 0) > n:
                stats.unique_pushed[n] += 1
                continue
            seen_short[code] = True
            resolved.append(raw)
            continue
        if n == 3:
            key = (fields[0], code)
            if code in seen_short:
                if key in remapped_short or seen_short[code]:
                    stats.master_pushed_back.append(fields[0] + "\t" + code)
                    continue
            seen_short[code] = key in remapped_short
        resolved.append(raw)
    return resolved


def rewrite_word_code(word: str, code: str, converter, merged_aux: dict) -> str | None:
    """重写词库源的 sp;aux 串；失败返回 None（整行保留原样）。

    与生产端 convert_spelling_code 一致：无 ';' 的 token（拉丁字母等）
    原样透传、不消耗汉字；带 ';' 的 token 逐个换双拼与辅码。
    """
    chars = iter(bare(word))
    tokens = code.split()
    if not tokens:
        return None
    rebuilt = []
    for token in tokens:
        if ";" not in token:
            rebuilt.append(token)
            continue
        char = next(chars, None)
        if char is None:
            return None
        entry = merged_aux.get(char)
        if entry is None:
            return None
        try:
            rebuilt.append(f"{converter(token.split(';', 1)[0])};{entry.normal}")
        except (LayoutError, ValueError, IndexError):
            return None
    if next(chars, None) is not None:
        return None  # 汉字数多于带码 token 数：整行保留
    return " ".join(rebuilt)


def convert_lexicon_source(
    text: str, target_name: str, code_index: int, converter, merged_aux: dict, stats: BuildStats, label: str
) -> str:
    """转换 base/tencent/moe/classics/wanxiang/words 之一；返回完整文件内容。"""
    text = replace_dictionary_name(text, target_name)
    lines = []
    in_body = False
    for raw in text.splitlines(keepends=True):
        if raw.strip() == "...":
            in_body = True
            lines.append(raw)
            continue
        if not in_body or raw.startswith("#") or "\t" not in raw:
            lines.append(raw)
            continue
        fields = raw.rstrip("\n").split("\t")
        if len(fields) > code_index and fields[code_index].strip():
            code = fields[code_index].strip()
            if ";" in code:
                new_code = rewrite_word_code(fields[0], code, converter, merged_aux)
                if new_code is None:
                    stats.source_anomalies.append(f"[{label}] {raw.rstrip()}")
                else:
                    fields[code_index] = new_code
                    stats.source_rows[label] += 1
                    raw = "\t".join(fields) + ("\n" if raw.endswith("\n") else "")
        lines.append(raw)
    return "".join(lines)


def source_code_index(text: str) -> int:
    """从 frontmatter 的 columns 推断码列下标（默认 text/code/weight → 1）。"""
    header = text.split("...", 1)[0]
    match = re.search(r"(?m)^columns:\s*\n((?:\s*-\s*\w+\s*\n)+)", header)
    if match:
        columns = [line.strip().lstrip("-").strip() for line in match.group(1).splitlines()]
        if "code" in columns:
            return columns.index("code")
    return 1


def remap_lexicon_row(code: str, text: str, base_aux: dict, merged_aux: dict) -> str:
    """重写源 lexicon 单字行的虎码辅码尾为自定义辅码。

    源行（自然码形态）的合法尾形集合是 generate_code 派生形：
    ''、X（三码）、XX（全码）、XXo、XX/、X/；其余（一简行、无理行、
    多字词带尾行）原样保留。辅码尾按虎码底表匹配（normal/14/13 与
    首辅位）后换成新表对应位；匹配不上的无理尾保留。
    """
    if len(text) != 1 or len(code) < 2:
        return code
    head, tail = code[:2], code[2:]
    if tail == "":
        return code
    aux, marker = "", ""
    if len(tail) == 1:
        aux, marker = tail, ""
    elif len(tail) == 2:
        if tail[1] == "/":
            aux, marker = tail[:1], "/"
        else:
            aux, marker = tail, ""
    elif len(tail) == 3 and tail[2] in "o/":
        aux, marker = tail[:2], tail[2]
    else:
        return code
    old = base_aux.get(text)
    new = merged_aux.get(text)
    if old is None or new is None:
        return code
    if len(aux) == 1:
        if aux != old.normal[:1]:
            return code
        aux = new.normal[:1]
    else:
        if aux == old.normal:
            aux = new.normal
        elif old.compat14 and aux == old.compat14:
            aux = new.compat14 or new.normal
        elif old.compat13 and aux == old.compat13:
            aux = new.compat13 or new.normal
        else:
            return code
    return head + aux + marker


def remap_lexicon_rows(rows, base_aux: dict, merged_aux: dict):
    return [
        (remap_lexicon_row(code, text, base_aux, merged_aux), text, *rest)
        for code, text, *rest in rows
    ]


def rewrite_supplemental(rows, converter, merged_aux) -> list[tuple[str, str, str]]:
    result = []
    for text, code, weight in rows:
        chars = bare(text)
        tokens = code.split()
        if len(tokens) != len(chars):
            continue
        rebuilt = []
        ok = True
        for char, token in zip(chars, tokens):
            entry = merged_aux.get(char)
            if entry is None or ";" not in token:
                ok = False
                break
            try:
                rebuilt.append(f"{converter(token[:2])};{entry.normal}")
            except (LayoutError, ValueError, IndexError):
                ok = False
                break
        if ok:
            result.append((text, " ".join(rebuilt), weight))
    return result


# ---------------------------------------------------------------------------
# 方案文件派生
# ---------------------------------------------------------------------------

def derive_sentence_core(scheme: str) -> str:
    text = (ROOT / "mohu_zrm_sentence_core.schema.yaml").read_text(encoding="utf-8")
    text = text.replace("mohu_zrm", f"mohu_{scheme}")
    text = re.sub(r"(?m)^    - mohu:/algebra/fly_zrm\?\n", "", text)
    return text


def derive_main_schema(scheme: str, layout: SpLayout, aux_customized: bool, display: str) -> str:
    text = (ROOT / "mohu_zrm.schema.yaml").read_text(encoding="utf-8")
    # 原生引擎词表路径：目录与文件名都指向本方案（须在通用替换前处理，
    # 否则目录名里的 zrm 不会跟随方案名）。
    text = text.replace(
        "mohu/data/zrm/mohu_zrm.lexicon.txt",
        f"mohu/data/{scheme}/mohu_{scheme}.lexicon.txt",
    )
    text = text.replace("mohu_zrm", f"mohu_{scheme}")
    # 自定义方案不带飞键：装配槽移除（user_* 槽保留，用户仍可自定义）。
    text = re.sub(r"(?m)^\s*- mohu:/algebra/fly_zrm\?\n", "", text)
    text = text.replace("魔虎·自然码", f"魔虎·{display}")
    if display == layout.display:
        text = text.replace("自然码双拼", f"{display}双拼")
        text = text.replace("自然码交互方案", f"{display}交互方案（自定义构建）")
    if aux_customized:
        text = text.replace(
            "做辅码的魔虎整句输入方案。",
            "做辅码的魔虎整句输入方案（本方案辅助码为用户自定义构建）。",
        )
    text = re.sub(r'(?m)^  version: "\d+"$', f'  version: "{date.today():%Y%m%d}"', text, count=1)
    text = re.sub(r"(?m)^  scheme: zrm$", f"  scheme: {scheme}", text)
    # 辅码提示/补辅码表按方案独立，避免覆盖共享的 lua/zrmdb.txt。
    text = re.sub(
        r"(?m)^mohu:$",
        f"mohu:\n  # 本方案的辅码表（构建时生成；与共享 zrmdb.txt 隔离）\n"
        f"  aux_db: lua/zrmdb_{scheme}.txt",
        text,
        count=1,
    )
    return text


def derive_custom_phrases(scheme: str) -> str:
    text = (ROOT / "mohu_zrm_custom_phrases.txt").read_text(encoding="utf-8")
    return text.replace("mohu_zrm", f"mohu_{scheme}")


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

def collision_report(layout: SpLayout, merged_aux: dict) -> list[str]:
    three: Counter = Counter()
    full: Counter = Counter()
    for (char, _py), weight in freq_simp_table.items():
        try:
            weight = int(weight)
        except (TypeError, ValueError):
            continue
        if weight <= 0:
            continue
        entry = merged_aux.get(char)
        if entry is None:
            continue
        try:
            sp = layout.to_code(READING_NORMALIZATION.get(_py, _py))
        except LayoutError:
            continue
        three[f"{sp}{entry.normal[:1]}"] += 1
        full[f"{sp}{entry.normal}"] += 1
    lines = ["", "## 辅码离散度（引擎字集内）", ""]
    lines.append("三码（双拼+首辅）与全码（双拼+双辅）的每码位字数分布。")
    lines.append("全码列是最重要的指标：同组字数=1 表示该组无歧义。")
    for title, counter in (("三码", three), ("全码", full)):
        worst = counter.most_common(8)
        singles = sum(1 for v in counter.values() if v == 1)
        lines.append(
            f"\n{title}：{len(counter)} 个码位；单字独占 {singles} 位"
            f"（{singles * 100 // max(len(counter), 1)}%）。最挤的码位："
        )
        lines.extend(f"  {code}: {count} 字" for code, count in worst)
    return lines


# ---------------------------------------------------------------------------
# 组包
# ---------------------------------------------------------------------------

NATIVE_LUA = ("mohu_runtime.lua", "mohu_sentence.lua", "mohu_tiger_sentence.lua")


def assemble_package(
    scheme: str, layout: SpLayout, output: Path, merged_aux: dict, lexicon_rows
) -> None:
    copy_common_files(output)

    # lua/：共享运行时 + 本方案专属辅码表 + native 三个 lua。
    copy_path(ROOT / "lua", output / "lua")
    (output / "lua/option_state_data.lua").unlink(missing_ok=True)
    for name in NATIVE_LUA:
        shutil.copy2(ROOT / "tiger_sentence_native" / name, output / "lua" / name)
    (output / "lua" / f"zrmdb_{scheme}.txt").write_text(
        zrmdb_lines(merged_aux), encoding="utf-8"
    )

    # mohu/：公共配置 + 本方案 lexicon；让位调优表清空（调优不迁移）。
    copy_path(ROOT / "mohu", output / "mohu")
    for existing in ("zrm", "flypy"):
        (output / "mohu" / f"four_code_yield_pairs_{existing}.txt").unlink(missing_ok=True)
    yield_pairs = output / "mohu" / f"four_code_yield_pairs_{scheme}.txt"
    yield_pairs.write_text(
        "# 四码字词让位对（自定义方案不迁移自然码调优；此文件为空占位）。\n",
        encoding="utf-8",
    )
    write_rows(
        output / "mohu" / "data" / scheme / f"mohu_{scheme}.lexicon.txt",
        lexicon_rows, root=ROOT,
    )

    # 模型目录说明（模型文件本身不随包分发，与 zrm/flypy 包一致）。
    model_dir = output / "mohu" / "model"
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "README.md").write_text(
        "请从 GitHub Releases 下载 mohu-sentence-ngram-v5.bin 放到本目录；\n"
        "缺失时整句引擎自动回退普通候选。\n",
        encoding="utf-8",
    )

    # macOS 引擎二进制（存在则带上；与已装魔虎共存时同字节）。
    engine = ROOT / "tiger_sentence_native/libtigerengine.dylib"
    if engine.is_file():
        runtime_dir = output / "mohu" / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(engine, runtime_dir / engine.name)

    if (ROOT / "解除隔离.command").is_file():
        target = output / "解除隔离.command"
        shutil.copy2(ROOT / "解除隔离.command", target)
        target.chmod(0o755)

    # 共存安装：不带 default.yaml；隐藏内部方案名。
    for path in sorted(output.glob("*.schema.yaml")):
        if path.name != f"mohu_{scheme}.schema.yaml":
            remove_schema_name(path)
    (output / "default.custom.yaml").write_text(
        "# 把本方案追加进方案列表（复制到 Rime 用户目录后重新部署生效）。\n"
        "# 若你已有 default.custom.yaml，只需把下面 patch 的内容并入。\n"
        f"patch:\n  schema_list/+:\n    - schema: mohu_{scheme}\n",
        encoding="utf-8",
    )


def copy_common_files(output: Path) -> None:
    for relative in COMMON_ROOT_PATHS:
        if relative == "安装说明.md":
            continue  # 自定义方案有自己的安装说明
        copy_path(ROOT / relative, output / relative)


def write_install_notes(scheme: str, layout: SpLayout, aux_source: str, display: str, output: Path) -> None:
    (output / "安装说明.md").write_text(f"""# 魔虎·{display}（自定义构建 mohu_{scheme}）安装说明

本方案由 tools/custom/build_custom_scheme.py 从魔虎自然码方案派生，
双拼布局：{layout.display}；辅助码来源：{aux_source}。

## 安装（可与已装的魔虎·自然码/小鹤共存）

1. 把本目录内**全部内容**合并复制到 Rime 用户目录
   （macOS 为 `~/Library/Rime/`，Windows 以小狼毫「用户文件夹」为准）。
   本包不带 `default.yaml`，不会覆盖你现有的方案列表。
2. 把包里的 `default.custom.yaml` 复制到用户目录（已有同名文件时，
   把其中 `patch:` 一段并入你的文件），使方案出现在选单。
3. 整句模型（可选但强烈建议）：从魔虎 Releases 下载
   `mohu-sentence-ngram-v5.bin` 放到 `~/Library/Rime/mohu/model/`；
   已装过魔虎的用户通常已有，无需重复下载。
4. macOS 首次安装后双击用户目录里的 `解除隔离.command`
   （或执行 `xattr -dr com.apple.quarantine ~/Library/Rime/mohu`）。
5. 重新部署（macOS：`"/Library/Input Methods/Squirrel.app/Contents/MacOS/Squirrel" --reload`），
   在输入法方案列表中选择「魔虎·{display}」。

## 已知差异（相对官方魔虎方案）

- 无飞键别名、无四码字词让位调优、无固定简码的人工调序迁移；
- 拆字提示（chaifen）与虎码反查仍显示虎码数据，与自定义辅助码无关；
- 一简/二简等声母简码继承自然码主码表（声母与拼音同形，各双拼通用）。

## 更新

修改布局/辅码输入文件后重新运行构建，再合并复制一次即可；
你的用户词库（*.userdb）与候选调频不受影响。
""", encoding="utf-8")


def self_check(scheme: str, output: Path, stats: BuildStats) -> list[str]:
    problems = []
    schema_path = output / f"mohu_{scheme}.schema.yaml"
    schema = schema_path.read_text(encoding="utf-8")
    if f"schema_id: mohu_{scheme}" not in schema:
        problems.append(f"{schema_path.name} 缺少 schema_id")
    master = (output / f"mohu_{scheme}.dict.yaml").read_text(encoding="utf-8")
    if f"name: mohu_{scheme}\n" not in master:
        problems.append("主码表 frontmatter name 不正确")
    words = (output / f"mohu_{scheme}.words.dict.yaml").read_text(encoding="utf-8")
    if f"name: mohu_{scheme}.words" not in words or "import_tables" in words:
        problems.append("整句词库 frontmatter 不正确")
    lexicon_path = output / "mohu" / "data" / scheme / f"mohu_{scheme}.lexicon.txt"
    if not lexicon_path.is_file() or lexicon_path.stat().st_size < 1000:
        problems.append("原生引擎词表缺失或过小")
    if f"mohu/data/{scheme}/mohu_{scheme}.lexicon.txt" not in schema:
        problems.append("schema 引用的 lexicon 路径与产物不一致")
    if not (output / "lua" / f"zrmdb_{scheme}.txt").is_file():
        problems.append("本方案辅码表 zrmdb 缺失")
    if f"lua/zrmdb_{scheme}.txt" not in schema:
        problems.append("schema 未指向本方案辅码表")
    word2_asset = output / "lua" / f"mohu_{scheme}_word2_codes.lua"
    if not word2_asset.is_file() or word2_asset.stat().st_size < 100:
        problems.append(f"组合让位读音资产 lua/mohu_{scheme}_word2_codes.lua 缺失或过小")
    if any(output.glob("*.userdb*")) or (output / "tools").exists():
        problems.append("包内混入了 userdb/tools 等不该分发的目录")
    return problems


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def ensure_zrm_chars() -> None:
    if ZRM_CHARS_DICT.is_file():
        return
    import io
    from contextlib import redirect_stdout
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        generate_chars(simplified=True)
    ZRM_CHARS_DICT.write_text(buffer.getvalue(), encoding="utf-8")


def build_aux(args, required_chars: list[str], coded: CodedCharTable | None = None,
              detected_layout: str = ""):
    base = load_auxiliary_tsv(TIGER_AUX_TSV)
    user: dict[str, AuxiliaryEntry] = {}
    aliases: dict[str, list[str]] = {}
    aux_source = "虎码 prefix2（默认）"
    derived_tsv: str | None = None
    if args.shape_code:
        source = load_shape_codes(Path(args.shape_code))
        if args.tiger_style_prune:
            synth_root_counts_by_length(source)
        user.update(derive_aux_from_shape_codes(source))
        aliases = source.aliases
        aux_source = f"形码推导（{Path(args.shape_code).name}，12 正常 + 13/14 兼容）"
        derived_tsv = "\n".join(
            f"{char}\t{entry.normal}\t{entry.compat14}\t{entry.compat13}"
            for char, entry in sorted(user.items())
        ) + "\n"
    if args.char_table and coded is not None:
        user.update(coded.aux)
        aliases = coded.aliases
        detected_note = f"，识别双拼：{detected_layout}" if detected_layout else ""
        aux_source = f"单字码表提取（{Path(args.char_table).name}{detected_note}）+ 虎码回退"
        derived_tsv = "\n".join(
            f"{char}\t{entry.normal}\t{entry.compat14}\t{entry.compat13}"
            for char, entry in sorted(user.items())
        ) + "\n"
    if args.aux:
        user.update(load_user_aux(Path(args.aux)))
        aux_source = f"用户辅码表（{Path(args.aux).name}）+ 虎码回退"
    outcome = merge_aux(user, required_chars, base=base)
    if args.strict and outcome.fallback_chars:
        sample = "".join(outcome.fallback_samples[:10])
        raise BuildError(
            f"--strict 要求辅码表全覆盖：还有 {outcome.fallback_chars} 个引擎字集内的字"
            f"没有你的辅码（如 {sample}）。补全后重试，或去掉 --strict 用虎码回退。"
        )
    return outcome, base, aliases, aux_source, derived_tsv


def run(args) -> int:
    if args.aux and args.shape_code:
        raise BuildError("--aux 与 --shape-code 只能二选一（形码推导已产出辅码表）")
    if args.char_table and (args.aux or args.shape_code):
        raise BuildError(
            "--char-table 与 --aux/--shape-code 只能二选一。"
            "想微调提取结果：先用 --char-table 构建，编辑产物里的 推导辅码表.tsv，"
            "再改用 --aux 传入。"
        )
    coded: CodedCharTable | None = None
    detected_layout = ""
    if args.char_table:
        print("[1/8] 读取单字码表并识别双拼布局…")
        coded = load_coded_chars(Path(args.char_table))
        detected_layout, ratio = detect_layout_for_table(coded)
        if ratio < 0.6:
            raise BuildError(
                f"--char-table 的前 2 键与内置布局（{'/'.join(LAYOUT_PRESETS)}）"
                f"最高命中率仅 {ratio:.0%}——这张表可能不是「双拼+辅码」的混合表，"
                f"或用了未知双拼。请检查文件，或改造为辅码表/形码全码表后"
                f"用 --aux / --shape-code 传入。"
            )
    sp_value = args.sp
    if sp_value is None:
        sp_value = detected_layout or "zrm"
    try:
        layout = resolve_layout(sp_value)
    except BuildError:
        raise
    name = args.name or layout.name
    scheme_display = args.display or layout.display
    if not NAME_PATTERN.fullmatch(name):
        raise BuildError(f"方案名 {name!r} 不合法：小写字母开头，仅小写字母与数字")
    if name in RESERVED_NAMES:
        raise BuildError(f"方案名不能是 {name}（与内置方案冲突），请用 --name 另起")

    print(f"[1/8] 校验布局 {layout.display}（{layout.source.name}）…")
    converter = make_converter(layout)

    print("[2/8] 准备辅码表…")
    required_chars = load_engine_charset()
    outcome, base_aux, aliases, aux_source, derived_tsv = build_aux(
        args, required_chars, coded=coded, detected_layout=detected_layout)
    merged_aux = outcome.table
    aux_customized = bool(args.aux or args.shape_code or args.char_table)
    if coded is not None:
        syllables = {layout.to_code(py) for py in all_pinyins()}
        coded.stats.unknown_prefixes = {
            prefix for prefix in coded.prefixes if prefix not in syllables
        }

    output = Path(args.output) if args.output else ROOT / "custom-dist" / f"mohu_{name}"
    print(f"[3/8] 输出目录 {output.relative_to(ROOT)}…")
    recreate_destination(output)

    stats = BuildStats()

    print("[4/8] 转换主码表与词库源…")
    ensure_zrm_chars()
    master_text = (ROOT / "mohu_zrm.dict.yaml").read_text(encoding="utf-8")
    master_converted = convert_master(master_text, name, converter, base_aux, merged_aux, stats,
                                      character_syllables=load_character_syllables(ZRM_CHARS_DICT),
                                      unique_shortcode=args.unique_shortcode,
                                      char_order=coded.order if coded else None)
    master_path = output / f"mohu_{name}.dict.yaml"
    master_path.write_text(master_converted, encoding="utf-8")

    # 分源转换走临时目录：运行时只消费合并后的单份 words dict，
    # 仓库维护期的 lexicon_sources/ 分源结构对派生方案无需物化。
    with tempfile.TemporaryDirectory(prefix=f"mohu-{name}-") as temp_dir:
        staging = Path(temp_dir)
        import io
        from contextlib import redirect_stdout
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            generate_chars(
                simplified=True,
                syllable_of=lambda py: layout.to_code(READING_NORMALIZATION.get(py, py)),
                aux=merged_aux,
                master_dict=master_path,
                reading_normalization=READING_NORMALIZATION,
                header_comment=(
                    f"# 【{name} 单字基础词库｜自动生成，请勿直接编辑】",
                    f"# 由 tools/custom/build_custom_scheme.py 以布局 {layout.display} 与自定义辅码生成。",
                    "# AUTO-GENERATED. DO NOT EDIT.",
                ),
            )
        chars_text = buffer.getvalue()
        stats.chars_rows = sum(1 for line in chars_text.splitlines() if "\t" in line and not line.startswith("#"))
        chars_path = staging / f"mohu_{name}.chars.dict.yaml"
        chars_path.write_text(chars_text, encoding="utf-8")

        source_specs = {}
        for suffix in SOURCE_SUFFIXES:
            if suffix == "chars":
                continue
            source = ROOT / "tools/data/lexicon_sources/zrm" / f"mohu_zrm.{suffix}.dict.yaml"
            text = source.read_text(encoding="utf-8")
            code_index = source_code_index(text)
            converted = convert_lexicon_source(
                text, f"mohu_{name}.{suffix}", code_index, converter, merged_aux, stats, suffix
            )
            target = staging / f"mohu_{name}.{suffix}.dict.yaml"
            target.write_text(converted, encoding="utf-8")
            source_specs[suffix] = target

        print("[5/8] 合并整句词库（含长词补行）…")
        supplemental, _report = master_long_word_rows(ROOT, source_paths("zrm", ROOT))
        supplemental = rewrite_supplemental(supplemental, converter, merged_aux)
        stats.long_word_rows = len(supplemental)
        sources = [chars_path] + [source_specs[s] for s in ("base", "words", "tencent", "moe", "classics", "wanxiang")]
        merge_dictionary(f"mohu_{name}.words", sources, output / f"mohu_{name}.words.dict.yaml",
                         supplemental_rows=supplemental)

        print("[6/8] 派生 schema 与原生引擎词表…")
        (output / f"mohu_{name}_sentence_core.schema.yaml").write_text(
            derive_sentence_core(name), encoding="utf-8")
        (output / f"mohu_{name}.schema.yaml").write_text(
            derive_main_schema(name, layout, aux_customized, scheme_display), encoding="utf-8")
        (output / f"mohu_{name}_custom_phrases.txt").write_text(
            derive_custom_phrases(name), encoding="utf-8")

        reading_frequencies = load_reading_frequencies(chars_path, is_syllable=layout.is_code)
        tiger_rows = load_rows(TIGER_LEXICON, reading_frequencies)
        # 源行是「自然码+虎码辅码」形态；换辅码体系时把单字行的辅码尾
        # 一并重映射（音节转换在 build_rows 内进行）。
        if aux_customized:
            tiger_rows = remap_lexicon_rows(tiger_rows, base_aux, merged_aux)
        zrm_syllables = load_character_syllables(ZRM_CHARS_DICT)
        inject_words = load_inject_words(source_specs["base"])
        lexicon_rows = build_rows(
            tiger_rows, name,
            character_syllables=zrm_syllables,
            word_weights=inject_words,
            converter=converter,
            fly={},
        )
        stats.lexicon_rows = len(lexicon_rows)

    print("[7/8] 组装发布目录…")
    assemble_package(name, layout, output, merged_aux, lexicon_rows)
    # 组合让位守卫资产（express 0.18.0）：读音集合与 zrm/flypy 独立，
    # 从本方案刚合并出的 words 词库派生；缺失时该规则在运行时自动关闭。
    word2_asset = write_asset(
        name, extract_codes(output / f"mohu_{name}.words.dict.yaml"), output / "lua")
    print(f"    组合让位读音资产：{word2_asset.name}")
    write_install_notes(name, layout, aux_source, scheme_display, output)
    if derived_tsv is not None:
        (output / "推导辅码表.tsv").write_text(derived_tsv, encoding="utf-8")

    print("[8/8] 自检与构建报告…")
    problems = self_check(name, output, stats)
    report = build_report(name, layout, aux_source, scheme_display, outcome, stats, aliases, supplemental,
                          args, problems, output, coded=coded, detected_layout=detected_layout)
    (output / "构建报告.txt").write_text("\n".join(report) + "\n", encoding="utf-8")

    if args.zip:
        archive = output.parent / f"rime-mohu-{name}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(output.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(output))
        print(f"已打包 {archive.relative_to(ROOT)}")

    print()
    print(f"构建完成：{output.relative_to(ROOT)}")
    print(f"详见 {output.relative_to(ROOT)}/构建报告.txt；安装步骤见其中的「安装」一节。")
    if problems:
        print("⚠ 自检发现问题：")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    return 0


def build_report(name, layout, aux_source, scheme_display, outcome, stats, aliases, supplemental, args, problems, output,
                 coded: CodedCharTable | None = None, detected_layout: str = "") -> list[str]:
    lines = [
        f"魔虎自定义方案构建报告：mohu_{name}",
        f"构建日期：{date.today():%Y-%m-%d}",
        "",
        "## 方案",
        f"- 双拼布局：{layout.display}（{layout.source.name}）；选单显示：魔虎·{scheme_display}",
        f"- 辅助码来源：{aux_source}",
        f"- 引擎字集：{len(load_engine_charset())} 字",
    ]
    if coded is not None and detected_layout and args.sp is not None and args.sp != detected_layout:
        lines.append(
            f"- ⚠ 你指定了 --sp {args.sp}，但码表前 2 键更像 {detected_layout} 布局；"
            f"辅码提取不受影响，双拼打法以本方案布局为准"
        )
    lines += [
        "",
        "## 辅码覆盖",
        f"- 用户覆盖：{outcome.user_chars} 字",
        f"- 回退虎码：{outcome.fallback_chars} 字"
        + (f"（样例：{''.join(outcome.fallback_samples)}）" if outcome.fallback_samples else ""),
    ]
    if outcome.missing:
        lines.append(f"- ⚠ 无任何辅码：{len(outcome.missing)} 字：{''.join(outcome.missing[:20])}")
    lines += collision_report(layout, outcome.table)
    if coded is not None:
        cs = coded.stats
        lines += [
            "",
            "## 单字码表提取",
            f"- 数据行 {cs.rows}（另有 {cs.skipped_non_han} 行假名/符号/拉丁等非汉字行、"
            f"{cs.skipped_multi_code} 行多码列，已跳过）",
            f"- 提取辅码：{len(coded.aux)} 字；≤2 键行（简码/裸音节，不参与）{cs.short_rows} 行；"
            f"3 键渐进行 {cs.graded_rows} 行",
            f"- ≥3 键行前 2 键识别为 {detected_layout or '（未识别）'} 双拼",
        ]
        if cs.long_code_rows:
            lines.append(f"- {cs.long_code_rows} 行辅码超出 2 键，已截断取前 2 键")
        if cs.graded_mismatch:
            lines.append(
                f"- ⚠ {cs.graded_mismatch} 行 3 键渐进与提取辅码首键不一致"
                f"（一字多辅码或表内口径混杂，可忽略）"
            )
        if cs.multi_aux_chars:
            lines.append(f"- 一字多辅码 {cs.multi_aux_chars} 字，取首现、其余见下方别名清单")
        lines.append(
            "- 同码候选序与三简撞位保留者已按原码表字序重排"
            "（源码表没有的字排在该码位组尾）"
        )
        if coded.stats.unknown_prefixes:
            sample = " ".join(sorted(coded.stats.unknown_prefixes)[:12])
            lines.append(
                f"- 前缀不在本方案音节集内 {len(coded.stats.unknown_prefixes)} 个"
                f"（如 {sample}）：辅码提取不受影响，这些音节按本方案布局的打法输入"
            )
    if aliases:
        sample = "; ".join(f"{char}←{','.join(codes)}" for char, codes in list(aliases.items())[:8])
        source_label = "单字码表一字多辅码" if coded is not None else "形码表多码字"
        lines += ["", f"## {source_label}（未采用，可考虑做成别名打法）", "", f"共 {len(aliases)} 字。样例：{sample}"]
    lines += [
        "",
        "## 主码表转换",
        f"- 转换 {stats.master_rows} 行；未识别形态（原样保留）{len(stats.master_unconverted)} 行",
    ]
    if stats.unique_pushed:
        pushed_desc = "、".join(
            f"{n} 简 {count} 字" for n, count in sorted(stats.unique_pushed.items())
        )
        lines.append(
            f"- 简码无重已启用：撞位往后推共 {sum(stats.unique_pushed.values())} 字"
            f"（{pushed_desc}；这些字改由全码/辅码渐进打出）"
        )
    if stats.master_unconverted:
        lines.extend(f"  {row}" for row in stats.master_unconverted[:10])
    if stats.master_pushed_back:
        lines += [
            f"- 三简位撞码（一码一字，行序后者往后推，删三简行留全码）：{len(stats.master_pushed_back)} 字",
        ]
        lines.extend(f"  {row}" for row in stats.master_pushed_back[:15])
        if len(stats.master_pushed_back) > 15:
            lines.append(f"  … 共 {len(stats.master_pushed_back)} 字")
    lines += [
        "",
        "## 词库源转换",
        f"- 单字表 {stats.chars_rows} 行；长词补行 {stats.long_word_rows} 行",
    ]
    lines.extend(f"- {label}: 改写 {count} 行" for label, count in sorted(stats.source_rows.items()))
    if stats.source_anomalies:
        lines.append(f"- ⚠ 无法重写、原样保留 {len(stats.source_anomalies)} 行（样例）：")
        lines.extend(f"  {row}" for row in stats.source_anomalies[:8])
    lines += [
        "",
        "## 原生引擎",
        f"- lexicon {stats.lexicon_rows} 行（含注入词，飞键闭包为空——自定义方案不带飞键）",
        "",
        "## 已知降级（预期行为）",
        "- 无飞键别名；四码字词让位调优为空；简码人工调序未迁移。",
        "- 拆字提示与虎码反查仍为虎码数据；如更换了辅助码，二者展示的仍是虎码。",
        "",
        "## 自检",
    ]
    verdict = "全部通过" if not problems else "发现问题"
    lines.append(f"- {'✅' if not problems else '❌'} {verdict}")
    lines.extend(f"  - {problem}" for problem in problems)
    lines += [
        "",
        "## 安装",
        "1. 把发布目录内全部内容合并复制到 Rime 用户目录（macOS ~/Library/Rime）。",
        "2. default.custom.yaml 追加方案；模型放到 mohu/model/（已装魔虎可复用）。",
        "3. macOS 双击 解除隔离.command；重新部署后选择「魔虎·" + scheme_display + "」。",
        "",
        "## 重新构建",
        f"uv run tools/custom/build_custom_scheme.py --sp {layout.source.name}"
        + (f" --shape-code {args.shape_code}" if args.shape_code else "")
        + (f" --char-table {args.char_table}" if args.char_table else "")
        + (f" --aux {args.aux}" if args.aux else "")
        + f" --name {name}",
    ]
    return lines


def emit_aux_template(args) -> int:
    target = Path(args.emit_aux_template) if args.emit_aux_template != "aux-template.tsv" else Path("aux-template.tsv")
    if target.exists() and not args.force:
        raise BuildError(f"{target} 已存在（加 --force 覆盖）")
    shutil.copy2(TIGER_AUX_TSV, target)
    print(f"已导出虎码辅码全量模板（{sum(1 for _ in TIGER_AUX_TSV.open(encoding='utf-8'))} 行）：{target}")
    print("改完后用 --aux 传入即可整体替换辅码。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sp", default=None,
                        help="双拼布局：预设名（zrm/flypy/jiajia）或布局 YAML 路径；"
                             "默认 zrm；配 --char-table 时未指定则自动识别码表的双拼")
    parser.add_argument("--aux", help="辅码表 TSV：字<Tab>辅码[<Tab>14位<Tab>13位]，缺字回退虎码；码在前也认")
    parser.add_argument("--shape-code", help="形码全码表 TSV：字<Tab>完整编码[<Tab>根数]，按魔虎取位规则推导辅码；码在前也认")
    parser.add_argument("--char-table", metavar="PATH",
                        help="单字成品码表：双拼(2键)+辅码 混合表（码在前/字在前均可），"
                             "剥双拼提辅码并自动识别双拼布局")
    parser.add_argument("--tiger-style-prune", action="store_true",
                        help="配合 --shape-code：按码长启用虎码式剪枝（≤3 码丢 13 位）")
    parser.add_argument("--name", help="方案名（默认取布局名；不能是 zrm/flypy）")
    parser.add_argument("--display", help="选单显示名（默认取布局 display；如「华南虎」→ 魔虎·华南虎）")
    parser.add_argument("--output", help="输出目录（默认 custom-dist/mohu_<name>）")
    parser.add_argument("--strict", action="store_true", help="辅码表必须覆盖引擎字集，缺字报错")
    parser.add_argument("--unique-shortcode", action="store_true",
                        help="简码无重：一/二/三简每个码位只留首个字，撞位字往后推（改由全码/"
                             "辅码渐进打出）；默认只整理三简且保留原生同码特例")
    parser.add_argument("--emit-aux-template", nargs="?", const="aux-template.tsv", metavar="PATH",
                        help="导出虎码辅码全量模板到 PATH（默认 ./aux-template.tsv）后退出")
    parser.add_argument("--force", action="store_true", help="允许覆盖 emit-aux-template 的已有文件")
    parser.add_argument("--zip", action="store_true", help="顺带产出 rime-mohu-<name>.zip")
    args = parser.parse_args()

    try:
        if args.emit_aux_template:
            return emit_aux_template(args)
        return run(args)
    except (BuildError, LayoutError, AuxError) as exc:
        print(f"构建失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
