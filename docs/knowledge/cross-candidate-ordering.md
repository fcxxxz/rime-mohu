# 魔虎跨候选调频（上下文候选重排）知识库

> 2026-09-02 词级上下文重排上线时沉淀。面向后续会话与维护者：架构在哪、
> 机制为什么这样设计、怎么测、坑在哪、下一步是什么。当前数字与完整排名以
> [频表顺序五方案基准](../reports/2026-09-02-cross-candidate-ordering-frequency-ranked.md)
> 为准；[实现报告](../reports/2026-09-02-word-order-cross-candidate.md)、
> [前报](../reports/2026-09-02-cross-candidate-ordering-benchmark.md)和
> [旧全量审计](../reports/2026-09-02-cross-candidate-ordering-audit.md)保留为历史工程测量。

## 1. 一页纸现状

- **功能**：`contextual_order`（跨候选调频）开启且上屏历史含汉字时，
  引擎对菜单前 N 个 smart 候选按上下文续写分重排。四档输入（纯双拼/
  首辅/末辅/首末辅）全覆盖：纯双拼走本 filter，辅码档走引擎解码左上下文
  （前报附录 A），互不重叠、无双重计分。
- **默认配置**（两个 llm schema 的 `tiger/` 节）：`word_order_signal: char`
  （字符续写分，万象同型机制）、`word_order: true`、
  `word_order_candidates: 20`、`word_order_rank_penalty: 1.0`。
- **当前权威指标（频表顺序，1,000 词 / 3,357 case）**：纯双拼在各方案前缀可用子集上，魔虎自然码 2,771/2,949（93.96%）、小鹤 2,774/2,952（93.97%）上屏后第一候选正确；两者修好率约 60.4%，修坏率 1.76%。夜莺修好率 62.03% 略高，但上屏后第一候选为 91.88%，修坏率 4.43%。
- **共同前缀敏感性分析（833 词 / 1,641 case）**：魔虎两方案上屏后第一候选均为 1,547/1,641（94.27%），修好 120/194（61.86%），修坏 20/1,447（1.38%）；夜莺为 91.96%、62.70%、4.33%。它消除了各方案前缀成功集合不同造成的子集偏差。
- **万象 Pro 的口径**：Pro (`amzxyz/rime-wanxiang` 的 `custom/wanxiang_pro`)
  使用自然码双拼与虎码首末辅助码，`context_reorder.lua` 依赖本地自学习 1/2-Gram
  共现库，原生 `contextual_suggestions` 默认关闭。协议一每个模板只提交一次前缀，
  因而没有预热历史时四档均无变化；这不等同于夜莺使用的预训练 grammar 上下文能力。
- **魔虎 V5 的发布信号**：默认采用字符续写分；当前权威统一样本上，纯双拼修好率约 60.4%，共同前缀子集为 61.86%。词级评分和 MHCTN01 容器保留为实验兼容能力，不进入默认发布包。
- **分发**：本次使用的 V5 模型文件未改动；需分发更新后的 libtigerengine dylib/dll、Lua filter/桥接与 schema。只复制旧模型不能启用跨候选上下文行为。
- **当前基准口径（2026-09-02）**：从频表前 30,000 行筛选 1,000 个严格同音目标词，配最多四个真实前缀，共 3,357 case；五方案共享 target/context universe，分别使用自身原生双拼和辅码。全部 case 保留，候选 Top-5 可见性只作诊断；报告同时给出 case 加权、目标词等权、共同前缀子集、辅码补救与 57 组完整排名。零分母记为不适用且不排名。训练集与测试集尚未完成完整去重审计。
- **历史 32,976 条审计**：`2026-09-02-cross-candidate-ordering-audit.md` 保留旧状态表，但其 Moran 动态 `moran.extended` 依赖未完整编译，不能参与当前排名。

## 2. 架构地图

```
上屏历史 commit_history:latest_text()（与 librime GetPrecedingText 同源）
  │ contextual_order 开 && 含 CJK（字节 \228-\233）才继续，否则零成本直通
  ▼
lua/mohu_word_order_filter.lua   ← lua_filter，llm schema filters 第 4 位
  │   （mohu_reorder_filter 之后、candidate_override 之前：用户显式覆盖
  │    优先于模型重排；yield 是运行时注入的全局，不能提为 upvalue）
  │ 收集前 N 个「可重排」候选：跳过 punct/pinned/native(mohu_llm_*)/
  │ ⚡️📌 注释前缀/单字；punct/pinned/native/简码构成稳定前缀
  ▼
tiger_sentence_native/mohu_tiger_sentence.lua 的 acquire_char_scorer /
acquire_word_scorer → 返回 (score_fn, engine_handle)；引擎句柄由 translator
引用计数管理，模块级共享（跨 schema 共享安全：打分不依赖码表）
  ▼
libtigerengine（tigerengine.cc / tigerengine_lua.cc）
  tiger_engine_context_char_scores(h, 上文, '\n'拼接候选, n, out)
    = Σ logP(候选码点 | 上文末 2 个 CJK 字及已出字)   ← 默认信号
  tiger_engine_context_word_scores(..., window)
    = logP(词 | 上文尾部逆向最大匹配出的末 2 词)      ← 实验信号
  tiger_engine_load_word_scorer(h, path)  显式装独立 MHKNM01（覆盖用）
  MHCTN01 单文件容器：[64B 头][TCSKNM02 字符层][MHKNM01 词层] 一次 mmap，
  字符层解码行为与非容器逐字节一致；词层仅供 word 信号
  （tools/merge_tiger_models.py 合并，v6 产物 768MB 在 /tmp，未随包）
  ▼
Lua 融合：F_k = score_k − rank_penalty×(k−1)，稳定排序，第 k 名写回第 k 个
参与槽位（OOV/未参与槽位不动——回填只写参与槽位，见 §5 曾修的 bug）
```

