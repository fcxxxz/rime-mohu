# gen_chars.py -- 生成單字表

import argparse

from modern_readings import load_modern_readings, simplified_reading_weight
from tiger_aux import load_master_compatibility_characters
from utils import *
from zrmify import zrmify

parser = argparse.ArgumentParser()
parser.add_argument('--simplified', action='store_true')
args = parser.parse_args()

if args.simplified:
    freq_table = freq_simp_table
    modern_readings = load_modern_readings(Path('tools/data/pinyin_simp.txt'))
    compatibility_characters = load_master_compatibility_characters(
        Path('mohu_zrm.dict.yaml'), aux_table,
    )
else:
    freq_table = freq_trad_table
    modern_readings = None
    compatibility_characters = set()

print('# 【单字基础词库｜自动生成，请勿直接编辑】')
print('# 用途：提供单字读音、字频、虎码辅码等数据，参与整句词库和原生词表构建。')
print('# 来源：mohu_zrm.dict.yaml、tiger.dict.yaml 及 tools/data/chars.txt 等。')
print('# 构建：make chars 或 make quick；修改入口不是本文件。')
print('# AUTO-GENERATED. DO NOT EDIT.')
header = open('tools/data/chars.dict.yaml').read()
header = header.replace('YYYYmmdd', get_chars_version())
for line in header.splitlines():
    fields = line.split('\t')
    if len(fields) >= 2 and len(fields[0]) == 1 and ';' in fields[1]:
        spelling, _, _ = fields[1].partition(';')
        fields[1] = spelling + ';' + aux_table[fields[0]].normal
        line = '\t'.join(fields)
    print(line)
print()

for ((char, py), w) in freq_table.items():
    if modern_readings is not None:
        w = simplified_reading_weight(char, py, w, modern_readings)
    sp = zrmify(py)
    entry = aux_table[char]
    print(f'{char}\t{sp};{entry.normal}\t{w}')
    # 兼容打法（13/14 位）：主表明确保留的目标字使用全权重，其余字低权重，
    # 不与正常辅码的首选竞争。
    rescued = (
        char in compatibility_characters
        and modern_readings is not None
        and (char, py) in modern_readings
    )
    compat_weight = w if rescued else 0
    for aux in entry.compat_codes():
        print(f'{char}\t{sp};{aux}\t{compat_weight}')
