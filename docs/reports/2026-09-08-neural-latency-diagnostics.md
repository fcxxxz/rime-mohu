# 神经重排延迟：分段诊断、A/A 稳定性与单点优化取舍

日期：2026-09-08。设备：Apple M1 Max。范围：基线冻结、纯测试/诊断增强与
实测复核；不修改 native/Lua/schema 生产逻辑，不部署运行库，不触碰 live
Rime/Weasel 目录，不自动提交 commit。

## 基线冻结

- HEAD `44926e5`（main，相对 origin/main ahead 1），工作区保留用户既有
  未提交改动（72 个 tracked/untracked 条目），本轮不回退、不覆盖。
- 工具链：uv 0.7.6、Apple clang 21.0.0、Lua 5.5.0（宿主）；native 构建按
  Makefile 使用 Lua 5.4.6 头文件。
- 本轮开始时关键产物哈希：`libtigerengine.dylib`
  `50e5b0ae…bd93b`；`neural_infer.h` `fc8d94fb…55ea`（完整清单存
  `.tmp-baseline-artifact-hashes.txt`）。
- 定向测试基线（全部通过）：
  - Python：`tests.test_neural_context_benchmark`、`tests.test_neural_toggle_schema`、
    `tests.test_reading_coverage`（14 项）。
  - Lua：`neural_context_config`（155 checks）、`mohu_neural_input_gate`（82 checks）、
    `mohu_word_order_filter`（全量，含 neural 门控与 legacy fixture 系列）、
    `option_sync`。
  - Native：`tigerengine-safety`、`tigerengine-lua-safety`、`tigerengine-context`、
    `tigerengine-user-model`、`tigerengine-reading-prior`、`tigerengine-word-score`、
    `tigerengine-neural-policy`、`tigerengine-neural-rerank`、`tigerengine-neural-prefix`、
    `tigerengine-neural-kernels`、`tigerengine-neural-context`——均 0 失败
    （仅 Accelerate cblas 弃用警告，与 HEAD 前状态一致）。
- 既有失败（与本轮无关，分类留档）：`tests/test_qq_dictionary_workflow.py::test_opencc_install_uses_bounded_https_archive_source`
  期待 `.github/workflows/build.yml` 含 `tools/install_opencc_ubuntu.sh` 文本，
  现行 build.yml 已改为 `brew install opencc`，仅 qq-dictionary-batch.yml 保留该脚本。
  该失败在本轮任何改动之前即存在。
- `git diff --check` 仅命中既有词典数据（`mohu_flypy.lexicon.txt` 等）的行尾空白，
  非本轮引入。

## 分段诊断增强（tests-only）

- `tests/neural_rerank_session_test.py`：新增 `menu_texts_timed()`，在**不增加**
  GetContext 调用次数的前提下输出 `get_context_ms`/`menu_decode_ms`；旧
  `menu_texts()` 保留为兼容包装（`neural_toggle/gate/personal` 三个 session
  驱动加载验证通过）。`type_keys()` 每键新增 `process_key_ms`、`get_context_ms`、
  `menu_decode_ms`、`end_to_end_ms`，旧 `process_ms`/`elapsed_ms` 原样保留。
- `tests/neural_rerank_latency.py`：统一为与
  `research/lm_sentence_compare/neural_context_benchmark.py` 一致的线性插值
  percentile；warm 输出 `p50/p95/p99` 与四阶段分布
  （count/p50/p95/p99/min/max）。`cold_ms` 保留单样本口径，`cold_distribution`
  显式为 `null`（一个样本不能伪装 cold p95/p99）；`native_scoring_ms`、
  `lua_total_ms` 显式 `null` 并附 `timing_limit`——公开 librime API 不可观测，
  禁止用独立 probe 相减推导。
- 新增 `tests/test_neural_rerank_latency.py`（9 项单元测试，无需真实 Rime），
  聚焦回归
  `tests.test_neural_rerank_latency + test_neural_context_benchmark + test_neural_toggle_schema + test_reading_coverage`
  共 23 项全部通过。

## 实测复核（隔离工作区 /tmp/mohu-neural-toggle-20260908/rime）

协议与既有报告一致：单 session、上屏「外婆」一次、反复输入 `jwgzle` 后
Escape、不提交目标；计时含 process_key + 菜单读取，不含 UI 绘制。

