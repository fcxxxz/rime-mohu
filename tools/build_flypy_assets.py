#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
from pathlib import Path

import fly_keys  # noqa: E402
import flypyify
import zrmify
from build_sentence_dictionary import build as build_sentence_dictionaries
from sync_flykey_quickcodes import build_expected  # noqa: E402
from tiger_aux import load_auxiliary_tsv

import opencc

ROOT = Path(__file__).resolve().parents[1]
T2S = opencc.OpenCC("t2s")

ZRM_DICTIONARIES = {
    "tools/data/lexicon_sources/zrm/mohu_zrm.chars.dict.yaml": "mohu_flypy.chars",
    "tools/data/lexicon_sources/zrm/mohu_zrm.base.dict.yaml": "mohu_flypy.base",
    "tools/data/lexicon_sources/zrm/mohu_zrm.words.dict.yaml": "mohu_flypy.words",
    "tools/data/lexicon_sources/zrm/mohu_zrm.tencent.dict.yaml": "mohu_flypy.tencent",
    "tools/data/lexicon_sources/zrm/mohu_zrm.moe.dict.yaml": "mohu_flypy.moe",
    "tools/data/lexicon_sources/zrm/mohu_zrm.classics.dict.yaml": "mohu_flypy.classics",
    "tools/data/lexicon_sources/zrm/mohu_zrm.wanxiang.dict.yaml": "mohu_flypy.wanxiang",
}

CODE_DICTIONARIES = {"mohu_zrm.dict.yaml": "mohu_flypy"}

# 小鹤飞键集合（单一事实源 tools/fly_keys.py，清单见 mohu_fly_keys.tsv）。
# 小鹤词典的飞键区块不镜像自然码母表（自然码 qx=qie 的
# qx→qo 内容对小鹤语义是错的），而是在音节转换完成后从小鹤主区块
# 全量再生成（tools/sync_flykey_quickcodes.py 的闭包逻辑）。
FLY_BLOCK_START = re.compile(r"^#\s*开始飞键\s*(\S+)\s*->\s*(\S+)")
FLY_BLOCK_END = re.compile(r"^#\s*结束飞键")

SENTENCE_SCHEMA = "mohu_zrm_sentence_core.schema.yaml"


def convert_syllable(code: str) -> str:
    if code == "pp":
        return code
    try:
        return flypyify.flypyify1(zrmify.unzrmify1(code))
    except Exception as exc:
        raise ValueError(f"cannot convert natural-code syllable {code!r}") from exc


def convert_spelling_code(code: str) -> str:
    converted = []
    for token in code.split(" "):
        if not token:
            continue
        if ";" not in token:
            converted.append(token)
            continue
        spelling, auxiliary = token.split(";", 1)
        converted.append(f"{convert_syllable(spelling)};{auxiliary}")
    return " ".join(converted)


def keep_primary_auxiliaries(
    text: str,
    code: str,
    primary_auxiliaries: dict[str, list[str]],
) -> str:
    allowed = primary_auxiliaries.get(text)
    if allowed is None:
        return code
    result = []
    for token in code.split(" "):
        if not token:
            continue
        # 正常辅码与 13/14 位兼容打法都保留，仅滤掉其他来源的别名。
        if ";" in token and token.split(";", 1)[1] not in allowed:
            continue
        result.append(token)
    return " ".join(result)


def convert_table_code(word: str, code: str) -> str:
    if len(word) == 1 and len(code) > 1 and code[0] != "o":
        return convert_syllable(code[:2]) + code[2:]
    if len(word) == 2 and len(code) == 4:
        return convert_syllable(code[:2]) + convert_syllable(code[2:])
    if len(word) == 2 and len(code) == 3:
        try:
            return convert_syllable(code[:2]) + code[2]
        except ValueError:
            # Unreasonable short codes such as 默认/mry are scheme-independent.
            return code
    if len(word) == 3 and len(code) == 4:
        return code[:2] + convert_syllable(code[2:])
    return code


def replace_dictionary_name(text: str, name: str) -> str:
    return re.sub(r"(?m)^name:\s*\S+\s*$", f"name: {name}", text, count=1)


def convert_dictionary(source_name: str, target_name: str) -> str:
    text = (ROOT / source_name).read_text(encoding="utf-8")
    text = replace_dictionary_name(text, target_name)
    lines = []
    in_body = False
    primary_auxiliaries = None
    if source_name == "tools/data/lexicon_sources/zrm/mohu_zrm.chars.dict.yaml":
        primary_auxiliaries = {
            char: entry.codes()
            for char, entry in load_auxiliary_tsv(
                ROOT / "tools/data/tiger_aux.txt"
            ).items()
        }
    for raw in text.splitlines(keepends=True):
        if raw.strip() == "...":
            in_body = True
            lines.append(raw)
            continue
        if not in_body or raw.startswith("#") or "\t" not in raw:
            lines.append(raw)
            continue
        fields = raw.rstrip("\n").split("\t")
        if len(fields) >= 2 and fields[1]:
            if primary_auxiliaries is not None:
                fields[1] = keep_primary_auxiliaries(
                    fields[0], fields[1], primary_auxiliaries
                )
                if not fields[1]:
                    continue
            fields[1] = convert_spelling_code(fields[1])
        lines.append("\t".join(fields) + ("\n" if raw.endswith("\n") else ""))
    return "".join(lines)


