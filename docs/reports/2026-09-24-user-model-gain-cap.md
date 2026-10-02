# 用户层路径增益封顶（2026-09-24）

## 动机

用户实际输入 `wzdkhodekucpnijidmssgoll`（未到货的库存你几点送过来）时，线上
首选是「味道获得库存你几点送过来」。静态层（无用户快照）中，错误词链和正解
只差约 0.48 nats；但真实 `user-ngram.snapshot` 把「味道获得」历史路径抬约
34 nats，首选变成正分并标记 `_personal`，形成“打不出→不上屏→学不会→更
打不出”的死循环。之前只用静态探针误读成「味道的」是诊断错误；正确线上
候选是「味道获得库存你几点送过来」，本报告以加载真实快照的复现为准。

## 实现

新增 `tiger/user_model_gain_cap`（两主方案默认 6.0，范围 [0,32]，0 关闭）：

- 引擎按每条解码路径累计用户融合增益 `Σ(log P_mix − log P_static)`；正增益
  达 cap 后，后续正增益不再进入路径分，`State.user_gain` 与实际 score 同步
  封顶；负增益仍可抵消已累计增益。
- 不删除/衰减用户快照，不修改个人词边 boost，不改变个人候选可见性门槛；
  只限制用户 trigram 对一条长路径的历史放大，保留正常学习词的闭环。
- C ABI `tiger_engine_set_user_model_gain_cap`、Lua `set_user_model_gain_cap`
  已接入；旧 ABI 静默使用引擎默认值。

## 证据与边界

用真实线上快照、V5 模型、线上 zrm 词表复现同一句：

| cap | 错误首选用户分 | 行为 |
|---:|---:|---|
| 0（关闭） | +0.66 | 错误路径历史增益不受限，personal |
| 2 | −5.09 | 历史增益被截断，错误路径不再 personal；但静态词边/模型仍可能使它第一 |
| **6（默认）** | **−1.09** | 错误路径用户增益约封顶 6，仍可能因静态模型与词边领先；不承诺单独把该冷句翻正 |
| 10 | −4.60 | 更宽松的历史学习 |

因此 A 的职责是**取消几十 nats 的用户层加冕**，不是独立解决静态模型的
语域错位或词边无上限累加；后两者仍在 V6 清单。用户层测试原有“重复学习可翻转”、
快照回环、反学习全通过；Lua 越界 cap 测试通过。

## 验证与部署

- `make tigerengine-user-model tigerengine-lua-safety tigerengine-word-form
  tigerengine-word-edge tigerengine-word-gate` 全绿。
- 线上 zrm schema 增加 `user_model_gain_cap: 6.0`，dylib/Lua 已重建并部署；
  完整重启 Squirrel 后生效。部署前备份：
  `~/Library/Rime/backups/2026-09-24-user-model-gain-cap/`。
- 该参数可设为 0 回滚封顶，或在 schema 中调整；不需要删除快照。

## 2026-10-02 追加：三道闸门补全与泄漏修复

用户实测 `q mh rfrfdelmdbbxdefazi` 出「人认得脸都憋得发紫」暴露了两处本报告
未覆盖的通道，同日修复（全部已部署 Mac，全套 tigerengine 测试零回归）：

1. **饱和泄漏**：原实现只在 `previous_gain < cap` 时回填封顶增量，路径增益
   达到 cap 后，后续字符的正增益整段留在 score 里（实测 9 字路径漏到
   +8.8 > cap 6）。现饱和后正增益归零，真正闭合到 cap。
2. **`tiger/personal_edge_internal_cap`（默认 1.5，[0,12]，12=旧行为）**：
   个人词命中静态同码真词（`from_static && rank<90`，注入词 99 不算）时，
   作长句内部边的 boost 封顶。认得×3（+5.5）即可翻边距 3.44 的句子，
   封顶后 ×10 不翻；OOV 自造词（魔虎——边通道是唯一进句路径）、注入词
   （夜莺）、整段命中边、单字调频保持全额。
3. **`tiger/bos_user_gain_cap`（默认 1.5，0=关闭）**：词级独立提交把每个
   提交当「句子开头」喂用户模型（BOS 锚定），长句头两字（prev2==kBOS 的
   trigram）的用户正增益单独封顶。真实快照实测：句中提交×50、独立提交
   ×50 均不翻。

**残留边界**：同一词独立提交 ×200 或 边×50+独立×50 仍可翻（0.84 nats），
力来自词内 bigram 回退（认→得 使任何 …认得… 路径在「得」处拿 interior
增益）——这是自造词浮出的同一机制，不再收紧；自愈路径＝正确句提交几次
或删词反学习。tigerengine-user-model 测试因此重写为新契约（神情/申请 对
只差头两字，旧「喂次选必翻」断言本身就是 BOS 锚定＝bug 行为）；新增
`make tigerengine-personal-internal` 目标含两道封顶的放开对照断言。
