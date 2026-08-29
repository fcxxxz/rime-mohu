# 魔虎大模型 registry

The complete LLM scheme packages ship this registry metadata only. Qwen checkpoint weights are
not part of the repository or addon archive; download each model separately
from its registry path and place it at the matching local path:

| id | registry path | local path | type | quantization |
| --- | --- | --- | --- | --- |
| `qwen35-0.8b` | `mlx-community/Qwen3.5-0.8B-MLX-4bit` | `mohu_llm/models/Qwen3.5-0.8B-MLX-4bit` | `qwen3_5` | 4-bit |
| `qwen3-0.6b` | `mlx-community/Qwen3-0.6B-4bit` | `mohu_llm/models/Qwen3-0.6B-4bit` | `qwen3` | 4-bit |

Verify downloaded directories against the corresponding manifest before
starting the scorer.  The default selection is `qwen35-0.8b`; there is no
automatic fallback to another model.  Use the `/model` menu to select a
different installed model.
