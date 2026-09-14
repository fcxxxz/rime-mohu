# 为什么打 `bjn` 不出「班」

日期：2026-09-11 ｜ 方案：`mohu_zrm`（线上 `~/Library/Rime`）

## 结论（一句话）

`bjn` 是**斑**的码，不是班的码。班在码表里的可输入码形只有 `bjp`（简快码）与
`bj;pn` / `bjpno` / `bjpn/` 系（全码），全部以 `p` 结尾——因为班的**辅码首字母是 p**。

## 一、现场复现（mira，镜像线上目录）

把线上 `~/Library/Rime`（排除 `model/`、`runtime/`、`build/`、`sync/`、`backups/`）
镜像到 `/tmp/live-mirror`，用 mira 直接输入：

| 输入 | 候选 |
|---|---|
| `bj` | 半 比较 毕竟 背景 **班**(5) 版 般 斑 办 板 … |
| `bjn` | **斑** 比基尼 瓣（n=3，**无班**） |
| `bjp` | **班** 保健品 粄 |
| `bjnv` | 半女 班女 **班** 半 版 … |

线上 `~/Library/Rime/mohu_zrm.chars.dict.yaml:58923-58924`：

```
班	bj;pn	459772
班	bj;pw	0
```

线上 `mohu_zrm_fixed.dict.yaml:1238` / `mohu_zrm_fixed_legacy.dict.yaml:658`：`班	bjp`。

## 二、码是怎么生成的

1. **单字三码 = 双拼 + 辅码首字母**。
   `tools/data/tiger_aux.txt:16212` → `班  pn  pw`；`:12536` → `斑  nv  nw  nn`。
   于是班 → `bj`+`p` = `bjp`，斑 → `bj`+`n` = `bjn`。
2. **`mohu_zrm.chars` 的 `bj;pn` 经 `mohu.yaml:/algebra/generate_code` 派生**为
   `bjpno` / `bjpn/` / `bjp/` / `bjpn` / `bjp` / `bj` ——**不会派生出 `bjn`**。
   反过来斑的 `bj;nv` 会派生出 `bjn`。所以 3 键输入下，班在码表侧根本不存在。
3. **`tools/data/tiger_aux.txt` 由 `tools/gen_tiger_aux.py` +
   `tools/tiger_aux.py:select_primary_code()` 生成**，该函数取
   **`tiger.dict.yaml` 里第一条四字母码**：

   ```
   1070:  班  np    43869
   8508:  班  pnnw  397     ← 被选中
   14823: 班  npnw  256
   ```

   选中镜像码 `pnnw`（刂王王）→ 辅码 `pn`（首字母 p）。

## 三、真正的数据不一致

`tools/data/tiger_chaifen.txt:954`（来源 `rime-tiger/opencc/hu_cf.txt`，`2c9f9df`）：

```
班	〔王王 · npnw pnnw〕
```

拆解表把 `npnw`（王刂王）列为**首选**，`pnnw` 只是次选；而班权重最高的虎码是 `np`
（n 开头，43869）。**三处信号（拆解首选、最高权重码、lexicon）都指向 n 开头，
只有 `tiger.dict.yaml` 的行序指向 p**，而生成器只认行序。

全量审计（`tools/data/tiger_chaifen.txt` × `tiger.dict.yaml`，99142 个可对齐单字）：

- 生成器选中码 == 拆解表首选码：98919
- 仅长度不同（辅码不变）：221
- **辅码前两位真的不同：2**（班、𩢆）

即这是**小面积数据不一致**，班恰好是其中之一。

## 四、为什么 native 引擎救不了

`tiger_sentence_native/mohu_tiger.lexicon.txt:5179` 里确实有

```
bjn	班	1	720
```

（lexicon 的加辅位会把该字**所有**虎码的首字母都收进去：班 `np`→n、`pnnw`→p，
故同时有 `bjn` 和 `bjp`；斑 `nvn`/`vnnw`→{n,v}，故有 `bjn`、`bjv`。）

但 `mohu_tiger_sentence.lua:1464`（线上与仓库 sha256 一致）：

```lua
if #context_input <= 4 and not context_input:find("'") and
    not (has_history_context and decode_context_takeover(env)) then
  return
end
```

**≤4 键直接 return**，3 键的简快码永远走不到 native 引擎；即便走到，1491 行也只
输出 `#utf_chars(item.text) > 1` 的多字候选。所以 `bjn 班` 这条数据对简快码是死数据。

## 五、历史

