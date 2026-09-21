# 简码换位

把某个字调到指定简码（一简 / 二简 / 三码固顶 / 同码次选），**改仓库源数据再生成**，不要手改 `mohu_*_fixed*.dict.yaml`。生成表页头写着 `DO NOT EDIT`。

编码一律写**自然码**。小鹤由生成器换算，飞键块（`wz→wk` 等）由 `sync_flykey` 补，不要在源文件里写飞键码或小鹤码。

## 选哪个文件

| 你想要的结果 | 改这个 | 例子 |
|---|---|---|
| 某字独占某个**三码**，两张简字表都固顶；被挤走的字去它自己的四码 | `tools/data/mohu_fixed_code_claims.tsv` | `喂 wzd`、`右 ybn`、`己 jiv`、`政 vgh` |
| 某字出现在**别人已经占用的码**上，而且是次选（问仍第一、文第二） | `tools/data/mohu_fixed_secondary_codes.tsv` | `文 wf`、`班 bjn` |
| 某字拿到 / 让出 **一码或二码** | `tools/data/mohu_fixed_simp_legacy_chars.txt` 对应存档行 | `方 fh`、`象 x` |

三条可以叠：像/象是存档改一简，像的三码 `xdj` 是级联自动落到完整码前缀，不必再写 claims。

**不要用** `tools/data/mohu_fixed_char_code_overrides.tsv` 做换位。它会把该三码的**二码前缀整段封死**（`abc` 会禁掉 `ab`）。音节上已有一/二简时（`wz`、`yb`、`wf`）会把整片简码拆掉。那份表只给生僻字的三码独占。

## 改完怎么生成

```bash
make dict
```

这是固定点：`rebuild_fixed_tiger` → `sync_flykey` → `build_flypy_assets` → lexicon。只跑 `make fixed_tiger` 或直接 `uv run tools/rebuild_fixed_tiger.py` 会得到缺飞键块的瘦表，diff 里出现大约两百行假删除，提交了会来回抖。

`fixed_tiger` 已依赖 claims / 次级码 / 存档 / overrides。只改其中任一源文件后再 `make dict` 就会重建。

## 验证

```bash
uv run python -m unittest tests.test_tiger_aux.FixedDictionaryTest.test_legacy_fixed_tables_preserve_moran_multi_short_code tests.test_tiger_aux.FixedDictionaryTest.test_code_claims_swap_three_code_across_generated_tables tests.test_tiger_aux.FixedDictionaryTest.test_generated_short_codes_prefix_current_full_codes
make flykey-check
```

码长计数变了要改 `test_legacy_fixed_tables_preserve_moran_multi_short_code` 里的 `expected_lengths`，并在旁边加日期注释。次级码 / 非前缀 claims 已由前缀自洽测试自动豁免，一般不用再加白名单。

新换位请在 `test_legacy_fixed_tables_preserve_moran_multi_short_code`（或 claims 专用测试）加正反断言：指定字在目标码、被挤字不在该码、被挤字落到哪条四码。

## 部署（macOS / 用户本机）

用户默认是**多重简字**（`multi_short_code=false` → 读 `mohu_zrm_fixed_legacy`）。唯一简字读 `mohu_zrm_fixed`。两张都要拷。

```bash
RIME="$HOME/Library/Rime"
BK="$RIME/backups/$(date +%Y-%m-%d-%H%M%S)-swap"
mkdir -p "$BK"
cp -p "$RIME"/mohu_zrm_fixed.dict.yaml "$RIME"/mohu_zrm_fixed_legacy.dict.yaml "$BK/"
cp -p mohu_zrm_fixed.dict.yaml mohu_zrm_fixed_legacy.dict.yaml "$RIME/"
killall Squirrel; sleep 3
open -g "/Library/Input Methods/Squirrel.app"
```

独立的 `mohu_*_tiger_fixed*.dict.yaml` **运行时不加载**，不用拷。验证 `~/Library/Rime/build/mohu_zrm_fixed*.table.bin` 的 mtime 已刷新，且 `${TMPDIR}/rime.squirrel` 最新 INFO 无 `E` 行。

