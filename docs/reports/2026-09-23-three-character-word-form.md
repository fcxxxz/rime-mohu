# 三字全码词形排序：码内词频加分（2026-09-23）

## 问题与范围

`bagerf` 的 native 静态首选原为「把个人」，第二是「八个人」：路径分别为
`把+个人`（−8.619）和 `八个+人`（−8.960）。base 词典里两词的权重却是
`八个人=354`、`把个人=173`。原引擎词表没有这两个三字词的全码行，完整
文本的词典证据不能参与解码。同码全局对数词频梯度即使补全词条，对该
2.05 倍词频差的区分也约 0.32 nats，小于旧字符路径的 0.34 nats 差。

本次把 base 词典权重≥1 的三字词以 rank 99 注入六键全码（包括飞键闭包），
并回填已有码形的同文本词频。rank≥90 条目只允许整段命中，不加入长句
内部边。仅六键三字静态整段词边增加 `word_form_weight ×
max(词频/该码最大词频, 0.35)`；二字词和长句多边路径不吃这项。C ABI、Lua
绑定、`mohu_zrm` / `mohu_flypy` schema 均设权重 6.5，设 0 可关闭。

## 可复核测试

- `uv run python -m unittest tests.test_mohu_lexicons tests.test_mohu_tiger_sentence_native tests.test_tiger_lexicon_fly -q`：21 项通过；覆盖六键词、简码回填、飞键闭包和词库往返生成。
- `make tigerengine-word-form tigerengine-lua-safety tigerengine-word-edge tigerengine-word-gate tigerengine-reading-prior tigerengine-context tigerengine-user-model tigerengine-safety`：通过，含 C/Lua setter 与 `bagerf` 真实模型排序；旧用例中 `吃一口` 注入导致分歧门窗口语义变化，测试已按定义更新并保留开门反例。
- 静态引擎（用户层关闭）下 `bagerf` 最终：八个人 −1.445、把个人 −4.725，分差约 3.28 nats。新词库但关闭该项时依旧把个人先（−6.305 对 −6.328）。

## A/B 结果及局限

以 `mohu_zrm.base.dict.yaml` 中同一六键码、至少两条三字词、最高词频严格高于第二名的码位为抽样范围；固定种子 20260923 抽 500 码位并加入 `bagerf`，共 501。每码位以 base 词典最高权重词为目标；同一 V5 模型、beam 200、all_ranks 1、reading_prior 1.0、composed_reading 1.3、word_edge 1.5、text_lexicon 6.5、user_model_weight 1.0；三组依次为 HEAD 旧词库/关新权重、新词库/关新权重、新词库/开 6.5。

| 指标 | 旧词库 | 仅注入 | 注入+码内权重 |
|---|---:|---:|---:|
| 词典最高权重词 top1 | 345/501 | 394/501 | 485/501 |

旧→最终改变 141、修好 140、修坏 0；只注入的阶段曾修好 53、修坏 4，最终加权恢复这 4 个。仍有 16 个未达到词典最高权重词首选，**不是这类问题的根治**。这个基准以词典权重作目标，不能代表自然语言语境下哪种文本更合理，也不是实际 Rime 菜单/用户学习评测。

同样口径固定种子抽 500 个二字重码，旧/新均为 299/500 且首选逐条一致（这组输入的 Rime smart 层一般另行接管）。**这不表示所有 4 键词条没有变化**：三字词同文本权重回填使 zrm、flypy 生成词表各有 5 条既有 4 键码位（diff 各 10 行删加记录）的第 4 列从 0 变成 base 权重（如 `smdb 什么都`）。这些 zrm 码位 `smdb/vmda/vmdm/vmdo/vmhk` 逐一旧/新首选相同；词典源数据与固定码表未改，但其低位候选分数可能变化，不能把二字样本不变外推到全部 4 键菜单。从 `research/_downloads/整句评测集v1/seg_1k.txt` 按顺序取前 100 条可逐字编码的 8–22 字真句，逐字取词表双拼最低档码：旧/新 top1 均 50/100，逐条 0 改变。另有 20 条替代长输入旧/新 0 改变，但其中只有 3 条解成预期文本；它们**不是**历史报告所说的 20 句原始清单，也不能声称所有长句零回归。所有实际比较产物在 `/tmp/mohu-three-char-{ab-500,results,corpus100,corpus100-results,two-char-ab-500,two-char-results,long20,long20-results}.tsv`；生成样本和 A/B 探针在 `/tmp/mohu_{three_char_cases,three_char_long_corpus,two_char_cases}.py` 与 `/tmp/mohu_three_char_ab.cc`。

## 本机部署与验收

仅将 `mohu_zrm` 的线上 schema 增加 `word_form_weight: 6.5`，并同步 zrm 引擎词表、`lua/mohu_tiger_sentence.lua` 与 `mohu/runtime/libtigerengine.dylib`；保留仓库中另一项尚未部署的 4 键简码词修复，没有覆盖线上完整 schema 或用户数据。原线上四文件备份位于 `~/Library/Rime/backups/2026-09-23-three-char-word-form/`。`Squirrel --reload` 后编译产物 `~/Library/Rime/build/mohu_zrm.schema.yaml` 含新值，编译日志无 `error building config`；有 `mohu_zrm.schema` 循环依赖警告，未阻断 schema 保存与加载。随后完整退出并重启 Squirrel，线上 dylib、Lua、词表与本次构建文件哈希分别一致，动态库签名有效。线上模型+线上词表、按 schema 参数的独立原生探针得到 `八个人` 第一（−1.445）、`把个人` 第二（−4.725）。探针绑定安装目录动态库的尝试曾以退出码 137 终止；重用同哈希的仓库动态库则正常。未单独捕获 Squirrel 真实候选菜单，故验收精确表述为“文件已部署、重启和编译已核对、原生解码已验证”，不是 GUI 菜单逐键实测。flypy 词表和 schema 已在仓库构建，但本次未部署到用户目录（当前启用方案是 mohu_zrm）。

本次只治理“三字六键全码词形的同码静态排序”。短语在长句内部以及未入词表的组合仍主要由字符模型控制；完整解决词界与语境冲突需另行训练/设计模型侧词级证据，并以真实句子集评估。
