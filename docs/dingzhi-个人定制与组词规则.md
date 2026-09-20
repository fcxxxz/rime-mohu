# 个人定制与组词规则（魔虎整句方案）

面向使用魔虎·自然码（`mohu_zrm`）或魔虎·小鹤（`mohu_flypy`）整句方案的用户。说清三件事：

1. 什么样的编码能参与组词组句；
2. 想加字、加词、加打法时，应该改哪个文件；
3. 哪些个性化会自动保存，哪些必须手动改文件。

文中候选行为均经 mira 实测（2026-09-20，方案版本 20260829；测试环境未加载 ngram 模型，真机位次可能略有差异，候选存在性不受影响）。示例以自然码为主，小鹤音节不同、机制完全一致。

## 一、组词组句只认「音节」

整句引擎的查询顺序是：**输入串 →（按方案的双拼+辅码规则）切成音节 → 到词典里取字词 → 组句**。词典里的编码结构固定为 `双拼;辅码`，例如自然码单字表中：

```
命	my;jd	1368062
命	my;jz	0
```

构建时由拼写运算（`mohu.yaml` 的 `generate_code`）从 `双拼;辅码` 派生出实际可输入的形式：

| 输入形式 | 例子（命） | 含义 |
|---|---|---|
| 双拼 | `my` | 纯双拼，参与构词 |
| 三码 | `myj` | 双拼+第一辅码，参与构词 |
| 全码 | `myjd` | 双拼+完整辅码 |
| 全码后缀 | `myjdo`、`myjd/` | 显式全码标记 |
| 三码后缀 | `myj/` | 三码筛单字（实验性） |

因此这些输入今天就能出词（实测）：

| 输入 | 候选 |
|---|---|
| `myly` | 1.命令 |
| `myjly` | 1.命令 2.命 |
| `myjd` | 1.命 |

「命令」本身在基础词库里按音节序列 `my;jd ly;jk` 存储，所以任何能拼出这两个音节的输入形式都能命中它——**组词组句的粒度是音节，不是字**。

## 二、为什么"手动加一个字"不能组词组句

方案里有三条互不相通的查询管线：

| 管线 | 数据源 | 查询方式 | 参与组词组句 |
|---|---|---|---|
| 整句引擎（smart） | `mohu_zrm.extended`（chars/base/words/tencent 等汇总） | 按音节切分 | ✅ |
| 字词码表（fixed） | `mohu_zrm_fixed.dict.yaml` | 整码精确匹配（`enable_sentence: false`） | ❌ |
| 自定义短语（custom_phrase） | `mohu_zrm_custom_phrases.txt` | 整码精确匹配 | ❌ |

给一个字手写一条裸码（比如在码表里加 `命 mij`），只会让**打完整码时单独出这个字**，永远不能拼上后面的输入，原因有两层：

1. **裸码不是合法音节拼写。** `mij` 不对应「命」在任何双拼下的音节（自然码 `my`、小鹤 `mk`），切分这一步就断了。
2. **就算把字塞进单字表也组不了词。** 实测给 chars 表加伪同音条目 `命 mi;jd` 后：`mij` 能出「命」，但 `mijly` 只出「命」不出「命令」——词表里的「命令」按 `my;jd + ly;jk` 存，伪音节匹配不上，而引擎也不会拿「单字+单字」现场拼双字词。`myjly` 能出「命令」靠的是词表直命中。

## 三、常见需求对照表

| 需求 | 正确入口 | 参与组句 | 需重新部署 | 自动记录 |
|---|---|---|---|---|
| 加常用词 | `mohu_zrm.words.dict.yaml` 第一块末尾（无码加词） | ✅ | ✅ | ❌ 手动 |
| 给字加新辅码（拆分） | 仓库源数据 `tools/data/mohu_chai.txt`（保留旧拆分）+ `make all` | ✅ | ✅ | ❌ 手动 |
| 个人速记短语 | `~/Library/Rime/mohu_zrm_custom_phrases.txt`（`文字<Tab>编码<Tab>权重`） | ❌ | ✅ | ❌ 手动 |
| 个人拼写别名（见下节） | `~/Library/Rime/mohu.custom.yaml` | ✅ | ✅ | ❌ 手动 |
| 候选调频 | 打字学习，存 `*.userdb` | — | 不需要 | ✅ 自动 |
| 候选置顶/沉底 | 候选管理模式（开关 `candidate_override_management`） | — | 不需要 | ✅ 自动 |

说明：

- 安装包用户改 `mohu_zrm.words.dict.yaml` 不需要跑 make——把词加进用户目录里这份词表、重新部署即可（部署会重新编译词典）。但注意**重新从发布包覆盖安装会连同你的词表改动一起覆盖**，覆盖前记得备份合并；`mohu.custom.yaml` 和 `*.userdb` 不在发布包里，不受影响。
- 自定义短语文件随发布包分发（内容为空、只有注释头），覆盖安装同样注意备份。

