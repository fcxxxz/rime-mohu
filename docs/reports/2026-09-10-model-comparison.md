# TinyCharLM 与 C2 同方法生产菜单对比（2026-09-10）

回答同一个问题：在完全相同的测试方法下，`neural_rerank` 背后应该保留 TinyCharLM 还是 C2。

## 方法

- 评估器：`research/semantic_student/compare_production_models.py`（本次新建，模型中立）。
- 数据：两套已捕获的真实生产菜单配对语料（按 `menu_index` 建立候选身份，按 `case_id` 去重）。
  - probe `pipeline-prod-paired-v2.jsonl`，sha256 `f442a3a2…24`，可评估菜单 45,374。
  - tnews `pipeline-tnews-paired.jsonl`，sha256 `88762083…ce`，可评估菜单 44,596。
- 基线：捕获的生产菜单顺序本身（production slot 0 为生产首选）。
- 共同外层策略（两模型完全一致）：
  - top-10 候选窗口；上下文取前 96 字符；候选文本取前 16 字符。
  - 冻结槽：单字、`⚡️` 简快、`📌` 置顶；冻结槽保持原位。
  - **保护首位修正**：当 slot 0 是冻结槽时，处理后的首选必须仍是 slot 0（旧 `production_eval.py` 在此错误地把最佳可动候选当作处理后首选，会虚增纠正数）。
  - V5 歧义门控 z-margin 0.5；首位翻转语义 margin 0.15；同分稳定按原索引。
  - 缺失 V5 分数、模型错误、非有限分数一律 fail-open 保持生产顺序。
  - 配对 bootstrap 10,000 次、seed 7，对每菜单 top-1 净变化求 95% CI。
- 两种决策口径：
  - **主口径（模型分决定）**：活动槽按模型 z 分排序，这是 C2 的部署形态。
  - **融合口径（fusion 0.5）**：`(1-w)·V5原生z + w·模型z`，w=0.5，模拟 TinyCharLM 原生分数融合形态。
- 模型工件：
  - C2：`checkpoint-epoch2.pt`，sha256 `cb82f3b1…de`，33.5 MB，8.72M 参数，PyTorch CPU。
  - TinyCharLM：`mohu_student/student_shared_kv.onnx(+.data)`，sha256 `667892ef…cb`，162 MB，ONNX Runtime CPU。
- 输出仅含聚合计数、哈希、延迟与错误类别；不含上下文或候选文本。

## 主口径结果（fusion 1.0）

| 指标 | C2 probe | TinyCharLM probe | C2 tnews | TinyCharLM tnews |
|---|---|---|---|---|
| 可评估菜单 | 45,374 | 45,374 | 44,596 | 44,596 |
| 门控参与 | 6,738 (14.8%) | 6,738 | 7,979 (17.9%) | 7,979 |
| 实际打分菜单 | 6,708 | 6,708 | 7,969 | 7,969 |
| 生产 top-1 | 94.99% | 94.99% | 97.56% | 97.56% |
| 处理后 top-1 | 95.03% | 91.86% | 97.63% | 87.31% |
| 纠正 | 71 | 585 | 54 | 196 |
| 回退 | 54 | 2,007 | 22 | 4,764 |
| 纠正:回退 | 1.31 | 0.29 | 2.45 | 0.04 |
| 回退率 | 0.119% | 4.42% | 0.049% | 10.68% |
| 净变化 pp | +0.037 | **-3.134** | +0.072 | **-10.243** |
| 95% CI pp | [-0.011, +0.086] | [-3.352, -2.916] | [+0.034, +0.112] | [-10.543, -9.952] |
| 打分延迟 p50/p95 | 12.5 / 66.9 ms | 12.9 / 46.7 ms | 11.4 / 14.1 ms | 11.9 / 19.3 ms |

## 融合口径结果（fusion 0.5）

| 指标 | C2 probe | TinyCharLM probe | C2 tnews | TinyCharLM tnews |
|---|---|---|---|---|
| 纠正 | 111 | 483 | 72 | 172 |
| 回退 | 129 | 2,027 | 306 | 4,822 |
| 纠正:回退 | 0.86 | 0.24 | 0.24 | 0.036 |
| 净变化 pp | -0.040 | **-3.403** | -0.525 | **-10.427** |
| 95% CI pp | [-0.106, +0.026] | [-3.619, -3.187] | [-0.610, -0.442] | [-10.723, -10.138] |

融合不救 TinyCharLM；C2 在融合下反而受损（其输入已含原生分特征，再融合等于重复计入）。

## 保护首位修正对历史 C2 数字的影响

旧报告（`2026-09-08-semantic-student-v1-launch.md`，由带缺陷的 `production_eval.py` 产出）：

- probe：89 纠正 / 54 回退，+0.077pp，CI [+0.026, +0.128] → 修正后 71/54，+0.037pp，CI [-0.011, +0.086]（CI 含 0，probe 上不再显著为正）。
- tnews：63/22，+0.092pp，CI [+0.052, +0.135] → 修正后 54/22，+0.072pp，CI [+0.034, +0.112]（仍显著为正）。
- 虚增来源：probe 30 个、tnews 10 个保护首位门控菜单中，分别 18 个和 9 个被旧评估器错误记为“纠正”。

## 结论

- **同方法下 C2 全面胜出**。四种组合（2 语料 × 2 口径）中只有 C2 主口径产生净收益；TinyCharLM 全部为深度负收益且回退率高达 4.4%–10.8%。
- TinyCharLM 之前的 95.65%/99.48%/98.73% 来自精选与合成同码干扰集，不反映真实生产菜单表现；它是字级条件概率打分器，不感知生产 rank/V5 分数等 IME 证据，在歧义菜单上 41% 的情形与生产顺序相左且大多为错。
- 建议保留 C2 作为 `neural_rerank` 唯一后端；TinyCharLM 退出生产接线（研究资产保留）。
- 待办（另行确认后执行）：schema 接线收敛、原生 dylib/ONNX 下线、Lua 字节序修正（若 C2 保留）、文档更新。

## 工件

- 评估器：`research/semantic_student/compare_production_models.py`；策略单测 `tests/test_semantic_model_comparison.py`（6 例）。
- 主口径结果：`research/semantic_student/runs/model-comparison-2026-09-10.json`。
- 融合口径结果：`research/semantic_student/runs/model-comparison-2026-09-10-fusion05.json`。
