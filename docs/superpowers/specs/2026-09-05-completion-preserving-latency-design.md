# 保留补全的输入延迟与启动优化设计

## 背景

当前卡顿由两条独立路径叠加：

1. `script_translator` 的补全流可在单键输入时产生数千个候选，两个 Lua filter
   会在第一次 `yield` 前拉取过多候选；
2. native TCSKNM02 模型虽然使用 mmap，但启动校验会访问几乎全部 573 MB
   文件页，导致冷启动 I/O、约 600 MB 工作集和 Weasel 初始化阻塞。

现有 `fix/input-latency-completion` 分支通过关闭补全和提前进入流式状态降低延迟，
但关闭了用户需要的音节/词语补全；流式状态还会重复延迟候选、过早过滤 native
候选并改变候选顺序。

## 已确认约束

- `smart` 与 `smart_static` 必须保留 `enable_completion`。
- `enable_word_completion` 也必须保留，不能借由配置继承被意外关闭。
- 万象词库继续作为 extended 词库的一部分，不拆成可选包。
- 正常规模候选流的 fixed、pinned、native、smart 与 punct 行为必须保持兼容。
- 超大候选流可以放弃本轮重排，但必须保留所有候选、原始顺序和用户可见注释。
- 损坏模型在运行时不得造成越界读取；完整深度校验不再默认阻塞每次启动。

## 方案比较

### 方案 A：关闭补全

延迟收益最大且实现简单，但直接违反产品约束，并会同时关闭 librime 的
`enable_word_completion` 默认继承。拒绝。

### 方案 B：按候选类型持续流式重排

可以尽早产出候选，但 filter 无法在上游结束前知道后续是否还有 fixed、punct 或
能为 3/4 字 native 候选提供词库覆盖的 smart 候选。当前分支的重复、丢候选和
顺序回归正来自这个信息不足。拒绝继续修补这一状态机。

### 方案 C：有界预取，超预算原序直通

先以候选数和短时间预算预取。若在预算内到达 EOF，使用原有完整状态机，保持现有
排序语义；若超过预算，放弃本轮重排，按上游顺序输出已取候选并继续同一个 iterator。
该方案把最坏同步工作限制在固定范围，同时让异常路径 fail-open。采用此方案。

## 设计

### 1. `mohu_reorder_filter` 两阶段处理

filter 在第一次输出前只执行原始候选预取，不运行匹配状态机，也不修改候选：

1. 从 `t_input:iter()` 保存唯一的 `advance/state`；
2. 最多把 `mohu/reorder_scan_budget` 个非 nil 候选放进 buffer；
3. count 尚未命中且时间尚未到期时，再调用一次 `advance(state)` 作 sentinel：返回
   nil 表示 EOF，返回候选表示超预算；因此 count 路径最多调用 `budget+1` 次；
4. 若观察到 EOF，把 buffer 包装成 list-backed iterator，运行原有精确状态机；
5. 若 sentinel 非 nil，把它保存在 buffer 末尾；若时间在调用 sentinel 前到期，则
   不额外消费候选。两种情况都跳过匹配/native 抑制，按原序输出 buffer，再继续原
   `advance(state)`；不得重新调用 `t_input:iter()`。

每次 `advance(state)` 都使用 `pcall`。iterator 抛错或返回非候选值时，已消费的
buffer 仍按原序输出，然后记录一次错误并终止该轮 translation；上游 iterator 已经
失败，无法承诺恢复尚未读取的候选。nil 只表示正常 EOF。

