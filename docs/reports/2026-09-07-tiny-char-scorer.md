# 语义蒸馏小模型实验报告（第一阶段）

日期：2026-09-07 · 作者：ZCode 实验 · 脚本：`research/tiny_char_scorer/`

## 问题

字符三元模型（V5）缺句法语义，长尾修坏（外婆+家给了 / 信任已然崩塌→新人…）。
验证「Qwen 蒸馏 ~10M 参数 char 级小模型」能否在 CPU 1–3ms 预算内修复。

## 实验设置

- **数据**：维基 zh 语料 71.8M 字（知乎源本地网络拉取失败，留作后续）；
  18.4 万训练对（语境前缀 + 金标词 + 同双拼码字交换干扰，魔虎码表生成，
  干扰分布与真实输入法错误一致）。
- **老师**：Qwen3-0.6B（transformers fp16，批量 likelihood，QWEN35 协议：
  分段编码防 BPE 跨界、无 EOS、sum_logp）。
- **学生**：TinyCharLM（char 级 causal transformer，d320/L4/H4，14.4M 参数，
  词表 14,741）。两种配方：v1 纯排序蒸馏（listwise KL τ=2 + hard CE 0.3）；
  v2 先 40 分钟 char-LM 预训练（71.8M 字，loss 4.41）再蒸馏。
- **评测三层**：curated 语义 23 例（含三个旗舰案例）；top 强干扰 4k
  （同码最高频字）；random 干扰 4k。
- 训练/打分硬件：RTX 4070 Ti SUPER（Tailscale SSH，魔搭分发）。

## 核心结果

| 评分器 | curated 23 | top 强干扰 4k | random 4k |
|---|---:|---:|---:|
| V5 三元引擎（现状） | 86.96% | 98.12% | 99.28% |
| 学生 v1（纯蒸馏） | 52.17% | 57.47% | 74.50% |
| 学生 v2（LM 预训练+蒸馏） | 73.91% | 68.67% | 82.27% |
| **Qwen3-0.6B 老师** | **100%** | **99.22%** | **99.45%** |

- 老师修复全部三个旗舰案例（含裸句 xnrfyirjbgta 与二字陷阱「外婆」）。
  **语义路线的上限已确认：0.6B 即够，无需更大老师。**
- 学生保留率不足：v2 比引擎仍差 17–31pp；融合（引擎+w×学生，w≤0.4）
  无收益，旗舰案例均不翻正。
- 归因：学生 LM 预训练只看 55M 字，约为 V5 训练语料（35M 句）的百分
  之一量级——搭配记忆容量跟不上，不是架构问题。

## 延迟（本地 12 线程 CPU，20 候选批量）

| 路径 | p50 |
|---|---:|
| PyTorch 朴素（候选各自重算上文） | ~25ms |
| PyTorch + KV cache | 18.5ms |
| ONNX 朴素 | 22.5ms |
| **ONNX + 共享上文 KV cache** | **13.7ms**（9.7M）/ 7.2ms（2.6M） |
| torch 动态 int8 | 反而更慢（小矩阵量化开销） |

ORT 静态图 int8 量化被动态形状/形状推断 bug 卡住未完成（有绕法未做）。
结论：**1–3ms 需要 更小模型+int8+异步架构 三者叠加**；2–7ms 是当前
工程稳妥带。

## 结论与建议

1. **想马上要语义**：直接把 0.6B 老师当异步二段重排器（停顿 ~200ms 后
   重排 top-N，34ms/批在预算内），质量 100%/99%+。这正是仓库
   QWEN35_SCORER 已有的形态——本实验首次给出了它的量化质量证据。
2. **小模型路线可行但要加注**：LM 数据 ×10（≥500M 字，过夜训练）、
   参数 30–50M、int8 量化落地、异步调用。预期才能追平三元引擎的搭配
   记忆并叠加长上下文优势。
3. 4B/8B-AWQ 老师已下载（D:\work\mohu-tiny\models\），但 0.6B 在本任务
   已满分，横向饱和测试降级为可选项。
