# gen_zrmdb.py -- 生成 zrmdb.txt

# zrmdb.txt 格式:
# 字 tab 碼1 space 碼2 space 碼3 ...
# 含正常辅码（12 位）与 13/14 位兼容打法。

from utils import *

for (char, entry) in aux_table.items():
    print(f'{char}\t{" ".join(entry.codes())}')
