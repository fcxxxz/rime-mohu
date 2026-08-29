# 魔虎大模型本地候选评分服务

该 scorer 由 `mohu-llm-zrm-latest.zip` / `mohu-llm-flypy-latest.zip` 完整方案包共享；标准 Mohu
的 Octagram 配置保持不变，本方案不启用 Octagram。模型权重按 `models/*.manifest`
单独下载，绝不随方案包分发。

`qwen35_scorer.py` 是一个与 Rime 宿主隔离的 MLX 服务。它使用
`mlx-lm==0.31.3` 加载 Qwen3.5 VLM checkpoint 的文本分支，对最多二十个完整候选
做一次 batched causal next-token likelihood forward pass；不会生成文本，也不会
把候选词写入日志。五个及以下候选使用稳定的五行快路径；超过五个候选时，内部
补齐到稳定的二十行形状，但响应只返回真实候选对应的分数。短序列还会右补到至少
八个 token，降低量化 kernel 的形状切换。不同序列 bucket 仍可能有轻微数值漂移，
因此只把同一次请求内的分数用于排序，不把不同请求的绝对分数直接比较。

## 模型

scorer 维护一个固定的注册表（`qwen35_scorer.py` 的 `MODEL_SPECS`），只接受注册过的
4-bit checkpoint。注册表目前包含两个模型：

| 选择名 | checkpoint | 结构 | 内容指纹 |
| --- | --- | --- | --- |
| `qwen35-0.8b` | `mlx-community/Qwen3.5-0.8B-MLX-4bit` | `qwen3_5` VLM 文本分支 | `8b1fc914…fc591b8` |
| `qwen3-0.6b` | `mlx-community/Qwen3-0.6B-4bit` | `qwen3` 纯文本 | `2de6c7d4…ba0548` |

Qwen3.5 是 VLM checkpoint，mlx-lm 通过 `language_model` 文本分支加载（视觉权重被忽略）；
Qwen3-0.6B 是纯文本模型，transformer 直接暴露在顶层，tied embedding 复用
`embed_tokens.as_linear` 做输出投影。两者都走同一套 batched likelihood 打分路径，
health 响应中的 `model.text_branch` 区分加载的是哪个分支。

默认模型仍是 Qwen3.5。也可以显式使用同一模型的本地 4-bit checkpoint：

```text
/Users/fuchuxuan/Library/Rime/mohu_llm/models/Qwen3.5-0.8B-MLX-4bit
/Users/fuchuxuan/Library/Rime/mohu_llm/models/Qwen3-0.6B-4bit
```

服务启动后 health 响应中的 `model.revision` 优先使用 `--revision`，否则从
Hugging Face snapshot 的 `refs/main` 或 snapshot 目录名发现；找不到时为
`unknown`。本地导出目录没有 refs 时，`unknown` 是预期状态；生产身份以
launcher/profile 中固定的 `model.sha256` 为准。该值是 checkpoint 目录中模型文件
（含相对路径）的内容 SHA-256，用于检测替换或不完整权重。

## 启动

启动 scorer 需要 Python 3 和 `mlx-lm==0.31.3`。安装器会优先使用方案包内的
`runtime/.venv/bin/python`，否则使用 `MOHU_QWEN35_PYTHON` 或系统 `python3`；若检测
不到 `mlx_lm` 会明确提示安装命令并保持输入法的 n-gram 候选可用，不会假报 scorer
已启动。推荐先执行 `uv pip install mlx-lm==0.31.3`，再重新双击方案安装器。

先确保模型目录已完整下载，然后在仓库根目录执行（Unix socket 路径必须显式
指定；服务不会默认占用一个可预测的全局 socket）：

```bash
uv run --with mlx-lm==0.31.3 \
  python -m tiger_sentence_native.qwen35_scorer \
  --model /Users/fuchuxuan/Library/Rime/mohu_llm/models/Qwen3.5-0.8B-MLX-4bit \
  --socket /Users/fuchuxuan/Library/Rime/mohu_llm/runtime/qwen35-reranker.sock \
  --idle-timeout 0 \
  --expected-sha256 8b1fc914a940d611e13ba1880ffdae553deb4504a0a6299256ac19470fc591b8
```

正式部署使用同目录的 `run_qwen35_scorer.command`，由 supervisor 启动；它会把
模型目录、socket 和 Python runtime 固定在 `mohu_llm/` 目录，并在指纹不符时
拒绝加载。launcher 读取 `scorer_models.zsh` 中注册的模型表，按同目录
`model-selection` 文件记录的选择（文件不存在时缺省为 `qwen35-0.8b`）加载对应
checkpoint；文件内容未知或模型缺失时会持续重试，不会静默切换到其他模型。

## 模型切换

在 Rime 输入 `/model` 打开动态菜单即可在注册模型之间切换。命令行
`switch_qwen_model.command` 仅用于自动化部署：

```bash
~/Library/Rime/mohu_llm/runtime/switch_qwen_model.command qwen3-0.6b   # 切到 Qwen3-0.6B
~/Library/Rime/mohu_llm/runtime/switch_qwen_model.command qwen35-0.8b  # 切回 Qwen3.5
```

切换是 fail-closed 的：脚本或 `/model` 菜单先按注册表校验目标 checkpoint 的内容
指纹，一致才会写入选择并加载对应 profile。默认始终为 `qwen35-0.8b`；未知选择、
缺失模型或指纹不符时不会静默回退到另一个模型，重排直接回退三元顺序。