`be240da`（2026-09-04「做了一些优化」）同时做了两件事：

```
 班	bjp		459772
-班	bjn		459772      ← 班失去 bjn
-斑	bjv		53974
+斑	bjn		53974      ← bjn 转给斑
```

## 六、修复尝试与结论（已实施 → 被测试否决 → 完整回滚）

### 6.1 方案 A 实测：技术上可行，但被仓库测试明文否决

改 `tools/tiger_aux.py:select_primary_code()`，让它优先选与最高权重码同首字母的四字母码
（班 → `npnw` 而非镜像码 `pnnw`），随后重跑 `gen_tiger_aux.py` / `gen_chars.py` /
`rebuild_fixed_tiger.py` / `build_mohu_lexicons.py`。

镜像验证（`bjn` 首选确实变成班）：

| 输入 | 改动前 | 改动后 |
|---|---|---|
| `bjn` | 斑 比基尼 瓣 | **班** 比基尼 斑 瓣 |
| `bjp` | 班 保健品 粄 | 保健品 **班** 粄（班被降级） |

**但 `tests/test_tiger_aux.py` 已明文固化旧行为**：

```python
# :899-904  多码字只保留第一个四码的正常简快码；镜像码不再生成第二条简快码
self.assertIn(("班", "bjp"), legacy_pairs)
self.assertNotIn(("班", "bjnp"), legacy_pairs)
```

外加 `DictionaryAuxiliaryInvariantTest.test_every_explicit_auxiliary_segment_uses_tiger_prefix2`
（词库中每个显式辅码段必须 ∈ `tiger_aux.txt` 的 `codes()`），以及 **2897 条**词库条目带
显式 `bj;pn` 段（如 `一班  yi;fi bj;pn`）。跑测试得 **8 failures / 1 error**。

**结论：`班 = bjp` 是设计如此（by design），不是 bug。** 改辅码等于一次大范围数据迁移：
`chars` + 2897 条词库 + 8 个 fixed 码表 + 测试断言，且班会被降级（词频 459772 的班要让位）。

### 6.2 方案 B（次级简码）同样无法"只加不减"

`tools/data/mohu_fixed_secondary_codes.tsv` 的语义是「视为已覆盖，**抑制**该字被前缀
匹配到的其他自动分配简码」（`rebuild_fixed_tiger.py:585-587`）。直接加 `班 bjn` 有把
`bjp` 挤掉的风险，需跑 rebuild 确认。

### 6.3 当前状态：全部回滚，仓库已回到介入前

已还原并逐一校验（`diff -q` 对 `/tmp` 副本）：`tools/tiger_aux.py`、
`tools/data/tiger_aux.txt`、`mohu_zrm.chars.dict.yaml`、`mohu_flypy.chars.dict.yaml`、
8 个 fixed 码表、2 个 lexicon，并把 `tools/data/tiger_aux.txt` 的 mtime 复位。

回滚后测试：**2 failures / 1 error / 3 skipped**，三项均与本次改动无关：

- `test_split_distributions_build_with_isolated_schemas`（ERROR）与
  `test_model_asset_target_stages_versioned_file_under_model`（FAIL）：沙箱批量删除保护
  （`rm -rf` 目标 >50 文件被拦）导致，纯环境限制。
- `test_opencc_install_uses_bounded_https_archive_source`（FAIL）：工作区
  `.github/workflows/build.yml` 的既有改动，与本次无关。

### 6.4 回滚时踩到的两个坑（下次务必注意）

1. **派生文件容易漏**：`mohu_flypy.chars.dict.yaml` 由 `build_flypy_assets.py` 从
   `mohu_zrm.chars.dict.yaml` 转换而来，改了 zrm 侧必须同步检查 flypy 侧。漏掉它会留下
   「flypy 班 = `bj;np` 而 fixed 表班 = `bjp`」的前缀断链，触发
   `test_generated_short_codes_prefix_current_full_codes`。
   （判别技巧：两个 chars 表同批生成，行数应相同；当时 flypy 比 zrm 多 1 行，正是班
   3 行 vs 2 行造成的。）
2. **`cp` 会刷新 mtime，而 version 由 mtime 决定**：`utils.get_chars_version()` 取
   `max(mtime of tiger_aux.txt, chars.txt, tiger_compatibility_chars.txt)` 的日期，
   `test_character_dictionary_version_includes_compatibility_targets` 用同一算法算期望值。
   用 `cp` 还原源文件会把 mtime 刷成当天，期望值随之漂移到今天，与 chars 里固化的
   `version` 对不上。**回滚数据文件后必须 `touch -t` 复位 mtime**（macOS 的格式是
   `[[CC]YY]MMDDhhmm[.SS]`）。

