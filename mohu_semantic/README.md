# 魔虎语义模型资产（mohu-semantic-v1）

「魔虎语义」开关（`neural_rerank`）的进程内语义模型。部署时整个目录
复制到 Rime 用户目录，方案默认路径 `tiger/semantic_model` / `tiger/semantic_vocab`
即指向 `mohu_semantic/mohu_semantic.onnx` 与 `mohu_semantic/vocab.tsv`。

- `mohu_semantic.onnx`：18.7M 参数 shared-context 语义排序模型（**C3**，
  2026-09-13 起；w=1.5 词形态＋整句形态双分布训练，native_score 通道为
  部署同源 V5 上下文字符分）。同集对拍相对 C2：tnews 词菜单净效应
  +0.88% vs +0.28%（修好:修反 9.3:1 vs 5.8:1），门开子集 61.9%→74.2%，
  整句形态中性，p95 延迟 21ms vs 67ms。`mohu.yaml` 的 `semantic_margin`
  随之调至 0.3（26.7:1 保护比档位）。详见
  `docs/reports/2026-09-13-c3-semantic-student.md`。
- `vocab.tsv`：首行 `MOHU_SEMANTIC_VOCAB_V1\t<score_mean>\t<score_std>`，
  之后每行「UTF-8 字符 \t id」。

模型加载失败会把开关自动退回「魔虎语义关」；开关保持「开」即加载成功。
重新生成（训练侧产物 → 本目录）：

```sh
uv run --with torch --with onnx --with onnxruntime python \
  research/semantic_student/export_c1_onnx.py \
  --checkpoint research/semantic_student/runs/student-pipeline-c3/checkpoint-epoch1.pt \
  --pairs research/semantic_student/datasets/native-c3-sent/dev-sent-0000.ctx.jsonl \
  --out research/semantic_student/runs/student-pipeline-c3-onnx
# 词表由 checkpoint 导出（vocab.json → vocab.tsv，首行含 V5 分数标准化参数）
cp research/semantic_student/runs/student-pipeline-c3-onnx/student_c1.onnx mohu_semantic.onnx
cp research/semantic_student/runs/student-pipeline-c3-onnx/vocab.tsv vocab.tsv
```

改动本目录文件后需同步校验：`make tigerengine-semantic`（设置
`MOHU_SEMANTIC_MODEL`/`MOHU_SEMANTIC_VOCAB` 指向本目录文件）。
回滚到 C2：从 git 历史恢复本目录两个文件，并把 `mohu.yaml` 的
`semantic_margin` 调回 0.15。
