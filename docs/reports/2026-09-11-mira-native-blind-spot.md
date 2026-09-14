# mira 跑不到 native：三个宿主缺口、一条被掩盖的排序回归

日期：2026-09-11
关联：[个人词库刷新卡顿：根因与修复](2026-09-11-personal-lexicon-refresh-lag.md)

## 1. 结论

`tests/mohu_zrm.test.yaml` 的 110 条用例里有 4 条长期失败。逐条排查后发现，
**mira 实际上从来没有真正跑起 native 句法引擎**——它跑的是引擎缺席时的
fail-open 回退路径。由此产生两个方向相反的失真：

- **假失败**：`automatic_word_learning::vsmc` 在 mira 默认配置下失败，但把
  native 通道补全后**立即通过**。它不是产品缺陷，是环境产物。
- **假通过**：`default::jiivo` 在 mira 默认配置下通过，但在 native 通道可用的
  条件下（也就是 Squirrel 的真实条件）返回 `既拙`，与用例期望的 `纪` 不符。
  **这是一条被 mira 掩盖的生产相关回归。**

顺带查出一个打包缺陷：`tools/build_flat_dist.py` 从来没有把
`libonnxruntime.1.dylib` 拷进 macOS 分发包。它正是「native 通道缺席」的直接
原因之一，本次一并修复。

## 2. 为什么 mira 里 native 从不生效

三个缺口叠加，任何一个都足以让 native 通道静默 fail-open：

| # | 缺口 | 日志现象 | 状态 |
|---|---|---|---|
| 1 | mira 链接 Homebrew Lua 5.5，而发行版 `libtigerengine.dylib` 按 Squirrel 的 Lua 5.4.6 ABI 编译 | `luaopen failed: version mismatch: app. needs 504.0, Lua core provides 505.0` | 已知，`tiger_sentence_native/README.md` 有记录 |
| 2 | `dist-*` 分发包缺 `libonnxruntime.1.dylib` | `dlopen(...): Library not loaded: @rpath/libonnxruntime.1.dylib` | **打包 bug，本次修复** |
| 3 | `dist-*` 不随包发 ngram 模型（CI 明确断言不发） | `engine create: char model: cannot open .../mohu-sentence-ngram-v5.bin` | 设计如此 |

`tests/mohu_zrm.test.yaml` 用 `source_dir: ../dist-zrm`，所以 mira 的宿主正好是
这三个缺口同时成立的环境。缺口 1 一旦解决，缺口 2 才会暴露出来（顺序上：
Lua ABI 先拦下 `luaopen`，onnxruntime 次之，模型最后）。

`make tigerengine-native` 把 `tiger_sentence_native/libonnxruntime.1.dylib` 列为
前置依赖，构建期是硬要求；但打包脚本只拷了引擎本身，README 描述的
「`libonnxruntime.1.dylib` 随引擎放在 `mohu/runtime/`」并没有落地。CI 的
`Validate flat packages` 步骤断言了 `libtigerengine.dylib` / `.dll` /
`runtime-preload.txt`，唯独没有断言 onnxruntime，所以这个包能一路绿着发出去。

## 3. 实测对照：native 关闭 vs 开启

用同一份 `tests/mohu_zrm.test.yaml`，只在「native 通道是否可用」这一个变量上
做对照。构造方法见 §6。

| 用例 | native 不可用（mira 默认） | native 可用 |
|---|---|---|
| `curated_short_codes_multi::mulo` | FAIL：`幕` / **`幕落`**（期望 `暮` ⚡️） | FAIL：同左 |
| `cross_candidate_order::mo{Down}{space}rj{space}yrug` | FAIL：**`原生`**（期望 `原声`） | FAIL：同左 |
| `default::yuviyy` | FAIL：**`於之莹`**（期望 `于之莹`） | FAIL：同左 |
| `default::jiivo` | PASS：`纪` | **FAIL：`既拙`**（期望 `纪`） |
| `automatic_word_learning::vsmc` | FAIL：**`种苗`**（期望 `柊杪`） | **PASS** |
| 失败总数 | 4 / 110 | 4 / 110 |

「失败总数不变」具有欺骗性——集合变了。这正是只看总数会漏掉回归的地方。

## 4. 两条结论的定性

### 4.1 `vsmc`：假失败，可以删掉这条担心

`automatic_word_learning` 的第三条断言依赖 native 的 `tiger/user_model` 通道：
先上屏 `柊杪`，再查 `vsmc`，期望学习后的词被顶到首位。native 缺席时学习不生效，
返回 `种苗`。补全 native 后通过。

