"""Evaluate the Qwen3-0.6B teacher itself as a candidate reranker.

This is the project's true reference baseline: the original commission asks
for a student no weaker than Qwen3-0.6B. Ranks every test menu's existing
candidates by the teacher's score (sum logprob, and length-normalised mean
logprob), then reports the same paired metrics as the student evaluator:
Top-1, MRR, corrections/regressions against the native order. The teacher
never generates text and never touches any Rime path.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def load_labels(path: Path, score_index: int) -> dict[str, list[float]]:
    labels: dict[str, list[float]] = {}
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            if "header" in row:
                continue
            scores = [0.0] * 20
            for entry in row.get("scores", []):
                menu_index = entry[0]
                if 0 <= menu_index < 20:
                    scores[menu_index] = entry[score_index]
            labels[row["case_id"]] = scores
    return labels


def evaluate(dataset_dir: Path, split: str, labels: dict[str, list[float]]) -> dict:
    top1 = 0
    mrr_sum = 0.0
    corrections = 0
    regressions = 0
    native_top1 = 0
    total = 0
    missing = 0
    for shard in sorted(dataset_dir.glob(f"{split}-*.jsonl")):
        with shard.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                record = json.loads(line)
                scores = labels.get(record["case_id"])
                if scores is None:
                    missing += 1
                    continue
                candidates = record["candidates"]
                order = sorted(
                    range(len(candidates)),
                    key=lambda index: (-scores[index], index),
                )
                target = record["oracle_rank"] - 1
                rank = order.index(target) + 1
                model_first = order[0]
                native_first = record["oracle_rank"] == 1
                top1 += rank == 1
                mrr_sum += 1.0 / rank
                corrections += (not native_first) and model_first == target
                regressions += native_first and model_first != target
                native_top1 += native_first
                total += 1
    return {
        "records": total,
        "missing_labels": missing,
        "top1": top1 / total,
        "mrr": mrr_sum / total,
        "native_top1": native_top1 / total,
        "corrections": corrections,
        "regressions": regressions,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--split", type=str, default="test")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    results = {}
    for name, score_index in (("sum_logprob", 1), ("mean_logprob", 2)):
        labels = load_labels(args.labels, score_index)
        results[name] = evaluate(args.dataset, args.split, labels)
        results[name]["scoring"] = name

    text = json.dumps({"labels": str(args.labels), "split": args.split,
                       "variants": results}, ensure_ascii=False, indent=2)
    print(text)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
