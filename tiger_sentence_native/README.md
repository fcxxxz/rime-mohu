# 魔虎整句引擎

魔虎自然码完整输入流水线 + 虎码辅助码的原生整句输入。语言模型与解码在
纯 C 动态库中执行，原生 Lua 层只负责候选输出，不主动改写或提交组合；
符号、反查、简快码、候选管理、加词和过滤器沿用默认魔虎组件。原生解码
架构与 TigerClaw 虎整句（Rime Lua 版）等价，模型直接复用其 TCSKNM 三元模型。
这是魔虎方案的原生整句组件，不启用 Octagram。

## 文件

- `tigerengine.cc` — TCSKNM01/02 模型读取（mmap）+ 增量 beam 解码 + C ABI
- `tigerengine_lua.cc` — Lua 5.4 绑定（luaopen_tigerengine）
- `nativetest.cc` — 基准对拍工具（与 Lua 版逐键比对输出）
- `mohu_sentence.lua` — 对外的 Mohu 整句 translator 入口
- `mohu_tiger_sentence.lua` — 兼容实现文件；旧部署仍可通过该文件加载
- `../lua/mohu_personal_lexicon.lua` — 个人多字词快照模块
- `mohu_zrm.schema.yaml` / `mohu_flypy.schema.yaml` — 自然码与小鹤完整方案

## 安装与模型

完整方案包按输入法分别提供：

- `rime-mohu-zrm-latest.zip`：自然码；
- `rime-mohu-flypy-latest.zip`：小鹤。

解压对应 zip 到 Rime 用户目录，然后执行一次“重新部署”。

`mohu-sentence-ngram-v5.bin` 是原生整句候选模型。放到
`~/Library/Rime/mohu/model/`；运行时固定读取该文件名。模型缺失或加载失败时记录一次错误并回退普通候选，模型目录不在输入热路径扫描。

### TCSKNM02 页校验与损坏模型回退

TCSKNM02 默认只在启动时校验文件头、分区算术、索引计数/键序和页起始范围；
不会顺序读取所有上下文页。实际解码首次触及某页时才校验该页的记录和后继表。
如果页或后继表损坏，当前 native 解码返回 `invalid n-gram page` 错误，Lua 层
放弃本轮 native 候选并保留 smart/普通候选，不把损坏数据当作“未命中”或继续
使用不完整的 beam。

发布校验或诊断时可对单次引擎创建启用完整页扫描：

```text
MOHU_TIGER_STRICT_VALIDATE=1
```

只有环境变量值严格等于字符串 `1` 才启用；未设置、空值、`0` 或其他值都保持
按需校验。严格模式在创建阶段发现坏页即拒绝模型；该变量每次创建重新读取，
不会在进程内缓存。TCSKNM01 与 MHKNM01 的既有启动校验语义不变。

本地开发需要 Lua 5.4 头文件（Squirrel 的 librime-lua 为 5.4.6）：

    curl -sL -o lua546.tar.gz https://www.lua.org/ftp/lua-5.4.6.tar.gz
    tar xzf lua546.tar.gz
    zsh build.sh


## 码表格式

每行 `code <TAB> text <TAB> rank <TAB> freq_rank [<TAB> reading_freq]`。
码形含裸双拼（YY）、加辅（YYX）与真实码形（YYX/、YYXX/、YYXXo）。
`reading_freq` 是可选的读音条件简频（同一字罕用读音≈0、主读音大，
如「万」mò=1 / wàn=1201402）：引擎装载期按 (字, 音节) 去重归一为
log P(读音|字) 先验并入路径分，压制字符级模型「只认字频、不认读音」
拼出的候选（如 mohuz 首选曾是「万虎」）。由
`tools/build_mohu_lexicons.py` 构建时从 `mohu_zrm.chars.dict.yaml`
权重列（与 chars.txt 读音简频同源）自动并入，源码表
`mohu_tiger.lexicon.txt` 保持 4 列不变；缺列时任何权重下均中性。
再生成：见基准目录 `unfixed_table.json` 的生成脚本（双拼取
`mohu_zrm.chars.dict.yaml`，虎辅取 `tools/data/tiger_aux.txt`，
权重取 `tools/data/chars.txt` 简频列）。

飞键行（`wz→wk`、`xq→xo`、`qx→qo`，与 mohu 的
`mohu_defs.yaml:/fly` 规则一致，含链式组合）由
`uv run tools/fix_tiger_lexicon_fly.py` 在生成后的码表上补齐；
`tests/test_tiger_lexicon_fly.py` 会校验覆盖完整（裸码与飞键码
条目数、rank 一一对应），缺失时按提示重跑补齐脚本即可。

