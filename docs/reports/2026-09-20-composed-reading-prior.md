# 组合读音罚分与变体读音归并（vgxju=整车 jū 首选修复）

> 2026-09-20。用户报告：小鹤 `vgxju`（整+辅助码x + jū 音节）首选是
> 「整车」（zhěngjū，不成词的读音组合），次选才是「整句」。本文记录
> 根因、两项修复（组合读音罚分 + 飞键换头变体读音归并）、验证数字与
> 部署。承接 [读音先验](2026-09-04-reading-prior.md)（2026-09-04）的
> 机制线。

## 现象与根因

`vgxju` 静态解码（probe_decode，V5 模型 + flypy lexicon，旧引擎）：

| 候选 | 分数 | 说明 |
|---|---:|---|
| 整车 | −12.66 | 组合路径 [vgx=整][ju=车 jū] |
| 整句 | −13.57 | 组合路径 [vgx=整][ju=句 jù] |

根因链（三层叠加）：

1. **车的 jū 音是源数据有意收录的多音字**（`tools/data/pinyin_simp.txt`
   `车 ju 60000` vs `车 che 1338066`），lexicon 里 `ju` 码下排名第 2
   （原版虎整句码表 `ju 车 1` 继承，照顾象棋单字直打）。
2. **字符三元 LM 按字打分、与读音无关**：`ie 车 1 249` 与 `ju 车 2 249`
   共享词 id 249，jū 音路径完整继承 chē 音语料统计——P(车|整) 领先
   P(句|整) 约 3.4 nats（无先验裸分：整车 −9.47 vs 整句 −12.88）。
3. **既有读音先验（2026-09-04）已生效但不够**：P(ju|车)=4.3% →
   先验 −3.15（关闭先验对拍确认已计入 −12.66），压不住 3.4 nats 的
   搭配差。先验设计针对「占比≈0 的罕用读音」（万 mò ≈ −14），对
   「占比不小但只用于专名/棋类的次读音」留了缝。

另发现**连带 bug**：开关先验时整句分数恰差 ln2=0.693——句 的 ju/jv
两种码形（飞键换头变体）各挂 254300 简频，引擎按「码头两字母」去重时
当成两个读音、总频翻倍，**主读音先验被错罚 ln2**。受影响的是全部
换头变体音节（ju→jv、yu→yv、xq→xo、qx→qo、wz→wk；flypy 216 行 /
zrm 295 行），句/居/举/于/与/玉/千/前/些/雪/修… 的主读音全部带 −0.69
错罚。

## 修复

**组合读音罚分**（`tigerengine.cc`）：单字边参与**多段组合路径**时
（非整段单边），读音先验再加一次（平方先验）。语义：组合路径宣称了
整个多音节读音，无义读音组合（zhěngjū）按 share² 压制；整段单边
（单字直打、整词命中）不吃罚分。新 ABI
`tiger_engine_set_composed_reading_prior_weight`（[0,4]，默认 1.0，
0 关闭）；`reading_prior_weight=0` 视为整套读音先验关闭（组合罚分
一并消失）。配置 `tiger/composed_reading_prior_weight`（两主方案
schema 默认 1.0，Lua 侧 mohu_tiger_sentence.lua 透传，旧 ABI dylib
静默保持内建默认）。

**变体读音归并**（码表格式 + 引擎）：lexicon 增加可选第 6 列
「规范音节头」（reading_canon，两字母小写），换头变体行指回源读音
（`jv 句 8 1062 254300 ju`）；引擎装载期按规范键去重归一。构建器
`tools/build_mohu_lexicons.py` 的 `emit()` 以 (码, 方案) 确定函数生成
（`fly_inverse[head]`），`load_rows` 同步解析（文件↔行往返无损）。
旧引擎忽略第 6 列、旧码表无第 6 列——双向兼容，行为退回各自现状。

## 验证

关键探针（新引擎，flypy lexicon；zrm `vgxjv`/`vgxju` 同序）：

