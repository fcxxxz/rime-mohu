# 词库 base_codes 双份拷贝移除（已实施）

> 2026-09-20 内存排查（用户反馈「打字久了变慢，怀疑引擎泄漏」）的副产物。
> 排查结论：引擎无泄漏、20 万句长跑延迟平稳、RSS 收敛 528.7MiB；但分解
> 驻留构成时发现词库层常驻 ~87MiB，其中约一半是 `base_codes` 整表深拷贝
> ——一份为「删词重建」服务的干净底稿。本文记录它的存在理由、为什么可以
> 不要、移除方案与**实施结果（§7）**。
>
> 行号以 2026-09-20 工作树为准（tigerengine.cc 有并行改动，实施前以函数名定位）。

## 1. 背景：驻留 529MiB 的实测分解

分阶段 RSS 测量（mach_task_basic_info；真实 u8 V5 模型 + zrm 码表 + 用户 60KB 快照）：

| 阶段 | RSS | 增量含义 |
|---|---|---|
| 程序启动 | 9 MiB | 二进制 + 运行库 |
| `tiger_engine_create` 后 | 96 MiB | **+87 MiB ≈ 词库内存结构**（模型 mmap 仅摸头部） |
| 解码摸热后 | 495 MiB | +371.6 MiB 模型全驻留 + 27 MiB 解码期缓存 |
| 20 万句长跑稳态 | 528.7 MiB | +33 MiB 用户 trigram 高水位（10 万条封顶）+ 个人词 |

87MiB 的构成估算：`codes` 与 `base_codes` 各 ~35–40MiB（同内容两份）、
`freq_rank` 与 `base_freq_rank` 共 ~15MiB、`proper_prefixes`（每码全部真
前缀，~30 万条）~15–20MiB。4.2MB 码表文本 → 每份 ~40MiB 的哈希对象，
膨胀来自 `LexEntry`（两个 string + 堆分配的码点 vector + 两个 double）
与 `unordered_map` 节点开销。

20 万句长跑判决（同日实测，41 分钟）供参考：decode p50 全程 9.7–11.1ms
无趋势、探针逐键 p50 ~0.5ms、user_tri 锯齿 25k↔98k 被 10 万条衰减硬封顶、
衰减停顿 max 75ms（提交路径、每 5–7k 句一次）。**本优化是优雅性/删词延迟
改进，不是救火**——529 的大头是 371.6MiB 模型且已 u8 量化到无损极限。

## 2. 现状机制：双份在保护什么

个人词覆盖层是**原地污染**式的：`apply_personal_row`（:1585）直接改写
live `codes`——

- 命中静态同码同词：把静态条目本身改掉（`personal_boost = row.boost;
  personal = true; personal_full_syllables = …`，:1588-1592）。改完后无法
  从该条目区分「静态原状」与「被覆盖过的静态」；
- 未命中：追加纯个人 `LexEntry` 并 `note_personal_code`（:1609）——后者
  会把个人码的长度塞进 `lengths`/`max_code_len`、前缀塞进
  `proper_prefixes`（:1558-1566），**元数据也被污染**。

于是删词（负载键消失）时只能整表重建：`apply_personal_parsed`（:1650）
检测到真删词后走非增量分支 `codes = base_codes; freq_rank =
base_freq_rank;`（:1691-1692）——从底稿**深拷贝 12 万键 map**（几十毫秒
分配 churn + 瞬时内存再 +40MiB），再重放整个负载 + `rebuild_metadata`。

`base_codes` 共两个真实用途：

1. 上述删词重建的干净底稿（:1691）；
2. `personal_has_full_syllables`（:1572）查「静态纯视图」——判断个人词
   每个音节在**静态码表**（不含个人词）中是否有对应单字，防止个人词
   自我佐证 full-syllable 标记。

`base_freq_rank` 则是**死防御**：`freq_rank` 只在装载期写入（:1437），
覆盖层从不改它，:1692 的恢复在保护不可能发生的事。

历史包袱注释（:1531-1538）：曾因不区分「真删词」与「行数上限截断缺席」
导致每轮刷新都整表重建（bench_decode 5000 行个人词实测 P50≈43ms /
max≈74ms 按键线程冻结），后加 `personal_payload_keys` 只把真删词送进
重建分支。即作者已知该路径慢，双份是当时的省事解，非深思熟虑终态。

## 3. 方案：外科手术式撤销（去底稿）

核心观察：**覆盖层登记簿已存在**——`personal_boosts` 以 `code\ttext`
为键记录了每个已应用键（:1529）。删词不需要底稿，按登记簿逐键撤销即可。

### 3.1 删键规则（替代 :1690-1701 非增量分支）

对每个「上次负载有、本轮消失」的键 `k = code\ttext`：