## 配置（schema 的 tiger/ 节）

- `engine_lib` / `model` / `lexicon`：路径覆盖，默认用户目录 `mohu/` 下同名文件
- `beam`：束宽（默认 200）
- `all_ranks`：>4 键时全部档位竞争（默认 true）
- `reading_prior_weight`：读音先验权重（默认 1.0，0 关闭，范围 0–4）。
  码表第 5 列读音简频 → log P(读音|字) 并入每步路径分，补偿字符级
  模型不认读音的盲区（万 mò 拼「万虎」排 mohuz 首选）。旧 ABI dylib
  无 `set_reading_prior_weight` 或旧 4 列码表时自动中性/默认
- `initial_quality`：原生候选质量（默认 50）。固顶候选为 100，默认 smart 候选为 5
- `long_input_length`：达到该 canonical raw 输入长度后（Rime 双拼音节之间的空格会先移除），express translator 使用不读 userdb 的 `smart_static`（默认 5）。smart userdb 的多字学习记录通过 native 个人词边快照和提交增量参与长句解码，不依赖按长度切换候选所有权
- `personal_lexicon_namespace`：个人词 `Memory` 使用的 `smart` userdb 命名空间
- `personal_lexicon_max_rows`：同步到 native 引擎的个人多字词上限（默认不限制；需要时可显式设回如 4096）
- `user_model`：用户调频层开关（默认 true）。含中文的上屏文本会喂入
  native 引擎的内存三元计数表，解码时每个 trigram 查询按
  `P = w·P_静态 + (1-w)·P_用户` 概率域融合——静态模型文件永不改写，
  调频学习全部发生在这一层
- `user_model_weight`：静态模型权重 w（默认 0.85；设 1.0 等价关闭用户层）
- `user_model_snapshot`：计数表二进制快照路径（默认
  `mohu/config/user-ngram.snapshot`。
- `user_model_snapshot_interval`：每 N 次中文上屏写一次快照（默认 64；
  方案卸载时若有未落盘计数也会兜底快照一次）
- `personal_refresh_interval`：个人词快照的时间防抖秒数（默认 30；设为 0 关闭防抖）
- `decode_context_chars`：跨候选左上文窗口（默认 2）。`contextual_order`
  开关（跨候选调频）打开且上屏历史非空时，整段最近上屏文本
  （`commit_history:latest_text()`，与 librime `GetPrecedingText` 同源）
  传给引擎，引擎取尾部 N 个汉字作解码左上文——beam 起步条件从
  `P(首字|BOS)` 变为 `P(首字|上文)`，对候选的比较保持一致。窗口按
  V5 字符级三元模型的结构上限截到 2；旧 ABI dylib 无该函数时静默降级
  为无上下文。附带配置 `decode_context_takeover`（默认 false）：存在
  上文时是否让 native 接管 4 键纯双拼词码——接管后 `P(首字|上文)`
  优先于已学词频；实测字符级模型裸排 4 键低于词库词频约 7pp，故默认
  关闭、保留 smart 词频权威；辅码（≥5 键）路径的上下文增益不受影响。
- `word_order`：词级上下文重排（默认 true，跨候选调频的 4 键与全长度
  词码查询）。`contextual_order` 打开且上屏历史含汉字时，
  `lua/mohu_word_order_filter.lua` 对菜单前 N 个 smart 候选批量取引擎
  上下文续写分，按 `F_k = score_k − rank_penalty×(k−1)` 融合后稳定重排
  ——词频权威保留，模型只在上下文条件下提升续写概率更高的候选（重排，
  不顶替；−7.3pp 接管实验的教训）。pinned/简码（⚡️）/native（已带引擎
  上下文，避免双重计分）/单字候选一律不动。旧 dylib、引擎未就绪、评分
  出错均逐字节直通。实测 20 候选批量打分 0.012ms/键。
- `word_order_signal`：评分信号（默认 `char`）。`char`＝字符续写裸分
  Σ logP(候选字|上文末 2 字)，octagram 同型机制，用主字符模型——无词层
  依赖、无 OOV 概念、内存与页缓存零增量；离线七变体对照实验中修好率
  约为 `word` 的 3 倍（45.1% vs 13.2%）。`word`＝词级分 logP(词|上文
  末 2 词)，需容器（MHCTN01）词层或 `word_scorer_model` 显式指定，
  OOV（−20 无信号）不参与重排。