## 七、最终方案：次级简码（已实施并验证）

方案 A 被否决后，改用仓库自带的**次级简码**机制
（`tools/data/mohu_fixed_secondary_codes.tsv`）——它正是为「同码追加低优先级简快码」设计的。

### 7.1 为什么可行

- 该机制的抑制条件是「**更短**的已分配码挡住前缀」
  （`rebuild_fixed_tiger.py:753-757`：`len(shorter) < length and prefix.startswith(shorter)`）。
  `bjn` 与班的 `bjp` **同为 3 键**，不构成前缀压制 → **`bjp` 完整保留**。
- 班不在 `mohu_fixed_char_code_overrides.tsv`（只有 6 个字：𤭢/欻/挼/扽/𰻝/咱）里，
  所以 `text in fixed_codes` 的报错检查不会触发。
- 2897 条带 `bj;pn` 的词库**零影响**（辅码没动）。

### 7.2 改动（共 6 个文件，各 1 行）

| 文件 | 改动 |
|---|---|
| `tools/data/mohu_fixed_secondary_codes.tsv` | +`班	bjn` |
| `mohu_zrm_fixed_legacy.dict.yaml` | +`班	bjn		0` |
| `mohu_zrm_tiger_fixed_legacy.dict.yaml` | +`班	bjn	0` |
| `mohu_flypy_fixed_legacy.dict.yaml` | +`班	bjn		0` |
| `mohu_flypy_tiger_fixed_legacy.dict.yaml` | +`班	bjn	0` |
| `tests/test_tiger_aux.py` | 2 处断言（见 7.4） |

4 个主表（`*_fixed.dict.yaml`、`*_tiger_fixed.dict.yaml`）以及 `lua/tiger_rank.txt`、
`tools/data/tiger_compatibility_chars.txt` **完全未变**（`shasum` 校验）。

### 7.3 端到端验证（干净线上镜像 A/B）

从线上 `~/Library/Rime` 重建镜像到 `/tmp/mirror-b`，只往 legacy 表加 1 行：

| `bjn` 候选 | 结果 |
|---|---|
| 基线 | `n=3  1:斑 2:比基尼 3:瓣`（无班） |
| 加 `班 bjn` 后 | `n=4  1:斑 2:班 3:比基尼 4:瓣` |

### 7.4 测试

`uv run python -m unittest discover -s tests` → **2F/1E/3skip**，与改动前基线**完全一致**
（3 项均为沙箱批量删除保护 / 工作区既有问题）。更新的两处断言：

1. `test_legacy_fixed_tables_preserve_moran_multi_short_code`：3 键计数 `4567 -> 4568`。
2. `test_generated_short_codes_prefix_current_full_codes`：豁免手工登记的次级简码
   （读 tsv，含 flypy 侧转换）。该测试本意是校验**自动生成**的短码，而次级简码是
   「借同码字码位」的手工例外——`加/梨/黎` 恰好满足全码前缀，所以这个遗漏此前未暴露。

### 7.5 与方案 A 的对比

| | 方案 A（改辅码） | 方案 B（次级简码） |
|---|---|---|
| `bjn` 候选 | **班** 比基尼 斑 瓣（班在首位） | 斑 **班** 比基尼 瓣（班在次位） |
| 班的 `bjp` | 被降级 | **完整保留** |
| 2897 条词库 | 断链 | **零影响** |
| 测试 | 6 个失败 | 2 处断言更新 |
| 语义 | 与拆解表对齐（更"正"） | 借斑的码位（是有意的例外） |

方案 B 是最小代价路径。若要让班成为 `bjn` 的**首选**，仍需方案 A 那类系统性改动。

## 附：复现手法

```bash
RIME="$HOME/Library/Rime"; M=/tmp/live-mirror
rsync -a --exclude 'model/' --exclude 'runtime/' --exclude 'backups/' \
  --exclude 'build/' --exclude 'sync/' --exclude '*.bak-*' "$RIME/" "$M/"
mira -C /tmp/mira-cache /tmp/live-bjn.test.yaml   # source_dir: /tmp/live-mirror
```

`mira` 里 native 加载不了（Lua ABI + 缺 model），但本问题是码表侧行为，
回退序与生产一致；`send` 的值里若含 `{BackSpace}` 这类花括号要加引号，否则
yaml-cpp 会 `TypedBadConversion`。
