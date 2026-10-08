# 魔虎 V5 训练语料清单（含下载地址与收集状态）

> 模型：`mohu-sentence-ngram-v5.bin` = `research/lm_sentence_compare/report.md` §18
> 的蒸馏 v5 单文件（训练产物 SHA-256 `c2c148ea…`，线上部署 u8 量化 389.7MB）。
> 整理：2026-09-24；同日完成可收集原始件的补收，收档于
> `research/_downloads/v5_sources/`（gitignored，不入库）。

## 总口径

- **语料池**：`research/lm_sentence_compare/corpus/fenci/`，共 **43,675,172 句**
  （2026-09-22 纯虎 10k 审计口径，见 `docs/reports/2026-09-22-pure-tiger-whole-sentence-10k.md`）。
- **实际入模**：`compose_mix.py` 按配比采样 + 全局去重后的混料（约 3.1 千万句级，
  修正整数重复逻辑重组同一配比 = 31,413,998 行）。
- **谱系**：v1 七源 → v2 +书籍八语域 → v3 +LLM 神经蒸馏 → v4 +万象词表 →
  v5 可追溯复核冠军（v10/v11 实验未超越，v5 保持部署）。

## 训练语料构成（11 源 + 书籍 + 词表 + 合成）

句数为提取后口径（report.md §12 七源表 + 本地实测）。

| 源 | 角色 | 提取句数 | 本地原始件 | 下载地址 | 收集状态 |
| --- | --- | ---: | --- | --- | --- |
| LCCC large | 日常口语（主） | 1,104 万 | `_downloads/lccc_large.jsonl.gz`（561MB，gzip CRC ✓） | https://huggingface.co/datasets/silver/lccc_large （`lccc_large.jsonl.gz`；CC-BY-NC-SA 4.0，仅研究） | 在库 |
| MNBVC OSCAR 2022.01 | 现代网页（新词主力） | 1,233 万 | `_downloads/oscar_202201.part_0000-0024.jsonl.gz`（25 分片 14GB，CRC ✓） | https://huggingface.co/datasets/oscar-corpus/OSCAR-2201 （选 `zhs`）；https://oscar-project.org | 在库 |
| 人民日报 1946-1990s | 正式新闻 | 5,306,184 句 | ⚠️ 原始分片已不可再生（见下节）；`fenci/rmrb.txt`（277MB）完好 | **已定位出处：MNBVC `news/20230196`**（文件路径含 `rmrb/7z/1947年09月/…`，即人民日报历史 OCR 库的 MNBVC 打包）：https://huggingface.co/datasets/liwu/MNBVC → `news/20230196/0-11.jsonl.gz` | **2026-09-24 已重收 12 片 2.0GB** → `_downloads/v5_sources/rmrb/`（CRC 全过）。注意：现存 12 片与当年所用的 45+ 片旧批次**不是同一批**，400 锚句抽样与 rmrb.txt 重叠率仅 0.25%（同源不同批次，年代分布吻合 1980-90s 为主） |
| MNBVC wiki 2023 | 词条书面 | 268 万 | `_downloads/mnbvc_pull/wiki/0-15.jsonl.gz`（2.2GB，CRC ✓；第 7 片 117MB 本身就是小片，非截断） | https://huggingface.co/datasets/liwu/MNBVC → `wiki/20230197/0-15.jsonl.gz` | 在库 |
| 知乎 KOL | 问答长文 | 171 万 | `_downloads/mnbvc_pull/zhihu/train-0000{0..4}-of-00005-*.parquet`（1.5GB，PAR1 ✓） | https://huggingface.co/datasets/wangrui6/Zhihu-KOL → `data/` 下 5 个 parquet | **2026-09-24 补齐**：`pull_hf.sh` 原脚本第 5 片文件名哈希写错（`ba61a53a`，HF 返回 Entry not found，当年实际只下成 4/5 片）。真实文件名 `train-00004-of-00005-c374cc9fbda9fda7.parquet`（299,200,473 字节），已补下并修正脚本 |
| THUCNews | 新闻域 | 与下合计 73 万 | ⚠️ 官方原包从未留档（`extract_thuc.py` 消费的 /tmp 分块已清空）；`fenci/thuc.txt`（36MB）完好 | 官方需注册：http://thuctc.thunlp.org/message （THUCTC）。可直下镜像：https://huggingface.co/datasets/SirlyDreamer/THUCNews → `THUCNews.jsonl`（2.22GB，title/text/label 全量 jsonl）；备选 14 类分片版 https://huggingface.co/datasets/Tongjilibo/THUCNews | **2026-09-24 已收镜像** → `_downloads/v5_sources/thucnews/THUCNews.jsonl`（字节数精确匹配，首尾行 JSON 合法） |
| TNews（CLUE） | 新闻域 | （与上合计） | ⚠️ 原包未留档；`fenci/tnews.txt`（4.1MB）完好 | https://storage.googleapis.com/cluebenchmark/tasks/tnews_public.zip | **2026-09-24 已收** → `_downloads/v5_sources/tnews/tnews_public.zip`（5.1MB，train/dev/test 齐全） |
| 书籍语域 | 泛化主力（v2 起） | 千万句级 | `corpus/fenci_books/*.txt`（392MB，21 文件：`books_weread_*` 12 语域 + `books_lit_*` 9 集，符号链接可读 ✓）+ `_downloads/400+本高质量完本合集.rar`（1.5GB） | **无公开地址**：weread 源为用户 NAS 精校库；lit/400+本为用户自集 | 在库（NAS 在则可再生） |
| 万象九张领域表 | 诗词/文言/专有（v4 起，×3 权重） | 715,082 词条切句 | **2026-09-24 重收** → `_downloads/v5_sources/wx_tables/*.dict.yaml`（33MB，9 文件字节数与 GitHub HEAD 一致） | `https://raw.githubusercontent.com/amzxyz/rime-wanxiang/wanxiang/dicts/{shici,lianxiang,diming,yixue,huaxue,yaopin,mingren,renming,wuzhong}.dict.yaml`（镜像：HF `amzxyz/rime-wanxiang` 的 `dicts/`） | 在库（随时可重拉） |
| LLM 神经蒸馏造句 | 错误驱动合成（v3 起） | 2,404 句 ×8 | `fenci/llm_distilled.txt`（108KB） | 非下载：`train_model/qwen_generate.py` 本地 Qwen3.5 生成 | 在库 |