- `word_order_candidates`：参与重排的候选数上限（默认 20，clamp 2–50）
- `word_order_scan_budget`：为凑齐可评分 block 最多同步拉取的候选数（默认
  `word_order_candidates × 3`，clamp 到候选上限至 1000）。在完整 block 或
  EOF 之前命中该上限时，本轮保持上游原序且不获取/调用 scorer；完整 block
  恰好由最后一个预算内候选补齐时仍可评分。
- `word_order_time_budget_ms`：候选收集的协作式时间预算（默认 4ms，0 关闭）。
  只在两次 iterator 调用之间检查，不能中断一次上游调用；计时器不可用时只
  关闭时间判断，候选数硬上限继续生效。
- `word_order_rank_penalty`：名次每前进一位所需的模型分优势（默认 1.0；
  离线网格的平滑平台区 0.95–1.4：0.95 时修好 45.7%/修反 1.5%，1.4 时
  40.3%/1.0%——即修反保护阈值，优势不足不动）
- `word_scorer_model`：显式指定独立 MHKNM01 词模型路径（默认空）。
  常规路径由单文件容器模型自带词层（见下），此键仅研究/覆盖用
- **单文件容器（MHCTN01）**：`tiger/model` 指向容器时一次 mmap 同载
  字符层（解码，行为与非容器一致）与词层（评分，`tiger_status` 显示
  `word_scorer=packed`）。合并工具：`python3 tools/merge_tiger_models.py
  --char <TCSKNM02.bin> --word <MHKNM01.bin> --out <merged.bin>`。
  词层语料含 LCCC（CC-BY-NC-SA）等，仅限个人/研究分发，商业分发需
  换语料重训
- 个人词快照的刷新时机（打字零影响的分片设计，三阶段状态机）：
  1. 提交只递增代数计数并标记待刷新，纯 ASCII/标点上屏不标记；
  2. 扫描以 ≤5ms CPU 预算的切片推进，且只在输入组合为空时启动/推进；
     每片另有 512 条硬上限，即使时钟粒度粗也有与时钟无关的上界——
     按键最坏只等一个切片（实测 5 万条 98 片、最差单片 5.0ms）；
  3. 扫描完成后进入 native 事务（`personal_begin/append/commit/abort`）：
     append 按同一预算逐行分片喂入（整行块、须以换行结尾），commit 原子
     切换——解析成本已随 append 摊销，commit 只剩哈希层比对与应用；
     无变化时保留解码缓存；事务期间解码始终使用旧快照。旧 ABI 的 dylib
     自动回退整体 `set_personal` 路径。
  4. 方案装载期做一次一次性全量（打字尚未开始）；设置
     `tiger/personal_lexicon_max_rows` 时回退整体扫描路径（需全局排序取头部）。
- 事务路径实测（512 行/块喂入）：5 千条 commit 0.4–0.6ms、5 万条约 6ms、
  50 万条约 120ms；append 最差块 0.5ms（哈希容量已按现有词表预留，
  避免 rehash 尖刺）。键集收缩的 commit 走全量重建（5 万条约 110ms），
  正常使用中仅在清库/异常导入时出现。
- `perf_log`：设为 `true` 时按候选轮次输出 `mohu_sentence perf len=… native=…ms lua=…ms phase=…` 日志，用于长句延迟归因（默认关闭）
- `make tigerengine-bench TIGER_NGRAM=<模型路径>`：native 解码延迟基准，输出各输入长度 P50/P95/P99 与逐键增量打字延迟；`TIGER_BENCH_ARGS` 传 `beam all_ranks iterations personal_rows`

## 合并行为

- 自定义、置顶与固顶简快码保持默认魔虎优先级。
- native 与 smart 候选在路径层合并：同一跨度、规范码和文本只保留一个逻辑候选，
  个人词边携带提交先验并可在没有 smart 刷新时独立显示；没有个人边时仍按
  原有词库文本集合约束 native 降级候选。候选排序不按 5/6/7/8 键切换所有权。
  辅助码只作为当前路径证据，选中后归一写回同一裸双拼 userdb 词条，每次提交只计一次。
  模型路径的排序带读音先验（`reading_prior_weight`）：辅码锁定末字后，
  首字按 log P(读音|字) 惩罚罕用读音，避免「万」mò 这类全局高频字的
  罕用读音拼字（mohuz→万虎）压过自然读音组合。
