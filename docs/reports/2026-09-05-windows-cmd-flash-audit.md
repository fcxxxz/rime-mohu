# Windows 打字期 cmd 窗口闪现复核与修复报告

- 日期：2026-09-05
- 复核基线：`a882db9`（main）
- 现象：Windows 上正常打字过程中，屏幕会不定期突然弹出 cmd.exe 控制台窗口，随即消失。
- 结论：根因判断成立。**在仓库自有 Rime 运行时代码中，用户小模型快照的目录创建是唯一无需用户显式操作、可由日常提交自动到达的 shell 调用**。模型版本扫描的 `io.popen` 已在 `acb327d` 修复；皮肤编辑器仍有用户主动触发的进程启动，不属于本现象。
- 状态：已删除快照路径上的 shell 调用，并补齐发行目录与 Windows 原生快照读写契约。

## 排查方法

全仓库静态排查所有可能在 Rime 运行期（打字热路径）拉起子进程的调用：

- Lua 侧：`io.popen` / `os.execute` / `os.spawn`
- Native 侧（.cc/.h）：`system(` / `popen(` / `CreateProcess` / `WinExec` / `ShellExecute`
- 脚本侧：`powershell` / `cmd` / `curl` / `Start-Process`
- 结合 `git log -S` 追溯可疑行的引入与修复历史，区分"运行时执行"与"测试/安装器执行"。
- 对实际安装的 Weasel/Lua runtime 与 Windows CRT 调用链做二进制和源码核对，而非只凭调用名推断窗口行为。

## 根因：用户小模型快照目录创建（唯一自动提交触发点）

位置：`tiger_sentence_native/mohu_tiger_sentence.lua`。

### 调用链

以下行号指复核基线中尚未修复的代码。

1. 翻译器初始化时 `init_user_model`（`mohu_tiger_sentence.lua:724`，于 `:1166` 接入）向 context 挂接 `commit_notifier`（`:761`）。
2. 每次上屏文本含任意非 ASCII 字节，计数器 `_tiger_user_model_commits` 递增；每累计 `tiger/user_model_snapshot_interval`（默认 **64**，`:671`）次，调用 `user_model_write_snapshot`（`:774-775`）。这里不是严格的“中文”检测，emoji、全角符号和其他非 ASCII 文本也会计数；回调还会在 native 更新失败或无变化时照常计数。
3. `user_model_write_snapshot`（`:697`）在导出成功且路径含目录时，每次写盘前都调用 `ensure_snapshot_directory(directory)`（`:705`），不检查目录是否已存在。
4. `ensure_snapshot_directory`（`:688-695`）Windows 分支执行：

   ```lua
   pcall(os.execute, 'md "' .. directory .. '" 2>nul')
   ```

### 为什么会闪窗

当前正式 Windows 运行栈中，脚本运行于无控制台的 `WeaselServer.exe`。宿主 Lua 的 `os.execute` 调用 CRT `system()`；后者以 `%COMSPEC% /c` 同步启动命令解释器。实际 CRT `_P_WAIT` 路径的 `CreateProcess` flags 为 0，未设置 `CREATE_NO_WINDOW`、`DETACHED_PROCESS` 或隐藏窗口的 `STARTUPINFO`，因此 cmd.exe 没有父控制台可继承时会创建可见控制台，表现为短暂闪窗。

该结论针对当前无控制台的 Weasel 宿主；若父进程已有控制台或显式无窗口创建，则不会出现同样的新窗口。`2>nul` 只在 cmd 启动后重定向标准错误，不改变进程创建或控制台分配。目录即使早已存在，原代码仍会启动 cmd。

### 触发时机与频率

| 时机 | 代码位置 | 说明 |
|---|---|---|
| 每 64 次含非 ASCII 字节的上屏通知 | `:774-775` → `:705` → `:691` | 主要现象来源；间隔按每个 translator 生命周期计数 |
| 会话销毁时有脏数据 | `fini_user_model`（`:781`，`:787-788`） | 重新部署、切换方案、关闭会话时补写快照，同样闪窗 |

该功能默认开启（`tiger/user_model` 默认 true，`:730`），但只有 native 引擎与模型成功加载、四个用户模型 ABI 齐全、用户未关闭功能并达到计数阈值时才触发。正式 Windows runtime 的默认配置满足 ABI 条件，但方案包不含模型，因此不能表述为“普通用户全部命中”。

### 引入历史

`ensure_snapshot_directory` 与该 `md` 调用随 `18aa931` 的用户小模型功能引入，当时只用于旧 `mohu_llm_*` 方案；`d22d0e2` 将它接入当前公共方案和 Windows 扁平包。原实现没有目录存在性检查，也没有无 shell 的创建路径。

## 同路径遗漏问题：Windows 快照只能首次写入

原实现先写同目录临时文件，再调用 `os.rename(temporary, path)`。POSIX `rename` 可原子覆盖已有目标，但 Windows CRT `rename` 在目标存在时返回 `EEXIST`。实机 Lua 复现返回 `nil, "File exists", 17`；已安装实例也表现为快照文件时间不再更新，而父目录持续出现临时文件活动。

因此 Windows 原代码通常只有第一次创建快照成功，后续 interval/fini 写入都保留旧快照并删除临时文件。这不导致 cmd 闪窗的判断失效，反而解释了为何目标目录和快照早已存在时仍持续触发 `md`。本次一并修复。

复核还发现两个相关边界：标准 Lua 的 `io.open` / `os.remove` 同样使用窄字符 CRT 路径，非当前代码页可表示的 Windows 用户目录会在替换前失败；而分发构建原先整棵复制 `mohu/`，开发工作区若已有真实快照，可能把个人上屏历史带入包内。最终修复因此把读、写、清理和替换整体移入 native，并在复制与 Git 层同时排除快照状态，而不是只替换最后一次 `rename`。

