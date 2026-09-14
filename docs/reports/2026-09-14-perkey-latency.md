# 每键延迟回归定位与修复（补全复发 + 每键 fork 子进程）

日期：2026-09-14。症状：用户体感「打前面的字有点卡」——每段输入的
**前几个键**卡，与整句（>4 键 native 解码）无关。语义开关（C3）当时已关，
排除语义推理因素。

## 方法

隔离工作区（/tmp 克隆线上 ~/Library/Rime 全部配置与用户数据，独立部署），
自建 librime C API 逐键探针（`process_key` + 菜单读取分别计时；Squirrel
自带 librime.1.dylib + Homebrew 1.17 头文件，只调用稳定早期 ABI），配合
macOS `sample` 采样热点栈。协议沿用 09-08 A/A 口径：单 session、上屏
「外婆」一次、目标词反复输入 + Escape、不提交目标。

## 发现一：smart 补全开关复发（回归）

两主方案 `smart:` 段没有 `enable_completion`，librime `script_translator`
**默认开启补全**。2026-09-09 用户曾在线上显式设为 false（当期知识库结论：
「打字延迟体感显著下降」，且仓库 schema 从未显式配置）——该修复只存在于
线上文件，09-13/09-14 的仓库→线上文件同步把它覆盖丢失。

量化（隔离工作区，p50）：开启补全时**每段第一键 ~68ms（无历史）/ ~80ms
（有历史）**，p95 70–97ms；显式 `enable_completion: false` 后第一键降至
~9–16ms（本项贡献 ~60ms）。机制与 09-09 结论一致：补全使 script_translator
在 extended 大词表上做前缀扫描，产生巨大候选流，逐键经过十几层 Lua filter
协程桥接（sample 热点为深层嵌套的 `LuaTranslation::Next → lua_resume`）。

修复：`mohu_zrm.schema.yaml` / `mohu_flypy.schema.yaml` 的 `smart:` 段
显式 `enable_completion: false` 并注明历史（防再次被「清理」）。根因教训
与 option_state_data 同类：**只改线上不进仓库的修复会在下次文件同步时丢失**。

## 发现二：ensure_engine 每键 fork `find` 子进程

`mohu_tiger_sentence.lua` 的 `ensure_engine` 在引擎缓存检查**之前**调用
`runtime.resolve_model`，后者用 `io.popen("find <model_dir> …")` 列目录选
最高版本模型（d22d0e2 引入的版本化模型选择）。而 `ensure_engine` 被
`ensure_decode_context` 与 `acquire_char_scorer`（word_order filter）**逐键**
调用——等于每个按键 fork 一次子进程并逐行读管道。sample 热点：
`LuaTranslator::Query → lua_resume → luaB_pcall → io_readline → __read_nocancel`
（~35% 采样）。

量化：贡献约 **10ms/键**（无历史路径亦中招），修复后 P1/P3 各键再降一个
数量级。

修复：`ensure_engine` 开头用**原始配置字符串签名**做快速路径（引擎已存在
且配置未变直接返回句柄），文件解析只在创建时执行；创建成功后记录
`engine_raw_signature`，释放路径同步清空。

## 修复前后总账（p50，ms，process_key + 菜单读取）

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 每段第 1 键（无历史） | 67.8 | **1.3** |
| 每段第 1 键（有历史 / 上屏后） | 76–81 | **1.3–1.7** |
| 词中第 2–4 键 | 10–23 | **1.6–4.2** |
| 6 键输入各键 | 17–29（首轮第 1 键 294） | **1.5–6.7** |
| 上屏（空格） | 0.2–2.4 | 0.2–2.4 |

残留：音节完成键（第 2/4 键）~4ms、6 键整句 ~6.7ms（native 解码为主体，
与 bench_decode 口径一致）；session 创建 ~1.3s 为每进程一次（词表/码表
解析），非每键成本。

## 验证

- Lua 单测：`mohu_tiger_sentence_native_test`、`mohu_tiger_two_char_test`、
  `mohu_tiger_user_model_test`、`mohu_tiger_context_test`、
  `mohu_tiger_no_early_commit_test`、`option_sync_test` 全部通过。
- 逐键探针 A/B（本报告数字）。
- 线上部署：schema×2 + `lua/mohu_tiger_sentence.lua` 覆盖、重新部署、
  重启 Squirrel（备份在 `~/Library/Rime/backups/2026-09-14-option-state-unify/`）。

## 遗留与建议

- **smart_static 未动**（只在长输入由 contextual translator 使用，不影响
  前几键；补全行为是否也要关，留待单独评估）。