Squirrel 的 Lua ABI 是 5.4.6。请把 LuaSocket 3.1 编译/安装到用户目录的
`lua/rocks`，并确认存在 `lib/lua/5.4/socket/unix.so`；只安装 5.5 版本会被
Squirrel 拒绝加载。

服务不会在运行时联网下载权重；请先按 `models/*.manifest` 单独下载并校验模型目录，
再启动服务。addon 本身永远不包含 Qwen 权重或 `model.safetensors` 文件。

`--http-port PORT` 只用于独立诊断。Rime 生产路径只使用 Unix JSONL；Lua 不执行
curl、HTTP prompt 或伪分数 fallback。

## 协议

每行一个 JSON 对象。`op` 可省略（省略即 score），`context_text`/`context` 是用于
条件评分的可读中文前缀；Lua 也可能把候选共同拥有的前缀放在这里，以便只比较
分叉后的续写。`raw` 仅作为输入法原始码的兼容字段，不会被当作中文上下文（没有
可读上下文字段时才兼容地使用它）。候选必须是完整候选文本，最多二十条：

```json
{"version":1,"request_id":"mohu-1","raw":"vhrg1","context_text":"已经","candidate_mode":"complete","normalize":"sum_logp","candidates":["已经中华人民共和国","已经中国人民"]}
```

成功响应的 `scores` 与候选按下标一一对应：

```json
{"ok":true,"status":"ok","version":1,"request_id":"mohu-1","model":{"id":"...","sha256":"..."},"scores":[{"sum_logp":-12.3,"predicted_tokens":5},{"sum_logp":-14.1,"predicted_tokens":4}]}
```

`sum_logp` 只累加候选续写 token 的条件对数概率，`predicted_tokens` 是实际累加的
token 数。`candidate_mode=complete` 在候选包含 context 前缀时会先按字符切开、
分别编码前缀和后缀，避免 BPE 跨边界把公共前缀成本混入比较；不包含 context 的
候选从边界 token 独立评分。空上下文会使用 tokenizer 的 BOS/pad/eos 边界 token
（Qwen3.5 使用 pad 边界），因此单字候选也有可比较的分数。调用方可按
`sum_logp / predicted_tokens` 做长度归一化，再与三元模型分数融合。评分不包含 EOS：
输入法候选经常是输入中的临时前缀，EOS 会把“是否已经结束句子”混入词序概率，并
使逐键结果不稳定。

错误响应始终包含 `status:"error"` 和不带用户文本的错误码；服务会 fail-open，
由 Rime 保留原始三元顺序。

## 性能基准

基准脚本关闭 score cache，每次测量都执行一次真实批量 forward，并使用单调墙钟
`time.perf_counter_ns()`：

```bash
uv run --with mlx-lm==0.31.3 \
  python -m tiger_sentence_native.qwen35_bench \
  --model /Users/fuchuxuan/Library/Rime/mohu_llm/models/Qwen3.5-0.8B-MLX-4bit \
  --warmup 10 --runs 200 \
  --candidate 中华人民共和国 --candidate 中国人民
```

输出包括 `p50_ms`、`p95_ms`、`p99_ms`、`max_ms`、最后一次分数和 health 元数据。

2026-08-28 在本机（launchd scorer 停止、独占 GPU）对两个注册模型的实测：

| 场景 | 模型 | p50 | p95 | p99 | mean |
| --- | --- | --- | --- | --- | --- |
| 5 候选快路径（context「今天天气」，200 次） | Qwen3.5-0.8B-4bit | 28.6ms | 29.9ms | 30.3ms | 28.7ms |
| 5 候选快路径（同上） | Qwen3-0.6B-4bit | 24.4ms | 28.3ms | 34.2ms | 25.2ms |
| 20 候选全量路径（context「已经」，100 次） | Qwen3.5-0.8B-4bit | 116.9ms | 136.9ms | 141.7ms | 106.8ms |
| 20 候选全量路径（同上） | Qwen3-0.6B-4bit | 68.9ms | 82.8ms | 95.3ms | 70.7ms |

0.6B 快路径 p50 约快 15%，20 行全量路径 p50 约快 41%，但快路径长尾（p99）
偶尔更抖。两个模型的打分质量都未经过独立语料评测，`alpha` 等融合参数沿用
0.8B profile，未重新标定。

`--no-gpu` 会显式选择 MLX 的 CPU device（MLX 默认设备就是 GPU，仅跳过
`set_default_device(gpu)` 并不会真正切到 CPU）。CPU backend 是单线程的调试
路径：0.6B 单次 forward 要分钟级（GPU 上是 ~24ms，差 4 个数量级以上），
对交互式重排完全不可用，不要在生产 launchd 配置里开它。

wire 上限为 20；Rime profile 会在传输前一次性根据 native margin 和候选结构选择
预算，不会在用户停顿时循环追加请求。当前 Apple Silicon 4-bit 模型五行快路径约
20-30ms，adaptive 二十行批次约 55-130ms（长上下文可能更高，首次 kernel 编译也会
抖动），所以神经开关仍是本地 opt-in，
未经独立语料评测不会默认开启。

## 生命周期与安全

- 模型只在独立 scorer 进程中加载；模块导入和 health 不会加载权重。
- Unix socket 父目录设为 `0700`，socket 设为 `0600`；非 socket 的同名路径不会被覆盖。
- Lua 客户端按字节读取响应并在 64 KiB 前置上限处断开，避免无界 `*l` 帧分配。
- `--parent-pid` 父进程消失或 `--idle-timeout` 到期后服务退出并清理 socket。
- 请求帧、上下文和候选有大小上限；候选文本从不进入 stdout/stderr、异常消息或
  benchmark 日志。