## 已排除项

| 位置 | 结论 | 证据 |
|---|---|---|
| `tiger_sentence_native/mohu_runtime.lua` 模型版本扫描 | **已修复，不再是触发点** | `acb327d`（2026-09-04）删除了用 `io.popen(command)` 列目录探测版本号的逻辑；现 `resolve_model`（`:23-30`）直接返回固定路径 `mohu/model/mohu-sentence-ngram-v5.bin`，注释明确记录了不打 shell 的原因 |
| `lua/rime_skin_editor.lua:119` `io.popen("uname -s")` | Windows 上不可达 | `detected_platform`（`:112-128`）在 `:116-118` 按 `package.config` 提前返回 `"windows"`，永远到不了 popen 行 |
| `lua/rime_skin_editor.lua` 的 `os.execute`/powershell | 仅用户主动触发 | 可由 `rime_skin_editor` 选项或 `/skin`、`/pifu`、`/pfbj` 命令直接触发；不属于普通提交自动路径 |
| Rime 同步助手 / 皮肤编辑器本地服务 | 用户安装或主动启动，且同步任务隐藏运行 | 包含 `Start-Process`、Python `subprocess` / `os.startfile` 等间接进程入口，均不在打字热路径；不能从全仓进程扫描中省略 |
| QWEN scorer（`tiger_sentence_native/QWEN35_SCORER.md`） | 当前树无可执行实现 | `qwen35_scorer.py` 与旧 `mohu-llm-*` 包已在 `d22d0e2` 删除，当前仅保留历史实验文档 |
| `lua/mohu_word_order_filter.lua` 的 scorer | 进程内调用，无子进程 | 走 native 函数 `tiger.acquire_word_scorer` / `acquire_char_scorer`（`:181-318`） |
| native C++（tigerengine.cc / tigerengine_lua.cc / bench / nativetest） | 无子进程调用 | grep 无 `system`/`popen`/`CreateProcess`/`WinExec`/`ShellExecute` |
| 仓库附带的 Lua 5.4.6 `liolib.c` / `loslib.c` | 同源参考，不是 Windows 宿主实现 | Windows 脚本实际运行于 Weasel 的 Lua 5.4.8；这些文件只说明标准 Lua 原语本身不会自行触发 |
| `lua/` 其余运行时脚本（personal_lexicon、option_state 等） | 纯文件 I/O | 无任何进程调用 |
| `tests/*.lua` 的 `os.execute` | 仅测试脚手架 | 只在跑测试时执行，不随方案分发 |

## 已实施修复

目标是自动提交路径零 shell，同时不牺牲干净安装和已有快照覆盖：

1. 新增非隐藏的 `mohu/config/README.md`。当前没有方案安装器，且 `upload-artifact` 不保留空目录，所以只执行 `mkdir` 或使用默认排除的隐藏 `.gitkeep` 都不足以保证 latest 包含该目录；普通文件可穿过 source copy、artifact 和 ZIP 三层。分发复制与 `.gitignore` 显式排除 `user-ngram.snapshot` 及 `.tmp-*`，防止泄漏或覆盖用户状态。
2. 删除 `ensure_snapshot_directory` 及其调用。父目录缺失或不可写时，native 临时文件创建失败并 fail-open，不再降级调用 shell。自定义 `tiger/user_model_snapshot` 时，父目录须由用户预先创建。
3. 新增 native `read_snapshot_file` / `atomic_write_snapshot_file`：Windows 全程使用 UTF-16 文件 API，写入同目录临时文件并 flush 后用 `MoveFileExW(MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)`；POSIX 使用 `fopen` / `rename`。interval 与 `fini` 共用同一写盘函数。上一版 Windows DLL 缺少这两个接口时仍保留进程内学习，但不再退回窄字符 Lua 文件 API；更新配套 DLL 后恢复持久化。
4. Lua 测试拦截 `os.execute` 并覆盖 interval、fini、已有目标、缺父目录与精确上一版 ABI；C++ 测试在含 Unicode 的 Windows 路径两次写入含 NUL 的快照并原生回读；latest/nightly 构建及 ZIP 都显式校验 marker。

## 复检清单

将来验证"打字期零 cmd 闪窗"时，对运行时目录执行：

```bash
rg -n -g '*.lua' 'io\.popen|os\.execute' lua tiger_sentence_native
```

预期只剩：`mohu_runtime.lua` 中说明已删除旧 `io.popen` 的注释，以及 `rime_skin_editor.lua` 的 Windows 不可达 `uname` 探测和用户主动启动入口；`mohu_tiger_sentence.lua` 为 0。该扫描只证明仓库内 Lua 直接调用，不能替代对 PowerShell/Python 间接进程入口或外部软件的审计。

## 验证结果

- Lua 用户模型回归通过：interval、`fini`、已有快照、缺父目录和上一版 Windows ABI 均未调用 shell。
- Windows MSVC 原生测试通过：Unicode 目录下连续覆盖含 NUL 快照、原生回读、失败保持旧目标且无临时文件残留。
- 临时静态 Lua 5.4 binding 探针通过：从真实 Lua binding 调用 Unicode/二进制读写及失败返回，退出码 0；探针产物未纳入仓库。
- 18 个相关 Python 测试通过；两套 flat package 实际生成后均含 marker 且不含快照；Ruff、四个 YAML 文件解析和 `git diff --check` 通过。
- 本机没有仓库要求的 GNU make/macOS Mira 工具链，未运行完整 `make test`；native Windows 源码和 Lua binding 已分别编译，完整 Windows DLL smoke 由更新后的 CI 执行。