- `mira` 依旧跑不到 native（Lua ABI 盲区），本报告数字来自隔离工作区
  逐键探针；探针源码在 /tmp（一次性），如需长期化应移入 tests/。
- 会话创建 ~1.3s 每进程一次，首次聚焦可感知；如需优化，方向是装载期
  并行/延后非关键组件。

## 补充定位（同日傍晚）：补全代价的精确机制与 moran 对照

用户质疑「116M 对 68M 词表差 1.7× 解释不了 60× 差距」——成立。用埋点
express translator（首拉计时 + 流尽计数）与逐层摘除 filter 的二分实验
定位，完整因果链为四环：

1. **补全候选洪流**：1 键输入时补全产出全部「以该字母开头的单音节条目」
   （即单字及其辅码变体）。实测 'w' → **2,889 个**、'j' → **7,557 个**
   候选/每键（词表首字母桶仅差 1.7×，但绝对数量在此量级）。
2. **首拉并不慢**：第一个候选 0.25ms 即出——洪流是惰性的，没人拉就不贵。
3. **抽干者是 mohu_word_order_filter**（`mohu_word_order_filter.lua:177`
   `while #block < limit`）：收集「前 20 个可重排候选」时要跳过所有
   不可重排候选，而 1 键输入的补全候选**全是单字**（可重排要求 ≥2 字），
   于是循环把整条 2,889 候选的流拉干。逐层摘除验证：最小链
   （pin+uniquifier）零抽干、'w' 3.8ms；仅加回 word_order 即复现
   2,889/50ms。
4. **逐候选成本 ~18µs**：express 的 transform/provenance/Yielder/Lua
   桥接 × 2,889 ≈ 51ms（'j' 7,557 × 同价 ≈ 252ms）。

**moran 对照**（同为 express+smart 动态创建、补全同为默认开、代码同源）：
'w' 首拉 0.28ms 且**流从未被拉干**（无 COUNT 日志）——moran 没有
word_order 类扫描者，菜单只拉 5 个候选，补全洪流「备而不用」，故
1.4ms。**结论：补全开关本身不是毒药，毒的是「补全洪流 × 无界跳过扫描」
的组合**；若将来想恢复补全，正确修法是给 word_order 的收集循环加
「已检视候选数」上界（例如 limit×4 后停止扫描、直接流式直通），
而不是动词表。

## 补全恢复为 moran 一致形态（同日实施）

用户确认要「跟 moran 一致的补全」后，实施了四层防护并重新打开补全：

1. **word_order 扫描双上界**（`tiger/word_order_scan_limit` 默认窗口×8，
   `tiger/word_order_scan_ms` 默认 2ms 墙钟）：收集窗口的检视量与时间
   均封顶，超限放弃本键重排、流式直通。
2. **1 键短路**（word_order 与 semantic gate 同型）：不足一个完整音节
   时不存在 ≥2 字候选（补全也只产出单字），扫描与门控直接跳过。
3. **reorder 尾部缓冲上界**（`mohu/reorder/trailing_limit` 默认 48）+
   **native 空表直通**：kDone 阶段 native_list 为空（≤4 键打词场景
   tiger 引擎不运行）时尾部完全流式；有 native 时缓冲至 48 后提前
   冲刷再流式。依据：native 的 quality（50）在流序上先于 smart（5），
   首个 smart 出现时 native_list 已完备。
4. **semantic gate 同型双上界**（`mohu/semantic_rerank/scan_limit`/
   `scan_ms`）：为将来重开语义时不再复发同型抽干。

修复后（补全开启、完整 filter 链、隔离工作区实测 p50）：

| 输入 | 补全关基线 | 补全开（修复后） | moran（补全开） |
|---|---|---|---|
| 首键 'w' | 1.3ms | **4.2ms** | 1.6ms |
| 首键 'j'（最大字母桶） | 1.7ms | **7.3ms** | 4.9ms |
| 词中 2-6 键 | 1.6-6.8ms | 1.8-5.4ms | 0.9-2.4ms |

与 moran 的残余差值（约 2-4ms）来自 filter 链深度（mohu 14 层 Lua
filter vs moran 约 10 层+5 个 OpenCC simplifier）与 express 逐候选
处理，属结构性成本而非病理性抽干——moran 在 'j' 上同样付出 ~3.5ms
的补全迭代器固有成本。回滚＝两主方案 schema 的
`smart/enable_completion` 改回 false 一行。

