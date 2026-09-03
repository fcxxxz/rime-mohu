# 2026-09-02 跨候选调频基准：魔然、夜莺、万象 Pro 与魔虎

> **历史机制与早期基准报告。** 本文保留修复过程、旧协议和原始数字，但各方案有效样本不等，且早期 Moran 测量先后经历误用 `moran_sentence`、误用魔虎编码以及动态 smart 词典未编译等问题，不能作为当前跨方案排名。当前唯一权威结果见 [频表顺序五方案基准](2026-09-02-cross-candidate-ordering-frequency-ranked.md)。

> 本报告当时回答的问题是：**「上屏历史重排下一次输入的候选」（跨候选调频）各输入法方案到底有没有、强不强、怎么实现的**，并记录魔虎 V5 的修复前基线与阶段性结果。全部数字来自同一套 librime 探针；不同方案的有效样本数可能不同，不能仅凭样本总量比较百分比。

> **方案正名说明**：本报告早期的“万象”数据实际来自夜莺方案（使用万象简体 grammar）。GitHub `amzxyz/rime-wanxiang` 的 `custom/wanxiang_pro` 是另一套 Pro 方案，依赖本地 `context_reorder` 自学习共现库；其冷启动协议一结果与夜莺不应合并。旧四方案阶段表见 `docs/reports/2026-09-02-word-order-cross-candidate.md`，同样只作为历史实现验收材料。

## 结论摘要

- **跨候选调频在魔然主方案中真实存在**：`moran.schema.yaml` 的 smart 翻译器开
  `contextual_suggestions: true`，并在 schema 尾部 `__include: moran:/octagram/enable_for_sentence`
  接入八股文语法模型（`zh-hant-t-essay-bgw.gram`，40MB，随 dist 分发）。实测（协议一，纯双拼）
  82.4% → 85.4%（**+3.0pp**）；在"直接打不对"的样本里修好 **31.2%**。此前测出 ±0 是因为误用
  `moran_sentence`（整句方案，未接 grammar）代表魔然。
- **夜莺同机制、效果最强**：`contextual_suggestions + grammar`（使用万象简体 grammar 模型），82.1% → 87.3%
  （**+6.1pp**），修好率 **45.5%**。与魔然的差距主要在语料（简体新语料 vs 繁体旧语料），机制相同。
- **万象 Pro 是独立方案**：`custom/wanxiang_pro.schema.yaml` 默认关闭原生 grammar，使用 `context_reorder.lua` 维护本地 1/2-Gram 自学习库；在单次冷启动协议一中没有预热记录，四档均无上下文变化。
- **魔虎 V5 修复前完全没有该能力**（±0.0）：4 键词码早退走 smart 词频、≥5 键虽进引擎但引擎不接
  上屏历史。本报告附带已实现并验证的引擎左上下文功能（见附录 A，staged 未提交），使辅码档
  （≥5 键）获得 **+2.1/+1.6pp**，且零回归。
- **4 键纯双拼是 V5 的核心缺口，且不能用现有字符级模型直接补**：让字符级引擎直接接管 4 键查询的
  实验使纯双拼 **82.0% → 74.7%（−7.3pp）**——字符级 trigram 的裸排明显弱于词库词频。正确路径是
  **词级模型评分**：保留 smart 候选与词频权威，在上下文条件下用词级分数重排候选（魔然/夜莺的
  grammar 正是这么做）。这是下一步修复的目标，详见第 5 节。

## 1. 阅读口径与术语

| 术语 | 定义 |
|---|---|
| **跨候选调频** | 上屏历史作为上文，影响**下一次输入**的候选排序。对应 mohu 的 `contextual_order` 开关（单次＝上屏后清空历史；跨候选＝保留历史）。librime 通路：`GetPrecedingText()` 取 `commit_history:latest_text()`（整段）→ `Poet::ContextualWeighted`（要求 `contextual_suggestions && grammar` 同时开启）→ 语法模型给候选打续写分重排。 |
| **自学习（词频调频）** | 上屏过某词 → userdb 记词频 → 下次该词排前。与上下文无关，所有方案默认具备（`enable_user_dict`）。**不是**本报告主指标。 |
| **词级 / 字符级模型** | 词级按词 id 三元组建模（octagram、V5 引擎的 `word_logp`）；字符级按码点三元组建模（V5 默认解码路径）。 |