- 在 `codes[code]` 桶内找 `text`：
  - 该条目若同时存在于**装载基线**（如何判基线见 3.3）→ 复位三个被污染
    字段：`personal_boost = 0; personal = false; personal_full_syllables =
    false;`（`rank`/`text`/`chars` 从未被覆盖层改过，不必动）；
  - 否则为纯个人条目 → 从桶内 `erase`。
- `personal_boosts`/`personal_counts` 移除该键。

非增量分支整体删除；`apply_personal_parsed` 只剩增量路径（加/改/外科删）。
`adjust_personal`（:1729）语义不变：注入条目同样进登记簿，下次负载缺席
时不构成删词证据（`personal_payload_keys` 语义原样保留）。

### 3.2 元数据去污染：引用计数旁路

`note_personal_code` 对 `lengths`/`max_code_len`/`proper_prefixes` 的写入
改为走旁路：`personal_code_refs: unordered_map<string_code, size_t>`，
首个个人码引入某长度/前缀时才写入主结构并计数，撤销到 0 时从主结构移除。
旁路大小随个人词（千级）而非码表（12 万键）。

`has_multi_char_entries`：方向安全——纯个人多字词被删后标志可能滞留
true，只会关闭一个复用优化（decode 的 ≤4 键缩写复用路径），不会错；
静态码表本就含多字词，实际恒真。文档化即可，不必精确回收。

### 3.3 静态基线的判定（替代 base_codes 查询）

「该 (code, text) 是否装载基线词条」不再查底稿，改在装载期一次性构建
轻量键集：`static_keys: unordered_set<string>`（`code\ttext` 拼接，
~17 万条 × 平均 ~15B ≈ 6–8MiB，替代 40MiB 的整表拷贝；若嫌大可存
`(code id, text) `对或哈希指纹，进一步压到 ~2MiB——取舍见 §6）。

`personal_has_full_syllables` 的静态纯视图：查 `codes` 的 2 键桶时加
`!entry.personal` 过滤即语义等价（它要的就是「静态表有此单字映射」）。

`base_freq_rank` 直接删除（死防御，见 §2）。

## 4. 不变量清单（实施时逐条守住的验收口径）

1. **真删词 vs 截断缺席**：只有 `personal_payload_keys` 里出现过的键消失
   才触发撤销；`adjust_personal` 即时注入、被 `personal_lexicon_max_rows`
   截断在外的键不构成删词证据（:1531 注释的历史教训）。
2. **静态条目命中只叠加 boost 不复制边**（知识库 09-16「个人词融合」）：
   撤销必须把静态条目完整复位而非误删——`static_keys` 判定错误会把静态
   词条整条 erase，属数据损毁级 bug。
3. **full-syllable 静态纯视图**：`personal_has_full_syllables` 不得看见
   个人词条目（自我佐证会让 `personal_full_syllables` 误真，改变 preedit
   分段行为）。
4. **boost 更新路径**：增量分支对已存在键只改 `personal_boost` 不重建
   条目（:1670-1681）；`adjust_personal` 对已有键只刷 boost（:1741-1750）。
5. **事务路径**：`personal_begin/append/commit` 的 commit 最终走同一
   `apply_personal_parsed`，撤销逻辑自动覆盖；abort 语义不变。
6. **boost 封顶 12 / commits 封顶 1e6**（:1549-1556、:1733）不变。
7. **多音节变体归并**：同 text 多辅码变体在 Lua 侧合并求和（知识库
   09-16 第二处修复），引擎侧每 (code,text) 键独立——撤销按键精确对应，
   不需要额外处理。

## 5. 验收

- 单测：`tests/tigerengine_safety_test.cc`、`tigerengine_user_model_test.cc`、
  `tigerengine_word_edge_test.cc`、`tests/mohu_tiger_two_char_test.lua`
  全绿；新增用例至少覆盖：真删词后静态条目字段复位、纯个人条目擦除、
  截断缺席不触发撤销、full-syllable 静态纯视图、删词后 decode 行为与
  「重建路径」逐字节一致（同负载序列下新旧引擎输出对比）。
- 基准：`bench_decode` 个人词 5000 行 + 真实码表，删词一轮应从
  P50≈43ms 级降到 <1ms（对照 :1538 历史数字；增量路径本就绕开重建，
  本项验收的是撤销路径本身）。
- 内存：分阶段 RSS 复测（方法：程序启动 → `tiger_engine_create` →
  数百句随机解码摸热，mach_task_basic_info 读数）。目标 create 阶段
  96 → ~55MiB（−`base_codes` ~40 −`base_freq_rank` ~8 +`static_keys`
  +6–8），稳态 528.7 → ~490MiB。