- 词层可用性判定：`ensure_engine` 创建后读一次 `tiger_status`，
  `word_scorer=packed|explicit|primary|off`；旧 dylib（无该字段/函数）自动
  视为不可用。char 信号只要求引擎存在（主字符模型），不需要词层。
- 引擎侧评分成本：20 候选批量 0.012ms/次；word 信号若用容器有 ~3.5ms
  p95 冷页尾延迟（词层 mmap 页冷访存），char 信号无此问题——这是把默认
  信号从 word 切到 char 的两个原因之一（另一个是修好率 3 倍）。

## 3. 机制知识（为什么这样设计）

- **librime/万象（octagram）的真实机制**（读源码得出的关键结论，勿再凭
  印象）：`Evaluate = entry_weight + Query(上文, 候选, is_rear)`，即候选
  **真实权重（词典+userdb）与语法分加法融合**；Query 是**字符级搭配查表**
  ——上文末 ≤(collocation_max_length−1) 字＋候选前 ≤ 同数目的字，查 .gram
  库（`Rime::Grammar/1.0` 格式）取搭配 log 概率＋常数惩罚（搭配 −12/弱 −24/
  句尾 −18，查不到贡献常数 → 词频主导）。**没有词表、没有 OOV 概念**。
  「八股文」是插件名（octa+gram 双关）；狭义八股文模型（essay-bgw.gram）与
  万象模型（wanxiang-lts-zh-hans.gram）是同格式两个文件，机制共享、语料
  不同。
- **七变体对照实验结论**（research/lm_sentence_compare/word_order_tune.py，
  28,764 条协议一数据）：char_raw（字符续写裸分）>> word_raw >> 各种 lift
  与混合。修好率 45.05% vs 13.16%（同修反 ≤1.5%）。**lift（减空上文基线）
  反而更差**：裸分自带的频率/流利度成分与名次权重互补，octagram 用裸分
  是有道理的。penalty 平台区 0.95–1.4（0.95→修好 45.7%/修反 1.5%；
  1.4→40.3%/1.0%），无悬崖。
- **词信号的天花板不在语料量而在分词管线**：kn5 用 1.5GB 七源语料重训仅
  13.2→14.9%。根因：jieba 用户词典（mohu_userdict.txt）里「上/海/一/三」
  等单字被灌 4,000,000 级词频，log(上)+log(海)≫log(上海) →「上海/三国/
  一X/万X」系在全部语料中被拆成单字，词层永远零整词计数（gold 词 14%
  缺表+12% 零计数）。**将来升级词层先修分词词典，再谈语料**。
- 修好/修坏是激进度的两端：penalty 是唯一旋钮（纯双拼）。辅码档的上下文
  来自引擎解码播种，无 penalty 旋钮；具体修好/修坏应以当前统一样本报告为准，
  旧调参集上的 1.3–1.5% 与 7–14pp 仅是历史实现证据。

## 4. 基准方法论（协议一）与资产

- **协议**：每个 case 单独创建 session；先记录无上文四档候选，再尝试提交真实前缀，
  随后记录有上文四档候选。前缀失败仍保留为 `prefix_failed` 并计入全量可用性，
  仅上下文修好/修坏的条件分母排除不可用配对。判定：rank1 文本==gold。
- **当前可复现资产**：输入构建、隔离运行和聚合分别由
  `research/lm_sentence_compare/build_cross_candidate_cases.py`、
  `run_cross_candidate.py`、`cross_candidate.py` 完成；正式产物与逐 shard 哈希写入
  `/tmp/mohu-cross-candidate-homophone-v1/run-manifest.json`。每个 case 新建 Rime session，
  每个方案、条件和 shard 使用独立 user directory，且禁止模型路径解析到 live
  `~/Library/Rime`。
- **Moran 构建约束**：必须跑完整 isolated workspace `rime_deployer --build`，不能只
  `--compile moran.schema.yaml`。Lua 动态创建 `script_translator@smart`，部署器单 schema
  编译无法发现它；验收必须存在 `moran.extended.table.bin`、`moran.prism.bin`、
  `moran_fixed_simp.table.bin` 和 `moran_english.table.bin`。
- **历史资产**：旧 `/tmp/kua3`、`/tmp/kua-templates` 和 32,976 条结果只用于追溯早期
  工程实验，不再作为当前五方案排名来源。
