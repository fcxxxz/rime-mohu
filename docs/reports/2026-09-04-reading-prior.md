# 读音先验：修复多音字罕用读音拼出的首选（mohuz→万虎）

> 2026-09-04。问题来自用户实测：`mohuz` 首选是「万虎」。本文记录根因、
> 修复（引擎 + 码表数据 + 配置旋钮）与验证数字。跨候选背景见
> [cross-candidate-ordering.md](../knowledge/cross-candidate-ordering.md) §3。

## 现象与根因

`mohuz` = `mo + hu + 末辅z`（5 键加辅输入）。按合并行为设计，两字辅码终态
排序由原生句模型接管，于是：

1. `hu` 音节带辅码 z 的字只有「虎」有真实频度（391,382），其余权重为 0；
2. 不存在任何 mó/mò + hǔ 真实词，全部候选都是单字拼凑；
3. 字符级三元模型**只认字的文本频率、不认读音**：「万」全局名次 285，
   P(万)·P(虎|万) = −13.75 压过莫虎（−14.68）等一切自然读音组合。

而 mò 读音在源数据里几乎不存在（`tools/data/chars.txt`：`万 mo 简频=1`，
仅用于复姓万俟；`万 wan` = 1,201,402）。该信息在两条链路上都断了：

- 旧原生码表 `mo 万 4 285`：rank 与 freq_rank 都是**全局字频**，不是读音条件频率；
- 引擎评分不消费它们：`all_ranks` 模式分数优先、rank 仅平局裁决
  （`kRankPenalty=0.03` 压不住 0.93 分差），`freq_rank` 只用于 >3000 名次
  的生僻字孤立惩罚——对「万」这种全局高频字永不触发。

## 修复（方案 A：读音条件先验）

贝叶斯上，字符 LM 给出 P(字序列)，码表似然项补上 P(码|字) = P(读音|字)：

- **码表**：可选第 5 列 `reading_freq`（读音条件简频）。由
  `tools/build_mohu_lexicons.py` 构建时从 `mohu_zrm.chars.dict.yaml`
  权重列自动并入（该列与 chars.txt 读音简频同源，如 `万 mo;fp 1`）；
  源码表 `mohu_tiger.lexicon.txt` 保持 4 列不变；飞键变体行回溯原音节
  挂同一频率。覆盖率 41,104/41,130 单字行（缺的 26 行是「啊/阿」类
  单韵母音节，保持中性 = 现状）。
- **引擎**（tigerengine.cc）：装载期按 (字, 音节) 去重取最大、按字求和，
  先验 `log((f+0.5)/(total+0.5))` 存入词条；expand 循环里以
  `reading_prior_weight · prior` 并入路径分（mass_score 随 score 差分
  自动一致）。万/mo ≈ −14，主读音 ≈ 0，多字词与个人词恒中性。
  新 ABI `tiger_engine_set_reading_prior_weight`（[0,4]，默认 1.0）。
- **Lua/schema**：`tiger/reading_prior_weight`（默认 1.0，0 关闭）；
  旧 ABI dylib 无该函数时静默用引擎内建默认，旧 4 列码表任何权重下中性。

## 验证

静态模型（无用户层）直连引擎，500 词池 = 频表前 300 + 随机 200（二字词，
编码取自词典去辅码）：

| 档位 | 旧码表 | 新码表 | top-1 变化 |
|---|---:|---:|---:|
| 4 键裸双拼 | 289/500 命中词典词 | 289/500 | **0**（排序逐词不变） |
| 一位末辅（tail1） | 400/500 | **407/500** | 17 翻转，可见修好 8、修坏 0 |
| 两位末辅（tail2） | 406/500 | **410/500** | 11 翻转，net +4，无可见修坏 |

翻正案例全是万虎同类多音字：`误闯`（旧「恶疮」，恶 wù 罕用）、`降班`
（旧「强军」，强 jiàng 罕用）、`朔月`（旧「数月」，数 shuò）、`底站`
（旧「的站」，的 dǐ）、`整个`（旧「整合」，整 zhěng? 拼字档）。其余
翻转均为 junk→junk（如 栗苞 档位下 力薄→李葆），无真实词受损。

关键探针：`mohuz` 首选 万虎→莫虎，万虎跌出前十；`mohup` 首选仍为
「模糊」（−9.08→−9.78，名次不变）。

测试：`tests/tigerengine_reading_prior_test.cc`（开关可逆、范围校验、
mohuz/mohup 断言，make tigerengine-reading-prior）；`tests/test_mohu_lexicons.py`
扩展第 5 列与飞键回溯用例（10 项全绿）；引擎 safety/lua-safety/
user-model/context 与 mohu_tiger_* Lua 测试全通过。

**权威五方案末辅基准未重跑**：`sentence-ngram-mobile.bin` 与 /tmp 模板
资产已不在本机，重跑需按 `research/lm_sentence_compare/run_cross_candidate.py`
头部注释重新准备。本报告的引擎级对比覆盖被改动组件（native 解码 fresh
排序）与受影响输入档位；published 96.81%/97.25% 口径的重验留作后续。

## 部署（本机）

`libtigerengine.dylib` + `mohu/data/zrm/mohu_zrm.lexicon.txt` 已装入
`~/Library/Rime/mohu/`（备份后缀 `.bak-20260904-140309`）。**需完全退出
并重启 Squirrel**（dylib 句柄按进程生命周期复用），无需重新部署方案。
回滚＝恢复两个 `.bak` 文件并重启。
