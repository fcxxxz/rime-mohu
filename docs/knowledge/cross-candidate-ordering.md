# 魔虎跨候选调频（上下文候选重排）知识库

> 2026-09-02 词级上下文重排上线时沉淀。面向后续会话与维护者：架构在哪、
> 机制为什么这样设计、怎么测、坑在哪、下一步是什么。当前数字与完整排名以
> [末辅上下文五方案基准（1,000 词）](../reports/2026-09-03-tail-auxiliary-context-benchmark.md)
> 为准；[频表顺序五方案基准](../reports/2026-09-02-cross-candidate-ordering-frequency-ranked.md)、
> [实现报告](../reports/2026-09-02-word-order-cross-candidate.md)、
> [前报](../reports/2026-09-02-cross-candidate-ordering-benchmark.md)和
> [旧全量审计](../reports/2026-09-02-cross-candidate-ordering-audit.md)保留为历史工程测量。

## 1. 一页纸现状

- **2026-09-08 独立神经开关**：两主方案的 `neural_rerank`（大模型关／开）
  默认关闭，与 V5 `contextual_order` 独立。`option_sync` 保存并跨应用同步，
  不设置 schema `reset`，避免覆盖重启恢复值。**2026-09-14 起单一来源化**：
  default.yaml 撤销 `switcher/save_options`（user.yaml 不再参与开关持久化，
  避免与状态文件双写打架）；`lua/option_state_data.lua` 为用户运行时状态，
  不入 git、不随包分发（build_split_dist 打包时显式剔除；随包覆盖会把
  用户开关重置成仓库测试残值——09-14 的「魔虎语义开关异常」事故根源）。关闭启动不加载神经权重；
  已加载后关闭只归零 neural weight，后续字符 scorer 调用前同步当前上下文
  的开关，缓存 scorer / 共享引擎也不能漏过。开启复用已加载权重、不重建 V5。
  两开关都关直接保留原序；仅神经开启时，没有实际融合也保留原序。
  真实神经测试驱动必须显式设置 `neural_rerank=true`，不能只配模型路径。

- **功能**：`contextual_order`（跨候选调频）开启且上屏历史含汉字时，
  引擎对菜单前 N 个 smart 候选按上下文续写分重排。四档输入（纯双拼/
  首辅/末辅/首末辅）全覆盖：纯双拼走本 filter，辅码档走引擎解码左上下文
  （前报附录 A），互不重叠、无双重计分。
- **默认配置**（`mohu_zrm` / `mohu_flypy` 两个主方案的 `tiger/` 节）：`word_order_signal: char`
  （字符续写分，万象同型机制）、`word_order: true`、
  `word_order_candidates: 20`、`word_order_rank_penalty: 1.0`。
- **当前权威指标（末辅，1,000 词 / 20,000 case）**：每词 20 个目标不在句首的真实前缀，测试句与 V5 消耗的 32,399,500 条训练混合句做规范化精确去重。第一候选判定忽略单字候选。魔虎自然码/小鹤的一位末辅上屏后命中为 96.81%/96.80%，上下文提升 +0.69/+0.71pp，修好率 66.67%/66.91%；两位末辅上屏后为 97.25%/97.11%，提升 +0.84/+0.83pp。
- **共同前缀敏感性分析（833 词 / 1,641 case）**：魔虎两方案上屏后第一候选均为 1,547/1,641（94.27%），修好 120/194（61.86%），修坏 20/1,447（1.38%）；夜莺为 91.96%、62.70%、4.33%。它消除了各方案前缀成功集合不同造成的子集偏差。
- **万象 Pro 的口径**：Pro (`amzxyz/rime-wanxiang` 的 `custom/wanxiang_pro`)
  使用自然码双拼与虎码首末辅助码，`context_reorder.lua` 依赖本地自学习 1/2-Gram
  共现库，原生 `contextual_suggestions` 默认关闭。协议一每个模板只提交一次前缀，
  因而没有预热历史时四档均无变化；这不等同于夜莺使用的预训练 grammar 上下文能力。
