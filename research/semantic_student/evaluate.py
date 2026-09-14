"""Held-out evaluation for the semantic student on untouched native-replay shards.

Loads a training checkpoint, scores every menu in the requested split, and
reports protocol-aligned metrics against the native baseline on the same
records: Top-1, MRR, corrections (native wrong -> model right), regressions
(native right -> model wrong), plus slices by native oracle rank, target
length, candidate count and context length.

The model only scores existing candidates; final order is a stable descending
argsort (ties keep native order), matching the apply semantics of
``tools/qwen_semantic_rerank.py``. Checkpoints are loaded with
``weights_only=True``; they contain only tensors and primitive containers.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import torch
from torch.utils.data import DataLoader

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from model import SharedContextRanker
from train import MenuTensorDataset, encode_dataset


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
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--split", type=str, default="test")
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--device", type=str, default="cuda" if torch.cuda.is_available() else "cpu")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    checkpoint = torch.load(args.checkpoint, map_location=args.device, weights_only=True)
    config = checkpoint["config"]
    vocab = checkpoint["vocab"]
    model = SharedContextRanker(
        len(vocab), d_model=config["d_model"], context_layers=config["context_layers"],
        candidate_layers=config["candidate_layers"], cross_layers=config["cross_layers"],
    ).to(args.device)
    model.load_state_dict(checkpoint["model"])
    model.eval()

    shard_paths = sorted(args.dataset.glob(f"{args.split}-*.jsonl"))
    if not shard_paths:
        raise SystemExit(f"no {args.split}-*.jsonl shards under {args.dataset}")
    packs = encode_dataset(
        shard_paths, vocab,
        score_mean=config["score_mean"], score_std=config["score_std"],
        limit=args.limit,
    )
    oracle_ranks = (packs["target"] + 1).tolist()
    cand_counts = packs["candidate_mask"].sum(dim=1).tolist()
    char_lens = packs["char_len"].gather(1, packs["target"].unsqueeze(1)).squeeze(1).tolist()
    context_lens = (packs["context_ids"] != 0).sum(dim=1).tolist()

    loader = DataLoader(MenuTensorDataset(packs), batch_size=args.batch_size, shuffle=False)

    slice_totals: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])  # n, top1, corr, regr
    mrr_sum = 0.0
    top1 = 0
    corrections = 0
    regressions = 0
    native_top1 = 0
    total = 0
    row = 0
    with torch.no_grad():
        for batch in loader:
            batch = {key: value.to(args.device) for key, value in batch.items()}
            logits = model.score_menu(
                batch["context_ids"], batch["candidate_ids"], batch["candidate_mask"],
                batch["native_rank"], batch["native_score"], batch["syllable_count"],
                batch["char_len"],
            )
            target = batch["target"]
            native_first = batch["native_first_correct"]
            model_first = logits.argmax(dim=-1)
            target_logits = logits.gather(1, target.unsqueeze(1)).squeeze(1)
            rank = 1 + (logits > target_logits.unsqueeze(1)).sum(dim=-1)
            hit = rank == 1
            corr = (~native_first) & (model_first == target)
            regr = native_first & (model_first != target)

            top1 += hit.sum().item()
            mrr_sum += (1.0 / rank.float()).sum().item()
            corrections += corr.sum().item()
            regressions += regr.sum().item()
            native_top1 += native_first.sum().item()
            total += target.numel()
            for index in range(target.numel()):
                slices = {
                    "all": True,
                    rank_bucket(oracle_ranks[row]): True,
                    f"cand_count_{min(20, cand_counts[row])}": True,
                    f"target_len_{min(6, int(char_lens[row]))}": True,
                    "with_context" if context_lens[row] >= 2 else "no_context": True,
                }
                for name in slices:
                    bucket = slice_totals[name]
                    bucket[0] += 1
                    bucket[1] += hit[index].item()
                    bucket[2] += corr[index].item()
                    bucket[3] += regr[index].item()
                row += 1

    metrics = {
        "checkpoint": str(args.checkpoint),
        "split": args.split,
        "records": total,
        "top1": top1 / total,
        "mrr": mrr_sum / total,
        "native_top1": native_top1 / total,
        "corrections": corrections,
        "regressions": regressions,
        "slices": {
            name: {
                "n": int(values[0]),
                "top1": values[1] / values[0],
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