- 一码简词只允许独立输入，不参与多键整句切分；句中每段至少消费双拼两键。
- 数字键交给默认 `selector` 选候选；分号与快捷键仍由 `mohu_processor` 处理。
- 动态库、模型、码表或 scorer 加载失败时记录一次错误，并自动保留默认魔虎候选。
- 方案不会在输入过程中提前上屏；候选确认、空格和回车均交给 Rime 默认编辑器处理。
- 神经重排只作用于原生整句候选，不改变置顶、固顶、自定义词和全局 filter 的优先级。
  每次 composition 只在传输前一次性选择评分预算：默认五条，native 首选不确定且
  候选分支足够多时可扩到已配置上限（协议上限二十条），未评分尾部保持原生顺序。
  同一次请求内完成比较；超过五条的请求内部固定为 20 行 kernel 形状，短序列补到
  8 token。不同序列 bucket 的量化 kernel 仍可能产生轻微数值漂移，不能把跨请求的
  绝对分数当作同一标尺。
- 提前上屏功能已移除；scorer 超时、模型不匹配或服务不可用时只回退候选顺序，
  不会改写组合或吞掉输入。
- 原生引擎句柄按 Rime 进程生命周期复用；修改模型或路径后需要重新加载 Rime。
- 长句路径在初始化和提交边界刷新个人词快照；快照同时包含用户造词和
  已有静态词的学习记录。native 候选提交后通过 `adjust_personal` 立即增量更新
  对应词边，完整快照仍负责同步、删除和重启校准。个人先验单调受限，再与
  句内上下文分融合。
- 旧版 `mohu_llm_*` / Qwen 神经重排方案已移除；当前发行包只有
  `mohu_zrm` 与 `mohu_flypy`，均使用 `mohu/model/` 下的 V5 native 模型。

## 验证记录（2026-08-26）

- 原生引擎与既有 Lua 基准的对拍脚本可复用 `tools/ziti_probe.c`；该工具现在支持
  `neural` 参数并使用 Squirrel 自带的 Lua 5.4/librime 栈。
- 延迟：纯双拼 20.2 → 1.8ms/键（直连）/ 2.24ms/键（全链路），真实码形更快
- 完整流水线探针：`vhrg1` 上屏「中华人民共和国」，`tz2` 上屏「投资」，
  `/date1` 上屏当前日期。
- Homebrew Mira 当前使用 Lua 5.5，而发行版 `libtigerengine.dylib` 按 Squirrel
  的 Lua 5.4.6 ABI 构建；Mira 中会出现 `version mismatch: app. needs 504.0,
  Lua core provides 505.0`，随后 native translator fail-open，表现为模型已存在
  但首选仍是 smart 词典候选。请在 Squirrel 中验证，或为目标宿主 Lua 版本重新编译
  `libtigerengine`；替换动态库后需重新部署并重启宿主。

## 故障排查：模型存在但首选未变

以 `ufqyhfmimh` 为例，V5 native 引擎的直接输出首选应为「神情很迷茫」，
「申请很迷茫」是 smart 词典候选。若 Rime 菜单仍以后者为首：

1. 确认当前方案是 `mohu_zrm`（自然码）或 `mohu_flypy`（小鹤），模型文件位于
   `~/Library/Rime/mohu/model/`，并且已重新部署。
2. 查看 Rime 日志中的 `mohu_tiger_sentence`。出现 `luaopen failed`、
   `version mismatch`、`loadlib failed` 时，native 通道未加载，Rime 会按设计回退
   到 smart 候选。
3. 如果 native 已加载但候选仍体现个人历史，检查
   `mohu/config/user-ngram.snapshot`。默认 `tiger/user_model: true`、
   `tiger/user_model_weight: 0.85` 会把上屏记录与 V5 模型融合；将权重设为 `1.0`
   （或暂时设 `user_model: false`）即可验证纯 V5 排序。
4. 更新或删除旧版 `mohu_llm_*` 文件后，必须完全重启 Squirrel。动态库句柄按进程
   生命周期保留；即使旧文件已经移到废纸篓，运行中的 Squirrel 仍可能继续映射
   `.Trash/mohu_llm/runtime/libtigerengine.dylib`。可用 `lsof -p $(pgrep -x Squirrel)`
   检查实际加载路径，确认只剩当前 `~/Library/Rime/mohu/runtime/libtigerengine.dylib`。
5. Squirrel 使用 Lua 5.4.6；不要把按 Lua 5.5 编译的 Mira/Homebrew 动态库与当前
   主方案混用。Mira 中若出现 `version mismatch: app. needs 504.0, Lua core
   provides 505.0`，属于宿主 ABI 不匹配；请在 Squirrel 中验证，或为目标宿主 Lua
   版本重新编译 `libtigerengine`。