- **魔虎 V5 的发布信号**：默认采用字符续写分；当前统一末辅样本上，一位末辅修好率为自然码 66.67%、小鹤 66.91%，两位末辅为 63.91%、62.33%。词级评分和 MHCTN01 容器保留为实验兼容能力，不进入默认发布包。
- **分发**：本次使用的 V5 模型文件未改动；需分发更新后的 libtigerengine dylib/dll、Lua filter/桥接与 schema。只复制旧模型不能启用跨候选上下文行为。
- **宿主重启要求**：动态库句柄按 Rime 宿主进程生命周期复用。迁移/删除旧版
  `mohu_llm_*` 后，即使旧文件已移到废纸篓，运行中的 Squirrel 仍可能继续使用已映射
  的旧 `libtigerengine.dylib`；更新运行时文件后必须完全退出并重新启动 Squirrel，
  必要时用 `lsof -p $(pgrep -x Squirrel)` 核对实际加载路径。
- **用户调频层**：native 解码默认读取 `mohu/config/user-ngram.snapshot`，按
  `tiger/user_model_weight`（默认 0.85）将个人上屏三元统计与 V5 概率融合。因而
  纯模型的首选与实际首选可能不同；排查模型排序时应先将权重设为 `1.0` 或关闭
  `tiger/user_model`，再比较 native 输出。
- **个人词融合**：不按输入长度切换候选所有权。smart userdb 是提交事实来源，
  native 句图同时接收用户造词和静态已学习词；静态命中只更新原词条先验而
  不复制边。native 候选提交后通过 `adjust_personal` 立即更新内存词边，完整
  快照负责同步、删除和重启校准。辅助码只作为当前路径证据，提交归一到同一
  裸双拼 userdb 词条，每次只计一次。**2026-09-16 个人词整段命中边起与静态
  词边同享 `word_edge_weight` 先验（内部个人子边不加）**：此前组合路径可经由
  共享个人子边搭乘 boost（qygfda 的 请+跟打 搭乘「跟打」c=9），与个人整词边
  同步涨分，boost 封顶 12 压不过字频差，用户词提交再多也翻不了身；先验只补
  整段边、不补内部边，否则又被组合搭乘而抵消。线上验证翻转点在 c=9（2.68 vs
  2.58），见 [个人词先验修复报告](../reports/2026-09-16-personal-word-prior.md)。
  同日第二处修复：同一教授映射因输入形态不同拆成多条辅码变体 userdb 行
  （qy;oa gf;**pg** / qy;oa gf;**pi**），快照扫描旧「先见者赢」去重只保留旧行
  计数、刷新还会重置 `adjust_personal` 内存累计，学习被冻结在旧行值；现
  `mohu_personal_lexicon.lua` 两条扫描路径对归一 (code,text) 合并求和。
- **当前基准口径（2026-09-03）**：从频表前 30,000 行筛选 1,000 个严格同音二字目标词，每词 20 个真实非句首前缀，共 20,000 case；五方案共享 target/context universe，分别使用自身原生双拼和末辅编码。只测试一位末辅、两位末辅；魔虎与魔然额外测试两位末辅 `o`、`/`。全部 case 保留，第一候选忽略单字候选，候选 Top-5 可见性只作诊断；报告同时给出 case 加权、目标词等权、共同前缀子集、辅码补救与完整排名。V5 训练混合语料已完成规范化精确句级去重审计。
- **历史 32,976 条审计**：`2026-09-02-cross-candidate-ordering-audit.md` 保留旧状态表，但其 Moran 动态 `moran.extended` 依赖未完整编译，不能参与当前排名。

## 2. 架构地图

**2026-09-10 魔虎语义进程内化**：外部 Python scorer 服务形态已撤销——
librime-lua 无 luasocket 且要求用户自启进程不成立。C2 改由
`libtigerengine.dylib` 内建 ONNX Runtime 会话推理（`semantic_infer.h`，
`tiger/semantic_model|semantic_vocab`，默认 `mohu_semantic/` 下模型+词表，
`mohu/runtime/libonnxruntime.1.dylib` 以 @loader_path 随包分发）。
开关文案改为「魔虎语义关／魔虎语义开」；模型加载失败自动把开关退回
「关」，因此开关保持「开」即加载成功。候选身份按最终菜单 slots 顺序
传入 native（跨 filter 的 Lua userdata 身份不稳定，弱表谱系解析已移除）。
`make tigerengine-semantic`（需 MOHU_SEMANTIC_MODEL/VOCAB 环境变量）覆盖
原生方向性/释放测试。

