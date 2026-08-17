# gen_chars.py -- 生成單字表

import argparse

from modern_readings import load_modern_readings, simplified_reading_weight
from utils import *
from zrmify import zrmify

parser = argparse.ArgumentParser()
parser.add_argument('--simplified', action='store_true')
args = parser.parse_args()

if args.simplified:
    freq_table = freq_simp_table
    modern_readings = load_modern_readings(Path('tools/data/pinyin_simp.txt'))
else:
    freq_table = freq_trad_table
    modern_readings = None

print('# 自動生成，請勿編輯。')
print("# AUTO-GENERATED. DO NOT EDIT.")
header = open('tools/data/chars.dict.yaml').read()
header = header.replace('YYYYmmdd', get_chars_version())
for line in header.splitlines():
    fields = line.split('\t')
    if len(fields) >= 2 and len(fields[0]) == 1 and ';' in fields[1]:
        spelling, _, _ = fields[1].partition(';')
        fields[1] = spelling + ';' + aux_table[fields[0]][0]
        line = '\t'.join(fields)
    print(line)
print()

for ((char, py), w) in freq_table.items():
    if modern_readings is not None:
        w = simplified_reading_weight(char, py, w, modern_readings)
    sp = zrmify(py)
    for aux in aux_table[char]:
        print(f'{char}\t{sp};{aux}\t{w}')