| 运行 | cold（进程首请求） | warm p50 | warm p95 | warm p99 |
|---|---:|---:|---:|---:|
| A/A 第 1 次 | 182.1ms | 15.120ms | 15.453ms | 15.484ms |
| A/A 第 2 次 | 22.4ms | 15.181ms | 15.794ms | 15.864ms |
| A/A 第 3 次（分段口径修正后） | 23.0ms | 15.124ms | 15.734ms | 16.053ms |

- A/A 差异：|Δp50| ≤ 0.064ms，|Δp95| ≤ 0.341ms → **热态噪声门约 ±0.3ms（p95）**。
- 第 2/3 次 cold 仅约 23ms，因为系统页缓存已被第 1 次预热——印证既有口径：
  进程首请求 ≠ 磁盘冷态，cold p95 需专门的 fresh-process 协议，本报告不宣称。
- 分段（第 3 次 warm，修正后口径）：process_key p50 15.070ms；get_context
  p50 0.032ms；menu_decode p50 0.009ms。三阶段之和 15.111ms vs end_to_end
  15.124ms，残差 0.013ms（free_context 及循环开销）——阶段互斥可加。
  菜单读取确认可忽略，末键成本几乎全部在 process_key 内（同步神经推理）。
- 与既有优化报告一致：热态 p50 15.12 vs 记录 15.61ms（同量级，样本日不同）。

### 分段口径修正（代码质量审查后）

首轮实现中 `get_context_ms` 误将候选解码与 free_context 计入（且 decode 被
两个阶段重复计入）。已修正为：`get_context_ms` 仅覆盖 `rime_get_context`
调用本身，`menu_decode_ms` 仅覆盖候选解码，free_context 不单独归因、落入
end_to_end 残差；`menu_texts_timed` docstring 明确各返回边界。审查其余
Minor 一并处理：报告层统一 round 3 位、`distribution` 消除重复排序、
失败路径改为输出 `ok:false` JSON（与 session 测试一致）、新增
`menu_texts_timed` 失败路径与兼容包装单测、docstring 指认
`research/lm_sentence_compare/neural_context_benchmark.py::percentile`
为规范实现。

## 单点优化取舍：本轮不实施，证据如下

按批准计划的优先级评估当前 `neural_infer.h`/`neural_kernels.h`（已含前缀
trie 共享与向量化 GELU/logsumexp）：

1. 候选「输出层 logsumexp 就地化（去 scratch 往返）」：阶段总成本仅
   0.359ms（profile 见 2026-09-08-neural-rerank-optimization.md「剩余热点」），
   理论收益 <0.1ms，**低于实测 ±0.3ms 噪声门**，无法通过验收；且需新增
   destructive kernel 接口、处理 vDSP alias 约束与 ULP 漂移，风险收益比不成立。
2. 当前真正瓶颈是 Transformer 线性 GEMM（8.627ms / 纯推理 P50 11.771ms）。
   有意义的改进需要权重布局预变换（违反「权重 mmap 不复制」设计约束）、
   BLAS 后端替换（BNNS/MPS）或量化/小模型——均为后端级实验，不是本轮
   允许的「单点、零语义变化」改动。
3. 结论：按计划门禁（p50 可重复改善 + p95 不回退 + 零候选回归）判定，
   本轮**保留诊断/测试、不落地生产优化**；GEMM 后端实验列为下一轮独立课题。
   Codex 分支的 early-commit/fusion/终态复用方案维持撤回状态，不再重试。

## 行为门（tests-only 改动不改变候选输出）

隔离工作区真实按键（`neural_rerank_session_test.py --repeat 2`）：

- 神经开：两轮均提交「外婆」、首选「嫁给了」、ok=true——与基线逐字一致。
- 神经关：两轮首选稳定为「家给了」（V5 原序，脚本默认期望值仅对应神经开
  路径，故 exit=1 属预期语义差异而非回归），两轮内部完全一致。

## 边界与后续

- 本报告所有数字为 librime API 口径（process_key + 菜单读取），**不是**
  Weasel/Squirrel 窗口绘制或目标应用收到文本的端到端延迟；仓库内无 UI 观测。
- 未运行 `make all`（会大规模重建词典/分发，与脏工作区不兼容）；未修改
  任何生产文件；未创建 commit。本轮新增/修改仅 3 个 tests 文件与本报告。
- 独立复核：代码质量审查确认兼容性/异常路径/percentile 数值正确，指出的
  分段重叠问题已修复并复测；25 项聚焦单元测试与三轮 A/A 全部通过。
- 后续优先级：① GEMM 后端/权重布局专项实验（含等价 oracle 与噪声门）；
  ② fresh-process cold 分布协议；③ Windows rime_api 隔离逐键测量。
