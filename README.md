<h1 align="center">魔虎 Rime 输入方案</h1>
<h3 align="center">虎码用户想打舒适区</h3>



授权协议：完整的方案发行依 [GPL v3](https://www.gnu.org/licenses/gpl-3.0.en.html) 协议发布。若某文件中另有说明，则该文件可依对应许可协议再发行。

---

本项目基于魔然的优秀音形基座和虎码字根的高离散性，规则相对清晰，学习成本相对集中，堪称虎码用户的终极退路

方案用双拼+虎码字根前两码，提供自然码与小鹤两组方案。包含智能、字词、整句和辅助码四种输入模式

```
推荐下载晴跟打Pro，全免费，包含双拼和虎码字根练习，以及后续的练单发文练习
```

双拼方案只保留 `ohm` 虎码反查；纯虎码方案使用反引号引导无声调全拼反查。


| 简快码                              | 整句辅助模式                   | 快捷加词                    |
|-------------------------------------|--------------------------|-------------------------|
| ![简快码](./etc/screenshot-bql.png) | ![整句辅助码](./etc/vgju.png) | ![快捷加词](./etc/jwci.gif) |


## 符号与斜杠命令

简快符号与虎码保持一致，例如 `;w` 输入「？」、`;d` 输入「、」、`;v` 输入「《」。符号菜单使用斜杠引导，例如 `/bd`（标点）、`/bq`（表情）、`/fh`（符号）、`/jt`（箭头）和 `/sx`（数学符号）。只输入 `/` 会显示常用命令提示。

日期时间命令包括 `/date`、`/time`、`/week`、`/cdate` 和 `/fjq`，也可使用首字母入口 `/rq`、`/sj`、`/xq`、`/nl`、`/jq`。日期、时间、象棋、节气符号分别使用 `/rqfh`、`/sjfh`、`/xqfh`、`/jqfh`。原有的 `odate`、`otime`、`oweek`、`ocdate`、`ojq` 等输入方式继续保留。皮肤编辑器可用 `/skin`、`/pifu` 或 `/pfbj` 打开。表情与符号联想词库合并了魔虎和虎码的数据，保留双方独有的触发词与候选。

辅助码后缀使用 `编码/` 查询，使用 `编码//` 后按空格进入自由加词。

# 方案维护

master 分支可使用如下命令进行日常维护：

```bash
make quick                           # 快速更新单字信息
make dict                            # 更新词库中的辅助码
make dist                            # 产生纯净方案到 ./dist 目录下
make dist DESTDIR=~/Library/Rime     # 将方案拷贝到 DESTDIR
make test                            # 执行单元测试
./make_simp_dist.sh                  # 产生简体版方案到 ./dist 目录下
```

注意：master 分支必须首先 `make quick` 后才能部署。

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