| 查询 | 旧首选 | 新首选 | 说明 |
|---|---|---|---|
| `vgxju` | 整车 −12.66 | **整句 −12.88**；整车第 4（−15.77） | 主修复 |
| `vgxjv`（飞键变体） | 整车 | 整句 | 变体同修 |
| `vgie`（整车 chē 词边） | 整车 | 整车（−9.43 不变） | 整词不受影响 |
| `jumapc`（车马炮） | 车马炮 | 车马炮（−18.80，仍领先次名 1.6 nats） | 象棋真组合保留 |
| `ju` 单字 | 举/车/巨… | 同序（车 rank 2，分数 −14.58） | 单字直打不变 |
| `mohuz`（万虎案例） | 莫虎 | 莫虎 | 2026-09-04 修复保持 |

数字核对：整车 −9.47（裸）− 3.148×2（平方先验）= −15.77 ✓；
整句 −12.88（裸）− 0.0000×2（句 ju 占比≈1，ln2 错罚已修）✓。

500 词 A/B（2026-09-04 同口径：频表前 300 + 种子随机 200，二字词，
静态引擎新旧配置对比 top-1）：

| 方案 | 档位 | 旧 | 新 | 翻转 |
|---|---|---:|---:|---|
| 小鹤 | 裸双拼 | 318/500 | 318/500 | 0（排序逐词不变） |
| 小鹤 | 一位末辅 | 418/500 | **419/500** | +拉脚，0 修坏 |
| 小鹤 | 两位末辅 | 414/500 | 414/500 | 0 |
| 自然码 | 裸双拼 | 307/500 | 307/500 | 0 |
| 自然码 | 一位末辅 | 416/500 | **417/500** | +拉脚，0 修坏 |
| 自然码 | 两位末辅 | 414/500 | 414/500 | 0 |

测试：`make tigerengine-reading-prior` 扩展（组合罚分开/关可逆、
vgxjv/vgxju 整句第一、vgie 整车第一、jv 单字序列开关一致、范围拒绝）；
`tests/test_mohu_lexicons.py` 扩至 12 项（第 6 列规范头往返、变体行
断言）；引擎全套（safety/lua-safety/user-model/context/word-*/
mobile/mapping/snapshot-io）与 `tests.test_mohu_tiger_sentence_native`、
`tests.test_tiger_lexicon_fly` 全绿。

## 部署（本机）

`libtigerengine.dylib` → `~/Library/Rime/mohu/runtime/`，两个 lexicon
（含第 6 列）→ `~/Library/Rime/mohu/data/{zrm,flypy}/`（备份后缀
`.bak-20260920`）。**需完全退出并重启 Squirrel**（dylib 句柄按进程
生命周期复用）。回滚＝恢复三个 `.bak` 文件并重启。

## 部署事故：vgxju 后 Shift+Delete 冻结（与本次引擎改动无关）

首次部署后用户报「打 vgxju 整机卡死」（Squirrel 100% CPU、驻留
736MB→13.4GB/80s）。排查链：引擎直连（含线上 user-ngram 快照、逐键
前缀、早候选、上下文播种）全部毫秒级；无头 librime 会话探针
（research 探针改造，mohu_zrm 全词典栈 + userdb + user.yaml 开关）
逐键喂 vgxju 不复现；**补发 Shift+Delete（XK_Delete=0xffff+Shift，
删词热键）后无头复现**：10 秒内 1.6GB。换回旧 dylib+旧词表**同样冻结**
——与本次部署的引擎/词表完全无关。

根因：WIP 新增的 `is_pin_candidate`（lua/mohu_candidate_override.lua，
Shift+Delete 去 pin 路径）沿真身链走查时用 userdata 身份判重
（`seen[current]` / `genuine == current`），而 librime-lua 每次
`get_genuine()` 都返回**新建的 userdata 包装器**（采样栈 432 帧
`lua_newuserdatauv`）——身份比较恒假，普通候选（真身即自身）无限
循环分配包装器。修复：深度上限 8 + 「同文本同类型」到头判定，
语义不变；`lua tests/mohu_candidate_override_test.lua` 等全绿，
无头 vgxju+Shift+Delete 12ms 完成。诊断资产：watchdog（RSS>1.5GB
自动 sample+kill+回滚）、`sample` 栈、macOS cpu_resource 诊断报告
（`/Library/Logs/DiagnosticReports/Squirrel_*.cpu_resource.diag`）、
glog 日志在 `$DARWIN_USER_TEMP_DIR/rime.squirrel/`。

