# 个人词库同步路径导致「越用越卡」根因报告

日期：2026-09-11
范围：`mohu_zrm` 方案（线上 `schema_list` 仅启用该方案）
症状：输入用一段时间后开始卡顿；重新部署后明显缓解，之后再次劣化。

## 1. 结论

根因不是内存泄漏，而是**个人词库刷新路径被配置强制退化为「整体扫描 + 整体重建」，
并且每次刷新都会触发 native 词表的全量深拷贝与元数据重建**。

触发链条（全部已核对线上实际文件）：

1. 线上 `mohu_zrm.schema.yaml:280`（仓库源码 `mohu_zrm.schema.yaml:280` 同）设置：

   ```yaml
   personal_lexicon_max_rows: 4096
   ```

2. `tiger_sentence_native/mohu_tiger_sentence.lua:831-836` 把它读入 `env._mohu_personal_max_rows`；
   `personal_scan_options()`（同文件 `:388-395`）只要该值是非负数字，就返回
   `{limit=N}, true` —— 第二个返回值 `true` 即 **monolithic（整体路径）**。

3. 于是 `personal_scan_tick()` 的 `:504-510` 分支永远走整体路径：

   ```lua
   local _, monolithic = personal_scan_options(env)
   if monolithic then
     local _, payload = personal_lexicon.snapshot(memory, options)  -- 全量扫描 + 全量排序
     personal_apply_monolithic(env, payload)
     return
   end
   ```

   `mohu_personal_lexicon.lua:66-94` 的 `M.collect()` 会遍历**整个用户词库**
   并对其做一次 `table.sort`，然后才裁到 4096 行。代码注释本身也写明了这个代价：
   `:383`「设置 tiger/personal_lexicon_max_rows 时回退整体扫描路径（需全局排序）」。
   **≤5ms 分片预算的避让设计（`:373-386`）被这一行配置完全旁路。**

4. 同一时刻，每次上屏都会调用 `adjust_personal`（`mohu_tiger_sentence.lua:679-681`），
   native 侧把它写进 `personal_boosts` / `personal_counts`，并 `personal_payload.clear()`
   （`tigerengine.cc:1373-1401`）。

5. 下一次刷新时，负载是**按提交次数降序取头部 4096 行**；刚上屏的新词计数为 1，
   排不进头部（并列时按 code 字典序裁决，与新旧无关），因此它**不在负载索引里**。

6. `Lexicon::apply_personal_parsed()`（`tigerengine.cc:1304-1353`）开头就做全量一致性检查：

   ```cpp
   for (const auto& applied : personal_boosts) {
     if (index.find(applied.first) == index.end()) { incremental = false; break; }
   }
   ```

   只要 `personal_boosts` 里有一个键不在本次负载中，就判定为「键集收缩」，
   走 `:1340-1352` 的整表重建：

   ```cpp
   codes = base_codes;        // 整本静态码表的深拷贝
   freq_rank = base_freq_rank;
   personal_boosts.clear();
   ...
   rebuild_metadata();        // 遍历全部 code，重建 proper_prefixes 等
   ```

   `base_codes` 来自 `mohu_zrm.lexicon.txt`（线上 4.1MB），`rebuild_metadata()`
   要为每个 code 的每个前缀插入 `unordered_set<std::string>`（`tigerengine.cc:1171-1188`）。
   这是一次**同步的整表重建，发生在按键事件线程上**。实测（5000 行个人词 +
   线上真实码表，见 §4.4）P50≈43ms、max≈74ms，**且每轮刷新都发生**。

7. 刷新有 30 秒防抖（`personal_refresh_interval` 默认 30，线上 schema 未覆盖），
   所以表现为：**打字过程中大约每 30 秒卡一下**；用户词库越大，第 3 步的全量扫描/排序越贵。
   叠加 Lua 侧整体快照的 20.7ms（§4.4），单次刷新总代价约 **60–90ms**，
   在内存吃紧的机器上分配开销还会进一步放大。

## 2. 为什么「重新部署一下就会好一点」

方案装载期会执行一次带 `force` 的全量同步（`mohu_tiger_sentence.lua:842`
`refresh_personal_lexicon(env, true)`）。这一次同步把 `personal_boosts` 精确对齐到
快照的 4096 个键，于是第 6 步的一致性检查成立、后续刷新走**增量**分支（便宜）。

之后每上屏一个新词，`personal_boosts` 就多一个不在头部 4096 里的键，不变式再次被打破，
整表重建恢复。同时重新部署还会重置 Lua 堆与引擎缓存。
这正好解释了「重新部署后好转一段时间、用一会又卡」的现象。

## 3. 修复建议

### 3.1 立即验证（改一行，不动代码）

把 `mohu_zrm.schema.yaml` / `mohu_flypy.schema.yaml` 中的

