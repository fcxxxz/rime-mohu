# 魔虎语义重排（Qwen 本机大模型）配置说明

> 版本：2026-10-09 · 部署环境：macOS (Apple Silicon) · 推荐下载：0.5B 4bit；旧服务默认档仍为 bf16

## 模型下载

默认方案包不含语义模型。轻量安装推荐从 `latest` Release 下载：

```text
mohu-semantic-qwen2.5-0.5b-4bit.zip
```

这是一个完整 ZIP，不用合并 `part-aa`。解压后把其中的
`Qwen2.5-0.5B-Instruct-4bit` 模型文件夹放到：

```text
~/Library/Rime/mohu/model/Qwen2.5-0.5B-Instruct-4bit/
```

`config.json`、`model.safetensors` 和 tokenizer 文件都在这个文件夹内。
保留同一 `model/` 目录中的 V5 文件及其他模型；不要把整个 `mohu/` 文件夹替换。
项目推荐目录是单数 **`model`**。`~/.cache/modelscope/models/` 是开发缓存回退
目录，其中复数 `models` 属于 ModelScope 自己的结构，保持原样。

**前提**：这一 ZIP 只包含权重，不安装本机语义服务或 Python/MLX 依赖。
适用平台为当前的 macOS Apple Silicon 服务。V5 整句引擎和可选语义服务是
两个组件；未安装 `com.mohu.rerank` 服务时，单独放入权重不会启动语义重排。
在已有服务的安装中，把下面这一行加入所用方案 `*.custom.yaml` 已有的 `patch`，
没有 custom 文件时可按以下内容创建；不要覆盖其他个人设置：

```yaml
patch:
  tiger/semantic_http_url: "http://127.0.0.1:8765?model=0.5b-4bit"
```

重新部署 Rime，再打开“魔虎语义开”。服务会按这个明确的档位查找模型，
不能假设只有下载 4bit 权重就能满足旧的 bf16 默认档。

### 其他档位与旧分卷

其他模型在 `semantic-models-v1` Release 提供。新发布脚本先生成标准 ZIP；
只有超过每资产大小上限时才分卷。`part-aa`、`part-ab` 是普通 tar 文件的
第一、第二段，不是量化格式或模型文件名。旧资产保留兼容，轻量模型请选新 ZIP。

| 档位 | 既有 438 句对照结果（仅参考） | 格式 |
|---|---|---|
| `0.5b-4bit` | 约 276 MiB 权重目录、净改善 +34 | MLX；推荐轻量下载 |
| `1.5b-8bit` | 约 1.6 GiB、净改善 +41 | MLX |
| `3b-8bit` | 约 3.1 GiB、净改善 +53 | MLX；较大模型 |
| `0.5b` / `1.5b` / `3b` | bf16 原版对照为 +35 / +42 / +53 | transformers |

旧 tar 分卷内是平铺文件，必须先创建对应模型子目录再解压，例如：

```bash
mkdir -p ~/Library/Rime/mohu/model/Qwen2.5-3B-Instruct-8bit
cat mohu-semantic-qwen2.5-3b-8bit.tar.part-* | tar -xf - \
  -C ~/Library/Rime/mohu/model/Qwen2.5-3B-Instruct-8bit
```

不要把旧 tar 的所有 `config.json` 平铺到 `mohu/model/` 根目录，不同模型会互相
覆盖，且按档位查找的服务不能识别这种布局。新 ZIP 已包含独立型号文件夹。

## 这是什么

打开「魔虎语义开」后，V5 引擎对自己不自信的候选菜单（top-2 分差小，
即"歧义菜单"）会交给本机常驻的 Qwen 小模型重新打分排序。候选文字更通顺的
排前面。旧 bf16 档实测（15 本完全未参与训练的书、438 句）：翻对 58 句、翻坏 23 句，
净收益 +35；4bit 档对应净收益 +34，均为既有测试结果。

## 开关

- **魔虎语义开**（`neural_rerank`）：方案开关菜单里直接切。关 = 完全直通，
  且后台服务 600 秒后自动退出（不占内存）；重新打开时引擎自动拉起服务
  （头一两句无重排，之后恢复）。

## 换模型（改你自己的方案 yaml）

