# 词读音覆盖修复：budelc 首选不是「不得了」

> 2026-09-08。词表规范注音（多音字读音）在引擎整句码表中缺行，导致整词
> 全拼输入不可达。本报告记录根因、修复范围、验证口径与回归线。

## 现象与根因

- `budele` 首选「不得了」，`budelc`（规范注音 bùdé**liǎo** 的编码，基础词典
  即按此生成）首选却是「不得聊」：native 引擎 20 个候选里完全没有「不得了」。
- 三层共同作用：
  1. `tools/data/chars.txt` 中该读音**简频为 0**（如 `了 liao 16638 0`）；
  2. `tools/data/pinyin_simp.txt`（现代读音登记表）缺失或频次为 0 时，
     `gen_chars.py` 经 `simplified_reading_weight` 把 chars 词典权重置 0；
  3. 引擎 master 词表（`mohu_tiger.lexicon.txt`）单字行只收了主读音
     （`了` 仅有 `l/le/ler/lerl`），而三字及以上词只有声母简码行（整段
     命中），全拼输入必须走单字路径 → 「了(liǎo)」不可达 → 整词不可达。
- 全词表扫描（7 张表、287 万词条）：缺失读音组合 3,194 个 / 3,155 字，其中
  97% 只出现在万象/古文表底权 ≤20 的繁体生僻词里；真实感知集中在多音字
  又音/旧读/专名读（了 liǎo 断层第一，覆盖了解/不了/不得了/受不了/一目了然
  等约 2,200 词码；其次骑 jì、咱 zá、暴 pù、谷 yù、恪 què、般若若 rè、
  六安六 lù、阿房宫房 páng、大栅栏栅 shí 等专名与旧读）。这些在词表里全是
  有意的双读并存行（如 坐骑 `zo ji`/`zo qi` 两行）。

## 修复内容

范围 = 词权 > 20 的全部 76 个 (字, 音节)（排除 `(即, ui)`：即无 shí 音，
属词表杂音行，最高权 38）。其中 10 个读音在固顶表短码分配时会挤占既有
高频短码（大 tài 抢「态」tlm、乐 yào 抢「妖」ykb、骑 jì 抢「玑」jin、
恪 què 抢「卻」qth、房/栅/会/圾/芎/斜 同类），已退出登记并简频归零
（chars.txt 读音行保留、引擎码表行保留，代价仅这 10 档读音先验偏严）；
最终生效登记 44 项新增 + 22 项频次同步，共 66 个读音。

1. `tools/data/chars.txt`：既有读音简频激活（`max(繁頻, 100)`，如
   `了 liao 16638`）+ 新读音行（繁頻 0、简频 = 词权合计/3，夹在
   [100, 10000]，如 `六 lu 371`、`垃 le 6039`）。
2. `tools/data/pinyin_simp.txt`：登记与频次同步（权重闸门，不登记则
   `gen_chars` 置 0）。
3. `tiger_sentence_native/mohu_tiger.lexicon.txt`：380 行单字行族（音节 +
   辅1 + 辅2 + `/` + `o` 五行，仿 `mo 万` 模式；28 个 master 缺失字的
   freq_rank 按简频最近邻取值）+ `fix_tiger_lexicon_fly.py` 自动补的 5 行
   梶（wz→wk）飞键镜像。固顶表因此新增 20 个变读短码，既有短码主人
   零损失（legacy 表仅 慜→鳘 一处权重 13 的低值替换与若干同字 0→100
   激活）。
4. `tools/rebuild_fixed_tiger.py`：简体读音审计期望快照更新
   （all-modern 8121→8129、mixed 1→20、all-compat 75829→75802、
   compatibility readings 94844→94829；新增 mixed 为保留 chars.txt
   读音行但不登记现代读音的字，已逐项审阅）。
5. `Makefile`：7 个 `tigerengine-*` 编译目标补 `-framework Accelerate`
   （neural 重排合入后 `cblas_sgemm` 链接缺失，测试目标在 HEAD 即挂）；
   `make test` 接入 `tests.test_reading_coverage`。
6. 新增 `tests/test_reading_coverage.py`：词权 ≥100 的 (字, 音节) 缺引擎行
   即 fail（4.5s 全表扫描），低权尾巴不设限。

## 验证

- 引擎实测（部署模型 + 新码表）：`budelc` 首选「不得了」（-13.44，次选
  不得聊 -14.75）；`budele` 不变（-8.012→-8.017）；`lcjx 了解`、`bulc 不了`、
  `zoji 坐骑`、`bdl 不得了` 均第 1；`lcbude` 下「了不得」从不可达变为可达。
- 回归：57 个输入（受影响音节 + 常用整句）新旧码表 top-5 对比，仅 2 处
  变化 = 目标修复 `budelc` + 新增覆盖 `rg→礽`，零意外翻转。
- 单测：`test_mohu_lexicons`、`test_tiger_lexicon_fly`、`test_tiger_aux`、
  `test_reading_coverage`、`test_flypy_assets`、`test_flat_distribution`
  等 make test 全部单测模块 OK；`tigerengine-reading-prior/safety/context`
  OK。注意：`mohu_word_order_filter` 的 lua 测试（in-block punct）在
  工作区即失败，源于并行的调试改动（`tigerengine.cc` 追踪探针 +
  `mohu_word_order_filter.lua`），与本修复无关。

## 遗留

- 词权 ≤20 的 ~3,100 个生僻异读仍未入引擎（Rime 词典候选仍可打出）；
  如需彻底清理，按本报告方法批量处理即可，价值有限。
- `(即, ui)` 等少量词表杂音行（<100 权）建议另行核对词表拼音源。
- 修改 `tigerengine.cc`/`libtigerengine.dylib` 是并行进行的其他工作
  （调试探针），与本修复无关，未纳入本次变更。

## 附：Shift+Delete 删词优化（同日第二批）

排查「budelc 仍首选不得聊」时发现两个候选管理缺陷并一并修复：

1. **内联删词对 native 候选失效**：`mohu_candidate_override.lua` 的
   `user_created_entry` 硬性要求候选类型为 `user_phrase`，而个人词在
   native 引擎内的候选（`mohu_zrm` 类型，品质 50 占首位）永远匹配不上，
   Shift+Delete 落到 no-op。已放开类型门：user_phrase 走 entry 快路径，
   其余类型按 (码, 词) 扫描 userdb，输入侧允许带辅码（存储码为输入码
   前缀），完全对不上时以唯一同文词条兜底。
2. **删除后引擎个人词层不同步**：native 引擎启动时把个人词快照进内存，
   userdb 删除不会自动传导（分片刷新只在输入组合为空时推进），词条在
   当前组合里立刻被引擎个人边顶回。新增
   `mohu_tiger_sentence.refresh_personal_now(memory)`（一次性整体快照
   替换），内联删除与 `==u` 管理器删除成功后都即时调用。
3. **两段式语义**（用户要求）：选中候选按一下 Shift+Delete 清空学习
   权重（userdb 计数归零 + 引擎个人层同步刷新，提示「已清空…；两秒内
   再按一次永久删除」）；两秒内再按一次永久删除（override 标记 +
   userdb 扣减 + 候选移除 + 引擎刷新）。armed 状态记录在 processor env，
   第二按不依赖 userdb 条目是否仍可寻回。权重本就为 0 的词条单按即删。
- 测试：`tests/mohu_candidate_override_test.lua` 重写永久删除用例为两段
   式并新增 native 候选（budelc→不得聊 场景）回归线；mira 用例
   `delete_learned_user_phrase` 同步更新为两按语义（mira 环境未在本地
   复跑，见知识库既有备注）。
