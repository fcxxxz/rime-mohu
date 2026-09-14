"""Paired bootstrap confidence interval for the model-vs-native Top-1 delta.

Resamples test records (paired per record) to produce a 95% CI for
``mean(model_first_correct) - mean(native_first_correct)`` plus correction and
regression counts, as required by the release-report methodology. Deterministic
seed; no record text is stored or logged.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from model import SharedContextRanker
from train import MenuTensorDataset, encode_dataset


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--split", type=str, default="test")
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--samples", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--device", type=str, default="mps" if torch.backends.mps.is_available() else "cpu")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=True)
    config = checkpoint["config"]
    vocab = checkpoint["vocab"]
    model = SharedContextRanker(
        len(vocab), d_model=config["d_model"], context_layers=config["context_layers"],
        candidate_layers=config["candidate_layers"], cross_layers=config["cross_layers"],
    ).to(args.device)
    model.load_state_dict(checkpoint["model"])
    model.eval()

    shard_paths = sorted(args.dataset.glob(f"{args.split}-*.jsonl"))
    packs = encode_dataset(shard_paths, vocab, score_mean=config["score_mean"],
                           score_std=config["score_std"])
    loader = DataLoader(MenuTensorDataset(packs), batch_size=args.batch_size, shuffle=False)

    model_hits: list[int] = []
    native_hits: list[int] = []
    with torch.no_grad():
        for batch in loader:
            batch = {key: value.to(args.device) for key, value in batch.items()}
            logits = model.score_menu(
                batch["context_ids"], batch["candidate_ids"], batch["candidate_mask"],
                batch["native_rank"], batch["native_score"], batch["syllable_count"],
                batch["char_len"],
            )
            target = batch["target"]
            model_first = logits.argmax(dim=-1)
            model_hits += (model_first == target).tolist()
            native_hits += batch["native_first_correct"].tolist()

    model_hits_arr = np.array(model_hits, dtype=np.int8)
    native_hits_arr = np.array(native_hits, dtype=np.int8)
    n = len(model_hits_arr)
    observed = float(model_hits_arr.mean() - native_hits_arr.mean())

    rng = np.random.default_rng(args.seed)
    deltas = np.empty(args.samples, dtype=np.float64)
    for index in range(args.samples):
        sample = rng.integers(0, n, size=n)
        deltas[index] = (model_hits_arr[sample].mean() - native_hits_arr[sample].mean())

    corrections = int(((model_hits_arr == 1) & (native_hits_arr == 0)).sum())
    regressions = int(((model_hits_arr == 0) & (native_hits_arr == 1)).sum())
    result = {
        "checkpoint": str(args.checkpoint),
        "split": args.split,
        "records": n,
        "model_top1": float(model_hits_arr.mean()),
        "native_top1": float(native_hits_arr.mean()),
        "delta_pp": observed * 100,
        "delta_ci95_pp": [float(deltas[int(0.025 * args.samples)] * 100),
                          float(deltas[int(0.975 * args.samples)] * 100)],
        "ci_lower_positive": bool(deltas[int(0.025 * args.samples)] > 0),
        "corrections": corrections,
        "regressions": regressions,
        "bootstrap_samples": args.samples,
        "seed": args.seed,
    }
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