**2026-09-10 TinyCharLM 移除决定**：同方法生产菜单对比（同一评估器、同一门控/保护槽/
margin、两套共 89,970 个真实菜单）中，TinyCharLM 在两种决策口径下全面回退
（probe -3.13pp、tnews -10.24pp，0.5 融合口径同样为负），而 C2 是唯一产生净收益
的后端（tnews +0.072pp CI 显著为正）。原生 char-LM 融合路径（`neural_infer.h`、
`neural_rerank_policy.h`、`set_neural_rerank` ABI、`scores.neural_reranked` 回填、
Lua 输入门控与开关观察者）已全部移除；dylib 重建后 245KB。`neural_rerank` 开关
现在唯一驱动 C2 语义服务（`lua/mohu_semantic_gate_filter.lua`，`uniquifier` 之后，
SCORE2 + 大端帧）。V5 的 `context_char_scores` 原样保留，供跨候选调频与语义门控
使用。下文 2026-09-08 的神经融合段落仅作历史记录，机制已不存在。
见 [同方法对比报告](../reports/2026-09-10-model-comparison.md)。

**2026-09-08 可选神经重排补充**：以下默认 V5 边界仍有效；配置神经模型时，
native 可以参与评分探测，但只有本次 `scores.neural_reranked == true` 才参与
回填。门控使用前五个不同文本的静态 V5 分，不含用户学习层；它只决定是否值得
尝试神经评分，不能证明神经翻转首选正确。神经分在 personalized V5 分的标准化
空间融合后映回 native 分数尺度，`weight=1` 也被限制为最多 0.5 的辅助权重，
不再允许神经接管。native 第一名有退分下限；首选翻转必须同时满足：拟提升候选
为神经第一名、相对其他非 native 第一名候选有足够神经优势、相对 native 第一名
另有独立神经优势，并以最小 native 分差跨过受保护分数线。任一条件不满足时整批
保留原始 native 分数并返回未应用标记；首选不变时只允许有限低位重排，最终稳定
排序不变同样不标记成功。门控回退不能让 native 探测挤占普通候选原来的重排窗口。
真实候选集、上文、本次评分和最终菜单必须一起验收，调试标记文件存在不能证明本次
推理已生效。见 [神经重排修复报告](../reports/2026-09-08-neural-rerank-fix.md)。

**2026-09-08 性能实现补充（已随输入门控部署本机）**：字符神经 scorer 改为
按 token 前缀 trie 共享每层 Transformer/QKV，并复用唯一预测位置的全词表
分布；末字仍计分，只省其无用输入隐藏状态。注意力严格限制祖先，位置编码
按路径深度；超过 256 节点改走有界路径注意力，避免整树平方内存。GELU 和
logsumexp 使用 Accelerate 向量算子，保持原评分目标。新增
`tiger/neural_rerank_context_chars`（0–160，默认160），与 V5 的末两字窗口
独立；先截取最近一条上屏文本尾部，再过滤 OOV。没有跨键评分缓存。
「外婆」实际20候选的节点数120→33、输出行60→31，隔离完整末键热态中位数
35.9→15.6ms，仍未达到10ms且冷启动无延迟保证。详见
[优化实现与验收](../reports/2026-09-08-neural-rerank-optimization.md)。
随后完成输入门控并覆盖本机三个运行文件、重启 Squirrel：不足两个完整双拼
音节不调用神经推理；达到门槛后仍需至少两个不同有效候选和静态 V5 不确定性
门控。有无上屏历史共用此规则；无历史且神经未实际融合时保持原序。候选
preedit 分段不能可靠对应候选字数时保守跳过，不以总字母数猜测完整音节。
部署、回滚位置及最终真实链路验收见
[神经输入门控报告](../reports/2026-09-08-neural-input-gate.md)。
随后用户现场发现个人词整段 preedit（如 `jwgzle`）被严格音节门控排成稳定
前缀：上文存在、神经已成功融合，仍无法移动个人词。native 现仅在每个
二字母片段能在静态码表验证对应单字时，为个人词生成 `jw gz le` 形式，
不放宽门控、不清空学习。验收必须包含真实 `_personal` 类型和 preedit，
不能用普通候选成功替代。详见
[个人词分段修复](../reports/2026-09-08-neural-personal-preedit.md)。
200例同码词离线窗口诊断中，2/4/8/16/160字正确率分别84/89.5/90/92/91.5%；
2字仅省约0.44ms纯推理中位数却修坏20例，默认不缩窗。该样本最长上文37字、
候选池2–4项且未做训练去重，不等于正式质量基准。同窗口新旧评分3030对的
完整排名一致，原始分最大绝对差1.907e-5。

