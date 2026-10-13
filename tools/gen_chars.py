# gen_chars.py -- 生成單字表

import argparse
import sys
from collections.abc import Iterator
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from modern_readings import load_modern_readings, simplified_reading_weight
from tiger_aux import load_master_compatibility_characters, AuxiliaryEntry
from utils import aux_table, freq_simp_table, freq_trad_table, get_chars_version
from zrmify import zrmify


def _default_syllable(pinyin: str) -> str:
    return zrmify(pinyin)


def generate_chars(
    *,
    simplified: bool = False,
    syllable_of=None,
    aux=None,
    master_dict: Path | str | None = None,
    reading_normalization: dict[str, str] | None = None,
    header_comment: tuple[str, ...] | None = None,
    on_skip=None,
) -> Iterator[str]:
    """逐行产出单字表内容；CLI 与自定义方案构建共用。

    syllable_of：拼音音节 → 双拼码（默认自然码）。自定义方案传布局
    引擎的 to_code；reading_normalization 传布局引擎的读音归一表
    （如 n→en，与 zrmify 的既有折算一致）。
    aux：字 → AuxiliaryEntry（默认 utils.aux_table 虎码辅码）。
    master_dict：兼容打法判定读的主码表（默认 mohu_zrm.dict.yaml）。
    """
    if syllable_of is None:
        syllable_of = _default_syllable
    if aux is None:
        aux = aux_table
    if master_dict is None:
        master_dict = Path('mohu_zrm.dict.yaml')
    if reading_normalization is None:
        reading_normalization = {}
    master_dict = Path(master_dict)

    if simplified:
        freq_table = freq_simp_table
        modern_readings = load_modern_readings(Path('tools/data/pinyin_simp.txt'))
        compatibility_characters = load_master_compatibility_characters(
            master_dict, aux,
        )
    else:
        freq_table = freq_trad_table
        modern_readings = None
        compatibility_characters = set()

    if header_comment is None:
        header_comment = (
            '# 【单字基础词库｜自动生成，请勿直接编辑】',
            '# 用途：提供单字读音、字频、虎码辅码等数据，参与整句词库和原生词表构建。',
            '# 来源：mohu_zrm.dict.yaml、tiger.dict.yaml 及 tools/data/chars.txt 等。',
            '# 构建：make chars 或 make quick；修改入口不是本文件。',
            '# AUTO-GENERATED. DO NOT EDIT.',
        )
    for line in header_comment:
        print(line)
    header = open('tools/data/chars.dict.yaml').read()
    header = header.replace('YYYYmmdd', get_chars_version())
    for line in header.splitlines():
        fields = line.split('\t')
        if len(fields) >= 2 and len(fields[0]) == 1 and ';' in fields[1]:
            spelling, _, _ = fields[1].partition(';')
            entry = aux[fields[0]]
            if isinstance(entry, AuxiliaryEntry):
                fields[1] = spelling + ';' + entry.normal
                line = '\t'.join(fields)
        print(line)
    print()

    for ((char, py), w) in freq_table.items():
        if modern_readings is not None:
            w = simplified_reading_weight(char, py, w, modern_readings)
        try:
            sp = syllable_of(reading_normalization.get(py, py))
        except (ValueError, IndexError, TypeError):
            # 自定义布局无法编码的读音（生产 zrmify 是全函数不会走到这里）
            if on_skip is not None:
                on_skip(char, py)
            continue
        entry = aux[char]
        print(f'{char}\t{sp};{entry.normal}\t{w}')
        # 兼容打法（13/14 位）：主表明确保留的目标字使用全权重，其余字低权重，
        # 不与正常辅码的首选竞争。
        rescued = (
            char in compatibility_characters
            and modern_readings is not None
            and (char, py) in modern_readings
        )
        compat_weight = w if rescued else 0
        for compat in entry.compat_codes():
            print(f'{char}\t{sp};{compat}\t{compat_weight}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--simplified', action='store_true')
    cli_args = parser.parse_args()
    generate_chars(simplified=cli_args.simplified)