**结论**：不是产品缺陷。要验证它，mira 必须能跑 native；否则这条用例应当按
「需要 native 宿主」标记跳过，而不是留一个长期红点。

### 4.2 `jiivo`：被掩盖的生产相关回归

`既拙` **不在任何码表里**（`grep '^既拙'` 无命中），所以它是 native 句法引擎
现场拼出来的二字组合；`纪` 则在 `mohu_zrm.chars.dict.yaml` 里有 `ji;iv` 码、
词频 335852。native 可用时引擎把 `jiivo` 解成 `既拙` 并排到首位，压过了
高词频单字 `纪`。

这条与本次卡顿修复无关：我的改动只影响 `apply_personal_parsed`（个人词库刷新），
而 mira 的 userdb 是全新的、没有个人词，走不到那条路径。

**结论**：需要在 Squirrel 里复验一次（mira 的 native 可用态是我用 Lua 5.5 变体
构造的，不是发行二进制），确认 `jiivo` 在真实宿主下是否也出 `既拙`。若是，这属于
native 句法排序问题，落在 `cross_candidate_order` / 词序那条线上，动它之前要先读
`docs/knowledge/cross-candidate-ordering.md`。

### 4.3 另外两条：与 native 无关

- `cross_candidate_order`：`docs/knowledge/cross-candidate-ordering.md` §5.7
  已记录「在 HEAD 即失败，与词级重排无关，已在干净提交复现」。本次实测在
  native 可用态下同样失败，与 §5.7 一致。
- `default::yuviyy`：码表权重冲突。`mohu_zrm.base.dict.yaml` 里
  `于之莹  yu;fn vi;ri yy;lw  1`，而 `mohu_zrm.wanxiang.dict.yaml` 里
  `於之莹  yu;lj vi;ri yy;lw  20`。两者裸码同为 `yuviyy`，繁体形权重 20 压过
  简体形权重 1。属于词条/权重取舍，不是引擎问题。
- `curated_short_codes_multi::mulo`：**已于同日修复**，见 §8。原始症状是期望
  `cand[2] = 暮 ⚡️`、实测 `cand[2] = 幕落`；根因是 `幕`/`暮` 在字表里共用
  `mu;lo` 且 `幕` 简频更高（186169 vs 32619），加上「暮 = 次级简码」那条分配
  在源码里已丢失（只剩过期产物里有），并非 `four_code_char_yield_exempt`
  的语义问题。

## 5. 打包修复（已落地）

- `tools/build_flat_dist.py`：新增 `MACOS_ENGINE_DEPENDENCIES`，在拷贝
  `libtigerengine.dylib` 之后把依赖一并放进 `mohu/runtime/`。依赖缺失时**显式
  报错**，不再发出一个装不上的包。
- `.github/workflows/build.yml`：`Validate flat packages` 增加
  `dist-zrm` / `dist-flypy` 的 `mohu/runtime/libonnxruntime.1.dylib` 断言。
- `tests/test_flat_distribution.py`：新增
  `test_flat_packages_ship_macos_engine_and_its_dependency`。

验证：

```
uv run python tests/test_flat_distribution.py -v     # 8 tests OK (skipped=1)
# 缺依赖时的报错：
# ValueError: macOS runtime is missing libonnxruntime.1.dylib;
#   libtigerengine.dylib resolves it via @loader_path (see tiger_sentence_native/README.md)
```

## 6. 复现方法

要让 mira 真正跑到 native，需要同时补齐 §2 的三个缺口。前两个是构建期的事：

```zsh
# 1) 按宿主 Lua（Homebrew 5.5）重编一份测试用 dylib，只用于 mira，不上线
cd /Users/fuchuxuan/PycharmProjects/rime-mohu
mkdir -p /tmp/lua55
sed 's|"lua-5.4.6/src/lua.hpp"|<lua.hpp>|' \
  tiger_sentence_native/tigerengine_lua.cc > /tmp/lua55/tigerengine_lua.cc
clang++ -O2 -std=c++17 -dynamiclib \
  tiger_sentence_native/tigerengine.cc /tmp/lua55/tigerengine_lua.cc \
  -I tiger_sentence_native -I /opt/homebrew/include/lua \
  -undefined dynamic_lookup \
  -I/opt/homebrew/opt/onnxruntime/include/onnxruntime \
  -L tiger_sentence_native -lonnxruntime.1 -Wl,-rpath,@loader_path \
  -framework Accelerate -o /tmp/lua55/libtigerengine.dylib
codesign --force --sign - /tmp/lua55/libtigerengine.dylib

# 2) 铺进 mira 的 source_dir（dist-* 是 gitignore 的构建产物）
cp /tmp/lua55/libtigerengine.dylib dist-zrm/mohu/runtime/libtigerengine.dylib
ln -sf ~/Library/Rime/mohu/model/mohu-sentence-ngram-v5.bin \
  dist-zrm/mohu/model/mohu-sentence-ngram-v5.bin

# 3) 跑（模型 573MB，mira 每轮要拷进暂存目录，整轮约 4 分钟）
/opt/homebrew/bin/mira -C /tmp/mira-cache-full tests/mohu_zrm.test.yaml
```

