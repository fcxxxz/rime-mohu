# 魔虎大模型与模型分包设计

## 目标

把普通魔虎方案与大模型整句方案明确分开：普通魔虎继续使用八股文语言模型；“魔虎大模型”只使用 TCSKNM n-gram 与可选 Qwen 重排。用户不需要记命令即可选择已安装的 Qwen3.5-0.8B 或 Qwen3-0.6B，模型权重不进入方案包。

## 方案边界

- `mohu_zrm` 及现有双拼方案保持现有显示名和行为，继续通过 `mohu:/octagram/enable_for_sentence` 使用 `zh-hans-t-essay-bgw.gram`。
- `mohu_tiger_sentence` 保留 schema ID 以兼容现有用户词频、`user.yaml` 和自定义配置，显示名改为“魔虎大模型”。该 schema 删除八股文 include，只保留 TCSKNM 原生整句候选和 Qwen 模型重排。
- “模型重排”是唯一需要用户打开的静态开关，显示状态为“模型重排关 / 模型重排开”。内部旧 option 名保留作为迁移兼容，不再显示“神经重排”。
- 没有可用 Qwen 模型时，魔虎大模型仍可用 n-gram 候选；模型重排安全关闭，不阻断输入。

## 模型配置与发现

用户目录下使用固定注册表和一个简单选择文件：

```text
~/Library/Rime/tiger/models/Qwen3.5-0.8B-MLX-4bit
~/Library/Rime/tiger/models/Qwen3-0.6B-4bit
~/Library/Rime/tiger/model-selection
```

`model-selection` 默认值为 `qwen35-0.8b`，不做自动回退。运行时只扫描这两个受支持的目录；目录存在且基础文件完整时才加入模型菜单，最终指纹校验仍由 scorer 执行。当前选择的模型缺失或校验失败时，菜单显示明确的安装提示，客户端回退到 n-gram 原序。

Rime 的 schema switch 状态是静态 YAML，不能按目录实时增删。因此：

- F4 方案菜单只显示静态的“模型重排”开关。
- `/model` 触发 Lua 动态模型菜单，只列出当前可用的模型，并显示当前选择。
- 选择模型写入 `model-selection`，清空 Lua score cache，并通知 scorer supervisor 重载；用户不需要执行 shell 命令。重载期间请求 fail-open 到 n-gram。
- 目录中新放入模型后，下一次打开 `/model` 即重新检测；目录被移除后不再显示。

## Scorer 生命周期

`run_qwen35_scorer.command` 从一次性 Python 进程改为用户级 supervisor。supervisor 读取 `model-selection`，只启动当前选中的模型，监测选择文件变化后停止旧子进程、启动新模型并等待预热完成。模型目录缺失、指纹不匹配或启动失败只记录简短状态，不影响 Rime 主进程。

Lua profile 改为从同一注册表读取选择、模型路径和固定 SHA-256，不再通过切换脚本复制 profile。客户端在每次 profile 重载时验证模型身份和协议；旧 scorer 或旧 profile 不匹配时保持 n-gram 顺序。

## 分包

### 标准魔虎包

现有 `make dist` 产物，包含普通魔虎方案、八股文 grammar 和已有词库；不包含 native 整句组件、Qwen scorer 或任何模型权重。

### 魔虎大模型插件包

新增独立 addon 产物，包含：

- `mohu_tiger_sentence.schema.yaml`（显示名“魔虎大模型”）
- TCSKNM n-gram 模型、虎码整句码表和 `libtigerengine`
- Lua translator、reranker、动态模型菜单、配置同步模块
- scorer supervisor、模型注册表、安装说明和两个模型清单

插件包不包含 Qwen 权重，也不复制普通魔虎包中已有的基础词库和 OpenCC 数据；安装前提是标准魔虎包已存在。

### Qwen 模型包

两个模型作为独立下载项发布，用户自行选择并放入上述目录：

- `qwen35-0.8b`：Qwen3.5-0.8B-MLX-4bit，约 622 MiB
- `qwen3-0.6b`：Qwen3-0.6B-4bit，约 335 MiB

模型包带有目录名、版本、量化格式和 SHA-256 清单。插件只接受注册表中的 4-bit 模型；不匹配的文件不进入菜单，也不会被加载。

## 错误与兼容

- 两个模型都缺失：显示安装提示，n-gram 正常工作。
- 当前选择模型缺失：不自动切换到另一个模型，模型重排暂时关闭。
- 模型目录不完整、量化格式错误、SHA-256 不匹配：scorer fail-closed，Lua 保留原生排序。
- 选择文件损坏或包含未知 ID：使用默认 ID `qwen35-0.8b` 进行检测；若默认模型也不存在，则进入无模型状态。
- 保留 `mohu_tiger_sentence` schema ID 和旧内部 option 名，避免升级时丢失用户数据；所有用户可见文案使用“魔虎大模型”和“模型重排”。
- native C ABI 的旧 `include_early` 参数继续保留为兼容接口，但 canonical Lua 永远传 0，运行时没有提前上屏路径。

## 验证

测试覆盖以下行为：

1. 普通魔虎 schema 仍包含八股文 grammar；魔虎大模型 schema 不包含 octagram include。
2. schema 显示名和“模型重排”开关正确，旧“神经重排”文案不再出现在活动配置。
3. 模型目录存在性、配置文件选择、未知选择和缺失模型提示正确。
4. `/model` 菜单只输出存在且可识别的模型；选择后配置写入、cache 清空和 scorer 重载信号正确。
5. scorer supervisor 在 3.5/0.6B 间切换，启动失败和 hash 错误均 fail-open。
6. 标准包和插件包均不包含 Qwen 权重；两个模型清单的大小、路径和 hash 与注册表一致。
7. 当前回归句 `yiyjjqkjiuuisbgb` 和 `nibuykzljyufnzhkle` 在 Qwen ready 时分别得到“一眼就看出是搜狗”和“你不要再精神内耗了”，无模型时保持 n-gram 原序。
