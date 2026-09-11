# 魔虎语义模型资产（mohu-semantic-v1）

「魔虎语义」开关（`neural_rerank`）的进程内 C2 语义模型。部署时整个目录
复制到 Rime 用户目录，方案默认路径 `tiger/semantic_model` / `tiger/semantic_vocab`
即指向 `mohu_semantic/mohu_semantic.onnx` 与 `mohu_semantic/vocab.tsv`。

- `mohu_semantic.onnx`：8.7M 参数 shared-context 语义排序模型（C2，
  `production_rank_prior=1.0`，候选维动态轴）。质量结论见
  `docs/reports/2026-09-10-model-comparison.md`（同方法生产菜单对比中
  唯一净收益后端：tnews +0.072pp，CI 显著为正）。
- `vocab.tsv`：首行 `MOHU_SEMANTIC_VOCAB_V1\t<score_mean>\t<score_std>`，
  之后每行「UTF-8 字符 \t id」。

模型加载失败会把开关自动退回「魔虎语义关」；开关保持「开」即加载成功。
重新生成（训练侧产物 → 本目录）：

```sh
uv run --with torch --with onnx --with onnxruntime python \
  research/semantic_student/export_c1_onnx.py \
  --checkpoint research/semantic_student/runs/student-pipeline-c2/checkpoint-epoch2.pt \
  --pairs research/semantic_student/datasets/native-v2/pipeline-prod-paired-v2.jsonl \
  --out research/semantic_student/runs/student-pipeline-c2-onnx
# 词表由 checkpoint 导出（首行含 V5 分数标准化参数）
cp research/semantic_student/runs/student-pipeline-c2-onnx/student_c1.onnx mohu_semantic.onnx
cp research/semantic_student/runs/student-pipeline-c2-onnx/vocab.tsv vocab.tsv
```

改动本目录文件后需同步校验：`make tigerengine-semantic`（设置
`MOHU_SEMANTIC_MODEL`/`MOHU_SEMANTIC_VOCAB` 指向本目录文件）。
