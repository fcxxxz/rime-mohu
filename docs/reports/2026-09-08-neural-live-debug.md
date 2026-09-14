# 本机「外婆家给了」现场诊断

后续：16:01:56 的用户真实记录已确认，上文存在且神经融合成功，但前两条
个人学习候选因 preedit 未分段被门控固定在前缀。见
[个人词分段根因与修复](2026-09-08-neural-personal-preedit.md)。以下保留诊断安装过程。
16:17 已部署修复，并将临时 helper、启用标记与日志移入
`~/Library/Rime/backups/neural-personal-preedit-20260908-161704/diagnostic-artifacts/`；
本机两个过滤器已恢复干净版，临时诊断不再运行。

2026-09-08 15:58 更新。用户反馈重启后仍得到「外婆家给了」，不能将此前
隔离测试通过当成本机问题已经修复，也不能将上文缺失的假设当成已确认根因。

## 已确认事实

- 用户再次重启后的 Squirrel PID 为 1371，实际映射本机新版 dylib；三个
  生产运行文件 SHA256 与 15:03 部署结果一致。旧进程／旧安装已排除。
- 本机编译 schema：`mohu_zrm`，神经 weight 1.0、margin 0.7、候选20、
  rank penalty 0；持久开关 `contextual_order=true`、`inflexible=false`。
- 上一轮本机数据副本对照：连续 `wlpojwgzle` → 外婆嫁给了；同会话
  `wlpo` 空格后 `jwgzle` → 嫁给了；外婆上屏后重建会话再输入 → 家给了；
  无历史直接输入 → 家给了。用户具体现场仍需真实调用数据确认。
- Lua 当前从 `context.commit_history.latest_text` 取外部上文，没有主动读取
  宿主文档光标左侧文字的代码。不能仅据此断言用户现场一定是上文缺失。

## 临时诊断安装

为捕获实际输入，不改模型、门控参数、候选顺序或 schema。
仅临时替换本机 `lua/mohu_word_order_filter.lua`、
`lua/mohu_unicode_display_filter.lua`，并增加
`lua/mohu_neural_target_trace.lua`。仓库对应生产 Lua 源码没有修改。

备份和新旧哈希清单：
`/Users/fuchuxuan/Library/Rime/backups/neural-live-debug-20260908-155840/manifest.json`。
两个旧文件位于此备份下相同 `lua/` 相对路径；第三个 helper 原先不存在。

日志：
`/Users/fuchuxuan/Library/Rime/mohu/config/neural-target-trace.jsonl`。
开关／截止时间文件：
`/Users/fuchuxuan/Library/Rime/mohu/config/neural-target-trace.enabled`。
二者权限为 0600。仅记录外婆、嫁给相关自然码／小鹤测试编码，支持分隔符及
至多两位后缀；不记录普通输入。匹配输入时记录最多160字的上屏历史、候选
preedit／来源／区间／参与资格、评分结果、`neural_reranked` 标记以及所有
调序过滤器之后的前10个候选。模块加载事件只记录版本路径与模型配置。
日志上限1MiB，**2026-09-08 16:58:40（本地时间）后自动停止记录**；
这不等于自动移除临时文件，定位完成后仍需恢复干净版本。

临时源码及隔离验证记录：`/tmp/mohu-target-trace-20260908/`。
新门控82检查和旧过滤器回归通过；带诊断的当前用户目录副本6会话通过，
日志能解析为 JSON，能观察真实神经融合成功标记与下游「嫁给了」首选。
编码白名单正反例通过。诊断不是性能测量版本。

安装后正常重启 Squirrel：PID 1371 → 4038，输入源保持不变。
安装结束时现场日志尚无实际输入记录，**待用户在原应用复现后检查**。

## 下一步与回滚

1. 读取现场日志；先找 `entry` 的 input/history/开关，再看 `batch` 候选
   资格、`scores` 成功标记、`reordered` 和 `final_candidate` 的排序差异。
   没有日志或只有 loaded 事件不能证明评分发生过。
2. 按现场证据定位，再添加对应失败回归与最小修复，不放宽门控掩盖问题。
3. 完成后原子恢复／更新两个正式过滤器；移走临时 helper 与截止时间文件，
   保留必要现场报告。使用备份 manifest 校验，不要覆盖后续正式修复。
4. 正常重启 Squirrel 并验证最终安装及真实输入结果。不修改用户词库、模型
   或学习记录，不用跨应用共享历史来盲目弥补会话边界。
