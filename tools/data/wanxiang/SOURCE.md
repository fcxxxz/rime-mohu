# 万象词源

本目录保存从 [万象拼音](https://github.com/amzxyz/rime-wanxiang) 同步并转码的普通词库来源。

## 许可与改动

上游仓库标注为 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。本项目保留上游仓库链接、固定 revision、每个源文件的 SHA-256 和来源文件名；本项目对原始词表做了以下改动：

- 去除已存在于魔虎活动词表（chars/base/words/tencent/computer/moe/classics）中的词；
- 同一词多读音时按最高上游权重确定性选择单一读音；
- 由本项目重新生成自然码双拼和发布权重（固定 20），并为每个音节附加魔虎主辅码；
- 由自然码表自动生成小鹤双拼表；
- 不复制上游任何双拼编码或权重。

`manifest.json` 是同步状态的唯一记录（revision + 每文件 SHA-256）。`raw/` 是本地缓存的上游快照（不入库，可用 `sync` 按固定 revision 重新下载校验），`entries.tsv` 是规范化中间产物（同样不入库，保留上游权重供审计）。

## 来源文件

同步万象主方案 `dicts/` 下的全部普通词表，排除单字表 `zi`、英文表 `en`、混合表 `mixed` 和反查/配置文件：

- `dicts/jichu.dict.yaml`：基础词库
- `dicts/lianxiang.dict.yaml`：联想词库
- `dicts/cuoyin.dict.yaml`：错音词库
- `dicts/duoyin.dict.yaml`：多音词库
- `dicts/shici.dict.yaml`：诗词词库
- `dicts/diming.dict.yaml`：地名词库
- `dicts/yixue.dict.yaml`：医学词库
- `dicts/huaxue.dict.yaml`：化学词库
- `dicts/yaopin.dict.yaml`：药品词库
- `dicts/mingren.dict.yaml`：名人、学者、作家、企业家等公众人物
- `dicts/yiren.dict.yaml`：艺人
- `dicts/wuzhong.dict.yaml`：物种词库
- `dicts/renming.dict.yaml`：人名
- `dicts/taifeng.dict.yaml`：台风名词库
- `dicts/fangyan.dict.yaml`：方言词库

## 使用

- `uv run tools/sync_wanxiang.py sync`：按 manifest 固定 revision 下载并校验全部快照，生成词典。
- `uv run tools/sync_wanxiang.py update`：查询上游分支头，revision 变化时同步，无变化时正常退出。
- `uv run tools/sync_wanxiang.py build`：仅从本地快照重建（需要先 sync 过）。
- `uv run tools/sync_wanxiang.py check`：校验快照哈希并复现构建结果。
