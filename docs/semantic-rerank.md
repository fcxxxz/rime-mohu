# 魔虎语义重排（Qwen 本机大模型）配置说明

> 版本：2026-10-03 · 部署环境：macOS (Apple Silicon) · 默认模型：Qwen2.5-0.5B

## 模型下载

默认安装包**不含任何语义模型**。从 GitHub Release [`semantic-models-v1`](https://github.com/fcxxxz/rime-mohu/releases/tag/semantic-models-v1) 按需下载：

| 资产 | 说明 |
|---|---|
| `qwen2.5-0.5b-4bit` | **轻量之王**：276MB / +34 / ~0.7GB 内存 |
| `qwen2.5-1.5b-8bit` | 1.6GB / +41 / ~2.5GB 内存 |
| `qwen2.5-3b-8bit` | **效果之王（推荐）**：3.1GB / +53（零损失）/ ~4GB 内存 |
| `qwen2.5-0.5b / 1.5b / 3b` | bf16 原版（+35/+42/+53），体积最大，兼容非 MLX 环境 |

量化实测（零泄漏 438 句）：**8bit 三档零损失**、0.5B-4bit 仅 -1 分、3bit 崩盘不发布。
配置：`semantic_http_url: http://127.0.0.1:8765?model=3b-8bit`（量化档需本机已装 mlx-lm，服务自动识别 MLX 目录）。

**模型安装位置**（二选一，Rime 目录优先）：
1. `~/Library/Rime/mohu/models/` —— 推荐，自包含随 Rime 备份迁移
2. `~/.cache/modelscope/models/` —— ModelScope 下载缓存（开发机回退）

解压后目录形如 `…/models/mlx-community--Qwen2.5-3B-Instruct-8bit/snapshots/master/`（release 解包即此结构，无需改动）；平铺放模型文件（config.json 在模型目录第一层）也支持。安装命令见 release 页说明。

## 这是什么

打开「魔虎语义开」后，V5 引擎对自己不自信的候选菜单（top-2 分差小，
即"歧义菜单"）会交给本机常驻的 Qwen 小模型重新打分排序。候选文字更通顺的
排前面。实测（15 本完全未参与训练的书、438 句）：翻对 58 句、翻坏 23 句，
净收益 +35。

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
- 服务实现：`research/tiger2code_bench/rerank_server.py`（gitignore 外，
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