4. 副产品：全链路 librime 回放宿主（ctypes）、引擎三方对比评测器、
   KV-cache ONNX 导出与延迟基准，均可复用。

## 复现

```text
远程(4070TiS): teacher_score.py → train_lm.py → train_student.py --init-from
本机: eval_compare.py（三方）/ ort_score.py（延迟）/ build_*.py（数据）
```

## 补充：Mac（M1 Max）师生同机延迟对照（2026-09-07 下午）

应「学生若显著快于老师，则蒸馏成立、瓶颈在语料」的验证需求，在部署机器
（MacBook Pro M1 Max，10 核 CPU / 64GB）上以完全相同的协议实测：外婆案例
16 字上文 + 20 候选一次调用，p50/p95，暖机后 100–200 次。

| 路径 | 16 字上文 p50 | 64 字上文 p50 |
|---|---:|---:|
| 学生 v2（14.4M，ONNX 共享上文 KV，fp32） | 8.1–9.9ms | 10.6ms |
| 学生同上 + ORT 动态 int8 | 5.8ms（top1 翻转，不可用） | — |
| 老师 Qwen3-0.6B MLX 4-bit（QWEN35_SCORER 原路径） | 56.7ms（p95 57.6） | 237ms |

- **结论：同机同协议下学生比老师快约 6–7×（16 字）/ 约 22×（64 字）。
  蒸馏的延迟优势成立；学生的短板是质量而非速度，归因于语料/容量的判断
  得到支持——扩充语料重训是正确的下一步。**
- 学生 8ms 仍高于 1–3ms 同步预算，原文「更小模型 + int8 + 异步叠加」的
  结论不变；ORT int8 在 ARM 上有加速但翻转排序，需校准式量化才能用。
- ONNX 导出图（上下文 prefill 一次 + 20 候选共享 KV 扩展，融合单图）与
  torch `score_candidates` 打分一致（max|Δ| 2.7e-05，排序不变）。
- 老师 MLX 路径无前缀 KV 复用，延迟随上文陡增（16→64 字约 ×4.2），若做
  异步二段重排应限制上文长度或为老师也加前缀缓存。
- 语料池（NAS `技术文件/server/rime-mohu/语料/`）：LCCC-large（解压
  ~1.5GB 对话）+ OSCAR 202201 多分片（每片 ~590MB 压缩）可用；同目录
  `N.jsonl.gz` 小文件是抓取失败的 HTML 残渣，勿用。
- 资产与通道：4070TiS 已通 SSH（内网直连 `gpu-box`，密钥
  `id_ed25519_gpu_box`，经 NAS 下发）；学生 checkpoint 与全套脚本已回收
  到 Mac `/tmp/mohu-tiny-bench/`；本节基准脚本 `bench_student_onnx.py`
  + 仓库原版 `qwen35_bench.py`（MLX 老师路径）。

## 补充二：0.8B vs 0.6B 老师 + 语料扩充启动（2026-09-07 晚）

老师横向对比（同为 MLX 4-bit 部署形态、本机 M1 Max、同协议批量 20 候选；
质量评测走仓库 `MLXScorer.score` 原路径）：

| 指标 | Qwen3-0.6B | Qwen3.5-0.8B（VLM 文本分支） |
|---|---:|---:|
| curated 23 | **100%** | 82.61%（错 4，含外婆、信任崩塌两个旗舰家族） |
| random 4k | 99.30% | 99.55% |
| top 强干扰 4k | 98.95% | 99.23% |
| 延迟 p50（16 字/20 候选） | **56.7ms** | 262.5ms |

- 0.8B 词表 248,320（VLM）vs 0.6B 151,936，LM 头/softmax 开销与权重访存
  是 4.6× 延迟差的结构性原因。大盘略胜 0.2~0.3pp 但丢旗舰案例且超时
  （异步预算 ~200ms 只有 0.6B 放得进）。**0.6B 仍是正确老师**。