- 回归观察项：删词后同码候选顺序、`mohu_reorder_filter_lexicon_test.lua`
  的置顶×personal 双用例。

## 6. 预期收益与取舍

- 常驻 −40~50MiB（进程 529 → ~480），删词路径从「深拷贝 12 万键 +
  重放负载」变 O(被删词数)，瞬时翻倍尖峰一并消失。
- 取舍：`static_keys` 键集占 6–8MiB（比底稿省 ~80%）；如需极限可换
  64 位哈希指纹集（~2MiB，代价是理论碰撞概率——17 万键下 64bit 碰撞
  ~1e-10，可接受，但初版建议保守用明文键）。
- 风险面：引擎核心覆盖层、不变量细（§4 七条）；改动量估 100–150 行，
  集中在 `Lexicon::apply_personal_parsed` / `note_personal_code` /
  `personal_has_full_syllables` / `adjust_personal` 及装载期
  `static_keys` 构建。
- 明确**不做**的事：不动 `proper_prefixes` 的存在价值（incomplete_code_tail
  依赖它）、不动用户 trigram 层（已被衰减封顶且无泄漏）、不动模型 mmap
  （u8 已是量化极限）。

## 7. 实施结果（同日，含三处对 §3 的补丁）

按 §3 方案实施于 tigerengine.cc，另加实施前评审发现的三处补丁：

1. **引用计数按键而非按码**（§3.2 修正）：前缀跨个人码共享（`abcd`/`abef`
   共有 `ab`）且可能静态已有，`personal_length_refs`/`personal_prefix_refs`
   按「长度/前缀本身」计数，且只对静态基线中不存在、由个人码首次引入的项
   建计数（计数项存在即「个人引入」标记）；静态所有的只借用、永不移除。
   `max_code_len` 在长度归零移除时按剩余 `lengths` 回算。
2. **空桶与死代码**：纯个人条目擦除后桶空即 `codes.erase(bucket)`；
   非增量分支删除后 `rebuild_metadata` 无调用者，连代码一并删除。
3. **`personal_has_full_syllables` 的过滤恒等价**：个人码 ≥4 键而该查询
   只查 2 键桶，live `codes` 的 2 键桶永不被覆盖层触碰，`!entry.personal`
   过滤是等价变换兼双保险。

**语义改进（有意偏离旧引擎）**：`adjust_personal` 即时注入、从未进入负载
的词边，在其他键真删词的刷新后现在**保留**——旧重建分支会顺手抹掉它们，
那只是重建手法的副作用（:1531 注释的意图从来是保留）。新增测试
`expect_personal_undo_keeps_adjust_injected_edges` 固化此语义。

新增测试（tests/tigerengine_safety_test.cc）：
`expect_personal_surgical_undo_matches_fresh_rebuild`（删词撤销后与
「从未见过被删词」的新引擎逐字节一致，含 6 键新码长/前缀归还与续写尾
探测）、`expect_personal_static_hit_undo_resets_fields`（静态命中三字段
复位 + 静态条目不误删）、上述注入词存活测试。

### 实测数字（同机同模型同码表，HEAD 旧引擎 vs 新引擎）

| 指标 | 旧（HEAD） | 新 | 说明 |
|---|---|---|---|
| `set_personal shrink` 删词一轮 | P50 43.30ms | **P50 2.06ms** | 历史数字 43ms 复现；剩余 ~1.6ms 为 5000 行负载解析本身，撤销 ~0.4ms/200 键；分片事务收缩段 commit 0.87ms |
| create 阶段 RSS 总量 | 90.5MiB | **66.3MiB** | −24MiB；暖机后（300 句）256.0 → 233.0MiB，恒定节省带入稳态 |
| 全量应用/no-op/增长 | 7.4 / 0.017 / 2.33ms | 8.2 / 0.011 / 2.28ms | 应用路径无回归（首次全量差 0.8ms 为 static_keys 构建，一次性） |

实测节省（−24MiB）小于 §6 预估（−40~50）：底稿实际 ~32MiB 而非 ~48MiB
（§1 的构成估算偏高），`static_keys`（174,502 键）约 8MiB 符合预估。推算
稳态 528.7 → ~505MiB（模型 mmap 371.6MiB 仍是大头）。

验收：tigerengine 全部 native 测试（safety/lua-safety/snapshot-io/
user-model/reading-prior/word-edge/word-gate/context/mobile/mapping/
word-score）与 tiger 相关 Lua 测试（含 §5 点名的
`mohu_tiger_two_char_test.lua`、`mohu_reorder_filter_lexicon_test.lua`）
全绿；dylib 已重编。测试序列未含「中途重排负载行序」，§5 逐字节口径按
稳定负载序列达成（§3.1 顺序保留 vs 重放重排的理论分叉未触发）。
