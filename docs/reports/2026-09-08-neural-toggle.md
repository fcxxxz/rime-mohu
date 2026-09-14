# 独立大模型开关

## 行为

- 两个主方案 `mohu_zrm` / `mohu_flypy` 增加 `neural_rerank`，菜单显示
  「大模型关／大模型开」，默认关闭；位置紧接 V5 的 `contextual_order`。
- F4 或 Ctrl+反引号打开方案选单；当前折叠菜单按 `2` 展开，翻到
  「跨候选调频」所在页，即可看到「大模型关」。选择它开启，再选择关闭。
- 与 V5 跨候选调频独立，关闭神经不会关闭 V5 或清理学习数据。
- 关闭启动不调用神经加载接口。已加载后关闭不再执行神经推理，但保留
  已加载权重供重新开启复用；输入法退出或引擎释放时才释放这部分内存。
- 开关在当前候选上立即生效；跨应用沿用按键驱动的 250ms 同步节流。
  状态保存到 `lua/option_state_data.lua`，并加入 Rime `save_options`。
  不设置 schema `reset`，避免重启时覆盖已保存状态。

## 实现

`lua/mohu_word_order_filter.lua` 每次请求读取两个独立开关。两者都关
直接保持原序；仅 V5 开启时恢复原 V5 路径；仅神经开启但未实际融合时
保持原序。原有音节门控、个人词分段修复和 native 回退边界保留。

`tiger_sentence_native/mohu_tiger_sentence.lua` 把原来启动时无条件加载
改为受开关控制。返回的字符 scorer 闭包在每次实际调用前检查所属
context，防止缓存函数或共享引擎借用别的上下文的开启状态。关闭失败
时拒绝继续评分，避免意外推理；开关不进入引擎配置签名、不触发 V5 重建。
复用已有 native `set_neural_rerank` 接口，本次不修改或替换 native 库。

额外修复加载失败后的重试抑制：关闭上下文的评分不会重置另一个开启
上下文的失败状态。translator init/fini 注册/解除开关通知，只在真实
off→on 变化后允许下次评分重试；空闲切换也有效，通知本身不加载模型。
多个 translator 的重复通知以及评分回调交错不会重复触发加载尝试。

旧神经测试均显式设置新开关为 true，避免默认关闭后静默变成 V5 测试。
会话驱动增加 `--neural-rerank on/off`，默认 on 保持原测试意图。

## 验证

- 53 项 Lua 开关回归覆盖关闭启动、缓存 scorer、共享上下文、加载/关闭失败、
  V5 独立性、开关保存与恢复。旧 word-order / native / context / selected
  segment / user-model 测试通过；音节门控 82 项、上下文配置 155 项通过。
- 两项 schema 测试与五项 Node schema-settings 测试通过。
- 隔离副本通过 Squirrel 自带 `rime_deployer --build` 编译。编译结果与
  本机原配置做语义对比，除新增开关、save_options 和构建元信息外不变；
  神经模型路径、weight 1.0、margin 0.7、候选数等参数保持原值。
- `tests/neural_toggle_session_test.py` 驱动真实 librime，临时包装实际
  native create / set_neural_rerank / context_char_scores，记录桥接调用。
  并非仅通过耗时或候选文本推断神经是否执行。
- 实际 F4 展开菜单同时显示「跨候选调频」和「大模型关」；用菜单开启
  后当前「外婆 + jwgzle」候选立即刷新为「嫁给了」，关闭后 V5 保持开启。
  两会话跟随开关，切换前后 native create 次数不增长。
- 三个独立进程依次执行 `initial-off`、`restore-on`、`restore-off`。
  开启状态能恢复；关闭状态能恢复且整个关闭启动/查询过程 **零 neural
  setup 调用**，字符评分仍在运行，`neural_reranked` 全部为 false。

## 部署

2026-09-08 16:59 已部署到 `/Users/fuchuxuan/Library/Rime`。仅安装三个
Lua 文件、自然码 schema 与 default 的源/编译配置，并将持久化的
`neural_rerank` 设为 false；`contextual_order` 仍为 true，其他选项不变。
本机没有小鹤主方案，未额外引入未安装的方案。未运行全库 `make all`，
未将仓库已有的词库改动带入本机，也未替换模型权重或用户学习库。

备份：`/Users/fuchuxuan/Library/Rime/backups/neural-toggle-20260908-165932/`。
`manifest.json` 保存安装前后与重启后哈希、进程和输入源；原文件按相对
路径保存，`new/` 是部署版本，`evidence/` 保存单元/真实会话结果及隔离
插桩脚本。回滚时先正常退出输入法，按 manifest 的八个文件列表恢复备份
原文件（不复制 `new/`），然后重新启动；不需要恢复或删除学习库。

Squirrel 正常退出并重启，PID `7281 → 18369`，输入源保持
`Squirrel.Hans`。启动后的自动构建只更新两个编译 YAML 的 `__build_info`；
状态文件只发生键序重排。语义校验确认菜单、模型参数及所有选项值符合
部署版本。native dylib SHA256 仍为
`61306ab5205ed94377a7d3238d1fbcd37192fd6a366d31bd469f6389615e5960`，
`lsof` 确认新进程使用本机该库。

从实际安装目录重新读取干净文件到隔离目录，关闭/开启各两个真实会话
通过：关闭保留 V5 路径，开启时「外婆 + jwgzle」首选「嫁给了」。
这不冒称已替用户观察其应用窗口；本机运行时未保留任何临时 trace hook。

本次涉及文件的 `git diff --check` 通过。全仓检查在无关已有的
`tiger_sentence_native/data/flypy/mohu_flypy.lexicon.txt` 五条尾空白上失败，
未修改该文件来清理格式。