跑完**务必还原** `dist-zrm/mohu/runtime/libtigerengine.dylib` 与
`dist-zrm/mohu/model/`，别把测试变体留在 dist 里。

其他环境注意：

- mira 的暂存目录固定在 `$TMPDIR/mira`，**不能并行跑两个 mira 实例**，否则会
  出现 `filesystem error: in remove_all` 之类的互踩崩溃。
- mira 0.2.0，链接 `/opt/homebrew/opt/lua/lib/liblua.5.5.dylib` 与
  `/opt/homebrew/opt/librime/lib/librime.1.dylib`。

## 7. 未决

1. `jiivo` 需要在 Squirrel 里复验。若确认 `既拙`，是 native 排序问题。
2. ~~`yuviyy` 的 `於之莹` / `于之莹` 权重取舍需要作者拍板~~ —— 作者已决定不处理
   （不是常用词）。
3. ~~`mulo` 的 `幕落` 是否应让位给 `暮`~~ —— 已按方案 B 落地，见 §8。
   剩下一个小问题：`mulo` 上 `幕` 会输给词 `幕落`（`幕` 不在
   `four_code_char_yield_exempt` 里）。要不要把 `幕` 也加进豁免，待定。
4. **是否给 mira 加一个正式的 native 宿主通道**：把上面的 Lua 5.5 变体构建做成
   一个 make 目标，让 native 相关用例真的被测到。否则 mira 会继续给出
   方向相反的信号。

## 8. 追加（同日）：`mulo` 按方案 B 落地

决定：`mulo` 归 `暮`，`幕` 让位到 `mulr`；实现方式为**交换简频**。

**只动一个源文件**：`tools/data/chars.txt`（`char / pinyin / trad_freq / simp_freq`）。

| 行 | 改前 | 改后 |
|---|---|---|
| `幕	mu` | 1443 / **186169** | 1443 / **32619** |
| `暮	mu` | 1214 / **32619** | 1214 / **186169** |

只动简频列；trad 列不动（那是另一套数据）。

**为什么 `mu` 前缀不受影响**：`mohu_pinyin.dict.yaml` 由
`tools/build_pinyin_reverse.py` 从 `tools/data/pinyin_simp.txt` 生成，
**与 chars.txt 不同源**，所以 `mu` 前缀的候选序仍是 幕 > 暮。

**生成链**：`make chars fixed_tiger` → `uv run tools/build_flypy_assets.py`
→ `make mohu_lexicons`。`mohu_zrm.chars.dict.yaml` / `mohu_flypy.chars.dict.yaml`
头部标着 AUTO-GENERATED，不可手改。动手前先验证过 `gen_chars.py` 的输出与工作区
**逐字节相同**（除 version 行），确认重生成不会覆盖手工编辑。

原生词典必须跟着走：`build_mohu_lexicons.py` 的单字 reading_freq 直接取自字表，
字频一改词典就过期。实测两个 lexicon **各只 +20 行**（就是 幕/暮 的各码行），
无额外抖动；`mohu_tiger.lexicon.txt` 是前置依赖而非产物，未被改写。

**结果（mira 实测）**：`mulo` → `暮` ⚡️、`mulr` → `幕`；
全套 **4/110 失败 → 3/111 失败**，无新增回归。

**连带变化（生成器级联，需知悉）**：

- `tools/data/tiger_compatibility_chars.txt`（救援/兼容字表）：**幕进、暮出**
  → `幕 mu;lr`/`mu;lm` 由 0 → 32619、`暮 mu;lm` 由 32619 → 0，
  于是 **`mulm` 从暮翻成幕**（测试未覆盖）。
- 意外收获：`暮 mulo 186169` 现在出现在 `*_fixed_legacy` 里，
  §4.3 提到的「丢失的暮 mulo」顺带解决。
- `mulo` 的 `cand[2]` 是 `幕落`（词）——`four_code_char_yield_exempt` 只列了暮，
  幕 不在豁免名单里，所以让位给词。要不要把幕也加进豁免，待定。

**测试**：`tests/mohu_zrm.test.yaml` 的 mulo 期望改为 `cand[1] = 暮` ⚡️，
并新增 `mulr` → `cand[1] = 幕`。
