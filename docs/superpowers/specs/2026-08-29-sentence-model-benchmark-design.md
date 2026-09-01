# 中文整句模型基准设计

## 目标

在固定版本和固定语料上，比较三种语言模型在整句输入首选排序中的表现：

1. Rime 八股文 `zh-hans-t-essay-bgw`；
2. 万象 LTS `wanxiang-lts-zh-hans`；
3. 当前仓库 Tiger 原生 TCSKNM n-gram `sentence-ngram-mobile.bin`。

输入编码采用当前仓库可复现的自然码双拼主线，并覆盖纯双拼、少量辅助码、每词一辅、每字一辅四种条件。主结果使用 20,000 条中文句子，其中 10,000 条新闻稿句、10,000 条日常用语句；每类再按可获得的主题/来源做分层统计。

## 比较边界

这是“整句首选准确度”基准，不是用户长期调频后的体验调查。每个独立运行实例都使用干净用户目录，关闭用户词典、候选置顶、候选调序、emoji/简繁转换等会改变文本的状态；语言模型和方案自带静态词库保留。候选确认方式统一为读取输入完成后的首选候选，不模拟人工翻页或回改。

八股文与万象使用同一 `mohu_zrm_sentence` schema、词库、棱镜和候选探针，只替换 octagram grammar；Tiger 使用当前仓库的 native lexicon、n-gram 和解码器。这是受控的模型替换比较，不是三个完整发行包的产品排名。报告给出：

- 同一自然码输入条件下的句级首选准确率；
- 目标文本是否进入前 5/10/20 及候选池；
- 首选与目标的字符级、句级错误统计。

不把不同词库中不存在的目标句计作语言模型排序错误，而单独记录为 coverage 缺失。

## 版本与来源

- 当前仓库：实验运行时记录输入源 Git commit、benchmark runner 的逐文件 SHA-256、schema 文件 SHA-256、Tiger lexicon SHA-256、n-gram SHA-256；优先使用工作树已部署且与仓库一致的资源。
- Rime probe：默认要求链接 Squirrel 的 `@rpath/librime.1.dylib`，并锁定 librime/lua/octagram 的 SHA-256；probe 自身可因编译器产生不同字节，需在需要历史二进制时另传 probe SHA-256。
- 八股文：记录 Squirrel/librime 自带或当前用户目录中的 `zh-hans-t-essay-bgw.gram` SHA-256，并记录 librime 版本。
- 万象：固定本次取得的 `wanxiang-lts-zh-hans.gram` 字节内容，以完整 SHA-256 为复现依据；模型文件不提交仓库。
- 语料：新闻使用固定 CLUE TNews `train.json`（15 个领域），日常用语使用固定 Hugging Face `silver/lccc` base-test JSONL.gz；只在本地保存抽样原文和来源哈希，不把全文提交到仓库。

## 输入条件

每个句子先通过固定的 pinyin 读取和自然码转换得到一串二键双拼。当前方案的“句中辅/词辅”在整句码流中使用一位辅助键；辅助键从当前仓库 `tools/data/tiger_aux.txt` 取每个字符的最长 Tiger 主码前两键的首键，多码字符按文件顺序取第一条，并在 manifest 中记录选择规则。

- `plain`: 仅双拼，按字符连续输入。
- `sparse_aux`: 确定性稀疏辅助：每四个词的词首加入一位辅助键（即第 1、5、9… 个词），记录实际辅助字符数和比例。
- `word_aux`: 依据固定的最大匹配分词结果，每个词的词首加入一位辅助键；分词器版本、词典和边界写入语料 manifest。
- `char_aux`: 每个汉字后加入一位辅助键。

上述“一个辅助码”指当前方案实际接受的单个辅助键（`YYX` 派生形），不是完整两键 Tiger 前缀。为避免把编码生成器本身混入模型分数，目标句若有无法读取的多音字、非汉字、数字/英文混输片段，则进入排除清单并保持固定数量补采。

## 指标

每个方案×输入条件报告：句级 top-1 exact accuracy、字符级 accuracy、目标进入前 5/10/20 的比例、coverage、平均候选数、平均/分位延迟，以及按来源、主题、句长和辅助码比例的切片。主显著性区间使用配对 bootstrap（10,000 次，固定 seed）；同时列出排除数、空候选数、引擎错误和 fail-open 次数。

## 执行流程

1. 生成并审计语料 manifest，锁定 20,000 条后不再按结果删句；Rime runner 只接受带 ownership marker 的 staging `data/`。
2. 用 50 条覆盖所有输入条件的校准样本验证拼音、辅助码、Rime `set_input`/候选读取和 Tiger native 解码一致性。
3. 在干净临时 Rime 用户目录中部署各方案；先跑 200 条 smoke，再跑全量。Rime probe 必须链接 Squirrel 的 `@rpath/librime.1.dylib`，并由 runner 做 ABI 检查。Rime 原始候选保存为按模型/模式分文件的 TSV，Tiger 另存 latency TSV；统计器再生成 joined per-case `results.jsonl`。
4. 运行独立统计器，生成 CSV/Markdown 汇总和错误样例；重新校验所有输入行数、哈希和分层配额。
5. 对不可直接公平比较的部分（例如 Wanxiang Lua 预测、Tiger Qwen 重排）单列，不混入三模型主表；Qwen 不属于本次三模型主结果。

## 预期产物

- `research/lm_sentence_compare/` 下的生成/运行/统计脚本和测试；
- 语料与资源 manifest（含来源、版本、哈希、许可证）；
- 原始候选/延迟 TSV、joined per-case `results.jsonl`、汇总 CSV 和最终 Markdown 报告；
- 一份当前魔虎功能清单，明确句中辅、简快码、出简让全、候选注入、上下文调频、native Tiger 解码等功能在基准中是否启用。

## 限制

端到端首选结果同时反映词库覆盖、分词策略、候选截断和模型排序，不能解释为纯语言模型 perplexity。新闻数据的版权/许可证若不能确认，只保存本地输入并在报告中标注，不声称可再分发。若某方案无法在同一 librime ABI 下启动，报告该方案为“未实测”并给出具体原因，不用静态结果冒充准确率。
