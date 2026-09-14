"""Deployed-shape evaluation of a pipeline-trained student.

Simulates the production gate filter exactly: only menus whose V5 top-2
z-margin is below the ambiguity threshold are reranked; the student scores
the top-K window (production rank + V5 context char scores as features); a
first-choice flip additionally requires the semantic z-margin. All other
outcomes preserve the production order byte-for-byte.

Reports corrections/regressions versus the production order on unique case
IDs, gate participation, flip rates, and a paired bootstrap CI for the
per-menu delta. Requires every window candidate to carry has_score=true
(matching the deployed SCORE2 contract); otherwise the menu fails open.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import torch

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from model import PAD, UNK, SharedContextRanker

MAX_CONTEXT = 96
MAX_CANDIDATE_CHARS = 16

PROTECTED_COMMENT_PREFIXES = ("⚡️", "📌")


def candidate_length(text: str) -> int:
    try:
        return len(text)  # python str is code points; mirrors utf8.len semantics
    except TypeError:
        return 0


def reorderable(item: dict) -> bool:
    """Deployed filter policy: single-char, quick-code and pinned candidates
    never participate in semantic reordering; they keep their slots frozen."""
    text = item["text"]
    if candidate_length(text) < 2:
        return False
    comment = item.get("comment") or ""
    if comment.startswith(PROTECTED_COMMENT_PREFIXES):
        return False
    return True


def znorm(values: list[float]) -> list[float] | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    std = math.sqrt(var)
    if not std > 0:
        return None
    return [(v - mean) / std for v in values]


def encode_feed(record: dict, window: int, vocab: dict[str, int], mean: float, std: float):
    production = record["production"][:window]
    if len(production) < 2:
        return None
    if any(not item["has_score"] for item in production):
        return None  # deployed contract: fail open on missing score evidence
    context_ids = torch.full((MAX_CONTEXT,), PAD, dtype=torch.long)
    ids = [vocab.get(char, UNK) for char in record["context_committed"][:MAX_CONTEXT]]
    if ids:
        context_ids[: len(ids)] = torch.tensor(ids, dtype=torch.long)
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
    return {
        "context_ids": context_ids.reshape(1, -1),
        "candidate_ids": candidate_ids,
        "candidate_mask": torch.ones(1, n, dtype=torch.bool),
        "native_rank": rank,
        "native_score": score,
        "syllable_count": syllable,
        "char_len": chars,
        "has_score": has,
    }


def evaluate(pairs: Path, model, vocab: dict, config: dict, device: str,
             window: int, gate_margin: float, flip_margin: float) -> dict:
    mean, std = config["score_mean"], config["score_std"]
    stats = {
        "menus": 0, "gate_participated": 0, "fail_open_no_score": 0,
        "corrections": 0, "regressions": 0, "student_top1": 0, "prod_top1": 0,
    }
    deltas: list[int] = []
    with pairs.open("r", encoding="utf-8") as stream, torch.no_grad():
        for line in stream:
            record = json.loads(line)
            if not record["context_ok"] or record["production_oracle_rank"] is None:
                continue
            target_text = record["target_text"]
            production = record["production"][:window]
            # Deployed filter policy: only reorderable candidates may move.
            slots = [index for index, item in enumerate(production) if reorderable(item)]
            if len(slots) < 2:
                stats["menus"] += 1
                prod_ok = production[0]["text"] == target_text
                stats["prod_top1"] += prod_ok
                stats["student_top1"] += prod_ok
                deltas.append(0)
                continue
            if any(not item["has_score"] for item in production):
                stats["fail_open_no_score"] += 1
                deltas.append(0)
                stats["menus"] += 1
                prod_ok = production[0]["text"] == target_text
                stats["prod_top1"] += prod_ok
                stats["student_top1"] += prod_ok
                continue
            texts = [item["text"] for item in production]
            stats["menus"] += 1
            prod_ok = texts[0] == target_text
            stats["prod_top1"] += prod_ok

            # V5 ambiguity gate over the reorderable window (deployed order).
            window_items = [production[index] for index in slots]
            v5 = [item["native_score"] for item in window_items]
            ez = znorm(v5)
            ambiguous = ez is not None and (sorted(ez, reverse=True)[0]
                                            - sorted(ez, reverse=True)[1]) < gate_margin
            if not ambiguous:
                deltas.append(0)
                stats["student_top1"] += prod_ok
                continue
            stats["gate_participated"] += 1

            feed = encode_feed(record, window, vocab, mean, std)
            feed = {key: value.to(device) for key, value in feed.items()}
            logits = model.score_menu(
                feed["context_ids"], feed["candidate_ids"], feed["candidate_mask"],
                feed["native_rank"], feed["native_score"], feed["syllable_count"],
                feed["char_len"], feed["has_score"],
            )[0].tolist()
            sz = znorm([logits[index] for index in slots])
            if sz is None:
                deltas.append(0)
                stats["student_top1"] += prod_ok
                continue
            best_window = max(range(len(sz)), key=lambda i: (sz[i], -i))
            # First-choice flip additionally needs the semantic margin; the
            # production-first reorderable candidate must be window slot 0 only
            # when slot 0 is the menu head. Otherwise flips stay within window.
            head_is_window0 = slots[0] == 0
            if head_is_window0 and best_window != 0 and (sz[best_window] - sz[0]) < flip_margin:
                best_window = 0
            best = slots[best_window]
            student_ok = texts[best] == target_text
            stats["student_top1"] += student_ok
            stats["corrections"] += (not prod_ok) and student_ok
            stats["regressions"] += prod_ok and not student_ok
            deltas.append(int(student_ok) - int(prod_ok))

    arrays = np.array(deltas, dtype=np.int8)
    rng = np.random.default_rng(7)
    n = len(arrays)
    boot = np.empty(10000, dtype=np.float64)
    for index in range(10000):
        sample = rng.integers(0, n, size=n)
        boot[index] = arrays[sample].mean()
    boot = np.sort(boot)
    ci_low = float(boot[250] * 100)
    ci_high = float(boot[9750] * 100)
    menus = stats["menus"]
    return {
        **stats,
        "student_top1_rate": stats["student_top1"] / menus,
        "prod_top1_rate": stats["prod_top1"] / menus,
        "regression_rate": stats["regressions"] / menus,
        "delta_pp": (stats["student_top1"] - stats["prod_top1"]) / menus * 100,
        "correction_regression_ratio": (
            stats["corrections"] / stats["regressions"] if stats["regressions"] else None
        ),
        "delta_ci95_pp": [round(ci_low, 4), round(ci_high, 4)],
        "ci_lower_positive": bool(ci_low > 0),
        "gate_margin": gate_margin,
        "flip_margin": flip_margin,
        "window": window,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--device", type=str, default="mps" if torch.backends.mps.is_available() else "cpu")
    parser.add_argument("--window", type=int, default=10)
    parser.add_argument("--gate-margin", type=float, default=0.5)
    parser.add_argument("--flip-margin", type=float, default=0.15)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=True)
    config = checkpoint["config"]
    vocab = checkpoint["vocab"]
    model = SharedContextRanker(
        len(vocab), d_model=config["d_model"], context_layers=config["context_layers"],
        candidate_layers=config["candidate_layers"], cross_layers=config["cross_layers"],
        production_rank_prior=config.get("production_rank_prior", 0.0),
    ).to(args.device)
    missing, unexpected = model.load_state_dict(checkpoint["model"], strict=False)
    model.eval()
    real_missing = [name for name in missing if "score_presence" not in name]
    if real_missing or unexpected:
        raise SystemExit(f"checkpoint mismatch: missing={real_missing}, unexpected={unexpected}")

    result = evaluate(args.pairs, model, vocab, config, args.device,
                      args.window, args.gate_margin, args.flip_margin)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
