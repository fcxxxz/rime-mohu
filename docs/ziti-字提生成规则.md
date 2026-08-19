# 魔虎字提生成规则（晴跟打 / TypeSunny）

本文档说明 `tools/gen_ziti.py` 生成魔虎字提文件（`魔虎.txt` 自然码组、`魔虎鹤.txt` 小鹤组）的口径。
字提文件格式与既有 `魔然.txt` 一致：`字<TAB>打法 · 拆分 · 拼音`，
其中「打法」编码部分为纯按键序列，`·` 后为助记信息（TypeSunny 取 `·` 前部分显示编码）。

## 打法符号

| 后缀 | 含义 |
| --- | --- |
| `_` | 空格上屏（输入编码后按空格取首选） |
| `o`（第五键） | 全码顶字键，方案预编辑显示 `°`；四码被词占位时补 `o` 强制出单字 |
| `/`（第五键） | 斜杠筛单字（`编码/` 只列单字），四码让词的另一出口 |
| 数字 `1~5` | 仅用于简码处的选重（简码被更常见候选压住时，如 `dya2`→丁） |

## 两条核心原则的落地

### 1. 有简出简

* 固顶码表 `mohu_*_fixed.dict.yaml`「生成单字」中的 1~3 键编码即简码，
  同字取最短；被挤的简码字还带备用简码（如 万=`mof`/`wjf`、丁=`dya`/`vga`），
  全部参与探测，取引擎第 1 位者。
* 有简码的字一律给简码打法，不给全码——方案「出简让全」（ijrq）会把
  简码字的全码（含 `yyxxo`）推迟到后位，全码反而不首选。
* 简码被更常见候选压住时（如 `dya` 下「顶」在「丁」前），按真实位置给
  简码选重 `dya2`；有备用简码则优先用备用简码（万→`wjf_`）。

### 2. 四码让词

智能（动词）模式下输入四码时，词组与智能组句优先。**被词占位的单字
按方案设计补第五键 `o`（全码顶字）或 `/`（筛单字），不用四码数字选重**：

* 四码第 1 位 → `四码_`（如 `vkop`→昭）。
* 四码被词占位 → `四码o_`（如 `jywgo`→鲸）；`o` 形式仍拿不到第 1 位时
  用 `/` 筛单字 `四码/_`（如 `cisa/`→词）——`/` 只列单字且必为整码上屏。
* `o`、`/` 都拿不到第 1 位 → 纯全码 `四码o` 兜底（打全码后菜单自选，
  如 `lxgpo`→裂）。

引擎真实行为补充（生成器据此逐字回放校验）：

* `o` 形式存在个别「死区」（如 `cisao` 下数字/空格都无法上屏），
  此时一律改用 `/` 形式。
* 四码候选列表里的「音节拼合候选」只覆盖部分输入，数字选取后不上屏
  还残留编码（如 `nrbb3` 选「暖」后剩 `bb`）——这正是四码让词不走
  数字选重的原因。

## 多码变体与兜底

* chars 词典中同字可有多个辅助码变体（不同拆分/读音），全部探测后
  取打法最优者（同级别取词典序靠前的变体）。如「螯」首选变体 `aoap`
  被压住，用 `aoac` 四码直接首选。
* 方案默认「常用字」字符集（简体常用 9,767 字）会隐藏繁体与生僻字，
  这些字（约 7.4 万，含 CJK 扩展区）引擎探测不可见，统一给
  纯全码 `四码o` 兜底打法（需开「全字集」Ctrl+X 使用）。

## 数据与验证

* 候选位置：librime 1.17（Homebrew，lua/octagram 插件）真实引擎，
  以隔离部署目录逐码查询首页候选（`tools/ziti_probe.c`）。
* 每次探测/回放前清空用户词典，保证「新用户初始状态」的可复现性。
* **所有**可回放打法（空格上屏与简码选重）逐条用全新状态回放，
  用 `get_commit` 比对上屏字符；失败的自动降级到下一优先形式后重建。

## 复现步骤

    # 部署目录（首次；flypy 组把 zrm 换成 flypy）
    mkdir -p /tmp/mohu-ziti/deploy
    cp *.yaml *.gram /tmp/mohu-ziti/deploy/ && cp -R lua opencc /tmp/mohu-ziti/deploy/
    printf 'patch:\n  schema_list:\n    - schema: mohu_zrm\n    - schema: mohu_zrm_fixed_legacy\n' \
      > /tmp/mohu-ziti/deploy/default.custom.yaml

    # 探针
    clang tools/ziti_probe.c -I/opt/homebrew/opt/librime/include \
        -L/opt/homebrew/opt/librime/lib -lrime \
        -Wl,-rpath,/opt/homebrew/opt/librime/lib -o /tmp/mohu-ziti/probe

    # 生成（zrm；flypy 同理）
    uv run python tools/gen_ziti.py queries --variant zrm > q.txt
    rm -rf /tmp/mohu-ziti/deploy/*.userdb && /tmp/mohu-ziti/probe /tmp/mohu-ziti/deploy mohu_zrm < q.txt > dump.txt
    uv run python tools/gen_ziti.py build --variant zrm --dump dump.txt --out 魔虎.txt

    # 数字打法全量回放校验 + 降级重建
    uv run python tools/gen_ziti.py digits-emit --file 魔虎.txt > dg.txt
    # 逐行隔离回放（每行前清 deploy/*.userdb），结果存 dgout.txt，失败行进 bad.txt
    uv run python tools/gen_ziti.py build --variant zrm --dump dump.txt --bad-keys bad.txt --out 魔虎.txt

    # 抽样复验
    uv run python tools/gen_ziti.py verify-emit --file 魔虎.txt --count 150 --only-real > v.txt
    # 隔离回放 v.txt 得 vout.txt
    uv run python tools/gen_ziti.py verify-check --file 魔虎.txt --result vout.txt --count 150 --only-real

注意：`*_fixed_legacy` 方案必须列入 schema_list，否则 lua 翻译器加载
legacy 词库时报错，四码/五码查询结果为空。