预算回退仍调用只做展示规范化的 emitter，把内部 `` `F`` 注释恢复为
`mohu/quick_code_indicator`。除此之外不替换、不删除、不重排候选。这样下游
`mohu_word_order_filter` 不会把 fixed 候选误认为可重排词。

默认候选上限应大于既有 `reorder_threshold=50`，初值采用 64；默认时间预算采用
4 ms。时间源优先使用 `rime_api.get_time_ms()`；读取失败时回退
`os.clock()*1000`，两者都失败时关闭本轮时间判断，只保留 count 硬上限。时间检查
只发生在两次 `advance` 之间，不能中断单次上游调用；因此“有界”严格指候选拉取
次数及最多 64 个候选上的状态机工作，不承诺任意第三方 iterator 的墙钟上限。

精确状态机恢复 `origin/main` 行为，不保留当前分支的 `streaming`、
`enter_streaming` 或部分 `lexicon_texts` 判定。处理 `delay_slot` 时先从 context
中摘除待处理列表，避免同一候选同时存在于 delay 与 smart 两个集合。

### 2. `mohu_word_order_filter` 有界评分

保留 `word_order_scan_budget`，但把它定义为 fail-open 边界：

- 在取得完整的目标 block 或 EOF 之前命中预算时，原序输出 prefix、block 和剩余流，
  不调用 native scorer；
- 在预算内取得完整 block 时，沿用现有评分、稳定排序和尾部流式输出；
- 在预算内到达 EOF 且 block 少于配置上限时，仍可对已有的两个以上候选评分；
- scorer 获取移动到有界预取和槽位检查之后，避免无可评分候选时进入 native 路径；
- `acquire_char_scorer` / `acquire_word_scorer` 只返回已经存在的 handle，不允许由
  filter 触发 engine create。native translator 的既有 eager init 仍负责创建 handle。

默认 count 上限继续为 `word_order_candidates * 3`，最大 1000。加入同一 4 ms
协作式时间预算；任一预算命中即原序直通。native scorer 是不可抢占的同步调用，
不计入 Lua 预算保证；其输入继续限制为最多 20 个候选，并以现有 native 基准单独验收。

### 3. 补全配置

只撤销提交 `136937a` 在下表节点新增的 `enable_completion: false`，不得修改 fixed、
custom phrase、reverse lookup 等节点既有值：

| 文件 | 节点 |
|---|---|
| `mohu_zrm.schema.yaml` | `smart`、`smart_static` |
| `mohu_flypy.schema.yaml` | `smart`、`smart_static` |
| `mohu_zrm_core.schema.yaml` | `smart`、`smart_static` |
| `mohu_flypy_core.schema.yaml` | `smart`、`smart_static` |
| `mohu_zrm_sentence_core.schema.yaml` | `translator`、`translator_static` |
| `mohu_flypy_sentence_core.schema.yaml` | `translator`、`translator_static` |

这些节点显式配置：

```yaml
enable_completion: true
enable_word_completion: true
```

显式配置避免未来 librime 默认值或继承关系变化。compile-only sentence schema 的
对应 translator 也保持同样语义，防止旧用户直接启用内部方案时行为分叉。部署后
检查 `build/<schema>.schema.yaml`，并以不完整音节及完整词前缀分别确认音节补全和
词语补全仍产生候选。

### 4. native 模型单映射与按需页访问

`MappedFile` 必须严格表达所有权：

- `open()` 与 `set_view()` 先释放已有 owner；
- borrowed view 不执行 `UnmapViewOfFile`/`munmap`；
- release 后清空指针、大小、mapping handle，并恢复默认 owner 状态；
- model loader 失败时立即释放其映射。

`tiger_engine_create` 首次映射模型后按 magic 分派。通用 owner 通过 move assignment
原子移交；移交后源对象立即为空，目标 `load_mapped()` 失败时自行 release：

- `MHCTN01` 保留 container owner，字符层和词层使用 borrowed view；
- `TCSKNM01/02` 把 owner 移交给 `KnModel`；
- `MHKNM01` 把 owner 移交给 `WordModel`；
- 未知格式直接失败。

Engine 中 container 声明在 model/wm 之前，析构时 borrowed view 先销毁、owner 最后
销毁。MHCTN01 的字符层损坏仍拒绝整个容器；可选词层损坏沿用现状，降级为字符层。
“单映射”仅指 primary V5：显式 `word_scorer_model`、`MH_BLEND` 和容器自身声明的
独立资源不计为重复探测映射。

这样普通 V5 路径只保留一个模型映射，不再经历 container、WordModel、KnModel
三次探测并遗留失败视图。

TCSKNM02 默认加载只验证 header、section、计数和索引：

- `header_size == 104`、版本和文件大小必须精确匹配，所有乘加先做溢出检查；
- 非零 context 的 `index_count` 必须等于 `ceil(context_count/index_stride)`，两者为零
  时允许空 section；
- section 必须满足 `header <= unigram <= bi_blocks <= bi_index <= tri_blocks <=
  tri_index <= file_size`，且每段 payload 不与下一段重叠；
- 索引 key 必须非递减；重复 key 维持现有“二分取最后一页”语义，不新增格式限制；
- page offset 必须位于真正的 block 区间并至少容纳 16 字节 record；格式没有已声明
  的页对齐约束，因此不额外要求对齐；
- bi block 截止 `bi_index_off`，tri block 截止 `tri_index_off`；
- `index_offset + page*16`、record span 与 successor span 全部先用除法或减法验证，
  再做指针加法，禁止依赖溢出后的比较；
- 不在 create 阶段遍历所有 context/successor block。

现有 `KnModel::mobile_lookup` 已在每个 record 和 successor 数组访问前做减法式边界
检查；本次审计并统一缓存命中和所有跳转路径的同等检查。`CtxResult` 显式区分
`missing` 与 `invalid`：普通缺失仍走回退概率；损坏页把 page id 写入 bi/tri 各自的
invalid-page cache，并向上返回 invalid。`logp` 将 invalid 转成非有限结果，当前 native
decode 错误路径据此终止本轮并由 Lua 回退 smart 候选；绝不把损坏数据静默当成正常
missing，也绝不越界。缓存只在 page id 已由索引边界检查确认后写入。

完整 `validate_pages` 保留为显式严格模式，使用
`MOHU_TIGER_STRICT_VALIDATE=1` 开启；仅字面值 `1` 启用，未设置、空值和其他值均
关闭。该模式供发布校验、诊断和安全测试使用，默认运行时不启用，以保证 mmap
保持按需分页。此次只改变 TCSKNM02 字符模型；MHKNM01 词模型继续执行既有完整
验证，MHCTN01 内的可选词层因此也可能全扫。默认 V5 不含词层，不影响本次目标。

### 5. 低风险启动期惰性构造

- 保留 `mohu_contextual_translator` 对 `smart_static` 的预建。简单删除预建只会把
  constructor 成本搬到第一次达到 `long_input_length` 的按键，违背逐键延迟目标；
  只有具备真正的 idle/background 调度后才应调整它。
- candidate manager 的 processor/translator 初始化时只保存 Memory namespace；`h`
  查询需要判断永久用户词、`u` 查询需要枚举用户词，以及 `h/u` 删除动作真正读写
  用户词时才调用各自的 `ensure_memory()`。其余导航、pin、普通隐藏与调序不创建。
- candidate override processor 仅在 Ctrl+0 重置学习权重或 Shift+Delete 判断/永久
  删除用户词时调用 `ensure_memory()`；普通隐藏、恢复和候选移动不加载它。

每个 env 记录一次创建尝试，避免 Memory 不可用时每个按键反复构造。创建失败时，
需要 Memory 的动作显示现有风格的“无法连接用户词典”提示并停止，不把永久删除降级
成普通隐藏。显式 refresh 可清除 attempted 后重试；`fini` disconnect 已创建对象并
清除 object/attempted/namespace，新 schema env 从未尝试状态开始。各组件仍持有并
释放自己的 Memory，不共享 wrapper，因为 iterator 状态、notifier 和 disconnect 生命周期
属于组件自身。

个人词初始全表扫描和全局锁拆分暂不与本批实现混合。前者需要持久化 payload 或带
top-K 的可暂停扫描才能同时保持 4096 行及“首次 native 解码已加载个人词”的语义；
后者需要并发生命周期测试。两者在模型全页扫描移除后不再是当前最大瓶颈，应作为
独立后续变更。

## 错误处理

- Lua 预算命中不是错误，不记日志；候选按上游顺序完整直通。
- iterator 报错时输出已消费 buffer、记录一次错误并结束；计时器报错时关闭时间预算、
  继续依赖 count 上限；scorer 报错时沿用既有 fail-open，且不重新启动 iterator。
- 模型 header/index 错误仍在 create 阶段失败并返回具体错误。
- 模型深层页损坏在默认模式下让本轮 native decode 失败并回退 smart；严格模式在
  create 阶段拒绝。
- format dispatch 或映射失败不得留下 mapping handle 或半初始化 handle。

## 测试与验收

### Lua 回归

- coroutine 驱动的首次 `yield` 测试，记录 `advance` 次数；
- `budget-1`、`budget`、`budget+1` 三个边界；
- 超预算路径按对象 identity、type、text、comment 保持原序；
- iterator 抛错、异常返回和计时器报错遵循已定义的终止/降级语义；
- `` `F`` 在回退路径仍转换为公开提示；
- pinned + delayed smart 不重复；
- 3/4 字 native 在后续 smart 覆盖存在时不丢；
- punct、fixed、native、smart 的短流输出与 `origin/main` 等价；
- word-order 预算命中不调用 scorer，block 完整时仍调用且正确重排；
- 两个 filter 串联时不丢候选、不重复、不把 fixed 送入词级评分；
- 对拍还覆盖 genuine wrapper、preedit、quality 和原 candidate object identity；
- 配置矩阵逐节点断言上述六个 schema 的两种 completion 均为 true，其他既有
  `enable_completion: false` 不变；extended 字典仍导入 Wanxiang。

