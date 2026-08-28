# 魔虎大模型整句引擎（tigerengine）

魔虎自然码完整输入流水线 + 虎码辅助码的原生整句输入。语言模型与解码在
纯 C 动态库中执行，原生 Lua 层只负责候选输出与可选神经重排，不主动改写或提交组合；
符号、反查、简快码、候选管理、加词和过滤器沿用默认魔虎组件。原生解码
架构与 TigerClaw 虎整句（Rime Lua 版）等价，模型直接复用其 TCSKNM 三元模型。
这是独立的魔虎大模型 addon，不属于标准 Mohu 方案包，也不启用 Octagram。

## 文件

- `tigerengine.cc` — TCSKNM01/02 模型读取（mmap）+ 增量 beam 解码 + C ABI
- `tigerengine_lua.cc` — Lua 5.4 绑定（luaopen_tigerengine）
- `nativetest.cc` — 基准对拍工具（与 Lua 版逐键比对输出）
- `../lua/mohu_tiger_sentence.lua` 位置见用户目录 — Rime Lua 薄壳
- `mohu_tiger_sentence.schema.yaml` — 方案

## 构建

需要 Lua 5.4 头文件（Squirrel 的 librime-lua 为 5.4.6，ABI 一致）：

    curl -sL -o lua546.tar.gz https://www.lua.org/ftp/lua-5.4.6.tar.gz
    tar xzf lua546.tar.gz
    zsh build.sh

部署（用户目录）：

    # 使用独立 addon；标准方案包仍由 make dist 生成。
    make llm-dist LLM_DESTDIR=/tmp/mohu-llm-dist \
      TIGER_NGRAM=/Users/fuchuxuan/Library/Rime/tiger/sentence-ngram-mobile.bin

发布包的普通用户不需要执行上面的构建命令：解压 `llm-dist` 后，双击
`install_mohu_llm.command` 即可安装。安装器会把 addon 文件复制到
`~/Library/Rime/`，在方案列表中注册 `魔虎大模型`，并重新加载 Squirrel；它会合并
`default.custom.yaml`，不会覆盖已有的 `default.custom.yaml`，重复运行也是安全的。

addon 需要 `sentence-ngram-mobile.bin` 和
`mohu_tiger.lexicon.txt`；ngram 不在仓库中，使用 `TIGER_NGRAM` 指向现有文件。
Qwen 权重也不随 addon 分发，必须按 `models/*.manifest` 中的 registry path
单独下载到 `tiger/models/`，并校验大小与 SHA-256。

Squirrel 的 librime-lua 使用 Lua 5.4.6；LuaSocket 也必须用 5.4 ABI 安装到
`~/Library/Rime/lua/rocks`（模块会自动加入该目录的 `package.path/cpath`）。
`run_qwen35_scorer.command` 由 launchd 常驻运行，Unix socket 与模型位于同一 `tiger/`
目录；没有 scorer、模型指纹不符或响应超时都会自动回到三元原序。

## 码表格式

每行 `code <TAB> text <TAB> rank <TAB> freq_rank`。
码形含裸双拼（YY）、加辅（YYX）与真实码形（YYX/、YYXX/、YYXXo）。
再生成：见基准目录 `unfixed_table.json` 的生成脚本（双拼取
`mohu_zrm.chars.dict.yaml`，虎辅取 `tools/data/tiger_aux.txt`，
权重取 `tools/data/chars.txt` 简频列）。

飞键行（`wz→wk`、`xq→xo`、`qx→qo`，与 mohu 的
`mohu_defs.yaml:/fly` 规则一致，含链式组合）由
`uv run tools/fix_tiger_lexicon_fly.py` 在生成后的码表上补齐；
`tests/test_tiger_lexicon_fly.py` 会校验覆盖完整（裸码与飞键码
条目数、rank 一一对应），缺失时按提示重跑补齐脚本即可。

## 配置（schema 的 tiger/ 节）

- `engine_lib` / `model` / `lexicon`：路径覆盖，默认用户目录 `tiger/` 下同名文件
- `beam`：束宽（默认 200）
- `all_ranks`：>4 键时全部档位竞争（默认 true）
- `initial_quality`：原生候选质量（默认 50）。固顶候选为 100，默认 smart 候选为 5

## 合并行为

- 自定义、置顶与固顶简快码保持默认魔虎优先级。
- 原生整句候选排在普通 smart 候选之前；当 smart/用户词库已有候选时，原生只在
  词库候选文本集合内参与排序，不会用静态码表额外拼出绕过用户词的分词。没有
  词库候选时才保留原生候选作为降级路径。
- 一码简词只允许独立输入，不参与多键整句切分；句中每段至少消费双拼两键。
- 数字键交给默认 `selector` 选候选；分号与快捷键仍由 `mohu_processor` 处理。
- 动态库、模型、码表或 scorer 加载失败时记录一次错误，并自动保留默认魔虎候选。
- 方案不会在输入过程中提前上屏；候选确认、空格和回车均交给 Rime 默认编辑器处理。
- 神经重排只作用于原生整句候选，不改变置顶、固顶、自定义词和全局 filter 的优先级。
  每次 composition 只在传输前一次性选择评分预算：默认五条，native 首选不确定且
  候选分支足够多时可扩到已配置上限（协议上限二十条），未评分尾部保持原生顺序。
  同一次请求内完成比较；超过五条的请求内部固定为 20 行 kernel 形状，短序列补到
  8 token。不同序列 bucket 的量化 kernel 仍可能产生轻微数值漂移，不能把跨请求的
  绝对分数当作同一标尺。
- 提前上屏功能已移除；scorer 超时、模型不匹配或服务不可用时只回退候选顺序，
  不会改写组合或吞掉输入。
- 原生引擎句柄按 Rime 进程生命周期复用；修改模型或路径后需要重新加载 Rime。
- 神经开关默认关闭；`/model` 菜单动态列出已安装模型。默认选择
  `qwen35-0.8b`，选择无效或模型缺失时 fail-closed，不自动切换到其他模型。

## 验证记录（2026-08-26）

- 原生引擎与既有 Lua 基准的对拍脚本可复用 `tools/ziti_probe.c`；该工具现在支持
  `neural` 参数并使用 Squirrel 自带的 Lua 5.4/librime 栈。
- 延迟：纯双拼 20.2 → 1.8ms/键（直连）/ 2.24ms/键（全链路），真实码形更快
- 完整流水线探针：`vhrg1` 上屏「中华人民共和国」，`tz2` 上屏「投资」，
  `/date1` 上屏当前日期；Qwen 目标回归命令见 `eval/latest-report.md`
- Homebrew Mira 当前使用 Lua 5.5，只能验证 ABI 失败后的默认候选降级；原生动态库
  使用 Squirrel 自带的 Lua 5.4.6 / librime 探针验证