个人词库可能把旧字顶回前面，多用几次会收敛，不是生成失败。

改的是**项目**，不是只改 `~/Library/Rime`。本地手改下次覆盖就没了。

## claims 规则

格式：`字符<TAB>三码`，三码必须是恰好三个小写字母。

- 通常取该字完整码（双拼 + 首选辅码）的前缀。
- 允许**非前缀记忆码**（`政 vgh`）：政的辅码是 f 系，`vgf` 仍保留，`vgh` 额外固顶。被挤的郑落到自己的四码 `vghm`（不一定是用户随口说的兼容位 `vght`，兼容打法仍能打出）。
- 唯一表在存档扩展**之前**预约码位，否则存档里更短的码会顺延抢走三码（`有 yb` 曾抢走 `右 ybn`）。
- 指定字保留它自己的其他自然简码。
- 同字、同码不能重复。

加一行前先 `rg` 目标码在 `mohu_zrm_fixed_legacy.dict.yaml` 的 `#----------生成单字----------#` 块里现在是谁。码以 `v` 结尾时先看飞键目标是否撞一简空间（`jv` / `yv` 一类）。

## 次级码规则

格式：`字符<TAB>编码`（一至四码都可以）。

- **只进多重表**，追加在同码现有行之后，所以是次选。
- 算作该字已覆盖：更长的自动前缀简码会被抑制（`加 jw` 让出 `jws`；`文 wf` 让出 `wfv`，紊接手）。
- 三码次级码不会挤掉别人同长度的自动三码；二码次级码会让出该字自己的三码自动行。
- 和 overrides 冲突会直接 `ValueError`。

## 存档（一 / 二码）规则

`mohu_fixed_simp_legacy_chars.txt` 来自魔然多重简字历史，格式：

```
source_line	字	编码	[可选完整码]
```

换一 / 二码归属时：**改对应行的字（和必要时的编码），保留 `source_line`**。不要把新行追加到文件末尾。三 / 四码不必手写，级联会按完整码前缀补。

改完在该行上方留一行日期注释，例如：

```
# 2026-09-20 方/放换位：放 的二码 fh 让给方，放 改用三码 fhl。
17863	方	fh
```

## 现行换位（2026-09-21）

以三个源文件为准；下表是对照。

| 字 | 打法 | 入口 | 被挤走的 |
|---|---|---|---|
| 喂 | `wzd`（小鹤 `wwd`，飞键 `wkd`） | claims | 味 → `wzda` |
| 右 | `ybn` | claims | 友 → `ybnr` |
| 己 | `jiv` | claims | 忌 → `jivh` |
| 政 | `vgh`，并保留 `vgf` | claims（非前缀） | 郑 → `vghm` |
| 文 | `wf` 次选（问仍首选） | secondary | 文原 `wfv` → 紊 |
| 方 | `fh` | 存档 | 放 → `fhl` |
| 象 | 一简 `x` 次选（小仍首选） | 存档 | 像 → `xdj` / 小鹤 `xlj` |
| 派 | 一简 `p` | 存档 | 平 → `pye` / 小鹤 `pke`；派让出的 `plk`/`pdk` 由 湃 递补 |

## 本地应急（会被覆盖）

只改已部署文件、且接受下次安装冲掉时：编辑

- 多重：`~/Library/Rime/mohu_zrm_fixed_legacy.dict.yaml`
- 唯一：`~/Library/Rime/mohu_zrm_fixed.dict.yaml`

只动 `#----------生成单字----------#` 块。行格式 `字<TAB>编码<TAB><TAB>权重`（四列，第三列空）。表是 `sort: original`，**同码行的上下顺序 = 候选顺序**。改完必须重新部署。

## 禁止项

- 为了换简码去改辅码 / `mohu_chai.txt` / `tiger_aux.txt`（会连带词库和整句音节）。
- 只改仓库不同步用户目录，或只改用户目录不改仓库。
- 并行会话里回退 claims；动手前看 `tools/data/mohu_fixed_code_claims.tsv` 的 mtime 和内容。
- 把飞键码、小鹤码写进三份源文件。
- 用 overrides 去抢已经有一 / 二简的音节。