**2026-09-09 smart 补全关闭的实测结论**：用户将 `smart:` 段
`enable_completion`/`enable_word_completion` 显式设为 false（历史状态为
librime 默认开启，仓库 schema 从未显式配置；魔然主翻译器同样未配置，
即两代方案一直默认开；`enable_word_completion` 在魔虎仓库从未出现）
后，**打字延迟体感显著下降，未发现功能损失**。归因：补全让
script_translator 每键在 122MB 的 mohu_zrm.extended 码表上做前缀扫描，
产生超大候选流（用户 11:49 的注释「避免补全候选被整流抽干造成逐键
卡顿」即为同一问题的过滤器侧缓解）。功能影响面核实：单字出简让全
**不受影响**——简码提前出字走拼写代数 `abbrev/^(.{3}).$/$1/`（四码字
自动生成三码拼写，词表层真匹配），与补全无关；真正依赖补全的只有
**词级码前缀匹配**（如 3 键提前出三字词），而词级让全 `ijrq/
enable_word` 本就关闭。**诗词长句联想实测不依赖此开关**：
`wfjpngyb`（问君能有）在补全开启时候选流中也没有任何 ≥5 字候选——
尽管「问君能有几多愁」「问渠哪得清如许」都在 base 词表和编译产物中
（逐候选落盘日志 + 反编译 table.bin 验证）——smart 层根本不产出这类
长词条补全；用户记忆中的诗词联想来自虎码方案（rime-tiger 的
tigress_ci 诗词词库）。native 词表也没有这两句。待观察项：词级
「少打几码」场景（用户日常依赖度低）。
**2026-09-14 复发并修复入库**：那次关闭只改在线上文件，09-13/09-14
的仓库→线上同步把修复覆盖丢失；隔离工作区逐键探针实测补全开启时
每段第一键 ~68–80ms。现已把 `smart/enable_completion: false` 显式写入
两主方案 schema（连注释带历史，防止再被「清理」）。
**同日第二处每键回归**：`ensure_engine` 曾在缓存检查前调用
`resolve_model`（io.popen 起 `find` 子进程，d22d0e2 引入），被逐键调用
等于每键 fork 一次、约 10ms/键；已加原始配置签名的快速路径。两项修复
后每段第一键 68–81ms→1.3–1.7ms、词中后续键 10–23ms→1.6–4.2ms。
**补全代价的精确机制（同日傍晚补充）**：1 键输入时补全产出全字母桶的
单字洪流（'w'=2,889 / 'j'=7,557 个候选），word_order_filter 的收集循环
（`while #block < limit`）因单字全部不可重排而把整条流拉干，逐候选
~18µs × 洪流 = 51–252ms/键；moran 同开补全但无此扫描者、菜单只拉 5 个，
故 1.4ms——「补全洪流 × 无界跳过扫描」的组合才是根因，词表大小（1.7×）
是干扰项。将来若恢复补全，应给 word_order 加已检视数上界。**同日已实施并重新打开
补全（moran 一致形态）**：word_order/semantic gate 扫描双上界（数量×8 +
墙钟 2ms）与 1 键短路、reorder 尾部缓冲上界（48）+ native 空表直通；
修复后补全开启下首键 'w'/'j' = 4.2/7.3ms（moran 同开补全为 1.6/4.9ms，
差值为链深结构成本）。完整定位、数字与探针方法见
[每键延迟报告](../reports/2026-09-14-perkey-latency.md)。