三个测试协议（同一探针、同一语料 `research/lm_sentence_compare/cases.jsonl` 19,996 句派生）：

| 协议 | 流程 | 测什么 |
|---|---|---|
| 一（主） | 上屏前缀 → 打词码（4 档：纯双拼/首辅/末辅/首末辅）→ 词是否第一候选 | 跨候选调频（上下文重排） |
| 二 | 上屏裸词 → 打含该词的整句 | 词频学习在句中的体现 |
| 三 | 打词码 → 选该词上屏 → 再打词码 | 自学习（词频调频） |

判定一律二元：第一候选文本 == 目标词。fresh/直接打 = 无上文；afterA/上屏后 = 有上文。

## 2. 机制对照（源码取证）

| 方案 | 跨候选调频 | 自学习 | 证据 |
|---|---|---|---|
| 魔然·主方案（moran） | ✅ grammar（bgw 繁体） | ✅ | `moran.schema.yaml:145` smart `contextual_suggestions: true`；`:482` `__include: moran:/octagram/enable_for_sentence`；dist 带 bgw/bgc gram 文件 |
| 魔然·整句方案（moran_sentence） | ❌ | ✅ | translator 无 contextual_suggestions、schema 无 grammar（本次误用它的教训） |
| 万象 Pro | ✅ 自学习 `context_reorder`（本地 1/2-Gram） | ✅ | `custom/wanxiang_pro.schema.yaml` 的 `context_reorder` 处理器与滤镜；原生 `contextual_suggestions` 默认关闭 |
| 魔虎 llm（修复前） | ❌ | ✅ userdb＋用户 ngram | `mohu_tiger_sentence.lua` `#input<=4` 早退走 smart；`decode(raw)` 不接上文 |
| 魔虎 llm（修复后，附录 A） | ✅ 辅码档（引擎左上下文） | ✅ | `set_decode_context` 播种字符级上文；4 键默认仍走 smart |
| 纯词库（mohu_zrm 去 grammar） | ❌ | ✅ | smart 有 `contextual_suggestions: true` 但无 grammar，`ContextualWeighted` 硬门槛不满足 |

## 3. 协议一历史实测（旧有效样本口径）

各条件**自己的可测样本**（前缀在该条件下可 gold 上屏且数据齐全；七条件共享样本见附录 B，
共享口径偏向短前缀、低估带语法方案）：

| 条件 | n | 直接打 | 前面上屏后打 | 提升 | 直接打不对里修好 |
|---|---:|---:|---:|---:|---:|
| 魔然·主方案（纯双拼） | 9,325 | 82.4% | 85.4% | **+3.0** | 31.2%（511/1637，另修反 234） |
| 夜莺（纯双拼） | 9,180 | 81.2% | 87.3% | **+6.1** | 45.5% |
| 纯词库 / V5修复前 / 魔然整句（纯双拼） | ~1.1–2.8万 | ~82.5% | ~82.5% | ±0.0 | — |
| **V5＋上下文（修复后，辅码档）** | 13,271 | 首辅 93.7 / 末辅 94.2 | 96.0 / 95.8 | **+2.1 / +1.6** | — |
| V5＋上下文（修复后，纯双拼） | — | 82.5% | 82.5% | ±0.0（takeover 默认关，见第 5 节） | — |

**关键负结果（4 键接管实验）**：临时打开 `decode_context_takeover` 让字符级引擎接管 4 键词码
（带上下文），纯双拼 82.0% → **74.7%（−7.3pp）**。字符级 trigram 在 4 键短输入上的裸排明显
弱于词库词频先验；上下文收益（+2pp 量级）不能弥补。这直接否定了"字符模型直接接管"的路线，
指向词级评分。

协议二/协议三（补充维度）：协议三（自学习）五条件全部 96.7→99.9~100.0（+3.1~3.3，人人都有，
userdb 能力）；协议二（上屏词→整句）词频学习在句中：纯词库 +3.0、魔然整句 +3.1、V5+用户模型
+1.3（fresh 起点更高）。与协议一正交，不构成方案间上下文能力的差异。

## 4. 真实例子（协议一，纯双拼，上屏前 → 上屏后首选）

魔然主方案修好：

| 前面上屏 | 打的词 | 直接打 | 上屏后 |
|---|---|---|---|
| 吃 | 自助 | 自主 | **自助** |
| 年度 / 下周 / 你正在上演宫廷 | 大戏 | 打戏 | **大戏** |
| 和谷歌 | 助理 | 主力 | **助理** |
| 汽车保养只更换 / 新摩托车走多远要换 | 机油 | 既有 | **机油** |

