# V5 错题集后训练：三模型对比与判决（2026-09-27）

> 语料：微信读书书库全量 3.02 亿句（A 轨纯码表口径）产出的 748 万清洗
> 错题对（[[clean/pairs.jsonl]]，剔重/剔坏 gold 后）；炼法 = 全量重训字符
> ngram（train_tcsknm，TCSKNM02 f32 → u8 量化）。对照组设计：A = fenci
> 全量 30 文件重训（隔离"重训本身"的影响，注意排除 forum/weibo——中立
> 探针纪律），B = A + gold_sentences ×2 上采样。

## 核心数字

| 指标 | 老 V5（部署版） | A（fenci 全量重训） | **B（+错题×2）** |
|---|---:|---:|---:|
| 书库随机 5 万句 top-1（主战场） | 97.35% | — | **99.46%（+2.11pp）** |
| fresh10k 未见卷（现代混合域，10k） | 99.37% | 99.32% | 99.07%（−0.30pp） |
| tier1 错题翻回率（gap<2，30k 抽样） | 0%（口径自洽） | 40.7% | **99.8%** |
| 模型体积（u8） | 390MB | 432MB | 625MB |

口径注：fresh10k 本轮用合并码表词典（17,672 字）+ all_ranks=0，与
table10k_r1（60K 字表）非同一口径；三模型同条件对比有效。fresh10k 与
gold_sentences 零重叠（blake2b 全量核对）。

## 判读

1. **书库域大涨**：97.35%→99.46%，错误率 2.65%→0.54% 砍掉八成。错题
   语料的价值被完全兑现（部分是记忆、部分是文言搭配的泛化——书库随机
   抽样里训练句只占 ~2.5%，2.11pp 增益绝大部分来自泛化）。
2. **A 的发现**：fenci 全量（1.3 亿行含书间重复）重训本身就翻回 40.7%
   错题、fresh10k 仅 −0.05pp——当年 31.4M 配比采样换成全量几乎无损，
   且模型仅大 10%。这说明 V5 的"错题"相当一部分是当年采样没采到的句子。
3. **B 的代价**：fresh10k −0.30pp（25-30 句从对变错）——文言错题 ×2
   （1,497 万句）拉高文言域权重、现代域相对稀释，属可预期的域交换。
4. **部署建议**：B 净收益显著为正（书库 +2.11pp ≈ 每百万句多对 2.1 万，
   vs fresh10k −0.30pp）。若在意现代域，可再训 B'（gold ×1）找平衡点；
   或错题语料按类别配平（压历史占比 60%→30%）。

## 复现

- 训练：`research/tiger2code_bench/v5_train.sh`（train_tcsknm min_bi=1
  min_tri=1，fenci 30 文件排除 forum/weibo；B 额外传 gold_sentences 两遍）
- 量化：`prune_tiger_ngram <f32> <out> --format u8`
- 评估：`eval_tier1.py --model … --tag …`（翻回率）；`fresh10k_bench.py
  --lexicon tiger2_merged.lexicon.txt --all-ranks 0 --model …`
- 产物：`v5train/modelA_u8.bin` / `modelB_u8.bin`（f32 同目录）