```yaml
personal_lexicon_max_rows: 4096
```

删掉（或设成远大于实际词库行数的值），回到分片路径，然后重新部署。
预期：每 30 秒的冻结消失，按键延迟不再随使用时长劣化。
代价：native 侧个人词条不再截断，常驻内存略增（这正是当初设 4096 的原因）。

### 3.2 代码层修复（推荐，保留 4096 的内存上限）

问题本质是**把「负载因行数上限被截断」误判成「用户删词」**。可在
`apply_personal_parsed()` 里区分二者：只有当某个键在**上一次负载**中存在、
而这次不存在时才算收缩；对「从未出现在负载里的键」，直接把它从
`personal_boosts` / `personal_counts` 中丢弃（或视为不参与本轮），
而不是触发全局重建。这样 4096 的截断不再导致每轮整表重建。

另外建议给整体路径也加耗时日志，便于回归监控。

### 3.3 次要项（非根因，但会放大卡顿）

- `tiger/user_model: true` 使每次上屏都调用 `update_user_model` →
  `invalidate_overlay_cache()`（`tigerengine.cc:2507-2515`）会清空 `logp_cache`
  与束搜索池。配合 573MB 的 mmap 模型，在内存紧张时会放大冷页缺页开销。
- 当前机器内存偏紧：`vm_stat` 显示 free 仅约 166MB、压缩器占用约 10.5GB
  （存了约 18.6GB 数据）、swap 为 0。这会让上述冷页代价更明显。
- 线上 `~/Library/Rime/lua/` 是混合版本：大部分文件是 09-05 的一批，
  少量是 09-10 的补丁（`mohu_tiger_sentence.lua` 等），与工作区源码并不一一对应。
  建议用 `make dist-zrm` 重新产出一致的一套再覆盖。

## 4. 修复实施与验证（2026-09-11 已完成）

### 4.1 已落地的改动

| 文件 | 改动 |
|---|---|
| `mohu_zrm.schema.yaml` / `mohu_flypy.schema.yaml` | 删除 `personal_lexicon_max_rows: 4096`，替换为"刻意不设"的说明注释 → 恢复分片路径（3.1） |
| `tiger_sentence_native/tigerengine.cc` | `Lexicon` 新增 `personal_payload_keys`（上一轮负载实际出现过的键）。一致性检查改为：键不在本轮负载中时，**仅当它曾出现在上一轮负载里**才判为真实删词并回退整表重建；从未进入负载的键保留词边与计数（3.2） |
| `tiger_sentence_native/mohu_tiger_sentence.lua` | 新增 `log_warning`；读到 `personal_lexicon_max_rows` 时打警告，提示已回退整体路径（3.3 监控） |
| `tests/tigerengine_safety_test.cc` | 新增 `expect_personal_truncation_does_not_force_rebuild()`：截断不重建 + 真删词仍重建 |

**语义边界（已知残留）**：若有人重新设上行数上限，某个**曾进入过负载**的词因边界
竞争掉出头部时，仍会被判为"删词"而触发一次重建。彻底消除需要把"负载是否被截断"
作为显式信号传给引擎（新增 ABI），当前选择不做——因为默认路径已无上限，
`payload_keys` 已覆盖"新上屏词被截断"这一主导触发模式。

### 4.2 3.3 各项的处理结论

- **`user_model` 的 `invalidate_overlay_cache()`**：**不改**。这是正确性取舍——
  用户模型计数变了，解码结果必须重算；`logp_cache` 在上一次上屏时刚被清空，
  两次上屏之间只累积数百到数千条，`clear()` 实际只有亚毫秒级，并非冻结来源。
  若想换取更低的每次上屏开销，可把 `tiger/user_model` 设为 `false`（会失去调频自适应性）。
- **内存偏紧**：属机器状态，非代码问题。
- **线上文件版本**：已核对——四个待改文件在覆盖前与"仓库修复前构建"**逐字节一致**
  （`shasum -a 256` 相同），说明线上就是从工作区复制的，因此采用定点覆盖而非整目录重发。

### 4.3 部署与验证结果

部署（备份在 `~/Library/Rime/backups/2026-09-11-personal-lexicon-fix/`）：

- `~/Library/Rime/mohu_zrm.schema.yaml`、`mohu_flypy.schema.yaml`
- `~/Library/Rime/lua/mohu_tiger_sentence.lua`
- `~/Library/Rime/mohu/runtime/libtigerengine.dylib`（重建，sha256 已核对）

通过：

- `make tigerengine-safety`（含新回归用例）、`tigerengine-lua-safety`
- `tigerengine-user-model`（真实 573MB 模型，输出真实翻转断言 `申请很迷茫 <- 神情很迷茫`）
- `tigerengine-context`、`tigerengine-reading-prior`、`tigerengine-word-score`
- `tests/mohu_tiger_sentence_native_test.lua`、`mohu_personal_lexicon_test.lua`、
  `mohu_tiger_user_model_test.lua`、`mohu_tiger_two_char_test.lua`