夜莺额外修好（魔然修不动，语料代差）：

| 前面上屏 | 打的词 | 魔然 | 夜莺 |
|---|---|---|---|
| 德云色 | 笑笑 | 仍"小小" | **笑笑** |
| 历史上最大地震在 / 学渣学霸差在 | 哪里 | 仍"那里" | **哪里** |
| 读懂消费者 | 心理 | 仍"心里" | **心理** |

两边都修不动：「调节好自己的心理」（搭配两可）等。

引擎左上下文（附录 A 功能）的真实效果：`ubji`（守纪编码）基线分 手机 −5.83 / 收集 −8.63；
喂「他向来」后 手机 −8.73 / 收集 −8.06（差距 2.80 → 0.68，方向正确）；喂「士兵」则反向
（收集 −11.34）；清空后逐分回到基线。用户示例「他向来→打守纪·末辅」：修复前所有方案
首级→首级（错），修复后 V5 首级→**守纪** ✓。

## 5. 词级模型评分缺口（修复目标）

### 5.1 缺口定义

4 键纯双拼词码查询时，魔虎的候选排序完全由 smart 词库词频决定，模型不参与；而魔然/夜莺靠
grammar 的词级搭配分实现了上下文重排（+3.0/+6.1pp）。字符级 V5 直接顶替词频会 −7.3pp，
因此缺的是**在上下文条件下对词候选的词级评分**，而不是更强的字符模型。

### 5.2 引擎现状（可复用的底子）

- 引擎已有**词级通路**：`word_mode`（`e->word_mode`）＋ `word_logp(pw2, pw1, wid)`（词 id 三元组）
  ＋ 词表 `word_id(text)`（`tiger.dict.yaml` 全词条，OOV 地板）。当前默认解码走字符级。
- 词级上下文槽位 `State::pw2/pw1`（0 = `<s>`）**未被上屏历史播种**（附录 A 只播种了字符级
  `prev2/prev1`）。
- Lua 侧 4 键早退：`#context_input <= 4 and not find("'")` 且无 takeover 即让位 smart
  （`decode_context_takeover` 默认 false，由 −7.3pp 实验决定）。

### 5.3 推荐修复路线：保留 smart 候选，词级分数重排

1. **引擎新 ABI**：`tiger_engine_context_word_score(handle, utf8_候选词, utf8_整段上文, window)`
   → `logP(w | 上文尾部词)`。实现：上文尾部按词表最大匹配切出最后 1–2 词 → `word_id` →
   `word_logp`；OOV 走地板。也可做成批量打分（一次喂多行候选）减少跨 FFI 往返。
2. **Lua 重排 filter**（不接管翻译器）：跨候选开关开启且有上文时，对菜单前 N 个候选（默认
   N≤20）逐个取 `logP(w|ctx)`，按 `α·词频分 + (1−α)·词级模型分` 融合重排。smart 的词频权威
   保留——这是 −7.3pp 实验的直接教训：**重排，不要顶替**。
3. **词级上下文播种**（可选增强）：`set_decode_context` 顺带把上文尾部词 id 播种 `pw2/pw1`，
   使 ≥5 键辅码档的整句解码也吃到词级上文（与字符级播种叠加）。
4. **替代路线**：按 librime `Grammar::Query(context, entry_text)` 接口把 V5 词层包装成
   grammar 组件（librime-tigergram 插件），让所有 `contextual_suggestions` 路径原生认它。
   工程量大（librime 插件 ABI），但 4 键场景自动接入且与魔然/夜莺行为完全同构。

### 5.4 验收基线（本报告数据即回归基线）

- 纯双拼：目标 ≥ 魔然（+3.0pp / 修好 31%），冲刺夜莺（+6.1pp / 修好 45%）；
  **不得伤及本来 82% 直接打正确的样本**（接管实验 −7.3pp 是红线）。
- 辅码档：保持附录 A 的 +2.1/+1.6 不回退。
- 每条改动跑协议一（探针与数据见第 6 节）。

## 6. 测试基础设施与复现

