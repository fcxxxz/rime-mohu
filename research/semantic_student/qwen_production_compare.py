"""Qwen3-0.6B deployed-shape comparison on production menus (GPU host).

Scores each production menu's top-K window candidates with sum-logprob given
the committed context (the same zero-shot LM scoring that defines the project
baseline), then applies the identical deployed-shape gate simulation used for
the student: V5 top-2 z-margin ambiguity gate, flip margin on first-choice
changes, everything else preserves production order. Also measures per-menu
GPU scoring latency for the latency comparison. Runs entirely on the
authorized training host; no network, no text persistence.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

HERE = Path(__file__).resolve().parent

MAX_CONTEXT_CHARS = 96
WINDOW = 10

PROTECTED_COMMENT_PREFIXES = ("⚡️", "📌")


def reorderable(item: dict) -> bool:
    """Same deployed filter policy as the student evaluator: single-char,
    quick-code and pinned candidates keep their slots and never move."""
    if len(item["text"]) < 2:
        return False
    comment = item.get("comment") or ""
    if comment.startswith(PROTECTED_COMMENT_PREFIXES):
        return False
    return True


def znorm(values):
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    std = math.sqrt(var)
    if not std > 0:
        return None
    return [(v - mean) / std for v in values]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--window", type=int, default=WINDOW)
    parser.add_argument("--gate-margin", type=float, default=0.5)
    parser.add_argument("--flip-margin", type=float, default=0.15)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    tokenizer = AutoTokenizer.from_pretrained(str(args.model_dir))
    model = AutoModelForCausalLM.from_pretrained(
        str(args.model_dir), dtype=torch.bfloat16, device_map="cuda")
    model.eval()

    stats = {"menus": 0, "gate_participated": 0, "corrections": 0,
             "regressions": 0, "student_top1": 0, "prod_top1": 0}
    latencies = []
    deltas = []
    batch_texts: list[str] = []
    batch_meta: list[tuple[list[str], str, list[float]]] = []

    def flush():
        nonlocal batch_texts, batch_meta
        if not batch_texts:
            return
        started = time.perf_counter()
        encoded = tokenizer(batch_texts, add_special_tokens=False,
                            return_tensors="pt", padding=True)
        input_ids = encoded.input_ids.cuda()
        attention = encoded.attention_mask.cuda()
        with torch.no_grad():
            logits = model(input_ids=input_ids, attention_mask=attention).logits.float()
        elapsed_ms = (time.perf_counter() - started) * 1000
        log_probs = torch.log_softmax(logits, dim=-1)
        cursor = 0
        for texts, target, v5, slot_texts in batch_meta:
            menu_lat = elapsed_ms * (len(slot_texts) / len(batch_texts))
            latencies.append(menu_lat)
            prod_ok = texts[0] == target
            stats["menus"] += 1
            stats["prod_top1"] += prod_ok
            ez = znorm(v5)
            ordered = sorted(ez, reverse=True) if ez else None
            if ez is None or (ordered[0] - ordered[1]) >= args.gate_margin:
                stats["student_top1"] += prod_ok
                deltas.append(0)
                continue
            stats["gate_participated"] += 1
            scores = []
            for index in range(len(slot_texts)):
                length = len(tokenizer(slot_texts[index], add_special_tokens=False)["input_ids"])
                seq = log_probs[cursor]
                total = 0.0
                n = seq.shape[0]
                for pos in range(n - length, n):
                    total += seq[pos - 1, input_ids[cursor, pos]].item()
                scores.append(total)
                cursor += 1
            sz = znorm(scores)
            if sz is None:
                stats["student_top1"] += prod_ok
                deltas.append(0)
                continue
            best = max(range(len(sz)), key=lambda i: (sz[i], -i))
            if best != 0 and (sz[best] - sz[0]) < args.flip_margin:
                best = 0
            qwen_ok = slot_texts[best] == target
            stats["student_top1"] += qwen_ok
            stats["corrections"] += (not prod_ok) and qwen_ok
            stats["regressions"] += prod_ok and not qwen_ok
            deltas.append(int(qwen_ok) - int(prod_ok))
        batch_texts, batch_meta = [], []

    seen = 0
    with args.pairs.open("r", encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line)
            if not record["context_ok"] or record["production_oracle_rank"] is None:
                continue
            production = record["production"][:args.window]
            slots = [index for index, item in enumerate(production) if reorderable(item)]
            if len(slots) < 2 or any(not item["has_score"] for item in production):
                continue
            seen += 1
            if args.limit and seen > args.limit:
                break
            texts = [item["text"] for item in production]
            slot_texts = [texts[index] for index in slots]
            context = record["context_committed"][-MAX_CONTEXT_CHARS:]
            context_ids = tokenizer(context, add_special_tokens=False)["input_ids"] if context else []
            for text in slot_texts:
                cand_ids = tokenizer(text, add_special_tokens=False)["input_ids"]
                batch_texts.append(tokenizer.decode(context_ids + cand_ids))
            batch_meta.append((texts, record["target_text"],
                               [production[index]["native_score"] for index in slots],
                               slot_texts))
            if len(batch_meta) >= 48:
                flush()
    flush()

    latencies.sort()
    menus = stats["menus"]
    result = {
        **stats,
        "student_top1_rate": stats["student_top1"] / menus,
        "prod_top1_rate": stats["prod_top1"] / menus,
        "regression_rate": stats["regressions"] / menus,
        "delta_pp": (stats["student_top1"] - stats["prod_top1"]) / menus * 100,
        "correction_regression_ratio": (
            stats["corrections"] / stats["regressions"] if stats["regressions"] else None),
        "gpu_menu_latency_ms": {
            "p50": round(latencies[len(latencies) // 2], 2),
            "p95": round(latencies[int(len(latencies) * 0.95)], 2),
        },
        "gate_margin": args.gate_margin,
        "flip_margin": args.flip_margin,
        "window": args.window,
        "model": str(args.model_dir),
    }
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