def compose_fly_blocks(expected: dict, fly: dict[str, str]) -> list[str]:
    """按 fly 集合顺序把生成结果编排成飞键区块行（块间空行分隔）。"""
    blocks: list[str] = []
    for index, (source, target) in enumerate(fly.items()):
        if index:
            blocks.append("\n")
        blocks.append(f"# 开始飞键 {source} -> {target}\n")
        blocks.extend(f"{line}\n" for line in expected.get((source, target), []))
        blocks.append("# 结束飞键\n")
    return blocks


def convert_code_table(source_name: str, target_name: str) -> str:
    """Convert the editable master directly; never allocate or overwrite its rows."""
    text = (ROOT / source_name).read_text(encoding="utf-8")
    text = replace_dictionary_name(text, target_name)
    lines = []
    in_body = False
    fly_drop = False
    for raw in text.splitlines(keepends=True):
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
            if len(fields) >= 2 and re.fullmatch(r"[a-z]+", fields[1]):
                fields[1] = convert_table_code(fields[0], fields[1])
            raw = "\t".join(fields) + ("\n" if raw.endswith("\n") else "")
        lines.append(raw)
    converted = "".join(lines)
    expected = build_expected(converted.splitlines(), [], fly_keys.FLY_FLYPY)
    return ("# 小鹤派生表；请修改 mohu_zrm.dict.yaml 后运行 make dict。\n"
            + converted.rstrip() + "\n\n"
            + "".join(compose_fly_blocks(expected, fly_keys.FLY_FLYPY)))


def split_dictionary_body(path: Path) -> tuple[str, list[str]]:
    text = path.read_text(encoding="utf-8")
    marker = "...\n"
    if marker not in text:
        raise ValueError(f"Rime dictionary is missing body marker: {path.name}")
    header, body = text.split(marker, 1)
    rows = [
        line
        for line in body.splitlines(keepends=True)
        if line.strip() and not line.startswith("#")
    ]
    return header + marker, rows


def flypy_schema(zrm_text: str) -> str:
    text = zrm_text.replace("mohu_zrm", "mohu_flypy")
    text = text.replace("*mohu_flypy_aux_translator", "*mohu_aux_translator")
    text = text.replace("自然码", "小鹤")
    text = text.replace("自然碼", "小鹤")
    text = re.sub(r"(?m)^    - 小鹤发明人：.*$", "    - 小鹤双拼方案：鹤氏", text)
    # 小鹤飞键集合与自然码不同（qx 在小鹤是 qia，且无 wz→wk），装配槽随之替换。
    # user_sentence_top 是空的用户自定义槽，小鹤方案保留引用，
    # 用户可照常在其中配置模糊音等自定义演算式。
    return text.replace("mohu:/algebra/fly_zrm?", "mohu:/algebra/fly_flypy?")


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build_flypy_custom_phrases() -> None:
    custom = T2S.convert((ROOT / "mohu_zrm_custom_phrases.txt").read_text(encoding="utf-8"))
    custom = custom.replace("mohu_custom_phrases", "mohu_zrm_custom_phrases")
    custom = custom.replace("mohu.extended", "mohu_zrm.words").replace("mohu_zrm.extended", "mohu_zrm.words")
    write(
        ROOT / "mohu_flypy_custom_phrases.txt",
        custom.replace("mohu_zrm", "mohu_flypy"),
    )


def build() -> None:
    for source, target_name in ZRM_DICTIONARIES.items():
        source_path = ROOT / source
        zrm_text = source_path.read_text(encoding="utf-8")
        zrm_name = target_name.replace("mohu_flypy", "mohu_zrm")
        zrm_text = replace_dictionary_name(zrm_text, zrm_name)
        write(source_path, zrm_text)
        target_path = ROOT / source.replace("lexicon_sources/zrm/", "lexicon_sources/flypy/").replace("mohu_zrm", "mohu_flypy")
        write(target_path, convert_dictionary(source, target_name))

    for source, target_name in CODE_DICTIONARIES.items():
        target_path = ROOT / source.replace("lexicon_sources/zrm/", "lexicon_sources/flypy/").replace("mohu_zrm", "mohu_flypy")
        write(target_path, convert_code_table(source, target_name))

    build_sentence_dictionaries(ROOT)

    custom = T2S.convert((ROOT / "mohu_zrm_custom_phrases.txt").read_text(encoding="utf-8"))
    custom = custom.replace("mohu_custom_phrases", "mohu_zrm_custom_phrases")
    custom = custom.replace("mohu.extended", "mohu_zrm.words").replace("mohu_zrm.extended", "mohu_zrm.words")
    write(ROOT / "mohu_zrm_custom_phrases.txt", custom)
    build_flypy_custom_phrases()

    # Public schemes retain intentionally distinct per-scheme runtime settings.
    zrm_text = (ROOT / SENTENCE_SCHEMA).read_text(encoding="utf-8")
    write(ROOT / SENTENCE_SCHEMA.replace("mohu_zrm", "mohu_flypy"), flypy_schema(zrm_text))



def main() -> None:
    parser = argparse.ArgumentParser(description="Build Flypy assets")
    parser.add_argument(
        "--custom-phrases-only",
        action="store_true",
        help="generate only mohu_flypy_custom_phrases.txt",
    )
    args = parser.parse_args()
    if args.custom_phrases_only:
        build_flypy_custom_phrases()
    else:
        build()


if __name__ == "__main__":
    main()