| 资产 | 位置（/tmp，重启会丢，重生成命令见下） |
|---|---|
| 探针二进制 | `/tmp/rime_wordgroup_dump`（librime C API：W=上屏行+B=查询行；`select_candidate` 提交 gold；候选 dump 前 5） |
| 六条件模板 | `/tmp/kua-templates/{lexicon,v5um,v5off,v5ctx,moran,yeying}`（v5ctx=新引擎+新 lua；moran 含 gram） |
| 协议一数据 | `/tmp/kua3`（32,976 items：in/、out/fresh、out/afterA、含 moranmain 补测、meta.json） |
| 协议二数据 | `/tmp/kua2`；协议三：`/tmp/kuab` |
| 生成脚本 | `/tmp/kua3_build.py`（items 构建与三方案编码）、`/tmp/kua3/worker.sh`、`/tmp/kua3/orchestrate.sh`、聚合 `/tmp/kua3_aggregate.py` |

重生成要点：语料 `research/lm_sentence_compare/cases.jsonl`；词池=语料句中二字词（去停用词、
三词典共有），前缀=词前整段文本；魔然须用**主方案** `moran.schema.yaml`（不是 moran_sentence）；
夜莺模板需补 `default.yaml/key_bindings.yaml/punctuation.yaml`（import_preset 解析）；
mohu llm 模板删除 build 后探针不会重编词典（部署器不编 dict），须保留 warm build 的 *.bin。

## 附录 A：已实现的引擎左上下文功能（staged 未提交）

- 引擎：`tiger_engine_set_decode_context(handle, 整段最近上屏文本, 窗口)`（三态：0 无变化 /
  1 应用并失效整帧 beam 缓存 / −1 错误；逐键重复零开销）；`rebuild()` root 播种
  `P(首字|上文尾部 CJK)`；窗口 clamp 1–2（字符三元结构窗口）。
- Lua：跨候选开关（`contextual_order`）开启时读 `commit_history:latest_text()` 整段喂引擎，
  与 librime `GetPrecedingText` 同源；旧 ABI dylib 自动降级；4 键接管由
  `tiger/decode_context_takeover` 控制（默认 false，−7.3pp 实验依据）。
- 配置：`tiger/decode_context_chars: 2`、`tiger/decode_context_takeover: false`
  （两个 llm schema）。
- 测试：`tests/tigerengine_context_test.cc`（真实模型：双向分数变化、清空回基线、三态）、
  `tests/mohu_tiger_context_test.lua`（15 项全绿）；现有 6 个 tiger lua 套件零回归；
  Makefile 目标 `tigerengine-context` 已接入 `make test`。
- 实测（协议一共享样本 13,271）：辅码档 +2.1/+1.6pp、纯双拼不变、fresh 与修复前一致；
  修复净效应 +1,180 / −510 判定。提交被项目级安全扫描拦截（命中若干与本次无关的
  `tools/*.py` 旧文件模式），处于 staged 状态。

## 附录 B：协议一七条件共享样本（13,271 items）

| 条件 | 直接打 | 上屏后 | 提升 |
|---|---:|---:|---:|
| 纯词库 | 92.2%（四档合并） | 92.1% | ±0.0 |
| V5 修复前 | 92.0% | 92.0% | ±0.0 |
| V5 静态（对照） | 92.0% | 92.0% | ±0.0 |
| V5＋上下文（修复后） | 92.1% | 93.1% | +1.0 |
| 魔然·整句方案 | 92.4% | 92.3% | ±0.0 |
| 魔然·主方案（仅纯双拼可比） | 81.6% | 83.7% | +2.1 |
| 夜莺 | 91.6% | 93.1% | +1.4 |

共享口径要求前缀在全部七条件下可 gold 上屏，交集偏向短前缀（无语法方案前缀上屏成功率仅
~60%，V5 ~88%），因此系统性低估带语法方案；分档评估请以第 3 节各条件自身样本为准。

## 附录 C：已知测量注意事项

- 重编 dylib 与旧二进制存在 ~2.3% 的近平局候选翻转（FP 结合序/平局裁断对构建敏感，双向、
  净 −0.1pp），对比实验须固定同一 dylib。
- 魔然主方案辅码档（5–6 键）首选为其固顶表输出（设计如此），与"词码查询"口径不可比，
  故主方案仅以纯双拼参与对比。
- `select_candidate` API 上屏与真实按键上屏等价（同一 `Context::Commit` → commit_notifier →
  Memory::memorize 链路），探针判定与用户实际所见一致。
