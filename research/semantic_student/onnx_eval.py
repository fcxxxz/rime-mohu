"""Full-split evaluation of an exported ONNX semantic student.

Runs the same metrics as ``evaluate.py`` (Top-1, MRR, corrections/regressions
against the native baseline, oracle-rank slices) through an ONNX Runtime
session with dynamic batching, so quantized exports can be gated on the full
held-out split instead of a sample. Menu application is a stable descending
argsort (ties keep native order).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import onnxruntime as ort
import torch

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from train import encode_dataset


def rank_bucket(oracle_rank: int) -> str:
    if oracle_rank == 1:
        return "native_top1"
    if oracle_rank == 2:
        return "native_rank2"
    if oracle_rank <= 5:
        return "native_rank3_5"
    return "native_rank6_plus"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--onnx", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True, help="vocab/config source")
    parser.add_argument("--split", type=str, default="test")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=True)
    config = checkpoint["config"]
    vocab = checkpoint["vocab"]
    session = ort.InferenceSession(str(args.onnx), providers=["CPUExecutionProvider"])

    shard_paths = sorted(args.dataset.glob(f"{args.split}-*.jsonl"))
    if not shard_paths:
        raise SystemExit(f"no {args.split}-*.jsonl shards under {args.dataset}")
    packs = encode_dataset(shard_paths, vocab, score_mean=config["score_mean"],
                           score_std=config["score_std"], limit=args.limit)
    oracle_ranks = (packs["target"] + 1).tolist()
    total_records = packs["target"].shape[0]

    slice_totals: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    mrr_sum = 0.0
    top1 = 0
    corrections = 0
    regressions = 0
    native_top1 = 0

    for start in range(0, total_records, args.batch_size):
        end = min(start + args.batch_size, total_records)
        feed = {
            "context_ids": packs["context_ids"][start:end].numpy(),
            "candidate_ids": packs["candidate_ids"][start:end].numpy(),
            "candidate_mask": packs["candidate_mask"][start:end].numpy(),
            "native_rank": packs["native_rank"][start:end].numpy(),
            "native_score": packs["native_score"][start:end].numpy(),
            "syllable_count": packs["syllable_count"][start:end].numpy(),
            "char_len": packs["char_len"][start:end].numpy(),
        }
        scores = session.run(["scores"], feed)[0]
        target = packs["target"][start:end].numpy()
        native_first = packs["native_first_correct"][start:end].numpy()
        for row in range(end - start):
            row_scores = scores[row]
            row_target = target[row]
            order = np.argsort(-row_scores, kind="stable")
            model_first = int(order[0])
            rank = int(np.where(order == row_target)[0][0]) + 1
            hit = rank == 1
            corr = (not native_first[row]) and model_first == row_target
            regr = bool(native_first[row]) and model_first != row_target
            top1 += hit
            mrr_sum += 1.0 / rank
            corrections += corr
            regressions += regr
            native_top1 += bool(native_first[row])
            oracle_rank = oracle_ranks[start + row]
            for name in ("all", rank_bucket(oracle_rank)):
                bucket = slice_totals[name]
                bucket[0] += 1
                bucket[1] += hit
                bucket[2] += corr
                bucket[3] += regr

    total = total_records
    metrics = {
        "onnx": str(args.onnx),
        "split": args.split,
        "records": int(total),
        "top1": float(top1) / total,
        "mrr": float(mrr_sum) / total,
        "native_top1": float(native_top1) / total,
        "corrections": int(corrections),
        "regressions": int(regressions),
        "slices": {
            name: {
                "n": int(values[0]),
                "top1": float(values[1]) / values[0],
                "corrections": int(values[2]),
                "regressions": int(values[3]),
            }
            for name, values in sorted(slice_totals.items())
        },
    }
    text = json.dumps(metrics, ensure_ascii=False, indent=2)
    print(text)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
