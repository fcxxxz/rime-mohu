# 神经输入门控与本机部署

日期：2026-09-08。设备：Apple M1 Max。范围：统一输入门控，并将此前共享
Transformer/QKV、向量算子、上下文窗口配置的优化一并部署到本机鼠须管。

## 规则

- 保留 `contextual_order`、`word_order` 和神经模型配置开关。
- 少于四个编码字母在获取 scorer 前直接返回；每个候选还必须在自己的输入
  区间内包含至少两个完整双拼音节，不能计入已经选定的前段编码。
- 使用候选实际 preedit 分段，要求段数对应候选字数，再统计至少两个有
  声韵编码的音节。只有总字母数足够不算通过；无法可靠分段的整词缩写、
  辅码混合或无分隔显示保守跳过，可能少触发，但不能误触发。
- 至少两个不同有效候选才进入评分；随后仍由现有静态 V5 不确定性门控决定
  是否实际调用神经模型。两个完整音节是必要条件，不是必然推理条件。
- 有上屏历史时使用上文；无历史时允许 BOS 与候选内部语境评分。
  无历史且本次 `neural_reranked` 不为布尔真时，保持候选对象及原始顺序，
  不利用 V5 的 BOS 分单独重排。非神经配置保持原有 V5 行为。
- 候选覆盖仍为配置的 20 项，上文窗口默认仍为 160 字；不增加跨键缓存。

## 验证

- `lua tests/mohu_neural_input_gate_test.lua`：82 检查、0 失败。
- `lua tests/mohu_word_order_filter_test.lua`：全部通过。
- `lua tests/neural_context_config_test.lua`：155 检查通过。
- 最终严格分段版本在隔离 librime 中进行插桩与无插桩各 12 个会话：
  `jwgzle`、`nihk`、`vsxn` × 无历史/外婆 × 两轮，均通过。
  插桩设在 C++ 实际神经调用处；36 个前 1–3 键观测无神经调用。
  两轮均确认「外婆 + jwgzle」及「无历史 + vsxn」实际推理并应用融合，
  使用原始 margin 0.7，未为测试放宽门控。无插桩测试只验证输出，不单独
  声称证明实际推理。
- 从安装目录复制三个最终文件回隔离目录，再跑 12 个会话，全部通过。
  不直接用测试驱动打开本机正在使用的用户目录。
- 最终安装文件的隔离完整末键测量（按键处理加菜单获取，不含屏幕渲染）：
  「外婆 + jwgzle」预热后 21 次，P50 **15.123ms**、P95 **15.506ms**；
  本次进程首轮 **23.713ms**。这不是冷启动上界；此前独立进程存在更高
  冷态离群值，不能声称已消除卡顿或达到 10ms。
- 构建产物签名验证通过。相关源码 `git diff --check` 通过；扩大范围时
  发现既有小鹤生成词表尾随制表符，未修改或部署这些无关文件。

隔离原始记录位于 `/tmp/mohu-neural-gate-20260908/` 的 `final-trace.json`、
`final-clean.json`、`post-deploy-gate.json`、`post-deploy-latency.json`。
临时目录记录不是永久归档，本报告保存关键结论。

## 部署与回滚

仅用临时同目录文件加原子重命名替换以下三个目标，避免就地覆盖已映射 dylib：

| 仓库来源 | 本机目标（相对 `~/Library/Rime/`） |
|---|---|
| `tiger_sentence_native/libtigerengine.dylib` | `mohu/runtime/libtigerengine.dylib` |
| `tiger_sentence_native/mohu_tiger_sentence.lua` | `lua/mohu_tiger_sentence.lua` |
| `lua/mohu_word_order_filter.lua` | `lua/mohu_word_order_filter.lua` |

备份目录：
`/Users/fuchuxuan/Library/Rime/backups/neural-gate-20260908-150305/`。
三个旧文件保留相同相对路径，`manifest.json` 保存新旧 SHA256 和原输入源。
需要回滚时，将备份的三个文件以同样原子替换方式恢复，然后正常重启鼠须管。

新 dylib SHA256：
`2fb8673d0e1b0ec02400e7367924bf82bee53be141d8c225a42e7ba15a073a2a`。

部署后 `codesign --verify --strict` 通过，安装文件与仓库来源哈希一致。
Squirrel 使用 SIGTERM 正常退出后重启，PID `46821 → 62915`；`lsof` 确认
新进程已映射本机目标路径的新库。输入源前后均为
`im.rime.inputmethod.Squirrel.Hans`，未切换输入源。
未覆盖模型、词库、schema 配置、用户数据库或学习快照；宿主正常退出和运行
可能进行其自己的常规学习数据持久化。未执行词库全量生成或全目录部署。