**2026-09-09 整句菜单显示裁剪（不改排序）**：新增
`lua/mohu_sentence_visibility_filter.lua`（挂 word_order 之后、
candidate_override 之前），配置 `tiger/sentence_visible_candidates`
（默认 1，clamp 0–50；0 = 全部显示恢复旧行为）。**2026-09-16 补
`tiger/sentence_deferred_candidates`（默认 -1 全保留，两主方案设 3，
clamp -1–50）**：全码 3 字输入（qygfda→X跟打 20 条变体）没有词组候选
可垫，「押后到词组之后」等于原样跟出；该键只保留押后队列中排名最前
N 条，深尾部不再显示（0.3.0）。只裁显示层——引擎评分池、重排、≤4 字
个人词可见性不受影响；辅码消歧是深尾部变体的正规选择路径。重排链看的候选池
不变——翻译器仍产 20 条（`mohu_tiger_sentence.lua` 的
`candidate_limit`）、word_order 仍按 `word_order_candidates=20` 批量
评分——本 filter 只裁显示层。**2026-09-14 重新接线**：09-09 的三轮
回归修复（音节容量豁免/类型无关判定 + 12 项单测）已落在 filter 代码，
但 schema 接线当时保持注释「待回归修复后恢复」、一直未启用；09-14
在两主方案 schema 恢复启用，实测 jsj 简码完好、整句只显示 1 条。
**同日 reorder 同文去重**：native（词级 preedit「xnys ka」）与 smart
（音节 preedit「xn ys ka」）同文候选因 preedit 分段差异不被 uniquifier
合并，造成 xnyska 双「信用卡」；flush 现在输出 native 时记录文本、
smart 侧同文本跳过（详见
[每键延迟报告](../reports/2026-09-14-perkey-latency.md) 傍晚第三轮）。
**2026-09-15 修补**：该去重原是单向的（只防 smart 副本），置顶/简码
替换位已输出的文本不进判重集合，而 `_personal` native 豁免词库门控
无条件输出——置顶后 native 个人词副本仍以第二条出现（用户报
「置顶了还是生成一样的候选」）。现以 `yielded_texts` 统一判重：先输出
者代表该文本（pin/fixed 位 > native > smart），测试补置顶×personal
双用例（tests/mohu_reorder_filter_lexicon_test.lua）。另：个人词边
单次提交 boost=log1p(1)×5≈3.47（上限 12，约 10 次封顶），标定即
「一次可见但小」——native 第一名随上下文分差波动是预期行为，稳定
直出走置顶（注意 pin 按置顶时的完整输入串记账，带辅码置顶对裸双拼
输入不命中）。
**2026-09-15 学习词不可见修复（两层叠加）**：用户报 xspizi 学过「熊皮子」
后仍只见一条候选。①引擎只给**个人词库**（userdb 扫描）的路径标 personal，
用户调频层（ngram 快照）顶到第一名的学习词是普通 native 路径，被
reorder 的词库门控（3–4 字、文本不在 smart 流）整条丢弃——学习闭环
断裂：不可见→无法提交→永远进不了 userdb。修复：解码路径累计**用户层
增益**（Σ[log 融合分−log 静态分]，`kUserGainPersonalThreshold=5.0`），
达标路径按 personal 输出（实测单次学习 trigram 增益 >10 nats、常规
边界 <1 nat，分隔干净；xspizi 仅熊罴子/熊皮子标 1，uhbjuf 全 0）。
②sentence_visibility_filter 0.1.x 把超配额句形候选**直接删除**，
0.2.0 改为押后到词组之后（可翻页到达），且 ≤4 字 `_personal` 不占
句形配额（用户词库随 native 序输出）；反复输入的长句 personal 仍
计入配额押后（09-09 洪水教训保留）。验证：xspizi 菜单
[熊皮子, 熊罴子, 兄痞子(smart 句), 熊皮, 熊罴…]；uhbjuf 不变
[上半身, 上班…]。同日用户另报「uhbjuf 有时上班第一、上半身次选」：
当前状态（repo+用户 userdb/ngram 快照）fresh/8 组上文/语义开关各
组合均无法复现，菜单稳定 [上半身, 上班…]；疑为 09-13/09-14 中间
部署态，如复现需记录当时上文与部署文件版本。完整证据链见
[学习词可见性报告](../reports/2026-09-15-personal-word-visibility.md)。
**句形判定（类型无关）**：覆盖到输入末尾（与 word_order 的
consumes_current_input 同型判定）＋ 达到门槛字数（`tiger/
sentence_min_chars` 默认 3，两字词与辅码消歧不裁）＋ 字数不超过覆盖
段音节容量（字母数/2）。稳定边界（一律不裁）：punct/pinned、⚡️/📌 标记、
不足门槛字数、声母简码/缩写匹配（容量超额即简码，词表 6,734 条如
`abjh→阿波罗计划`）、部分跨度候选（词组选词）。
**当日三轮实测回归（均已修复）**：① 首次部署因线上 schema 残留
`mohu:/algebra/drop_last_code?` 死引用导致 zrm 编译失败，Rime 静默
沿用旧产物（排查教训：每次部署后必须查日志 `error building config`）；
② 只裁 native 类型时误裁声母简码——简码以无 ⚡️ 标记的 native 候选
出现，用户实测「简快码首选丢失」后停用，音节容量豁免修复；③ 只裁
native 时 express 全长词组与 `_personal` 变体顶上——用户模型会把
反复输入的长句全部学成 `mohu_zrm_personal` 类型（逐条 yield 落盘
日志实证），故句形判定必须类型无关、personal 不豁免；个人短词由门槛保护。诊断方法教训：filter 是惰性协程，菜单只拉前几条，
收尾日志对长输入永不执行，必须逐 yield 即时落盘。
单测：`tests/mohu_sentence_visibility_filter_test.lua`（12 项）。