## 后续

- 车马炮未入 lexicon 词表（base 词典有 `ju;yc ma;nm pc;cn` 但整句
  词表缺行）——本次靠组合路径保留；后续可补词表行使其走整词边。
- 罕见组合仍可能以第 4～5 位出现（整车的 −15.77 仍在前列）——属
  「可选但不首选」的设计预期，不再压分以免伤真组合（权重 2.0 时
  车马炮会被据马炮反超，实测安全上界约 1.5）。

## 追加（同日）：权重 1.3、快照外科清理与删词完整性

用户复测反馈 vgxju 的「整车」仍在第二，且不希望逐词手删。三个来源
依次定位并处理：

1. **学习层残留**：用户当日测试大量误上屏「整车」，user-ngram 快照
   的 trigram 加成约 +4~5 nats，静态罚分（即使 1.5）压不住。用引擎
   forget API 做一次外科清理（导入快照 → `forget_text("整车", 100)`
   → 导出回写，原快照备份 `.bak-20260920-polluted`）。常规上下文下
   整车跌出引擎前六；仅紧跟「整车」上屏后的即时重复仍排第一（合理）。
2. **native 个人词条**：误上屏同时在 mohu_zrm_tiger_prefix2.userdb
   写入整车@vgju/xju 个人词——个人词是整词边、不吃读音先验，是菜单
   第二名的最终真身。补 lua：`finish_permanent_delete` 对 native 类型
   候选在删个人词后**同时写隐藏记录**（此前删词后组合路径仍重新生成
   同一候选，「删了还在」）；用户按一次 Shift+Delete 即完整清除
   （删词+隐藏+反学习）。
3. **默认权重 1.0 → 1.3**：1.5 时 jumapc 的「车马炮」被「局麻炮」
   反超掉出首页（实测），1.3 为保住其首选的上界附近值（车马炮
   −19.74 vs 据马炮 −20.40）。500 词 A/B @1.3 与 @1.0 逐词一致
   （裸双拼/两位末辅零变化、一位末辅 +1 拉脚、零修坏）。

另：`is_soft_deletable` 白名单补入 native 类型（mohu_zrm/mohu_flypy）
——此前 Shift+Delete 对 native 候选静默 kNoop，是「删不掉」的直接
原因；两段式（第一按清权重、armed 第二按永久删）对 native 个人词
照常生效。

## 追加 2（同日傍晚）：文本词典先验同样读音盲——第二名的最后真身

用户删净个人词后「整车」仍居 vgxju 第二。smart 层验证（禁用 native
翻译器）完全不产整车；引擎层（reading 1.0 / composed 1.3）整车已
-16.7 第 4——但**线上 schema 开着 `tiger/text_lexicon_weight: 6.5`**
（2026-09-16 的整候选成词加分），而该加分按文本查词表、与读音无关：
组合路径「整车 jū」文本命中词典词「整车 chē」，白拿 +6.5，
-16.7+6.5=-10.2 恰好压过整局(-14.1)/整剧(-14.7) 排第二。此前所有
引擎探针都用默认 text_lexicon=0，故从未测出。

修复：`State` 增加 `composed_penalty` 累计（路径上单字边付过的组合
读音罚分，负 log 值）；`to_out` 中 **付过组合罚分的路径不再享受文本
词典加成**——「词典里有这个词」的投票属于词的词典读音，不给宣称了
不一致读音的路径加分。数字：vgxju 全权重下 整句 -12.88 / 整局
-14.11 / 整剧 -14.73 / **整车 -16.71（第 4）**；vgie 整车词边 -2.93、
ju 单字车第 2 不变；jumapc 的车马炮降至第 2~3（据马票 -19.61 vs
车马炮 -19.74，0.13 nats 差）。引擎测试全套通过（word-edge 的
「同一个 > 统一个」用例不受影响——两路径均为主读音、不付罚分）。
坑：罚分是负数，门控判断必须是 `composed_penalty < 0`（首版写成
`> 0` 恒假，引擎分数 -10.21 直接暴露）。