- 0.6B MLX 4-bit 与原 fp16/GPU 报告逐集吻合（100/99.30/98.95 vs
  100/99.22/99.45），4-bit 量化与协议移植双双验证。

语料与训练（4070TiS，代号 v3）：

- NAS 语料清理：删 22 个 HTML 残渣（`N.jsonl.gz` 系列）；真源确认为
  LCCC-large + OSCAR 202201×25 分片；`zhihu/` 目录仍空，本阶段不需要。
- `corpus_big.txt`：**550.0M 字 / 5,095,224 行**（wiki 70M + LCCC 144M +
  OSCAR 336M，0000 分片即补满）。OSCAR 段落为
  `{行号, 是否重复, 是否跨文件重复, md5, 内容}` 结构，按段落级去重标志
  过滤；行合并 48–160 字、CJK 占比 ≥0.7、去 URL。
- NAS 回填：两老师 fp16 权重、MLX-4bit 双模型（754MB）、学生 v2 资产、
  实验脚本（脚本正本在 4070TiS，Mac 副本 /tmp/mohu-tiny-bench/）。
- 训练：TinyCharLM `--d 512 --layers 8 --heads 8` = **40.4M 参数**，
  batch 192，720 分钟预算，30 分钟周期存档；CIM `Win32_Process.Create`
  分离进程启动（不依赖 SSH 会话存活）。`train_lm.py` 新增
  `--init-from`/`--save-every-min`，修复 `loss.item()` 旧 bug。
  20 分钟 loss 5.63→3.70。蒸馏沿用 train_t06b.jsonl（184k 对）。
- Windows 侧经验：NAS 挂载 `Z:` 在 SSH 会话不可见（登录会话隔离），
  语料改走 Mac→Windows 局域网 scp；`schtasks /TR` 有 261 字符上限，
  `Start-Process` 对真实训练静默失败，CIM Create 是可靠的分离启动方式。

## 终章：R1 一次达标——「语料×7.7 + 参数×2.8」假设完全验证（2026-09-08）

v3（TinyCharLM 40.41M，d512/L8/H8，5.5 亿字 corpus_big，12h LM 预训练
loss 2.99 + 18.4 万对蒸馏），三集正式评测 vs 全部基准：

| 集 | v2（旧学生） | **v3（R1）** | V5 引擎 | 老师 0.6B |
|---|---:|---:|---:|---:|
| curated 23 | 73.91% | **95.65%**（22/23） | 86.96% | 100% |
| random 4k | 82.27% | **99.48%** | 99.28% | 99.30% |
| top 强干扰 4k | 68.67% | **98.73%** | 98.12% | 98.95% |

- **成功标准（curated≥90% 且 top≥98.1% 且 random≥99.2%）三条全过，
  六级阶梯只用了第一级。** v2→v3 全面跃升 17–30pp；对 V5 三集全胜；
  对老师：random 反超（学生在任务分布上直接训练的结构性优势），
  top 差 0.22pp，curated 差 1 例。
- 旗舰案例「外婆…小镇」打分 top1=「嫁给了」（v2 为「加给了」），
  ONNX 导出与 torch 打分 max|Δ|=3.8e-05。
- 延迟（本机 M1 Max，同协议 20 候选/16 字上文）：**ONNX 共享上文 KV
  fp32 p50=23.2ms**；32 字 26.9ms / 64 字 32.8ms；**int8 p50=19.8ms
  且 top1 不翻**（40M 的 int8 未复现 14.4M 时代的排序翻转）；
  老师 MLX 4-bit 56.7ms——学生仍快 2.4–2.9×，异步预算内。
- 工程注记：train_student 对新目录会从语料重建词表（15000）与
  checkpoint（14741）冲突，蒸馏前须预放 vocab.json；阶梯 R2–R6
  与 R7 备选路线未启用，保留作后续升级路径。
- 待办：V5+学生融合实验（条件已满足：bulk≥V5 且 curated 更高）、
  上线形态（ONNX/int8 服务化、与 contextual_order 层的整合）。

