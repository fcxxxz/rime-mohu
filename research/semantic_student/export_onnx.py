"""Export the trained semantic student to a versioned ONNX artifact.

Exports ``SharedContextRanker.score_menu`` as one ONNX graph with fixed menu
shape (batch 1, MAX_CANDIDATES x MAX_CANDIDATE_CHARS) plus dynamic batch for
CPU serving, verifies numerical parity against the PyTorch model on a real
record from the dataset, and writes an artifact manifest (model/vocab hashes,
config, parity error). The scorer only reads candidate ids/features and
emits one score per menu index; applying scores back to a menu stays in
protocol space (stable descending argsort, ties keep native order).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import torch

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from model import SharedContextRanker
from train import MAX_CANDIDATES, MAX_CANDIDATE_CHARS, MAX_CONTEXT, encode_dataset


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ScoreMenuWrapper(torch.nn.Module):
    """Flatten score_menu into ONNX-friendly inputs (no dict arguments)."""

    def __init__(self, model: SharedContextRanker):
        super().__init__()
        self.model = model

    def forward(self, context_ids, candidate_ids, candidate_mask, native_rank,
                native_score, syllable_count, char_len):
        return self.model.score_menu(
            context_ids, candidate_ids, candidate_mask, native_rank,
            native_score, syllable_count, char_len,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--opset", type=int, default=17)
    args = parser.parse_args()

    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=True)
    config = checkpoint["config"]
    vocab = checkpoint["vocab"]
    model = SharedContextRanker(
        len(vocab), d_model=config["d_model"], context_layers=config["context_layers"],
        candidate_layers=config["candidate_layers"], cross_layers=config["cross_layers"],
    )
    model.load_state_dict(checkpoint["model"])
    model.eval()
    wrapper = ScoreMenuWrapper(model).eval()

    shard = sorted(args.dataset.glob("dev-*.jsonl"))[0]
    packs = encode_dataset([shard], vocab, score_mean=config["score_mean"],
                           score_std=config["score_std"], limit=64)
    context = packs["context_ids"][:1]
    candidates = packs["candidate_ids"][:1]
    mask = packs["candidate_mask"][:1]
    rank = packs["native_rank"][:1]
    score = packs["native_score"][:1]
    syllable = packs["syllable_count"][:1]
    chars = packs["char_len"][:1]

    with torch.no_grad():
        reference = wrapper(context, candidates, mask, rank, score, syllable, chars)

    args.out.mkdir(parents=True, exist_ok=True)
    onnx_path = args.out / "semantic_student.onnx"
    torch.onnx.export(
        wrapper,
        (context, candidates, mask, rank, score, syllable, chars),
        str(onnx_path),
        input_names=["context_ids", "candidate_ids", "candidate_mask", "native_rank",
                     "native_score", "syllable_count", "char_len"],
        output_names=["scores"],
        dynamic_axes={
            "context_ids": {0: "batch"},
            "candidate_ids": {0: "batch"},
            "candidate_mask": {0: "batch"},
            "native_rank": {0: "batch"},
            "native_score": {0: "batch"},
            "syllable_count": {0: "batch"},
            "char_len": {0: "batch"},
            "scores": {0: "batch"},
        },
        opset_version=args.opset,
        dynamo=False,
    )

    try:
        import onnxruntime as ort
        session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
        onnx_scores = session.run(
            ["scores"],
            {
                "context_ids": context.numpy(), "candidate_ids": candidates.numpy(),
                "candidate_mask": mask.numpy(), "native_rank": rank.numpy(),
                "native_score": score.numpy(), "syllable_count": syllable.numpy(),
                "char_len": chars.numpy(),
            },
        )[0]
        parity = float(abs(onnx_scores - reference.numpy()).max())
    except ImportError:
        parity = None

    (args.out / "manifest.json").write_text(
        json.dumps(
            {
                "checkpoint": str(args.checkpoint),
                "checkpoint_sha256": sha256_file(args.checkpoint),
                "onnx_sha256": sha256_file(onnx_path),
                "vocab_sha256": hashlib.sha256(
                    json.dumps(vocab, ensure_ascii=False).encode("utf-8")
                ).hexdigest(),
                "vocab_size": len(vocab),
                "config": {key: value for key, value in config.items()},
                "shapes": {
                    "context": MAX_CONTEXT, "candidates": MAX_CANDIDATES,
                    "candidate_chars": MAX_CANDIDATE_CHARS,
                },
                "opset": args.opset,
                "parity_max_abs_diff": parity,
            },
            ensure_ascii=False, indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    print(f"exported {onnx_path} parity={parity}")


if __name__ == "__main__":
    main()
