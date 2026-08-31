# 魔虎大模型整句引擎（mohu_llm）

魔虎自然码完整输入流水线 + 虎码辅助码的原生整句输入。语言模型与解码在
纯 C 动态库中执行，原生 Lua 层只负责候选输出与可选神经重排，不主动改写或提交组合；
符号、反查、简快码、候选管理、加词和过滤器沿用默认魔虎组件。原生解码
架构与 TigerClaw 虎整句（Rime Lua 版）等价，模型直接复用其 TCSKNM 三元模型。
这是独立的魔虎大模型 addon，不属于标准 Mohu 方案包，也不启用 Octagram。

## 文件

- `tigerengine.cc` — TCSKNM01/02 模型读取（mmap）+ 增量 beam 解码 + C ABI
- `tigerengine_lua.cc` — Lua 5.4 绑定（luaopen_tigerengine）
- `nativetest.cc` — 基准对拍工具（与 Lua 版逐键比对输出）
- `mohu_sentence.lua` — 对外的 Mohu 整句 translator 入口
- `mohu_tiger_sentence.lua` — 兼容实现文件；旧部署仍可通过该文件加载
- `../lua/mohu_personal_lexicon.lua` — 个人多字词快照模块
- `mohu_llm_zrm.schema.yaml` / `mohu_llm_flypy.schema.yaml` — 自然码与小鹤完整方案

## 安装与模型选择

这是独立的魔虎自定义候选模型 addon。完整方案包按输入法分别提供：

- `mohu-llm-zrm-latest.zip`：魔虎大模型·自然码，只安装自然码方案；
- `mohu-llm-flypy-latest.zip`：魔虎大模型·小鹤，只安装小鹤方案；
- `rime-mohu-llm-runtime-latest.zip`：共享动态库、原生整句模型和 Qwen 模型清单，不能单独提供输入方案。

如果只使用自然码，下载并解压 `mohu-llm-zrm-latest.zip`，双击其中的
`install_mohu_llm_zrm.command`，然后在 Squirrel 菜单执行“重新部署”。安装器只注册
`mohu_llm_zrm`，保留已有用户词库和用户配置，重复运行安全。

`data/sentence-ngram-mobile.bin` 是原生整句候选模型。官方 Release 使用固定哈希的 TCSKNM02 字符级模型；本地 v5 也属于 TCSKNM02，可在构建时通过 `TIGER_NGRAM` 指定。它与可选的 Qwen 神经重排模型是两层不同组件：Qwen 不随 zip 分发，也不替换包内的原生模型。Qwen3 0.6B 4-bit 下载后放到
`~/Library/Rime/mohu_llm/models/Qwen3-0.6B-4bit`，再在输入法中输入 `/model` 选择
`Qwen3-0.6B-4bit`，或运行：

    ~/Library/Rime/mohu_llm/runtime/switch_qwen_model.command qwen3-0.6b

切换脚本会按 manifest 校验模型指纹。切换后重新部署 Squirrel 以加载 profile；如果
模型不存在、指纹不匹配或神经服务不可用，输入法会继续使用原生魔虎候选。原生模型
始终由包内的 `data/sentence-ngram-mobile.bin` 提供，`/model` 只选择神经重排器。


需要 Lua 5.4 头文件（Squirrel 的 librime-lua 为 5.4.6，ABI 一致）：

    curl -sL -o lua546.tar.gz https://www.lua.org/ftp/lua-5.4.6.tar.gz
    tar xzf lua546.tar.gz
    zsh build.sh

部署（用户目录）：

    # 使用独立 addon；标准方案包仍由 make dist 生成。
    make mohu-llm-zrm-dist MOHU_LLM_ZRM_DESTDIR=/tmp/mohu-llm-zrm \
      TIGER_NGRAM=/path/to/sentence-ngram-mobile.bin

发布包的普通用户不需要执行上面的构建命令：从 GitHub Release 下载
`mohu-llm-zrm-latest.zip` 或 `mohu-llm-flypy-latest.zip` 后，双击对应的
`install_mohu_llm_*.command` 即可安装。
安装器会把文件复制到 `~/Library/Rime/mohu_llm/`，只注册所选方案并重新加载
Squirrel；它会合并 `default.custom.yaml`，不会覆盖已有配置，重复运行也是安全的。

Qwen 模型重排还需要 Python 3 和 `mlx-lm==0.31.3`。安装器会自动检查 `mlx_lm`；
如果本机没有，会明确提示执行 `uv pip install mlx-lm==0.31.3`，输入法仍保留
n-gram 候选，不会假报模型服务已启动。

自然码包需要 `data/zrm/mohu_llm_zrm.lexicon.txt`，小鹤包需要
`data/flypy/mohu_llm_flypy.lexicon.txt`；两个包都包含共享的
`data/sentence-ngram-mobile.bin`。
Qwen 权重也不随方案分发，必须按 `models/*.manifest` 中的 registry path
单独下载到 `mohu_llm/models/`，并校验大小与 SHA-256。

Squirrel 的 librime-lua 使用 Lua 5.4.6；LuaSocket 也必须用 5.4 ABI 安装到
`~/Library/Rime/lua/rocks`（模块会自动加入该目录的 `package.path/cpath`）。
`run_qwen35_scorer.command` 由 launchd 常驻运行，Unix socket 位于
`mohu_llm/runtime/`；没有 scorer、模型指纹不符或响应超时都会自动回到三元原序。

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

- `engine_lib` / `model` / `lexicon`：路径覆盖，默认用户目录 `mohu_llm/` 下同名文件
- `beam`：束宽（默认 200）
- `all_ranks`：>4 键时全部档位竞争（默认 true）
- `initial_quality`：原生候选质量（默认 50）。固顶候选为 100，默认 smart 候选为 5
- `long_input_length`：达到该 canonical raw 输入长度后（Rime 双拼音节之间的空格会先移除），express translator 使用不读 userdb 的 `smart_static`（默认 5）
- `personal_lexicon_namespace`：个人词 `Memory` 使用的 userdb 命名空间（LLM 方案为 `smart`）
- `personal_lexicon_max_rows`：每次同步到 native 引擎的个人多字词上限（默认 4096）

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
- 长句路径只在初始化、提交边界刷新个人词快照；个人多字词可作为 native 句图内部边，静态多字词仍保持完整输入命中语义。短输入继续由带 userdb 的 smart translator 提供个人词和常规调频。
- 这意味着长句不会完整复制 Rime userdb 的所有学习排序；快照中的提交次数会转成有限 native boost。跨候选调频的完整行为仍由短输入 Rime 路径保留。
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