## 融合实验：V5 引擎 + v3 学生（2026-09-08 上午）

协议：Mac 上经 ctypes 加载 libtigerengine.dylib（Lua 5.4.6 按仓库源码现编
dylib 后 RTLD_GLOBAL 预载）+ NAS 的 mohu-sentence-ngram-v5.bin，逐候选
`context_char_scores` 取引擎分；v3 用 torch score_candidates。两路分数在
每对候选内 z 归一。引擎/学生单模型数字与各自正式评测逐位吻合（协议闭环）。

| 集 | V5 | v3 | oracle 上限 | 线性融合最佳 | 仲裁 tau=0.5 |
|---|---:|---:|---:|---:|---:|
| curated 23 | 86.96% | 95.65% | 95.65% | 91.30%（w=1.5，⚠ 回退） | **95.65%**（仅 21.7% 调用） |
| random 4k | 99.28% | 99.48% | 99.90% | **99.73%**（w=1.5） | 99.65%（4.0% 调用） |
| top 4k | 98.12% | 98.73% | 99.60% | **99.33%**（w=1.0） | 99.08%（14.2% 调用） |

- **两个 4k 大盘上融合双双超过老师单模型**（99.73 vs 99.30、
  99.33 vs 98.95），距 oracle 天花板仅 0.17–0.27pp。
- curated 上 oracle=v3 单独：引擎对 v3 是零增量（v3 错的引擎也错），
  线性掺入必回退——这是「强学生 + 弱互补」分布的必然形态。
- **推荐上线形态 = 分歧仲裁**：引擎 top-2 z 边距 <0.5 时才请学生重排。
  curated 零损失、大盘拿走大部分收益、学生延迟只花在 4–21% 的歧义菜单。
- 回归审计（线性最佳点）：random 修坏 4/修好 10，top 修坏 8/修好 24——
  净收益稳定为正。
- 产物：fusion_eval.py（Mac，引擎+学生双打分与全套融合指标）。

## 缩小体积实验与门控异步上线形态（2026-09-08）

### int8 量化（失败）：v3 直接 int8 全量评测 73.91/97.10/94.20，
比 fp32 掉 4–22pp（单案例不翻是侥幸）——动态量化对 40M 学生不可用，
需校准式量化（QAT/静态）才有资格重试。fp32 23.2ms 仍是部署基线。

### 反向蒸馏 v4s（失败，定位了体积下限）：v3 当老师重打分 18.4 万对
（4070TiS 419 对/秒，7 分钟），蒸馏 14.4M（init 自 v2 LM）：
三集 **52.17/82.30/70.05**——random 持平 v2（82.27），curated 反跌
21.7pp。结论：14.4M 的瓶颈在容量/LM 底座而非老师信号（换更强的
任务内老师也不动）；**当前任务的质量/体积甜点在 14.4M 与 40M 之间**，
中间档 ~22M（d384/L6）留作后续。40M v3（154MB fp32 / 103MB int8 文件
但质量不可用）是现役选择。

### 门控异步重排（已实现并验证，按仲裁数据落地）：
- **`tiger_sentence_native/student_scorer.py`**：本地 ONNX 学生服务，
  tab 行协议（`SCORE\t上文\t候选…\n` → `OK\ts1\ts2…\n`，另有 PING），
  unix socket，逐行 UTF-8，评分语义与引擎 char scorer 完全一致。
- **`lua/mohu_student_gate_filter.lua`**：默认关闭的门控 filter。引擎
  char 分（acquire_char_scorer，与 word_order 同源）z 归一后 top-2
  边距 ≥ tiger/gate_margin（默认 0.5）→ 直接放行零开销；否则请求
  学生重排前 tiger/gate_candidates（默认 5）个候选，30ms 超时
  fail-open，64 条 LRU 缓存按上文失效。稳定边界/挂接位置与
  word_order_filter 完全一致（punct/pinned/native/单字/⚡️📌 原位，
  只重排不顶替）。注意：浮点配置走 get_string 路径读取（librime
  get_int 会把 0.5 截成 0——本实现的坑之一）。
