# 二字词三码小鹤派生转换修复（按词音判型）

- 日期：2026-10-12
- 起因：排查 `rwu` 码位时发现——小鹤派生表把 `任务` 转成 `rxu`（正确应为 `rwu`，`wù` 两套双拼同码）。
- 定性：**存量 bug，与三字简词合并无关**（`4ece197` 时错码即存在）。

## 根因

`convert_table_code`（build_flypy_assets）对二字词三码**固定按「前两键=首字双拼」转换**。但主表 116 条二字词三码是两种码式的混合：

| 码式 | 例 | 数量（按主读音可判） |
|---|---|---:|
| 双拼+声母（雾凇遗产） | 哪里 `na`+`l` | 40 |
| 声母+双拼（手工高频简词） | 任务 `r`+`wu` | 34+ |

「声母+双拼」型词的前两键若恰构成有效自然码音节（`rw`=ruá、`bz`=bèi、`hw`=huà），就被盲转换成错误音节码。**码串不该变的也被改坏**（wù/yǔ/xū 等两方案同码）：任务 rwu→rxu、服务 fwu→fxu、基于 jyu→jku……小鹤用户按自然打法打不出来。

## 修复

新增共享判型函数 `convert_two_char_short_code`（`tools/build_mohu_lexicons.py`）：按字音对照判定码式——
- `code[:2]`∈A 的双拼且尾键=B 声母 → 转换 `code[:2]`（双拼+声母型）；
- `code[1:]`∈B 的双拼且首键=A 声母 → 转换 `code[1:]`（声母+双拼型）；
- 两形同真按前者；字音缺失返回 None 走旧兜底。

接入三处（三条派生路径同口径）：

1. `tools/build_flypy_assets.py` `convert_table_code`（主表→小鹤派生表；chars 源缺失时降级，兼容测试夹具）
2. `tools/build_mohu_lexicons.py` `_convert_code`（根词表→引擎词表，短码原为无脑透传：`rwu` 碰巧对、`一些 yxx` 应为 `yxp` 也错）
3. `tools/custom/build_custom_scheme.py` `convert_master_code`（自定义方案构建器，独立实现了一份同 bug）

## 验证

- `make dict` 全链路通过；重跑前后 diff 逐条审查：主表小鹤版 28 对改动、引擎小鹤词表 18 对改动，**双向抽查全部正确**（背景 `bzj→bwj`、化为 `hww→hxw` 等判型成功；引擎侧还纠正了主表之外的 濒临/来了 等词）。
- 可判型二字词三码 116 条错码复测：**0 错**。
- CI 同款 `sync_flykey_quickcodes --check --scheme flypy`：一致（飞键块零变化，符合「三码无完整音节不参与飞键派生」）。
- zrm 侧（主表/根词表/引擎 zrm 词表）：本修复零改动。
- 测试：`tests.test_flypy_assets` 10/10、`tests.test_build_custom_scheme` 全绿（其 `test_master_dict_matches_official_flypy` 正是双路径一致性断言，修复前挂、修复后过）、`tests.test_custom_aux`/`test_sp_layout` 40/40。
- ruff：改动的三个文件无新增问题（custom builder 有 1 个先于本次改动的 I001）。

## 已知边界（未处理，另行决策）

- 6 条三字四码（什么都/这么X）在引擎小鹤词表仍为透传（主表派生路径正确）；量小、属另一码式议题。
- 字音缺失的二字词三码走旧兜底（盲转换或透传），与修复前行为一致。
- 环境中另有并行会话的未提交改动（主表兼容行清理、同码调序、lua/tests/docs 等），本修复未触碰其内容；`tests.test_maintenance_sources` 的 `simp_chars.txt` 失败为该会话 WIP 所致，与本修复无关。
