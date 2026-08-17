#!/usr/bin/env python3

from __future__ import annotations

import re
from pathlib import Path

import flypyify
import zrmify

import opencc

ROOT = Path(__file__).resolve().parents[1]
T2S = opencc.OpenCC("t2s")

ZRM_DICTIONARIES = {
    "mohu_zrm.chars.dict.yaml": "mohu_flypy.chars",
    "mohu_zrm.base.dict.yaml": "mohu_flypy.base",
    "mohu_zrm.words.dict.yaml": "mohu_flypy.words",
    "mohu_zrm.tencent.dict.yaml": "mohu_flypy.tencent",
    "mohu_zrm.computer.dict.yaml": "mohu_flypy.computer",
    "mohu_zrm.moe.dict.yaml": "mohu_flypy.moe",
}

FIXED_DICTIONARIES = {
    "mohu_zrm_fixed.dict.yaml": "mohu_flypy_fixed",
    "mohu_zrm_fixed_legacy.dict.yaml": "mohu_flypy_fixed_legacy",
}

GENERATED_CHARACTER_MARKER = "#----------生成单字----------#\n"
WORD_TABLE_MARKER = "#----------词库----------#\n"

SCHEMAS = {
    "mohu_zrm.schema.yaml": ("mohu_zrm", "魔虎·自然码"),
    "mohu_zrm_fixed.schema.yaml": ("mohu_zrm_fixed", "字词·魔虎·自然码"),
    "mohu_zrm_sentence.schema.yaml": ("mohu_zrm_sentence", "整句·魔虎·自然码"),
    "mohu_zrm_aux.schema.yaml": ("mohu_zrm_aux", "辅筛·魔虎·自然码"),
}

REMOVED_SECTIONS = {
    "english",
    "japanese",
    "japanese_o",
    "std_t2s",
    "std_t2hk",
    "std_t2tw",
    "std_t2jp",
    "std_t2dzing",
    "reverse_universal",
    "reverse_stroke",
    "reverse_cangjie5",
    "reverse_zrlf",
    "reverse_bopomofo",
    "reverse_tick",
    "reverse_lookup",
    "recognizer_secondary",
}

REMOVED_LINE_TOKENS = (
    "mohu_english",
    "mohu_japanese",
    "mohu_reverse",
    "affix_segmentor@japanese_o",
    "matcher@recognizer_secondary",
    "table_translator@english",
    "table_translator@japanese",
    "lua_filter@*mohu_english_filter",
    "reverse_lookup_translator@reverse_tick",
    "reverse_lookup_translator@reverse_universal",
    "reverse_lookup_translator@reverse_stroke",
    "reverse_lookup_translator@reverse_cangjie5",
    "reverse_lookup_translator@reverse_zrlf",
    "reverse_lookup_translator@reverse_bopomofo",
    "simplifier@std_t2",
    "mohu:/key_bindings/mohu_ctrl_s",
)


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


def convert_fixed_code(word: str, code: str) -> str:
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
            fields[1] = convert_spelling_code(fields[1])
        lines.append("\t".join(fields) + ("\n" if raw.endswith("\n") else ""))
    return "".join(lines)


def convert_fixed_dictionary(source_name: str, target_name: str) -> str:
    text = (ROOT / source_name).read_text(encoding="utf-8")
    if GENERATED_CHARACTER_MARKER in text:
        start = text.index(GENERATED_CHARACTER_MARKER)
        end = text.index(WORD_TABLE_MARKER, start)
        text = text[:start] + text[end:]
    text = replace_dictionary_name(text, target_name)
    text = text.replace("mohu_zrm_tiger_fixed", "mohu_flypy_tiger_fixed")
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
        if len(fields) >= 2 and fields[1] and re.fullmatch(r"[a-z]+", fields[1]):
            fields[1] = convert_fixed_code(fields[0], fields[1])
        lines.append("\t".join(fields) + ("\n" if raw.endswith("\n") else ""))
    converted = "".join(lines)
    table_name = (
        "mohu_flypy_tiger_fixed_legacy"
        if target_name.endswith("_legacy")
        else "mohu_flypy_tiger_fixed"
    )
    _, native_rows = split_dictionary_body(ROOT / f"{table_name}.dict.yaml")
    parent_rows = []
    for raw in native_rows:
        fields = raw.rstrip("\n").split("\t")
        if len(fields) < 3:
            raise ValueError(f"invalid generated character row: {raw!r}")
        parent_rows.append(f"{fields[0]}\t{fields[1]}\t\t{fields[2]}\n")
    generated = GENERATED_CHARACTER_MARKER + "".join(parent_rows) + "\n"
    return converted.replace("...\n", "...\n\n" + generated, 1)


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