### Native 安全与格式

- header/index 损坏仍在 create 阶段拒绝；
- successor 越界模型在默认模式下 create 不全表扫描，实际 lookup 不崩溃、不越界；
- 同一损坏模型在严格模式 `1` 下 create 拒绝，未设置/空值/`0` 时走惰性路径；
- TCSKNM01、TCSKNM02、MHKNM01、MHCTN01 分派与既有结果一致；
- load 失败、重复 load、free 后不残留 owner 映射；
- 容器字符层损坏拒绝、可选词层损坏降级；
- Windows 与 POSIX 上 borrowed view 析构不解除宿主映射，owner 只解除一次。

### 性能验收

使用真实 573,052,280 字节 V5 模型和安装版 Windows DLL。固定同一 Windows/Rime
版本、同一模型哈希和同一 lexicon；每个样本使用新进程，先做一次不计入结果的
warm-up，再记录至少 5 次中位数。用 `GetProcessMemoryInfo`/page-fault counter 和
`VirtualQueryEx` 记录工作集、缺页及映射。模型加载与 17 万行
lexicon 解析分别计时，避免把两段成本混成一个不现实的绝对目标：

- 真实模型 + 一行 lexicon 的 warm create 从当前约 196 ms 降低至少 80%，目标
  50 ms 内；