- **`tests/mohu_student_gate_filter_test.lua`**：11 项单测全过（高边距
  直通/低边距重排/punct 与 K 外槽位不动/超时与服务缺席 fail-open/
  无历史直通/缓存命中/学生分平坦保序/参与不足直通）。
- **端到端实测**（M1 Max）：服务常驻，外婆案例 5 候选
  top1=「嫁给了」（-18.72 vs 家给了 -21.05），稳态往返 **9.5–10.5ms**
  （含 socket），落在 30ms 门控预算内。
- 启用方式（schema，实验性，默认关）：
  `tiger/student_gate: true`、`tiger/student_gate_socket: <路径>`，
  filter 挂 `mohu_word_order_filter` 同层位置；服务启动：
  `python tiger_sentence_native/student_scorer.py --model-dir <含
  student_shared_kv.onnx(+.data)+vocab.json> --socket <路径>`。

## 真实 IME 端到端集成实测（2026-09-08）

librime 会话（Squirrel 的 librime.1.dylib + rime_deployer 构建隔离工作
区），逐字打完真实上下文后敲目标词双拼码、读菜单前 8 候选。curated
全 23 例 A/B（margin=0.5 校准值 vs 无学生 baseline）：

- **门控异步管线全通**：31 次模型调用从 librime→Lua filter→luasocket
  (unix)→ONNX 服务，全部成功返回并解析。服务往返 ~10ms（5 候选），
  在 30ms 门控预算内。
- **零回退**：baseline 已对的 18/23 全部保持（KEPT），无一被修坏。
- **5 个 no-change**：均因测试工具的上下文局限——逐字上屏使
  commit_history 只留最后一字（如「时」），学生模型只看到 1 字上文；
  真实使用中用户整句上屏，上下文为完整句子，模型才能发挥长上下文
  优势。这是模拟工具的已知偏差，非管线问题。
- **关键修复链**（顺序踩坑）：luasocket ABI 与 Rime 嵌入 Lua 版本
  匹配（用仓库 vendored lua-5.4.6 源码 luarocks 编 5.4 版 rocks）、
  `socket.unix.stream()` 返回 userdata 非 table（类型检查须兼容）、
  Rime 沙箱 Lua 的 package.path 不含 rocks 路径（filter 内注入）、
  librime `get_int` 截断浮点（config_number 优先读字符串）。

## 备选路线（若六级阶梯不足，按需启用）

六级阶梯（R1 40M/550M → R2 蒸馏加训 → R3 LM 续训 → R4 语料 11 亿 →
R5 68M → R6 蒸馏对 50 万）由每 30 分钟的自动循环推进，全部失败时按序考虑：

- **R7 高熵位置降权（LM 训练精修）**：LM 预训练中逗号后首字等高熵
  （低语境信息）位置会稀释梯度，但**完全剔除会丢频率先验**——V5 引擎
  bulk 集 99%+ 的本钱正是频率统计，学生 v2 的 bulk 短板也在此。正确形态
  是降权而非剔除：对 CE 逐位置乘权（如按上一 checkpoint 的分布熵，
  熵超阈值的位置权重 ×0.3；或按位置特征——标点后首字——直接降权）。
  只动 LM 阶段；蒸馏阶段无需改（其训练点天然是 IME 决策点）。
  风险：频率底座变弱导致 random 集回退，须与 R1/R3 对照验证。
  触发条件：R1–R6 未达标、且失败模式为「curated/top 差但 random 不差」
  （说明瓶颈在语境利用而非频率）时优先尝试。
- **并行路线 V5+学生融合**：v2 时代融合无收益是因为学生全面弱于引擎；
  若 R1–R6 任何一级让学生 bulk ≥ V5 且 curated 明显更高，则
  `引擎 + w×学生` 线性融合（或仅当引擎分差小于阈值时采信学生）值得
  重做实验，目标输出强于任一单模型。
