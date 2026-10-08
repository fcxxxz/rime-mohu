# 字词码表维护

魔虎自然码的人工码表只有一张：仓库根目录的 `mohu_zrm.dict.yaml`。
字、词、简码和完整码都直接在这张表里维护，不再有唯一／多重切换、claims、secondary、overrides 或旧简码存档。

## 格式

YAML 文件头声明 `name: mohu_zrm`、`sort: original` 和 `text/code` 两列；`...` 后是普通码表。
每行 `字词<Tab>编码`，不再维护权重。一个字词有多个打法就写多行。

```text
是	u
试	u
试	uisp
式	uip
式	uipu
师	uipf
哪里	nal
```

上面只说明格式，具体归属以现行表为准，不会自动改成示例的归属。
`use_preset_vocabulary: false` 禁止从预设词库补入词频。同码条目的初始顺序按行序维护；用户候选管理、整句排序属于运行时功能，不等同于改主表。

- 加一个打法：加一行。
- 删一个打法：只删对应行，不必删掉这个字词的其他编码。
- 换位／调同码顺序：直接修改同码的行。
- 完整码与简码是明确的独立条目；修改后程序不会替你自动递补、分配或回写。
- 符号码也在这张表里；飞键别名在同一表的飞键区，修改时一并检查对应打法。

## 检查、构建、部署

```bash
make check-code-table
make dict
```

构建不会改写 `mohu_zrm.dict.yaml`。小鹤表 `mohu_flypy.dict.yaml` 是从主表转换的派生产物，不单独维护。
把对应方案的主表、schema 和 Lua 等运行时资源同步到 Rime 用户目录后重新部署；不能只复制码表而沿用仍引用旧表的 schema/Lua。
本次迁移不自动修改个人用户目录或删除个人词库。

整句词典、读音／字根数据、模型词表是仍有实际用途的引擎资源，不是另一个简码维护入口；本次没有删除它们。
历史码表与旧生成规则可从 Git 历史找回，不再保留在现行编码源数据中。

主方案中，码表字／词注入选项统一命名为 `mohu/inject_table_chars`、`mohu/inject_table_words`；旧的 `inject_fixed_*` 和 `show_*_anyway` 配置不再读取。使用个人 patch 时需同步改名。

## 发布包中的词典

每个方案现在只发布两个方案词典：

- `mohu_zrm.dict.yaml` / `mohu_flypy.dict.yaml`：直接维护的两列字词码表。
- `mohu_zrm.words.dict.yaml` / `mohu_flypy.words.dict.yaml`：构建合并的完整整句词库，包含单字读音和词条，不再引用分库。

原来的 `chars/base/tencent/moe/classics/wanxiang/words` 来源已移到 `tools/data/lexicon_sources/`，仍用于读音生成、审核导入、词频维护与原生词表构建，但不会进入发布包。添加人工整句词只改 `tools/data/lexicon_sources/zrm/mohu_zrm.words.dict.yaml`，再运行 `make dict`；不要直接修改根目录的合并产物。

打包使用明确白名单，不会因为一个文件名字以 `mohu_zrm` 或 `mohu_flypy` 开头就把它带入包中。

总计仍有 **5 个 `.dict.yaml`**：上述两个方案词典，加上 `tiger.dict.yaml`（虎码反查）、`mohu_pinyin.dict.yaml`（虎码方案的拼音反查）与 `mohu_charset.dict.yaml`（常用字集分类）。这三份是独立辅助功能的数据，本次保留功能，没有把“两个方案词典”冒称为“全包只有两个词典”。

根目录的合并词库与小鹤来源中间文件不入 Git；首次构建请先 `uv sync --locked`，再运行 `make quick`。`*_sentence_core.schema.yaml` 只保留词库／棱镜编译配置，不再复制交互式方案的开关和 Lua 管线。