## 四、个人拼写别名：给熟悉的打法加个别名

**场景**：你习惯把「命」打成 `mij`（实际音节是 `my`），希望 `mij` 出「命」、`mijly` 出「命令」。这和方案内置的飞键（`wz→wk`、`ju→jv` 等）是同一机制——音节层别名。

**做法**：在 Rime 用户目录（macOS 为 `~/Library/Rime`，与 `mohu.yaml` 同级）新建 `mohu.custom.yaml`：

```yaml
patch:
  algebra/user_sentence_bottom:
    __append:
      - derive/^myj/mij/
```

重新部署后实测效果：

| 输入 | 打补丁前 | 打补丁后 |
|---|---|---|
| `mijly` | （空） | **1.命令 2.命** |
| `mij` | 1.秒出警 | 1.秒出警 2.命 |
| `mi` | 1.米 2.密 3.迷… | 不变（无污染） |
| `myly` / `myjly` | 1.命令 | 不变（无回归） |

### 写法要点（都是踩过的坑）

1. **必须挂在 `algebra/user_sentence_bottom`**。拼写运算的顺序是 `user_force_top → user_sentence_top → 飞键 → generate_code → user_sentence_bottom → user_force_bottom`。挂在 `generate_code` 之前会把裸音节也别名过去（`mi` 的候选里混进「命」）；挂在之后只别名三码/全码形态。
2. **必须用 `__append` 的 map 形态**，直接给节点赋裸列表规则会被静默忽略。
3. 规则是正则前缀替换：`derive/^myj/mij/` 把所有 `myj` 开头的拼写（`myj`、`myjd`、`myjdo`…）克隆一份 `mij` 开头的。锚定 `^` 更安全。
4. **作用域是音节级，做不到字级**（Rime 架构里没有字级拼写）。但实际可见范围很小：`my;j*` 音节的持有者只有「命」（高频）和几个零频生僻字（被常用字过滤挡住、不可见）；受影响的词恰好就是以「命」开头的词。想收窄到全码（`derive/^myjd/mijd/`）也可以，但那样三码 `mijly` 就失效了。
5. **只对整句方案生效**。`user_sentence_*` 挂载点明确不含字词模式；给单个方案打补丁（`mohu_zrm.custom.yaml` 的 `speller/algebra/+`）实测无效，不要走那条路。

小鹤用户按小鹤音节另写规则即可（例如「命」是小鹤 `mk;j*`，写成 `derive/^mkj/...`）；自然码规则对小鹤是空操作（小鹤没有 `my;*` 音节），两个方案共用同一个 `mohu.custom.yaml` 互不干扰。

## 五、别名规则的日常维护

**输入法不会自动写这个文件。** Rime 的 `*.custom.yaml` 是部署期只读配置，运行期唯一会自动写入的是 `*.userdb` 类数据（调频、候选管理、顶置）。所以以后想再给别的字加打法，就是：

```yaml
patch:
  algebra/user_sentence_bottom:
    __append:
      - derive/^myj/mij/    # 命 → mij
      - derive/^mk/mik/     # 下一个别名，追加一行即可
```

改完重新部署一次（Squirrel 菜单「重新部署」，或命令行 `"/Library/Input Methods/Squirrel.app/Contents/MacOS/Squirrel" --reload`）。删除对应行（或删掉整个文件）再部署即回滚。

## 附录 A：拼写运算挂载点速查

| 挂载点 | 作用范围 | 典型用途 |
|---|---|---|
| `user_force_top` / `user_force_bottom` | 所有方案（含字词模式） | 手机模糊键盘映射等 |
| `user_sentence_top` / `user_sentence_bottom` | 类整句方案 | 模糊音、双拼方案映射、本文的拼写别名 |

模糊音示例（加在 `mohu.custom.yaml` 的 `algebra/user_sentence_top`，引用 `mohu_defs.yaml` 预置规则）：

```yaml
patch:
  algebra/user_sentence_top:
    __append:
      __patch:
        - mohu_defs:/bufen/en_eng
```

## 附录 B：已知边界

- **全码接续怪癖**：`myjdly`（全码+双拼）目前不出「命令」（实测菜单是「名将令」等），与三码接续 `myjly` 行为不一致，待排查。
- **三码歧义**：`mijd` 会被切成 `[mi][jd]`（米浆）抢占，「命」的全码别名形式排在后面。这是既有的切分行为，与别名无关。
- `mij` 单打时「秒出警」（固词表声母三简）仍排第一，「命」第二；不影响 `mijly` 出词。
