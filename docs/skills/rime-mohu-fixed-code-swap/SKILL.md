---
name: rime-mohu-fixed-code-swap
description: Use when the user asks to swap or pin mohu short codes, 换位, 简码指定, claims, 三码固顶, 次级简码, 一简/二简换字, or to change which character owns a 1/2/3-key code in rime-mohu.
---

# 魔虎简码换位

**必读：** [`docs/fixed-code-swap.md`](../../fixed-code-swap.md)。下面是执行时容易走错的约束，细节以那篇为准。

## 入口（按码长，不要猜）

| 目标 | 源文件 |
|---|---|
| 三码在唯一表 + 多重表都固顶 | `tools/data/mohu_fixed_code_claims.tsv` |
| 同码次选（不抢首选） | `tools/data/mohu_fixed_secondary_codes.tsv` |
| 一码 / 二码归属 | `tools/data/mohu_fixed_simp_legacy_chars.txt` 原行 |

源码写自然码。不要手改 `mohu_*_fixed*.dict.yaml`。不要用 `mohu_fixed_char_code_overrides.tsv` 做换位（会封死二码前缀）。

用户说「X 和 Y 换位 / X 改成 N 码」时按字面码位执行，不要用「多重表已经是目标状态」挡在前面。无码位时低频字上短码只是兜底；政/郑 说明真实动机经常是末根/手感。

## 生成与落地

1. 改对应源文件（claims 加一行；存档改原行并留日期注释，不追加到文末）。
2. `make dict`（不要只跑 `make fixed_tiger` / 不要直接 `uv run tools/rebuild_fixed_tiger.py`）。
3. 更新 `tests/test_tiger_aux.py` 里相关断言和 `expected_lengths`。
4. `uv run python -m unittest tests.test_tiger_aux.FixedDictionaryTest.test_legacy_fixed_tables_preserve_moran_multi_short_code` 以及 claims / 前缀测试；`make flykey-check`。
5. 拷 `mohu_zrm_fixed.dict.yaml` 和 `mohu_zrm_fixed_legacy.dict.yaml` 到 `~/Library/Rime/`，重启 Squirrel。独立 `*_tiger_fixed*` 不用拷。
6. 没说「发布」就停在仓库 + 本机部署。

改的是项目源数据，不是只改已部署文件。

## 红旗

- 手改生成单字块当正式方案
- 为换简码去改辅码 / 拆分
- 只 rebuild 不 `make dict`
- 把小鹤码或飞键码写进 TSV
- 方向反了（用户写「jiv 是己」却写成忌）
- 并行会话回退了 claims