- **分桶口径**（用户偏好的展示方式）：按目标词 fresh 名次分桶看 afterA
  翻正率；「重码词」在纯双拼下 100%（双拼本质），辅码的作用就是压重码。
- **魔然主方案必须用魔然编码**（in/moran.*）：前报补测误喂魔虎编码，
  辅码档 fresh 塌到 4%，曾误导出「固顶表不可比」的错误结论，已用
  moranmain2 条件（moran 模板 + moran.schema.yaml + in/moran.*）重测修正。
  模板 /tmp/kua-templates/moran 里有完整魔然家族 + 简/繁 essay gram。

## 5. 已修过的坑（别再踩）

1. **filter 回填 bug**：OOV 候选夹在已评分候选之间时，第 k 名必须写回
   「第 k 个**参与**槽位」而非第 k 个槽位，否则丢候选+复制候选。测试
   tests/mohu_word_order_filter_test.lua 有最小重现。
2. **char 分没有 OOV**：字符续写分是累加和（−16~−50 很正常），−19.9 阈值
   只适用于 word 信号。
3. **`yield` 不能提为模块级 upvalue**：它是 librime-lua 运行时注入的全局，
   加载期捕获得 nil。
4. **llm schema 不得出现 "octagram" 字样**：tests/mohu_llm_schema_split_test
   的子串断言（连注释都会踩）。
5. **延迟测量**：跨会话基线漂移 ~1.4ms，必须同会话交替配对、取每
   (id,mode) 多次中位数；后台训练进程会污染 p95（Δmax 20ms+ 毛刺）。
6. **`make test 2>&1 | tail` 会吞退出码**（管道取 tail 的 0）——查
   pipestatus 或直接跑。
7. mira 测试 `mohu_zrm::cross_candidate_order` 在 HEAD 即失败（与词级重排
   无关，已在干净提交复现）。
8. 新 lua_filter 组件本身有逐候选桥接开销（fresh 直通也有 ~+0.1ms p50/
   0.4ms p95）——延迟优化的方向是并入 mohu_reorder_filter，不是优化评分。

## 6. 遗留与后续

- **延迟 p95 临界**（0.5–0.7ms vs 0.5 线）：根因见 §5.8；方案=把重排并入
  mohu_reorder_filter（少一次桥接）。
- **词层升级路径**：修 mohu_userdict.txt 单字频率 → 重分词 → 重训
  （train_wordkn.cc，64GB 内存跑 35M 句峰值 ~14GB，~51 分钟）→ word/mix
  信号实验。kn5（/tmp/mohu-word-kn5.bin，797MB）不入库不上线。
- **上调空间**：penalty 降到 0.95 可到修好 45.7%/修反 1.5%（模拟口径），
  需要更激进时动 schema 默认即可，无需改代码。
- **Windows**：引擎源码已含全部功能，需用新源码重编 libtigerengine.dll
  （build.sh 是 macOS 的）。

## 7. 当前数字快照（2026-09-02，统一 3,357 case）

下表是纯双拼的 case 加权主指标；“上屏后”只在该方案成功提交前缀的配对上计算，因此另列共同前缀子集作敏感性分析。完整四档、目标词等权、辅码补救和 57 组排名见权威报告。

| 方案 | 直接第一候选 | 前缀可用 | 上屏后第一候选 | 上下文提升 | 修好率 | 修坏率 |
|---|---:|---:|---:|---:|---:|---:|
| 魔然 | 2983/3357（88.86%） | 1979 | 1798/1979（90.85%） | +2.07pp | 100/222（45.05%） | 59/1757（3.36%） |
| 夜莺 | 2968/3357（88.41%） | 2155 | 1980/2155（91.88%） | +2.88pp | 147/237（62.03%） | 85/1918（4.43%） |
| 万象 Pro（冷启动） | 2979/3357（88.74%） | 2361 | 2086/2361（88.35%） | +0.00pp | 0/275（0.00%） | 0/2086（0.00%） |
| 魔虎 V5（自然码） | 2964/3357（88.29%） | 2949 | 2771/2949（93.96%） | +5.29pp | 202/334（60.48%） | 46/2615（1.76%） |
| 魔虎 V5（小鹤） | 2964/3357（88.29%） | 2952 | 2774/2952（93.97%） | +5.25pp | 201/333（60.36%） | 46/2619（1.76%） |

共同前缀成功的 1,641 case（833 词）上，魔虎两方案均为上屏后 94.27%、提升 +6.09pp、修好 61.86%、修坏 1.38%；夜莺为 91.96%、+3.23pp、62.70%、4.33%。因此夜莺修好率略高，但魔虎最终首选率、净提升和抗修坏能力更强。

旧 32,976 条不等样本快照保留在历史实现报告和历史审计中。它们记录了当时的工程验收，但不能替代本节统一样本与完整依赖构建后的排名。

万象 Pro 的 0% 是冷启动协议结果：Pro 的 `context_reorder` 依赖已积累的本地
1/2-Gram 共现记录，不是随包预训练的 grammar。长期使用后的自学习收益应使用单独的
预热协议评估。
