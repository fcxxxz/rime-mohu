# 四码字词行序、简快词让位与丢失读音的系统性修复（修订版）

## 规则依据（不再推断，全部来自仓库原始数据）

- **旧避让表原文**（`git show 4ece197^:mohu/four_code_yield_pairs_zrm.txt`，446 行）：
  「输入四码时，本表中的二字词排在行内所列单字之前，**被顶单字仍保次选**」。
  实际行：`必须 庳`、`补丁 卟`、`一点 邑 嗌`、`打开 溚`、`思考 涙`、`弟弟 嘀`、`抵抗 滌`、`演练 芫`。
  生成判据（`tools/build_four_code_yield_pairs.py`，已删）：**流中第一条入秩词**的词秩 < 4 × 字秩；
  适用范围仅限 **fixed 表内的四码字**（`_four_code_detail.tsv`）；读音全码字（逍/削/肖类）不参与避让，
  旧管线里它们走辅码层、排在 smart 词之后。
- **合并前层级**（e660742 前的多表体系）：三码简快码=简字表（fixed_legacy 多重简字）专属槽（削 xty）；
  四码全码=chars 表读音+辅码（权重序，削 xcyv 上是 肖→逍→削）；**兼容码**=旧辅码拼法别名（削 xc;yp，
  独占码位首选）；被顶字保次选；简快码词（>2 字缩写）排在其码位的全码单字**之后**。
- e660742/4ece197 合并统一时丢失：①被顶字次选语义变成「字沉到全部词后」；②简快词注入位改成
  首选后第 2 位（越过单字）；③兼容辅码别名 8,489 行未迁移；④91 个字的整个读音无行；
  ⑤三码槽所有权重排（肖 占了 削 的 xcy——旧简字表无 肖 xcy）。

## 修复实现

1. **码表行序** `tools/fix_four_code_char_order.py`：被顶字行紧跟**行序第一条入秩词**的行后
   （budy: 补丁,卟,布丁,不定）。判据词秩<4×字秩；表尾全码区行不动；幂等。共 433 码位。
2. **简快词让位** `lua/mohu_ijrq_filter.lua` 0.4.2：secondary（>2 字简快词）三种落位——
   让位字后（0.4.1 最终首选跟随，jnqu 不变）、**不让全的全码单字后**（被顶字保次选、字优先于简快词，
   bixu 必须→庳→变成现实）、否则自然位置。新增正反单测 2 条。
3. **读音恢复** `tools/restore_lost_char_readings.py`：91 字 238 行读音行 + `--include-variants`
   8,489 行兼容辅码别名（削 xcyp 重新独占首选；两字母声母简码行不恢复）。

## 实际候选（mira 全量重编实测）

| 编码 | 合并前（避让表语义） | 修后实测 |
| --- | --- | --- |
| bixu | 必须 → 庳 → 变成现实 | **必须 → 庳 → 变成现实** ✓ |
| budy | 补丁 → 卟 → 布丁 → 不定 | **补丁 → 卟 → 布丁 → 不定** ✓ |
| yjjd | 演讲 → 弇 → 引经据典… | **演讲 → 弇 → 引经据典 → 研究决定** ✓ |
| yidm | 一点 → 邑(嗌)… | 一点 → 邑 → 已点 ✓ |
| jnqu | 进去 → 机难轻失 → … → 进 | 逐项不变（让全字用例）✓ |
| xty | 削（专属简快码） | 削 #1（血糖仪/小太阳/县太爷 按简单鹤规则随后）✓ |
| xcyp | 削（兼容码独占） | **削 #1** ✓ |
| xcyv | 小雨等 smart 词 → 辅码字（肖→逍→削） | 小雨 → 逍 → 性从业者 → 小于 → 削 → 肖（词先字后，逍 次选）|
| xcy | — | 肖 → 小 → 逍 → 削(#4)（xcy 归属见下）|
| dakl/sikk | 打开 → 溚；思考 → 涙 | ✓ |
| jinm | 骑 jì 可达 | 纪念 → 几年 → 骑(#3) ✓ |

## 验证

- `make check-code-table`（168,709 条）、`make dict` 全链路通过；两工具重跑 0（幂等）。
- `lua tests/mohu_ijrq_fixed_test.lua` 全过（含 2 条新增回归）。
- `make test`：4/111 失败均为非本次项——cross_candidate_order/vsmc/candidate_override 是 mira 无 native
  的已知环境失败；**wzwv（𱈃 抢 微 首选）在 HEAD 码表+HEAD lua 下同样复现**（嫌疑 f202556 的 reorder
  改动或环境，bisect 记录：/tmp/bisect-a、/tmp/mira-bisect-b），需单独立案。

## 遗留

- `wzwv` 首选错误为 HEAD 已有问题（default 与 tiger_priority 两处断言），与本次改动无关。
- `xcy` 三码归属：旧简字表无 肖 xcy，统一时被导出规则发明给肖；削 的 xcy 要回来属三码策展
  （改主表行序即可），未动。
- 碰撞码位（433 个）词序仍为主表行序、不参与 smart 调频——10-10 的设计取舍，恢复学习序需改
  express/合并层，待拍板。
- 未提交（工作区另有并行会话改动）。

## 本机部署（2026-10-13 11:40）

最小集同步四件：`mohu_zrm.dict.yaml`、`mohu_zrm.words.dict.yaml`、`lua/mohu_ijrq_filter.lua`、
`mohu/data/zrm/mohu_zrm.lexicon.txt`（引擎词表）。备份 `~/Library/Rime/backups/2026-10-13-113950-fourcode-ijrq/`
（含被替换的 09:21 本地变体码表）。Squirrel `--reload` 后完全重启；`build/mohu_zrm{.table,.prism}.bin`
与 `mohu_zrm.words.table.bin` 全部 11:40 重编，`squirrel.yaml` 皮肤 wechat_copy 保持，日志零 E 行。
并行会话未提交的自定义方案 lua（mohu.lua 等）按 10-12 惯例**未**一并部署。

## 状态

主表 160,220 → 168,709 行；`lua/mohu_ijrq_filter.lua` 0.4.2；工具 2 个；本报告与
`docs/code-table.md` 规则段同步。