```
上屏历史 commit_history:latest_text()（与 librime GetPrecedingText 同源）
  │ contextual_order 必须开启；默认 V5 仍要求 CJK 上文
  │ 可选神经：至少两个完整音节和两个不同有效候选；无上文允许 BOS 评分
  │ 无上文时只有本次 neural_reranked=true 才重排，门控回退保持原序
  ▼
lua/mohu_word_order_filter.lua   ← lua_filter，两个主方案 filters 第 4 位
  │   （mohu_reorder_filter 之后、candidate_override 之前：用户显式覆盖
  │    优先于模型重排；yield 是运行时注入的全局，不能提为 upvalue）
  │ 收集前 N 个「可重排」候选：跳过 punct/pinned/native(mohu_zrm/mohu_flypy)/
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
- **字符级模型不认读音（2026-09-04 修，读音先验）**：≥5 键两字辅码
  输入的排序由模型接管，但字符三元模型只看字的文本频率——`mohuz`
  （mo+hu+末辅z）里辅码唯一锁定「虎」、又不存在 mó/mò+hǔ 真实词时，
  全局高频字「万」（freq_rank 285）凭 mò 读音挤到首选「万虎」，而
  mò 读音在 chars.txt 里简频=1（几乎只用于复姓万俟）。旧码表
  `mo 万 4 285` 的 rank/freq_rank 都是**全局字频**，且引擎评分根本
  不消费 freq_rank（只用于 >3000 名次的生僻字孤立惩罚）、rank 仅
  平局裁决（kRankPenalty=0.03 压不住 0.93 的分差）。修复：码表加
  可选第 5 列「读音条件简频」（构建时由 build_mohu_lexicons.py 从
  chars 词典权重列并入，与 chars.txt 读音简频同源），引擎装载期归一
  为 log P(读音|字) 先验并入每步路径分（`tiger/reading_prior_weight`，
  默认 1.0，0 关闭）。贝叶斯上是给 LM 补上 P(码|字) 似然项，与
  octagram 的 entry_weight+Query 加法融合同构。
- **字符级模型看不见词界（2026-09-13 修，词边先验）**：字符三元独占的
  路径分会被「错词与后文跨词界粘连」反杀——`vegeuurufaviiiyikbqiuuruyivgjuhw`
  （这个输入法**支持**一口气输入一整句话）首选「只吃」：V5 局部对「支持」
  领先 6.18 nats，被「只吃一口」搭配的 7.26 反杀，净输 1.09；「支持」是
  viii 的 rank-1 词条而「只吃」不是词，词表证据此前不可见（多字词只允许
  整段命中边）。修复：`tiger/word_edge_weight`（schema 默认 1.5，0=逐字节
  旧行为）让静态多字词作句中内部边并每边加有界分——librime
  entry_weight+Query 词频地板结构的 native 对应物。19,996 句整句 top1
  62.2%→65.4%（w=1.5；语料峰值 0.5 档 65.8%），回退主因是码表缺词
  （暴利/农妇/对华）。详见
  [词边先验报告](../reports/2026-09-13-word-edge-prior.md)。
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
- **当前可复现资产**：输入构建、训练集重叠审计、隔离运行和聚合分别由
  `research/lm_sentence_compare/build_tail_aux_cases.py`、
  `audit_training_overlap.py`、`run_cross_candidate.py`、`cross_candidate.py` 完成；正式产物与逐 shard 哈希写入
  `/tmp/mohu-tail-benchmark-v1/run-manifest.json`。runner 支持只复用完整校验 shard 的 `--resume`，中断的部分输出会自动重跑。每个 case 新建 Rime session，
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
4. **主方案不走 Octagram**：当前 `mohu_zrm` / `mohu_flypy` 直接使用 native
   Tiger 字符模型；旧 `mohu_llm_*` schema 已从发行方案移除。
5. **延迟测量**：跨会话基线漂移 ~1.4ms，必须同会话交替配对、取每
   (id,mode) 多次中位数；后台训练进程会污染 p95（Δmax 20ms+ 毛刺）。
6. **`make test 2>&1 | tail` 会吞退出码**（管道取 tail 的 0）——查
   pipestatus 或直接跑。
7. mira 测试 `mohu_zrm::cross_candidate_order` 在 HEAD 即失败（与词级重排
   无关，已在干净提交复现）。**更关键的是：mira 默认根本跑不到 native。**
   Lua 5.5 / 5.4 ABI 不匹配（`luaopen` 失败）+ `dist-*` 缺
   `libonnxruntime.1.dylib` + 不随包发 ngram 模型，三层叠加使引擎 fail-open，
   于是 native 相关用例既会**假失败**（`automatic_word_learning::vsmc`）也会
   **假通过**（`default::jiivo`，native 可用时返回 `既拙`）。评估 native 排序
   别只看 mira；构造可用宿主与完整对照见
   `docs/reports/2026-09-11-mira-native-blind-spot.md`。
8. 新 lua_filter 组件本身有逐候选桥接开销（fresh 直通也有 ~+0.1ms p50/
   0.4ms p95）——延迟优化的方向是并入 mohu_reorder_filter，不是优化评分。
9. **读音先验的回归口径**（2026-09-04）：改动只影响 native 解码的 fresh
   排序，4 键裸双拼 500 词（频表前 300 + 随机 200）新旧码表 top-1
   零变化；末辅档同池对比见当期报告。权威五方案 harness 需要
   sentence-ngram-mobile.bin 与 /tmp 模板（均已不在），重跑前先按
   `research/lm_sentence_compare/run_cross_candidate.py` 头部注释重备
   资产。

## 6. 遗留与后续

- **延迟 p95 临界**（0.5–0.7ms vs 0.5 线）：根因见 §5.8；方案=把重排并入
  mohu_reorder_filter（少一次桥接）。
- **词层升级路径**：修 mohu_userdict.txt 单字频率 → 重分词 → 重训
  （train_wordkn.cc，64GB 内存跑 35M 句峰值 ~14GB，~51 分钟）→ word/mix
  信号实验。kn5（/tmp/mohu-word-kn5.bin，797MB）不入库不上线。
  **2026-09-13 判决**：词级解码重启前提（同源分词词典＋可商用语料＋
  在线词级个人模型）未满足前不再投入——净新增收益实测仅 13%（vs 字符
  45%），且与词边先验的稳定核心重叠；词对选择性归语义层（C3 计划见
  训练合同）。青简外部对照与完整论证见
  [词级判决报告](../reports/2026-09-13-word-decode-verdict.md)。
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
