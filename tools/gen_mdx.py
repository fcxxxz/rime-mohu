#!/usr/bin/env python3
"""Export the optional lookup dictionary using the same Tiger data as Rime."""
from __future__ import annotations

import argparse
from html import escape
from pathlib import Path

if __package__:
    from .gen_chaifen_filter import load_decompositions
    from .tiger_aux import TIGER_EQUIVALENTS, AuxiliaryEntry
    from .utils import all_chars, aux_table, pinyin_table
    from .zrmify import zrmify
else:
    from gen_chaifen_filter import load_decompositions
    from tiger_aux import TIGER_EQUIVALENTS, AuxiliaryEntry
    from utils import all_chars, aux_table, pinyin_table
    from zrmify import zrmify

ROOT = Path(__file__).resolve().parents[1]


def auxiliary_decompositions(
    char: str, auxiliary: AuxiliaryEntry, decompositions: dict[str, str],
) -> list[tuple[str, str]]:
    source_char = TIGER_EQUIVALENTS.get(char, char)
    description = decompositions.get(source_char, char)
    return [(code, description) for code in auxiliary.codes()]


def gen_fullcode(sps: list[str], chais: list[tuple[str, str]]) -> str:
    result = '<span class="section-label">全碼</span>'
    for sp in sps:
        for auxcode, _ in chais:
            result += f'<span class="fullcode-item">{escape(sp)};{escape(auxcode)}</span>'
    return result


def gen_aux(chais: list[tuple[str, str]]) -> str:
    return ''.join(f'<span class="chai-item"><ruby>{escape(code)}<rt>{escape(description)}</rt></ruby></span>'
                   for code, description in chais)


def gen_page(char: str, sps: list[str], chais: list[tuple[str, str]]) -> str:
    return (f'<link href="main.css" rel="stylesheet" type="text/css" />'
            f'<div class="char-card"><span class="character">{escape(char)}</span>'
            f'<div class="">{gen_fullcode(sps, chais)}</div>'
            f'<div class="chai">{gen_aux(chais)}</div></div>')


def main() -> None:
    from mdict_utils.base.writemdict import MDictWriter

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    decompositions = load_decompositions(ROOT / 'tools/data/tiger_chaifen.txt')
    pages = {}
    for char in all_chars:
        sps = [zrmify(pinyin) for pinyin in pinyin_table.get(char, [])]
        chais = auxiliary_decompositions(char, aux_table[char], decompositions)
        pages[char] = gen_page(char, sps, chais)
    writer = MDictWriter(pages, title='魔虎', description='魔虎虎码拆字')
    with args.output.open('wb') as output:
        writer.write(output)


if __name__ == '__main__':
    main()