在 `~/Library/Rime/mohu_zrm.schema.yaml`（或 flypy 对应文件）的 `tiger:` 节里：

```yaml
  # 基础形式（默认 0.5B）
  semantic_http_url: http://127.0.0.1:8765

  # 换 3B 档：短别名
  semantic_http_url: http://127.0.0.1:8765?model=3b

  # 或直接写模型目录名（ModelScope 缓存里的文件夹名）
  semantic_http_url: http://127.0.0.1:8765?model=Qwen2.5-1.5B-Instruct
```

改完**重新部署**即生效。下次触发重排时服务自动加载新模型（2–10 秒，
期间自动保底不重排），无需重启任何进程。

### 档位对照（2026-10-02 实测，零泄漏 438 句）

| model= | 模型 | 常驻内存 | 翻对 | 翻坏 | 净收益 | 触发延迟 |
|---|---|---:|---:|---:|---:|---:|
| `0.5b` | Qwen2.5-0.5B | ~1.2GB | 58 | 23 | +35 | ~70ms |
| `1.5b` | Qwen2.5-1.5B | ~3.5GB | 63 | 21 | +42 | ~150ms |
| `3b` | Qwen2.5-3B | ~6.5GB | 83 | 30 | +53 | ~300ms |

内存换收益约"翻倍内存 +7 分"，按机器余量选。写错的名字自动回退默认档。

## 门控参数（一般不用动）

`mohu:` 节（`mohu.yaml` 或 schema 内）：

```yaml
  semantic_rerank:
    enable: true            # 总开关（false = 永不触发，与菜单开关叠加）
    gate_margin: 0.5        # V5 top-2 z 分差 ≥ 此值 → 引擎自信，不劳烦大模型
    semantic_margin: 0.15   # 大模型要翻转首选需领先的语义 z 分（防抖）
    candidates: 5           # 参与重排的候选数
    word_gate: false        # 词证据分歧门（实验性补充开门条件）
```

## 服务进程

- LaunchAgent：`com.mohu.rerank`（`~/Library/LaunchAgents/com.mohu.rerank.plist`）
  - 开机不自动启动（RunAtLoad=false），由引擎按需唤醒；崩溃自动重启
  - `RERANK_MODEL`：启动默认档（日常切换请用上面的 `?model=`，别改这个）
  - `RERANK_IDLE_EXIT_SEC`：空闲自退秒数（默认 600）
- 健康检查：`curl http://127.0.0.1:8765/health`
- 日志：`/tmp/mohu_rerank.log`
- 服务实现：`research/tiger2code_bench/rerank_server.py`（gitignored，
  属本机部署物）

## 故障排查

| 现象 | 原因与处理 |
|---|---|
| 重排一直不生效 | 检查「魔虎语义开」是否打开；`curl :8765/health` 是否 `loaded:true` |
| 打开开关后头几句没重排 | 正常：服务冷启动（加载+预热约 5 秒），之后恢复 |
| 想确认有没有在跑 | `pgrep -fl rerank_server`；10 分钟没打字它会自己退出 |
| 换档后迟迟不生效 | 服务在加载大模型（3B 约 10 秒）；看 `/tmp/mohu_rerank.log` 的 `switched to` |
| 完全不想用了 | 菜单关掉即可；要彻底移除：`launchctl bootout gui/$UID/com.mohu.rerank` 并删 plist + schema 里那行 `semantic_http_url` |

## 技术要点（开发者向）

- 链路：lua `mohu_semantic_gate_filter`（V5 分差门+冻结槽+fail-open）→
  `mohu_tiger_sentence.semantic_score` → C `tiger_semantic_http_score`
  （350ms 超时，仅环回，自动 kickstart）→ launchd 服务（async 单线程推理，
  启动预热，防 MPS Metal 竞争崩溃）。
- 评分公式：候选按 `logP(候选|上文)/长度 + 0.5·native_z` 混合排序
  （权重经 held-out 验证；改权重 = 服务的 `RERANK_NATIVE_W` 环境变量）。
- 进程内 ONNX（旧 C2 学生）已被淘汰，不再提供下载；代码路径保留仅为
  兼容旧安装，新装无需任何 ONNX 文件。