## 明确不在 V5 训练集的近邻语料

| 文件 | 用途 |
| --- | --- |
| `corpus/fenci/forum.txt`（天涯 2026-03，26MB） | **中立探针专用**（`corpus/testset/neutral_v1`），报告明确「我们未训」 |
| `corpus/fenci/weibo.txt`（924KB） | v5 之后的口语补强实验（`pull_oral_boost.sh`：MNBVC forum 增量 + `dirtycomputer/weibo_senti_100k`），未进入部署模型 |

## 复现链

`train_model/`：`pull_hf.sh`（wiki+知乎）→ `pull_wx_tables.sh`（万象九表）→
`extract_new.py`（rmrb/OSCAR/wiki/LCCC）→ `extract_zhihu.py` → `extract_thuc.py` →
`segment_any.py`（切句）→ `compose_mix.py`（配比混料）→ `train_tcsknm.cc` →
`merge_models.py`（蒸馏）。词频/词表数据另见 `research/_downloads/README.md`。

## 数据完整性事件记录（2026-09-24 盘点 + 补收）

1. **rmrb 原始分片已被覆盖为 HTML 错误页**：`_downloads/12-33.jsonl.gz`（每个 96K）
   全是 HuggingFace `wiki/20230197/12.jsonl.gz` 的错误页——某次 wiki 重下用了裸数字
   文件名把原 rmrb 分片冲掉了；幸存的 `45.jsonl.gz`（168M，gzip CRC ✓）内容实为
   MNBVC zhwiki，**不是**人民日报，只是同批误存件。原始 45+ 片旧批次无法从公开
   渠道复原（MNBVC 现存 12 片为另一批次）。`fenci/rmrb.txt`（5,306,184 句）是
   **唯一与 V5 训练严格对应的 rmrb 中间产物，请重点备份**。
2. **知乎 KOL 第 5 片当年就没下成**（脚本文件名错误，本机只剩 4KB 错误页）——
   即 V5 的 zhihu.txt（77MB，171 万句）实际提取自 4/5 片。2026-09-24 已按正确
   文件名补齐第 5 片，原始件现为完整 5/5。
3. **THUCNews / TNews 官方原包从未入过 `_downloads`**（提取中间产物一直只有
   fenci txt）。2026-09-24 已补收可用镜像/原包（见上表）。
4. 其余在库原始件 43 个 gzip（oscar 25 + wiki 16 + LCCC + 45.jsonl.gz）于
   2026-09-24 全量 `gzip -t` CRC 校验通过。

## 对照方法（MANIFEST）

`research/_downloads/v5_sources/MANIFEST.sha256` 覆盖上述全部原始件与不可再生
中间产物（fenci 11 txt + fenci_books 21 txt + testset），共约 26GB。在仓库根目录：

```sh
shasum -a 256 -c research/_downloads/v5_sources/MANIFEST.sha256
```

不含 `weread_top100/`（书库跑分语料，63GB，非 V5 训练）与 `corpus/extracted/`
（衍生品）。重跑训练前先 `-c` 一遍即可确认原料未损坏/未变动。

## 下载注意

- HuggingFace 直连有 DNS 污染：仓库脚本用 `--resolve huggingface.co:443:13.35.202.40`
  硬解析（见 `pull_hf.sh`）；也可整体把 `huggingface.co` 换成 `hf-mirror.com` 前缀
  镜像（2026-09-24 实测 `resolve/main` 路径在镜像可用）。
- 许可提醒（report.md §12/§17）：LCCC CC-BY-NC-SA 4.0、THUCNews/TNews 仅研究用途，
  **模型限个人与研究使用，商业分发需换语料重训**。