- 同一隔离口径下 page faults 从约 126,000 降到 2,000 内，模型相关工作集增量
  低于 30 MB；
- 真实模型 + 真实 lexicon 的全引擎 create 不要求低于 50 ms，但不得再包含接近
  573 MB 的模型顺序读；与 tiny-model + real-lexicon 基线相比只允许小幅固定开销；
- 单 handle 只保留一个约 573 MB 虚拟映射；free 后归零；
- 保留 completion 的 240 键同会话基准在相同语料、候选页大小和冷/热口径下运行
  3 个会话并合并逐键样本，不得再出现整流抽干，P99 目标低于 50 ms；
- 正常有限候选流的输出与 `origin/main` 按 type/text/preedit/comment 对拍一致。

## 非目标

- 不删减、拆分或改成可选的万象词库。
- 不关闭音节补全或词语补全。
- 不在本批重训、剪枝或量化模型。
- 不引入独立 worker 进程或修改 Weasel/TSF。
- 不把 `smart_static` 的构造成本转移到第一笔长输入。
- 不改变个人词在首次 native 解码前已完成装载的既有语义。
- 不在本批修改 `base_codes` 个人词回滚存储；其基线去重需独立设计和测试。
- 不在没有并发测试的情况下重写全局引擎锁和 Lua binding 锁。
