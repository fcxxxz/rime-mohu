# 2026-09-15 学习词不可见（熊皮子/xspizi）修复报告

> 用户报告两个症状：① `uhbjuf`（上半身全码）有时首选「上班」、次选
> 「上半身」；② 打过一次「熊皮子」后再打 `xspizi`，学习词不出现，
> 菜单只有一条（熊罴子）。②已完整定位并修复；①在当前所有可构造的
> 状态下无法复现（见文末）。

## 复现方法

隔离工作区 = 线上 `~/Library/Rime` 全量副本（源 yaml、lua、mohu/
运行时、userdb、`user-ngram.snapshot`、语义模型），外加线上
`build/` 编译产物（单 schema 部署不编译 import_tables，缺它 smart
词表不加载、菜单只剩 native——首两轮结论全部作废重测的教训）。
探针：`research/lm_sentence_compare/probes/rime_candidate_dump_squirrel`
（fresh）与 `rime_wordgroup_dump_squirrel`（afterA 提交前缀）；
逐候选落盘日志复用 09-09 的诊断手法（filter 协程收尾不执行，
必须逐 yield 即时落盘）。

## 症状②根因：两层叠加

进入流（线上数据、xspizi）显示引擎排序正确：native 前两名是
熊皮子（-9.71，用户层已学习）、熊罴子（-9.60）；但输出流全部丢失。

### 层 1：reorder filter 的词库门控丢弃「用户层学习词」

`lua/mohu_reorder_filter.lua` 的 native 门控设计为「native 只允许输出
词库（smart 流）也能覆盖的文本」（防静态码表造错词），例外只有
≥5 字、2 字终态、`_personal` 类型。而 `personal` 标记只来自**个人词库**
（userdb 扫描）；仅靠**用户调频层**（ngram 快照，提交文本的字符
trigram）顶上来的学习词是普通 native 路径。熊皮子是码表没有的自造词，
smart 流只物化 ≤2 条 poet 句（本轮是「兄痞子」），于是 20 条 native
候选（含第一名熊皮子、第二名熊罴子）被整条丢弃，菜单头变成 smart
句「兄痞子」。学习闭环就此断裂：不可见 → 无法提交 → 永远进不了
userdb/个人词库 → boost 不增长。

### 层 2：sentence_visibility_filter 直接删除超配额句形候选

09-14 重新接线的整句显示裁剪（`sentence_visible_candidates: 1`）把
第 2 条及以后的句形候选**从菜单删除**（非押后）。即使学习词穿过门控，
排第二也会被删——「过滤把候选写死了，只能出现一个」。对照魔然
（rime-moran）：其过滤链没有任何整句裁剪，全部候选按序显示。

## 修复

1. **引擎（tigerengine.cc）**：解码路径累计用户层增益
   `user_gain = Σ[log(融合分) − log(静态分)]`（与 logp_cache 同生命
   周期的并行缓存，beam 展开逐 trigram 累加），达到
   `kUserGainPersonalThreshold = 5.0` 的路径按 personal 输出。阈值
   标定依据：单次学习的 trigram 增益 >10 nats，常规路径边界 <1 nat。
   无 ABI 变化，dylib 直接替换。实测（用户真实快照）：
   - xspizi：熊罴子、熊皮子 personal=1，其余 18 条（胸痞子/兄痞子/
     熊罴字…）全部 0；
   - uhbjuf：上半身、上班X 系全部 0（无误标）。
2. **裁剪 filter（lua/mohu_sentence_visibility_filter.lua 0.2.0）**：
   超配额句形候选从「删除」改为「押后到全部词组之后」（可翻页到达，
   搜狗式首条整句+词组观感保留、魔然式完整性保留）；≤4 字 `_personal`
   不占句形配额（用户词库随 native 序输出），反复输入的长句 personal
   仍计入配额押后（09-09 洪水教训保留）。

## 端到端验证（隔离工作区，线上 userdb + ngram 快照）

- `xspizi` → **[熊皮子, 熊罴子, 兄痞子, 熊皮, 熊罴, …]**：学习词第一、
  用户默认词第二、smart 句占句形配额位、词组随后。
- `uhbjuf` → [上半身, 上班, 上半, …] 不变。
- 测试：`tests/mohu_sentence_visibility_filter_test.lua` 14 项全过
  （押后语义 + 短 personal 豁免新用例）；tigerengine
  safety/lua-safety/snapshot-io/user-model/reading-prior/word-edge/
  word-gate/context 与 mohu_tiger_* lua 套件全过。

## 症状①（uhbjuf 首选上班）：未复现，附排查记录

当前状态（repo HEAD lua + 线上 dylib + 用户 userdb + ngram 快照 +
语义模型开）穷举：fresh、8 组上文（今天/我要/明天/明天要/要去/周一/
明天早上，含提交成功与失败两组）、引擎禁用（fail-open 模拟）、裁剪
开关，菜单稳定 **[上半身, 上班, …]**。机制上「上班」是 smart 部分跨度
词（4/6 键），native「上半身」quality 50 恒在 smart 之前，word_order
跳过 native，不可能翻越。结论：该现象只可能出自 09-13/09-14 的某个
中间部署态（当日线上文件多次更换、存在 14:24 版 translator 与 18:11
版 schema 的错配窗口），或极强上文下的 word_order 翻转（需
score(上班)−score(上半身) > 1.0 nat，本轮所有上文均未达到）。如再次
出现：记录当时的上屏历史与 `~/Library/Rime` 内 lua/schema/dylib 的
修改时间，并确认 Squirrel 已完全重启（dylib 按进程映射，旧进程会用
旧库，见知识库「宿主重启要求」）。

## 部署清单

- `tiger_sentence_native/libtigerengine.dylib` → `~/Library/Rime/mohu/runtime/`
  （需完全退出并重启 Squirrel）
- `lua/mohu_sentence_visibility_filter.lua` → `~/Library/Rime/lua/`
- `tiger_sentence_native/mohu_tiger_sentence.lua`（及配套 mohu_runtime/
  mohu_sentence）→ `~/Library/Rime/lua/`
- schema 注释更新（mohu_flypy/mohu_zrm），参数值不变