- `tests.test_neural_toggle_schema`、`test_flat_distribution`、`test_mohu_migration`、
  `test_split_release_workflow`

### 4.4 schema 级测试（mira）：确认本次改动零新增失败

`tests/mohu_zrm.test.yaml` 的 `source_dir` 是 **`../dist-zrm`**（生成物），
所以 mira 测的是发行包而不是仓库根目录。因此做了 A/B：

| 被测的 `dist-zrm` | 结果 |
|---|---|
| 未改动（仍含 `personal_lexicon_max_rows: 4096` + 旧 dylib） | **4/110 失败** |
| 同步本次修复（schema + lua + dylib）后 | **4/110 失败，名单完全相同** |

失败名单（**均为既有问题，与本次修复无关**）：

- `mohu_zrm::curated_short_codes_multi::mulo`
- `mohu_zrm::cross_candidate_order::mo{Down}{space}rj{space}yrug`
- `mohu_zrm::default::yuviyy`
- `mohu_zrm::automatic_word_learning::vsmc`

两次结果完全一致 ⇒ 本次改动不引入任何新的 schema 级回归。

> **更正（同日复查）**：本节原先写的「只有 4/110 失败也反证 native 引擎确实被
> 加载了」是错的，方向恰好相反。mira 根本没能加载 native——Lua 5.5 / 5.4 ABI
> 不匹配，`luaopen` 直接失败，引擎 fail-open 回退到 smart 词典序；正因为回退序
> 与绝大多数期望一致，才只暴露出 4 条。native 相关用例在 mira 里既会**假失败**
> （`automatic_word_learning::vsmc`，补全 native 后通过），也会**假通过**
> （`default::jiivo`，补全 native 后变成 `既拙`）。完整证据与复现方法见
> [mira 跑不到 native](2026-09-11-mira-native-blind-spot.md)。
>
> 就本报告的核心结论而言不受影响：A/B 两次用的是同一套「native 不可用」宿主，
> 个人词库刷新的修复另有 Lua 侧与原生单测的直接覆盖（见 §4.3、§4.5）。

**遗留**：`dist-zrm/` 目前是"半同步"状态（我只覆盖了本次改动的 3 个文件）。
完整重发需 `make dist-zrm`，它会整体重建该目录（119 个文件），
受本机批量删除保护限制需人工确认。

上述 4 个既有失败已另行排查完毕，结论见
[mira 跑不到 native](2026-09-11-mira-native-blind-spot.md)：其中 1 条是 mira 的
假失败、1 条是被 mira 掩盖的真回归（`default::jiivo`），另 2 条与 native 无关。

**尚未验证**：线上实际手感需在 Squirrel「重新部署」后由使用者确认；
日志中不应再出现 `personal_lexicon_max_rows ... is set` 警告。
（部署是否已发生已另行复核，见 §6 —— 已发生；剩余待确认项只有主观手感。）

### 4.5 实测数据（bench_decode，5000 行个人词 + 线上真实码表）

为了把"冻结"量化，给 `bench_decode.cc` 加了一个用例
`set_personal after off-payload commit`：先应用负载，再 `adjust_personal` 注入一个
**不在负载里**的键（模拟上屏新词计数为 1、排不进头部），然后重复应用同一负载。
这正是线上每 30 秒发生的动作。用同一份 `bench_decode.cc` 分别链接修复前/修复后的
`tigerengine.cc`（修复前源码由当前源码精确反推三处改动得到，`diff` 已验证）实测：

| 用例 | 修复前 P50 | 修复前 max | 修复后 P50 | 修复后 max |
|---|---|---|---|---|
| `set_personal after off-payload commit` | **42.8 ms** | **74.0 ms** | **1.7 ms** | **1.8 ms** |
| `set_personal shrink rebuild`（真删词） | 45.8 ms | 45.8 ms | 44.1 ms | 44.1 ms |
| `txn commit shrink rebuild`（真删词） | 40.1 ms | — | 41.6 ms | — |
| `set_personal no-op` | 0.011 ms | 0.012 ms | 0.014 ms | 0.018 ms |
| `set_personal growth +200` | 1.7 ms | 1.7 ms | 2.1 ms | 2.1 ms |

读法：

- 截断触发路径 **P50 42.8ms → 1.7ms（约 25×）**，max **74.0ms → 1.8ms（约 41×）**。
- "真删词"路径仍保持 ~44ms 重建 —— 语义没有被削弱，只是不再被截断误触发。
- 修复后的 1.7ms 是"增量更新 5000 行 boost"的固有成本，且不再每轮都付。

