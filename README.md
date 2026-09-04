<h1 align="center">魔虎 Rime 输入方案</h1>
<h3 align="center">虎码用户想打舒适区</h3>



授权协议：完整的方案发行依 [GPL v3](https://www.gnu.org/licenses/gpl-3.0.en.html) 协议发布。若某文件中另有说明，则该文件可依对应许可协议再发行。

---

本项目基于魔然的优秀音形基座和虎码字根的高离散性，规则相对清晰，学习成本相对集中，堪称虎码用户的终极退路

方案用双拼+虎码字根前两码，提供自然码与小鹤两组方案。两个发布包均只提供一个主方案，整句、辅助码和简快码能力已合并到主方案中。

```
推荐下载晴跟打Pro，全免费，包含双拼和虎码字根练习，以及后续的练单发文练习
```

双拼方案只保留虎码反查，由 `ohm` 或反引号 `` ` `` 引导，提示为〔虎〕；纯虎码方案使用反引号引导无声调全拼反查。


| 简快码                              | 整句辅助模式                   | 快捷加词                    |
|-------------------------------------|--------------------------|-------------------------|
| ![简快码](./etc/screenshot-bql.png) | ![整句辅助码](./etc/vgju.png) | ![快捷加词](./etc/jwci.gif) |


## 符号与斜杠命令

简快符号与虎码保持一致，例如 `;w` 输入「？」、`;d` 输入「、」、`;v` 输入「《」。符号菜单使用斜杠引导，例如 `/bd`（标点）、`/bq`（表情）、`/fh`（符号）、`/jt`（箭头）和 `/sx`（数学符号）。只输入 `/` 会显示常用命令提示。

日期时间命令包括 `/date`、`/time`、`/week`、`/cdate` 和 `/fjq`，也可使用首字母入口 `/rq`、`/sj`、`/xq`、`/nl`、`/jq`。日期、时间、象棋、节气符号分别使用 `/rqfh`、`/sjfh`、`/xqfh`、`/jqfh`。原有的 `odate`、`otime`、`oweek`、`ocdate`、`ojq` 等输入方式继续保留。皮肤编辑器可用 `/skin`、`/pifu` 或 `/pfbj` 打开。表情与符号联想词库合并了魔虎和虎码的数据，保留双方独有的触发词与候选。

辅助码后缀使用 `编码/` 查询，使用 `编码//` 后按空格进入自由加词。

## 魔虎方案

自然码用户下载 `rime-mohu-zrm-latest.zip`，小鹤用户下载 `rime-mohu-flypy-latest.zip`。解压包内文件到 Rime 用户目录后执行一次“重新部署”。两个包分别只启用对应方案，包内不含语言模型。

完整安装步骤、模型放置、macOS 专用的 `解除隔离.command`、Rime 同步助手和常见问题统一见 [安装说明](安装说明.md)。

V5 的跨候选上下文重排由运行时引擎、Lua filter/桥接和 schema 配置共同启用，不需要替换模型文件。使用同一模型的既有安装如需此能力，必须同步更新这些运行时文件。2026-09-03 的隔离五方案基准以 1,000 个二字目标词、每词 20 个训练集精确去重的真实前缀测量：魔虎在一位末辅上的上下文修好率为自然码 66.67%、小鹤 66.91%，完整口径与排名见 [报告](docs/reports/2026-09-03-tail-auxiliary-context-benchmark.md)。

更新运行时文件或从旧版 `mohu_llm_*` 迁移后，除“重新部署”外还必须完全退出并重启 Squirrel；动态库按宿主进程生命周期加载，旧进程可能继续使用已移入废纸篓的旧 `libtigerengine.dylib`。若模型文件存在但首选仍是 smart 候选，请先查看日志中的 `mohu_tiger_sentence` 错误，并用 `lsof -p $(pgrep -x Squirrel)` 核对实际加载路径。

如果 native 已加载但结果仍受个人历史影响，请检查 `mohu/config/user-ngram.snapshot`。默认 `tiger/user_model: true`、`user_model_weight: 0.85` 会把上屏记录与 V5 模型融合；清空或暂时关闭该用户层，才能观察纯 V5 模型排序。

### 放置 V5 模型

从 GitHub Release 下载 `mohu-sentence-ngram-v5.bin`，放到：

```text
~/Library/Rime/mohu/model/mohu-sentence-ngram-v5.bin
```

以后如果有 `v5.10`、`v6` 等版本，放在同一目录即可；运行时按文件名中的数字版本自动选择最高版本。没有模型文件时会回退到普通候选。

# 方案维护

master 分支可使用如下命令进行日常维护：

```bash
make quick                           # 快速更新单字信息
make dict                            # 更新词库中的辅助码
make dist-zrm                        # 生成扁平自然码方案包目录
make dist-flypy                      # 生成扁平小鹤方案包目录
make test                            # 执行单元测试
./make_simp_dist.sh                  # 产生简体版方案到 ./dist 目录下
```

注意：master 分支必须首先 `make quick` 后才能部署。

### 万象 nightly

万象词库每日自动同步。同步检查通过并合并到 `main` 后，GitHub Actions 会更新 [nightly 滚动 Release](https://github.com/fcxxxz/rime-mohu/releases/tag/nightly)，其中提供：

- [自然码 nightly](https://github.com/fcxxxz/rime-mohu/releases/download/nightly/rime-mohu-zrm-nightly.zip)
- [小鹤 nightly](https://github.com/fcxxxz/rime-mohu/releases/download/nightly/rime-mohu-flypy-nightly.zip)

这是预发布版本，固定使用 `nightly` 标签和资产名。Release 说明会记录万象上游 revision、主分支合并提交、增删统计和 CC BY 4.0 署名信息；如果某次发布失败，下一次定时运行会检查 revision 或资产是否缺失并自动补发。

## 从魔然迁移

这次方案 ID 改名不保留兼容入口。先退出输入法，再预览迁移：

```bash
uv run tools/migrate_moran_to_mohu.py ~/Library/Rime
```

确认操作清单后执行：

```bash
uv run tools/migrate_moran_to_mohu.py ~/Library/Rime --apply
```

脚本会在用户目录中创建 `mohu-migration-backup-时间戳` 备份，把旧配置和学习数据迁入自然码组；小鹤组从空用户数据开始。遇到未知旧引用时脚本会在写入前停止。完成后重新部署 Rime。
