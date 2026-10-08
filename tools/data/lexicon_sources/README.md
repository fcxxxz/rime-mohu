# 整句词库构建原料

这些文件是仍在使用的来源数据，不是旧码表存档，也不直接进入发布包。

- `zrm/`：自然码来源；`words` 是人工补充词，`base/tencent/moe` 是维护中的词库，`chars` 是读音与辅码生成结果，`classics/wanxiang` 是有来源与许可的导入数据。
- `flypy/`：从自然码来源转换得到的构建中间数据。
- `tools/build_sentence_dictionary.py` 按原导入顺序生成根目录的 `mohu_zrm.words.dict.yaml` / `mohu_flypy.words.dict.yaml`，保留读音、编码、缺省值和频率，不自行合并频率或重新分配编码。
- 发布包只包含合并后的词库，不复制本目录。
- 加整句词只改 `zrm/mohu_zrm.words.dict.yaml`；字词简码仍只改根目录 `mohu_zrm.dict.yaml`。

Git 只维护自然码的词库来源。`flypy/`、`zrm/mohu_zrm.chars.dict.yaml` 和根目录合并词库均是忽略的构建产物；`make quick` 可从干净 checkout 完整重建，不再在仓库重复保存大型合并数据。
