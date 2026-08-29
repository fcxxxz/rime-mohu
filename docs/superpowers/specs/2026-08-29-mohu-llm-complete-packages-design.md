# 魔虎大模型完整方案包设计

## 目标

把当前单一的 `mohu_tiger_sentence` overlay 重构为两个可独立安装、可直接使用的完整方案包：

- `mohu-llm-zrm.zip`：魔虎大模型·自然码
- `mohu-llm-flypy.zip`：魔虎大模型·小鹤

两个方案共享 Qwen scorer 和 native 解码实现，但各自使用正确的双拼码表、schema 依赖和用户词库。用户解压后双击安装器即可完成部署，不需要手工编辑 YAML 或执行切换命令。

## 命名

- 运行时根目录统一使用 `~/Library/Rime/mohu_llm/`。
- 共享运行时放在 `mohu_llm/runtime/`，Qwen 权重放在 `mohu_llm/models/`，配置放在 `mohu_llm/config/`。
- 方案数据放在 `mohu_llm/data/zrm/` 和 `mohu_llm/data/flypy/`。
- 新 schema ID 为 `mohu_llm_zrm` 和 `mohu_llm_flypy`，显示名分别为“魔虎大模型·自然码”和“魔虎大模型·小鹤”。
- 不保留旧 `mohu_tiger_sentence` schema，也不保留旧 `tiger/` 运行时路径；安装后只使用新命名。

## 目录与职责

```text
~/Library/Rime/
├── mohu_llm_zrm.schema.yaml
├── mohu_llm_flypy.schema.yaml
└── mohu_llm/
    ├── runtime/
    │   ├── libmohu_llm_engine.dylib
    │   ├── qwen35_scorer.py
    │   ├── run_qwen_scorer.command
    │   ├── install_qwen_launch_agent.command
    │   ├── scorer_models.zsh
    │   └── mohu_llm_reranker_profile.lua
    ├── data/
    │   ├── sentence-ngram-mobile.bin
    │   ├── zrm/mohu_llm_zrm.lexicon.txt
    │   └── flypy/mohu_llm_flypy.lexicon.txt
    ├── models/
    │   ├── Qwen3.5-0.8B-MLX-4bit/
    │   └── Qwen3-0.6B-4bit/
    └── config/
        └── model-selection
```

`sentence-ngram-mobile.bin` 是双拼无关的共享语言模型；整句 lexicon 是方案专属输入码形，不能在两个方案之间复用。Qwen 模型目录不随方案包复制，由独立模型包提供。

## Rime 行为

每个新 schema 都包含完整的 Rime pipeline：固定字、用户词库、普通 smart translator、native 整句 translator、模型重排、符号、反查、简码和候选管理。自然码 schema 使用 `mohu_zrm`/`mohu_zrm_fixed` 依赖，小鹤 schema 使用 `mohu_flypy`/`mohu_flypy_fixed` 依赖。

两个 schema 都保留“模型重排关/模型重排开”开关，不再显示“神经重排”；提前上屏逻辑继续不存在。模型缺失、hash 不匹配或 scorer 不可用时只保留 n-gram 原序，不吞输入。

`/model` 菜单只负责 Qwen 模型选择，选择文件统一为 `mohu_llm/config/model-selection`。目录扫描、选择校验和 supervisor 重载不依赖当前使用的是自然码还是小鹤。

## 安装

每个方案包根目录包含自己的安装器和 `package.json` 清单，清单声明 schema ID、数据子目录、共享运行时版本和所需文件。安装器必须：

1. 校验清单和所有运行时/数据文件存在。
2. 原子复制 schema、Lua、runtime 和对应 data 目录到用户 Rime 目录。
3. 合并 `default.custom.yaml`，只追加对应 schema ID，不重复写入，也不破坏 block/inline/comment patch。
4. 为当前方案注册 schema，并 reload Squirrel；scorer 启动失败只能显示提示，不能阻断输入。

安装自然码包不会注册小鹤 schema，反之亦然；两包都安装时两个 schema 都可在 F4 中选择。重复运行任一安装器必须幂等。

## 构建与发布

Makefile 提供三个清晰目标：

- `make mohu-llm-zrm-dist`：完整自然码方案包。
- `make mohu-llm-flypy-dist`：完整小鹤方案包。
- `make mohu-llm-runtime-dist`：仅供开发/CI 使用的共享 runtime 包。

GitHub Actions 在 macOS arm64 runner 上构建两个方案包，下载并 hash 校验 n-gram，验证 dylib 签名、两个 lexicon 和 schema 依赖，然后上传：

- `mohu-llm-zrm-latest.zip`
- `mohu-llm-flypy-latest.zip`
- `mohu-llm-runtime-latest.zip`

Qwen3.5 和 Qwen3 权重继续作为独立 Release 资产；方案包和 runtime 包不得包含 `*.safetensors`、`*.gguf` 或任何模型权重副本。模型资产解压后必须直接产生对应的模型目录名，不能包含构建机绝对路径。

标准 `rime-mohu-zrm-latest.zip` 和 `rime-mohu-flypy-latest.zip` 继续保留普通魔虎与八股文行为，不携带 native runtime。

## 测试与验收

测试必须覆盖：

- 两个新 schema 的显示名、schema ID、双拼依赖和专属 lexicon 路径。
- 单独安装自然码/小鹤包只注册对应方案；两包同时安装时列表和路径均正确。
- 安装器对空白、block、inline、带注释和重复 `default.custom.yaml` 的幂等合并。
- 两个方案都能在 native 引擎缺失时回退到普通候选，且不启用提前上屏。
- `/model` 在两个 schema 下共享选择状态，supervisor 能切换 Qwen3.5/Qwen3。
- 每个 Release zip 的根目录、可执行权限、签名和文件边界；模型 zip 的根目录和 manifest hash。
- 自然码回归句 `yiyjjqkjiuuisbgb`、`nibuykzljyufnzhkle`，以及小鹤等价输入得到相同目标句。

验收标准是：用户只下载标准包、任一完整 LLM 方案包和所需 Qwen 模型，双击一次安装器后即可在 F4 看到对应方案，输入时有候选框，模型重排可用；未安装 Qwen 时方案仍能正常输入。
