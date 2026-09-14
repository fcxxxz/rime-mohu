"""Export the C1 pipeline-trained student to ONNX and benchmark CPU latency.

Exports ``score_menu`` with the deployed input set (context/candidate ids,
mask, rank, V5 context char score, syllable/char lengths, score presence),
verifies parity against PyTorch on a real paired production record, and
measures single-menu CPU latency. The production rank prior is folded into
the exported graph so an untrained/zero-head export preserves menu order.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from model import PAD, UNK, SharedContextRanker

MAX_CONTEXT = 96
MAX_CANDIDATE_CHARS = 16
WINDOW = 10


class ScoreMenuExport(torch.nn.Module):
    def __init__(self, model: SharedContextRanker):
        super().__init__()
        self.model = model

    def forward(self, context_ids, candidate_ids, candidate_mask, native_rank,
                native_score, syllable_count, char_len, has_score):
        return self.model.score_menu(
            context_ids, candidate_ids, candidate_mask, native_rank,
            native_score, syllable_count, char_len, has_score,
        )


def real_feed(pairs: Path, vocab: dict, mean: float, std: float):
    with pairs.open("r", encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line)
            if not record["context_ok"] or record["production_oracle_rank"] is None:
                continue
            production = record["production"][:WINDOW]
            if len(production) < 2 or any(not item["has_score"] for item in production):
                continue
            context_ids = torch.full((1, MAX_CONTEXT), PAD, dtype=torch.long)
            ids = [vocab.get(char, UNK) for char in record["context_committed"][:MAX_CONTEXT]]
            if ids:
                context_ids[0, : len(ids)] = torch.tensor(ids, dtype=torch.long)
            n = len(production)
            candidate_ids = torch.zeros(1, n, MAX_CANDIDATE_CHARS, dtype=torch.long)
            rank = torch.arange(1, n + 1, dtype=torch.long).reshape(1, n)
            score = torch.zeros(1, n)
            has = torch.zeros(1, n)
            syllable = torch.ones(1, n)
            chars = torch.ones(1, n)
            for column, item in enumerate(production):
                char_ids = [vocab.get(char, UNK) for char in item["text"][:MAX_CANDIDATE_CHARS]]
                if char_ids:
                    candidate_ids[0, column, : len(char_ids)] = torch.tensor(char_ids, dtype=torch.long)
                score[0, column] = (item["native_score"] - mean) / std
                has[0, column] = 1.0
                syllable[0, column] = max(1, len(item["text"]) // 2)
                chars[0, column] = len(item["text"])
            return record, {
                "context_ids": context_ids,
                "candidate_ids": candidate_ids,
                "candidate_mask": torch.ones(1, n, dtype=torch.bool),
                "native_rank": rank,
                "native_score": score,
                "syllable_count": syllable,
                "char_len": chars,
                "has_score": has,
            }
    raise SystemExit("no eligible paired record for parity check")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--pairs", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--opset", type=int, default=17)
    args = parser.parse_args()

    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=True)
    config = checkpoint["config"]
    vocab = checkpoint["vocab"]
    model = SharedContextRanker(
        len(vocab), d_model=config["d_model"], context_layers=config["context_layers"],
        candidate_layers=config["candidate_layers"], cross_layers=config["cross_layers"],
        production_rank_prior=config.get("production_rank_prior", 0.0),
    )
    missing, unexpected = model.load_state_dict(checkpoint["model"], strict=False)
    real_missing = [name for name in missing if "score_presence" not in name]
    if real_missing or unexpected:
        raise SystemExit(f"checkpoint mismatch: {real_missing}/{unexpected}")
    model.eval()
    wrapper = ScoreMenuExport(model).eval()

    record, feed = real_feed(args.pairs, vocab, config["score_mean"], config["score_std"])
    with torch.no_grad():
        reference = wrapper(**feed)[0].tolist()

    args.out.mkdir(parents=True, exist_ok=True)
    onnx_path = args.out / "student_c1.onnx"
    torch.onnx.export(
        wrapper,
        tuple(feed.values()),
        str(onnx_path),
        input_names=["context_ids", "candidate_ids", "candidate_mask", "native_rank",
                     "native_score", "syllable_count", "char_len", "has_score"],
        output_names=["scores"],
        dynamic_axes={
            key: {0: "batch"} for key in feed
        } | {
            "candidate_ids": {0: "batch", 1: "candidates"},
            "candidate_mask": {0: "batch", 1: "candidates"},
            "native_rank": {0: "batch", 1: "candidates"},
            "native_score": {0: "batch", 1: "candidates"},
            "syllable_count": {0: "batch", 1: "candidates"},
            "char_len": {0: "batch", 1: "candidates"},
            "has_score": {0: "batch", 1: "candidates"},
        },
        opset_version=args.opset,
        dynamo=False,
    )

    try:
        import onnxruntime as ort

        session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
        numpy_feed = {key: value.numpy() for key, value in feed.items()}
        exported = session.run(["scores"], numpy_feed)[0][0].tolist()
        parity = max(abs(a - b) for a, b in zip(reference, exported))

        warm = dict(numpy_feed)
        for _ in range(10):
            session.run(["scores"], warm)
        latencies = []
        for _ in range(300):
            started = time.perf_counter()
            session.run(["scores"], warm)
            latencies.append((time.perf_counter() - started) * 1000)
        latencies.sort()
        latency = {
            "p50_ms": round(latencies[150], 2),
            "p95_ms": round(latencies[285], 2),
            "max_ms": round(latencies[-1], 2),
        }
    except ImportError:
        parity = None
        latency = None

    manifest = {
        "checkpoint": str(args.checkpoint),
        "onnx_sha256": __import__("hashlib").sha256(onnx_path.read_bytes()).hexdigest(),
        "parity_max_abs_diff": parity,
        "latency_cpu_ms": latency,
        "config": {key: value for key, value in config.items()},
        "record_case_id": record["case_id"],
    }
    (args.out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: manifest[k] for k in ("parity_max_abs_diff", "latency_cpu_ms")}, indent=2))


if __name__ == "__main__":
    main()