Lua 侧（`/tmp` 一次性脚本，4150 行，`os.clock()` 计 CPU 时间）：

| 路径 | 耗时 |
|---|---|
| 整体 `M.collect`（全量遍历 + 全局 `table.sort`） | 14.5 ms |
| 整体 `M.serialize` | 6.2 ms |
| 整体 `snapshot` 合计 | **20.7 ms** |
| 分片路径最坏单片（512 条硬上限） | **0.61 ms** |

即分片路径把最坏单次等待从 20.7ms 压到 0.61ms（约 34×）。注意此脚本用 Lua 表迭代器
代替真实 LevelDB `iter_user`，**整体路径的真实遍历成本比 20.7ms 更高**，结论只会更强。

### 4.6 内存代价（撤销 4096 上限的代价）

用 `/usr/bin/time -l` 测 max RSS（同一 bench，只改 `personal_rows`）：

| personal_rows | max RSS | 相对 0 行 |
|---|---|---|
| 0 | 654.4 MiB | — |
| 1000 | 669.3 MiB | +14.8 MiB |
| 2500 | 671.3 MiB | +16.8 MiB |
| 5000 | 672.4 MiB | +17.9 MiB |

注意增量**不是线性**的：1000→5000 行只多 3.1 MiB，即**边际成本约 0.8 KiB/行**；
那 ~14.8 MiB 的"固定项"主要是 bench 自身的临时对象高水位，不是叠加层。
按线上约 4150 条计，撤销上限的代价约 **3 MiB** —— 相对 573MB 的模型可忽略。
换句话说，当初用 4096 上限"省内存"省下的是个位数 MiB，
换来的是每 30 秒一次 43–74ms 的按键线程冻结，这笔交易是亏的。

### 4.7 结论

单次刷新总代价由「Lua 整体快照 ~21ms+（被低估） + native 整表重建 ~43ms」
降到「分片最坏单片 0.61ms + 增量 1.7ms」，且后者分摊在多个空闲 tick 上。
每 30 秒一次的阻塞式冻结被消除，内存代价约 3 MiB。

## 5. 关键证据索引

| 位置 | 作用 |
|---|---|
| `mohu_zrm.schema.yaml:280` | `personal_lexicon_max_rows: 4096`（强制整体路径） |
| `mohu_tiger_sentence.lua:388-395` | `personal_scan_options` 返回 monolithic |
| `mohu_tiger_sentence.lua:504-510` | 整体路径分支 |
| `mohu_tiger_sentence.lua:679-681` | 每次上屏调用 `adjust_personal` |
| `mohu_tiger_sentence.lua:842` | 装载期强制全量同步（重部署缓解的原因） |
| `mohu_personal_lexicon.lua:66-94` | 全量遍历 userdb + `table.sort` |
| `tigerengine.cc:1304-1353` | 一致性检查 + `codes = base_codes` 整表重建 |
| `tigerengine.cc:1373-1401` | `adjust_personal` 写入头部之外的键 |
| `tigerengine.cc:1171-1188` | `rebuild_metadata()` 重建全部前缀 |

## 6. 线上落地状态复核（同日 18:0x）

§4.4 末尾曾写「尚未验证：需在重新部署后由使用者确认」。复核结论：**修复已全部上线，
且重新部署已经发生过。**

逐文件核对（`shasum -a 256`，仓库 vs `~/Library/Rime/`）：

| 线上文件 | 状态 |
|---|---|
| `mohu_zrm.schema.yaml` | 只剩说明注释，无 `personal_lexicon_max_rows` 赋值 ✓ |
| `mohu_flypy.schema.yaml` | 同上 ✓ |
| `lua/mohu_tiger_sentence.lua` | `f0d9744278e0…`，与仓库逐字节一致 ✓ |
| `mohu/runtime/libtigerengine.dylib` | `0d318cf4cb58…`，与仓库一致 ✓ |

**「是否重新部署过」的判定方法**（此前没有可查证据，这里补一个）：看 Rime 的编译产物目录
`~/Library/Rime/build/`。本次 `build/mohu_zrm.schema.yaml` 的 mtime 为 **15:53:25**，
**晚于**全部覆盖动作（schema 15:33:53、lua 15:34:26、dylib 15:43:18），
且该产物内已不含 `personal_lexicon_max_rows` ⇒ 覆盖完成后确实触发过一次部署，
新的 schema 与 Lua 已被加载。

> 注：`~/Library/Rime/` 下没有 Squirrel 运行日志可查（`mohu_vis_diag.log` 停在 09-09），
> 所以「是否仍偶发卡顿」无法从日志侧取证，只能靠体感确认。
> 若 dylib 是在 Squirrel 运行期间被替换的，保险做法是**完全退出并重开 Squirrel**，
> 以确保新动态库被进程重新加载。
