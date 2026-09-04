# 扁平化魔虎方案包设计

## 目标

发布两个面向普通用户的扁平化 Rime 方案包：

- `rime-mohu-zrm-latest.zip`
- `rime-mohu-flypy-latest.zip`

两个包分别包含自然码和小鹤的完整魔虎方案，不再发布普通方案包或带 `llm` 的对外名称。压缩包根目录直接对应 Rime 用户目录，用户解压/复制后只需执行一次 Rime“重新部署”。

V5 n-gram 模型不进入方案包，作为独立的 `mohu-sentence-ngram-v5.bin` Release 资产发布。用户将模型文件放入 Rime 用户目录的 `mohu/model/`；运行时按文件名中的数字版本选择最高的 `mohu-sentence-ngram-vN.bin`。因此未来发布 v6/v10 时无需重打方案包。

## 命名与目录

对外和内部均移除 `llm` 命名：

- 自然码公开 schema ID：`mohu_zrm`
- 小鹤公开 schema ID：`mohu_flypy`
- 运行时根目录：`mohu/`
- 模型目录：`mohu/model/`
- 方案数据目录：`mohu/data/zrm/` 和 `mohu/data/flypy/`

现有普通方案的 compile-only 文件需要使用不冲突的内部依赖名，避免公开 schema 与旧普通 schema ID 重名。每个扁平包复制对应 scheme 的完整 compile-only schema/词典集合，但只在 `default.yaml` 注册一个公开 schema。两个公开 schema 的依赖、Lua candidate type、模型路径、测试和默认方案列表全部切换到新命名。

## 扁平包内容

每个方案包的 zip 根目录直接放置 Rime 用户目录内容，不包含 `base/`、安装脚本或绝对路径：

```text
rime-mohu-zrm-latest.zip
├── default.yaml
├── mohu_zrm.schema.yaml
├── mohu_zrm_* compile-only schemas and dictionaries
├── lua/
├── opencc/
├── mohu/
│   ├── data/zrm/mohu_zrm.lexicon.txt
│   └── model/                         # 用户单独放置 vN 模型
└── ...
```

`default.yaml` 只注册当前包的公开 schema；自然码包不注册小鹤，小鹤包不注册自然码。方案包不包含任何 `mohu-sentence-ngram-vN.bin`，但 schema 指向 `mohu/model/`，运行时扫描并选择最高数字版本；目录为空、文件名不符合规则或引擎加载失败时保持 fail-open。

如提供便于拖放的模型 zip，其内容只包含：

```text
mohu-sentence-ngram-v5.zip
└── mohu/model/mohu-sentence-ngram-v5.bin
```

模型文件使用 `mohu-sentence-ngram-vN.bin` 命名和 CI 校验的 SHA-256。模型包可独立更新，不触发方案包重打包；选择逻辑使用数字比较而非字符串或修改时间排序。

## 构建与发布

Makefile 增加扁平方案包目标，先将方案内容直接复制到目标根目录，再压缩；不再使用现有 LLM `base/` 安装包目标作为 Release 入口。GitHub Actions 只发布两个扁平方案 zip 和独立的 `mohu-sentence-ngram-v5.bin` 模型资产，删除旧普通包和带 `llm` 名称的 Release 资产。

CI 验收以下不变量：

1. zip 内不存在 `base/`、绝对路径或 `..` 路径；
2. 方案包只包含一个公开 schema 和对应 scheme 数据；
3. 方案包不包含 V5 模型或 Qwen 权重；
4. 模型资产固定命名为 `mohu-sentence-ngram-v5.bin`，安装目标为 `mohu/model/`，运行时按数字版本选择最高的 `vN` 文件；
5. 解压到空 Rime 目录后，重新部署可以发现对应公开 schema。

## 用户配置取舍

扁平包面向空的或专门准备的 Rime 用户目录，直接复制同名文件可能覆盖现有配置。此次不保留旧安装器的配置合并、迁移和兼容逻辑，以换取无需脚本的最短安装路径。已有用户若使用该包，应先备份 Rime 目录。

## 测试

新增或调整测试覆盖：

- 两个公开 schema 的 ID、依赖、candidate type、模型路径和默认列表；
- 扁平 zip 根目录和单方案边界；
- 方案包不含模型，模型包路径正确且只有一个模型文件；
- `mohu/model/` 缺失、无合法 `vN` 文件或最高版本加载失败时 native 引擎回退普通候选；
- workflow 只上传新的扁平方案包和独立模型包。