def remove_top_level_sections(text: str, names: set[str]) -> str:
    result = []
    skipping = False
    for line in text.splitlines(keepends=True):
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):", line)
        if match:
            skipping = match.group(1) in names
        if not skipping:
            result.append(line)
    return "".join(result)


def remove_conversion_switch(text: str) -> str:
    lines = text.splitlines(keepends=True)
    result = []
    skipping = False
    for line in lines:
        if re.match(r"^  - options: \[ std_[ts]", line):
            skipping = True
            continue
        if skipping and re.match(r"^  - name:", line):
            skipping = False
        if not skipping:
            result.append(line)
    return "".join(result)


def normalize_schema(path: Path, schema_id: str, display_name: str) -> str:
    text = T2S.convert(path.read_text(encoding="utf-8"))
    text = remove_top_level_sections(text, REMOVED_SECTIONS)
    text = remove_conversion_switch(text)
    text = "".join(
        line
        for line in text.splitlines(keepends=True)
        if not any(token in line for token in REMOVED_LINE_TOKENS)
        and line.strip() != "- reverse_lookup_translator"
        and not re.match(
            r"^\s+(reverse_(?:lookup|universal|tick|stroke|cangjie5|zrlf|bopomofo)|japanese_o|english):",
            line,
        )
        and line.strip() not in {"- stroke", "- cangjie5", "- bopomofo", "- zrlf"}
    )
    text = re.sub(r"(?m)^  schema_id: \S+$", f"  schema_id: {schema_id}", text, count=1)
    text = re.sub(r"(?m)^  name: .+$", f"  name: {display_name}", text, count=1)
    text = text.replace("states: [ 通用, 增广 ]", "states: [ 常用字, 全字集 ]")
    text = text.replace("states: [ 通用, 增廣 ]", "states: [ 常用字, 全字集 ]")
    text = re.sub(r"(?m)^  charset: (?:both|trad)$", "  charset: simp", text)

    if schema_id.endswith("_fixed"):
        if "  dependencies:\n" not in text.split("\nswitches:\n", 1)[0]:
            text = text.replace(
                "\nswitches:\n",
                "  dependencies:\n    - mohu_charset\n    - tiger\n\nswitches:\n",
                1,
            )
        if "  - name: extended_charset\n" not in text:
            text = text.replace(
                "  - name: emoji\n    states: [ 🈚, 🈶 ]\n",
                "  - name: emoji\n    states: [ 🈚, 🈶 ]\n"
                "  - name: extended_charset\n    states: [ 常用字, 全字集 ]\n",
                1,
            )
        if "lua_filter@*mohu_charset_filter" not in text:
            text = text.replace(
                "  filters:\n",
                "  filters:\n    - lua_filter@*mohu_charset_filter\n",
                1,
            )
        if "\nmohu:\n  charset: simp\n" not in text:
            text = text.replace("\nmohu:\n", "\nmohu:\n  charset: simp\n", 1)

    replacements = {
        "mohu_fixed": "mohu_zrm_fixed",
        "mohu_sentence": "mohu_zrm_sentence",
        "mohu_aux": "mohu_zrm_aux",
        "mohu.extended": "mohu_zrm.extended",
        "mohu.chars": "mohu_zrm.chars",
        "mohu_tiger_prefix2": "mohu_zrm_tiger_prefix2",
        "mohu_sentence_tiger_prefix2": "mohu_zrm_sentence_tiger_prefix2",
        "mohu_aux_tiger_prefix2": "mohu_zrm_aux_tiger_prefix2",
        "mohu_fixed_tiger_prefix2": "mohu_zrm_fixed_tiger_prefix2",
        "mohu_custom_phrases": "mohu_zrm_custom_phrases",
        "prism: mohu_aux": "prism: mohu_zrm_aux",
        "prism: mohu\n": "prism: mohu_zrm\n",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = text.replace("*mohu_zrm_aux_translator", "*mohu_aux_translator")

    if "reverse_lookup_translator@reverse_tiger" not in text:
        marker = "    - punct_translator\n"
        text = text.replace(marker, marker + "    - reverse_lookup_translator@reverse_tiger\n", 1)
    if "\nreverse_tiger:\n" not in text:
        section = (
            "\nreverse_tiger:\n"
            "  tag: reverse_tiger\n"
            "  dictionary: tiger\n"
            "  enable_completion: true\n"
            "  prefix: \"ohm\"\n"
            "  tips: 〔虎码〕\n"
            "  comment_format:\n"
            "    - xform/(\\w\\w);(\\w\\w)/$1[$2]/\n"
        )
        text = text.replace("\npunctuator:\n", section + "\npunctuator:\n", 1)
    if "    reverse_tiger:" not in text:
        text = text.replace(
            "  patterns:\n",
            '  patterns:\n    reverse_tiger: "^ohm[a-z]+$"\n',
            1,
        )
    return text


def flypy_schema(zrm_text: str) -> str:
    text = zrm_text.replace("mohu_zrm", "mohu_flypy")
    text = text.replace("*mohu_flypy_aux_translator", "*mohu_aux_translator")
    text = text.replace("自然码", "小鹤")
    text = text.replace("自然碼", "小鹤")
    text = re.sub(r"(?m)^    - 小鹤发明人：.*$", "    - 小鹤双拼方案：鹤氏", text)
    text = "".join(
        line
        for line in text.splitlines(keepends=True)
        if "mohu:/algebra/user_sentence_top?" not in line
    )
    return text


def write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def build() -> None:
    for source, target_name in ZRM_DICTIONARIES.items():
        source_path = ROOT / source
        zrm_text = source_path.read_text(encoding="utf-8")
        zrm_name = target_name.replace("mohu_flypy", "mohu_zrm")
        zrm_text = replace_dictionary_name(zrm_text, zrm_name)
        zrm_text = zrm_text.replace("mohu_tiger_fixed_simp", "mohu_zrm_tiger_fixed")
        write(source_path, zrm_text)
        target_path = ROOT / source.replace("mohu_zrm", "mohu_flypy")
        write(target_path, convert_dictionary(source, target_name))

    for source, target_name in FIXED_DICTIONARIES.items():
        source_path = ROOT / source
        zrm_text = source_path.read_text(encoding="utf-8")
        zrm_name = target_name.replace("mohu_flypy", "mohu_zrm")
        write(source_path, replace_dictionary_name(zrm_text, zrm_name))
        target_path = ROOT / source.replace("mohu_zrm", "mohu_flypy")
        write(target_path, convert_fixed_dictionary(source, target_name))

    extended = (ROOT / "mohu_zrm.extended.dict.yaml").read_text(encoding="utf-8")
    extended = T2S.convert(extended)
    extended = replace_dictionary_name(extended, "mohu_zrm.extended")
    extended = extended.replace("mohu.chars", "mohu_zrm.chars")
    for suffix in ("base", "words", "tencent", "computer", "moe"):
        extended = extended.replace(f"mohu.{suffix}", f"mohu_zrm.{suffix}")
    write(ROOT / "mohu_zrm.extended.dict.yaml", extended)
    flypy_extended = extended.replace("mohu_zrm", "mohu_flypy").replace(
        "# 自然码单字表", "# 小鹤单字表"
    )
    write(ROOT / "mohu_flypy.extended.dict.yaml", flypy_extended)

    custom = T2S.convert((ROOT / "mohu_zrm_custom_phrases.txt").read_text(encoding="utf-8"))
    custom = custom.replace("mohu_custom_phrases", "mohu_zrm_custom_phrases")
    custom = custom.replace("mohu.extended", "mohu_zrm.extended")
    write(ROOT / "mohu_zrm_custom_phrases.txt", custom)
    write(
        ROOT / "mohu_flypy_custom_phrases.txt",
        custom.replace("mohu_zrm", "mohu_flypy"),
    )

    for filename, (schema_id, display_name) in SCHEMAS.items():
        path = ROOT / filename
        zrm_text = normalize_schema(path, schema_id, display_name)
        write(path, zrm_text)
        write(ROOT / filename.replace("mohu_zrm", "mohu_flypy"), flypy_schema(zrm_text))


if __name__ == "__main__":
    build()