测试：`mohu_word_order_filter_test`、`mohu_semantic_gate_filter_test`
（8/8，mock context 补充 `input` 字段以匹配 1 键短路新契约）、
`mohu_reorder_filter_lexicon_test`、`mohu_express_tiger_test`、
`mohu_hint_filter_runtime_test`、tiger 系列、`option_sync_test`、
`tests.test_neural_toggle_schema` 全部通过。线上已部署
（schema×2 + 三个 filter，备份同前），16:51 重启 Squirrel。

## 补全语义勘定（同日，菜单转储实验）

用探针的菜单翻页模式（注意：mohu 的 ↓ 被键绑定消费，翻页须用
`=`/Page_Down；RimeContext 必须用 RIME_STRUCT 初始化 data_size，
否则 get_context 静默不填 menu 字段）逐项测定 librime 补全语义：

1. **`enable_word_completion` 默认开启**（librime 1.16/1.17 同），
   显式 `false` 才关闭；不配置 = 词组补全可用。moran 与 mohu 的
   smart 段均未配置，故两家默认都有词组补全。
2. **词组补全的门槛是「已敲满 4 个完整音节」（8 键）**：此后更长
   的词条会作为补全候选出现且排名靠前（实测次选）。实测：
   `wfjpngyb` →「问君能有几多愁」次选（mohan #2 / mohu #4）；
   `grgrvayn` →「关关乍引禽」次选。
3. **不足 4 个音节永远不会提前出更长词条**：`grgrju`（3 音节）
   翻遍 60 页无「关关雎鸠」——4 音节词的真前缀均 <4，属硬边界
   （moran 同样为 0，实测）；mohu_min 裸 script_translator 对照
   确认非管线过滤。`core_word_length` 与该门槛无关（设 2 无效）。
4. 单字级补全（`enable_completion`）：把未敲完的音节扩展为该声母
   下的全部单字（'v' 一键 → zh 声母字海）；这是此前性能事故的
   洪流来源，也是补全开关最直观的可见效果。
5. 排位差异：同一输入 moran 把补全句排 #2、mohu 排 #4，均在首页；
   如需对齐可后续调整 express 排序权重。

排查教训：本节之前的多个中间结论（「librime 无词组补全」）系实验
污染——显式设过 `enable_word_completion: false`、又残留
`core_word_length: 2`，两次污染分别导致假阴性；2×2 矩阵重测后才
勘定。菜单翻页键与结构体初始化两个坑见第 1 条括号。

## 傍晚第三轮：同文双候选、整句裁剪失联与排位对齐（同日实施）

用户报告三个问题，一并定位修复：

**① xnyska 出现两个「信用卡」（回归 bug）**。二分定位：摘掉 native
翻译器即单候选 → 双出 = native（tiger）+ smart（词表）同文。诊断
filter（插在 uniquifier 前）实测两候选 text/comment 完全相同、
**preedit 不同**：native 是引擎的词级分段 `xnys ka`（信用|卡），
smart 是音节分段 `xn ys ka`——uniquifier 按 preedit 判不等、不合并。
裸 script_translator 双翻译器对照证明 uniquifier 本身正常。修复：
reorder 的 flush 输出 native 时记录文本（`emitted_native_texts`），
delay/smart/trailing 及 kDone 流式路径同文本跳过，由 native 版本
代表该文本（提交走 native 个人词路径，与「模型负责排序」设计一致）。
顺带修复完整码下的双「关关雎鸠」。补全开关与用户词库均与此无关
（两者分别排除）。

**② 4 字以上整句显示多个候选（回归）**。根因：`sentence_visibility_
filter` 的三轮回归修复（音节容量豁免/类型无关判定）2026-09-09 已
落在 filter 代码并带 12 项单测，但 **schema 接线一直是注释状态、
从未恢复**——「待回归修复后恢复」的注释与已修复的代码脱节。今日
在两主方案 schema 重新启用。验证：wfjpngyb 整句只剩 1 条；
`jsj` 简快码首选完好（⚡️ 标记正常，09-09 担心的丢失未复发）。

**③ 补全句排位与 moran 对齐**。①+② 生效后，`wfjpngyb` 的 mohu
菜单为 `[文君能有, 问君能有几多愁, 文君, 闻君, 文俊]`，与 moran 的
`[文君能有, 問君能有幾多愁, …]` **逐位一致**（此前 mohu 因双候选
+ 未裁剪排到 #4）。剩余排位差异（若有）来自词频/用户库，属正常。

测试：reorder lexicon、sentence visibility（12 项）、word_order、
express IJRQ、semantic gate（8/8）、tiger 系列、option_sync、
candidate_override 全部通过。线上已部署（schema×2 + reorder，
备份同前），18:11 重新部署并重启 Squirrel。

