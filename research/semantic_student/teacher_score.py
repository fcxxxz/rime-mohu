"""Offline Qwen3-0.6B teacher labeling for native-replay menus.

Discriminative, non-generative scoring: for every menu record the teacher
assigns each existing candidate the sum log-probability of its text tokens
conditioned on the committed context (one forward pass per sequence, batched
by token budget, bf16, no sampling, no text generation). Output rows carry
the model identity and per-candidate scores bound to ``case_id``/``menu_index``;
they feed the student's KL term and never enter any Rime path.

Usage (on the training GPU host):
  python teacher_score.py --dataset <dir> --model-dir D:\\work\\qwen3-0.6b \
      --split train --limit 200000 --out teacher_labels/train.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

LABEL_FORMAT = "qwen-semantic-teacher-labels/v1"
MAX_CONTEXT_CHARS = 96
TOKEN_BUDGET = 6144
MAX_MENUS_PER_BATCH = 48
LOGPROB_CHUNK_ROWS = 128


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--split", type=str, default="train")
    parser.add_argument("--limit", type=int, default=200_000)
    parser.add_argument("--skip", type=int, default=0,
                        help="skip the first N records (resume a partial labeling run)")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    tokenizer = AutoTokenizer.from_pretrained(str(args.model_dir))
    model = AutoModelForCausalLM.from_pretrained(
        str(args.model_dir), torch_dtype=torch.bfloat16, device_map=device
    )
    model.eval()

    shard_paths = sorted(args.dataset.glob(f"{args.split}-*.jsonl"))
    if not shard_paths:
        raise SystemExit(f"no {args.split}-*.jsonl shards under {args.dataset}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    header = {
        "format_version": LABEL_FORMAT,
        "model_dir": str(args.model_dir),
        "model_sha256": sha256_file(args.model_dir / "model.safetensors"),
        "tokenizer_sha256": sha256_file(args.model_dir / "tokenizer.json"),
        "scoring": "sum_logprob_of_candidate_tokens_given_context",
        "max_context_chars": MAX_CONTEXT_CHARS,
    }

    written = 0
    started = time.time()
    batch_records: list[dict] = []
    seqs: list[list[int]] = []
    seq_targets: list[tuple[int, int, int]] = []  # (record row, candidate index, candidate token count)
    token_count = 0

    def flush() -> None:
        nonlocal written, batch_records, seqs, seq_targets, token_count
        if not seqs:
            return
        max_len = max(len(seq) for seq in seqs)
        pad_id = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else 0
        input_ids = torch.full((len(seqs), max_len), pad_id, dtype=torch.long, device=device)
        attention = torch.zeros((len(seqs), max_len), dtype=torch.long, device=device)
        for row, seq in enumerate(seqs):
            input_ids[row, : len(seq)] = torch.tensor(seq, device=device)
            attention[row, : len(seq)] = 1
        with torch.no_grad():
            logits = model(input_ids=input_ids, attention_mask=attention).logits
        lengths = torch.tensor([len(seq) for seq in seqs], dtype=torch.long, device=device)
        cand_lens = torch.tensor([c for _, _, c in seq_targets], dtype=torch.long, device=device)
        starts = lengths - cand_lens
        max_cand = int(cand_lens.max().item()) if seqs else 0
        max_len = int(lengths.max().item()) if seqs else 0
        rows = torch.arange(len(seqs), dtype=torch.long, device=device)
        cols = torch.arange(max_cand, dtype=torch.long, device=device)
        positions = starts.unsqueeze(1) + cols.unsqueeze(0)  # (B, C)
        valid = (cols.unsqueeze(0) < cand_lens.unsqueeze(1)).float()
        clamped = positions.clamp(max=max_len - 1)
        token_ids = input_ids.gather(1, clamped)  # (B, C)

        # Chunked fp32 log-softmax keeps peak memory bounded: the full-batch
        # (B, T, V) fp32 tensor would need ~15GB on this 16GB GPU.
        sums = [0.0] * len(seqs)
        for chunk_start in range(0, len(seqs), LOGPROB_CHUNK_ROWS):
            chunk_end = min(chunk_start + LOGPROB_CHUNK_ROWS, len(seqs))
            chunk_rows = rows[chunk_start:chunk_end]
            chunk_clamped = clamped[chunk_start:chunk_end]
            chunk_tokens = token_ids[chunk_start:chunk_end]
            lp = torch.log_softmax(logits[chunk_start:chunk_end].float(), dim=-1)
            picked = lp[chunk_rows.unsqueeze(1) - chunk_start, (chunk_clamped - 1).clamp(min=0), chunk_tokens]
            picked = picked * valid[chunk_start:chunk_end]
            for local_row, value in enumerate(picked.sum(dim=1).tolist()):
                sums[chunk_start + local_row] = value

        per_record: dict[int, list[list[float]]] = {}
        means: list[float] = []
        for row, (record_row, candidate_index, cand_len) in enumerate(seq_targets):
            means.append(sums[row] / cand_len if cand_len > 0 else 0.0)
        for row, (record_row, candidate_index, _cand_len) in enumerate(seq_targets):
            per_record.setdefault(record_row, []).append(
                [candidate_index, sums[row], means[row]]
            )
        with args.out.open("a", encoding="utf-8") as sink:
            for record_row in sorted(per_record):
                record = batch_records[record_row]
                sink.write(json.dumps({
                    "case_id": record["case_id"],
                    "raw_input": record["raw_input"],
                    "scores": sorted(per_record[record_row]),
                }, ensure_ascii=False) + "\n")
                written += 1
        batch_records, seqs, seq_targets, token_count = [], [], [], 0

    with args.out.open("w", encoding="utf-8") as sink:
        sink.write(json.dumps({"header": header}, ensure_ascii=False) + "\n")

    seen = 0
    stop = False
    for shard in shard_paths:
        if stop:
            break
        with shard.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                seen += 1
                if seen <= args.skip:
                    continue
                if args.limit and seen > args.skip + args.limit:
                    stop = True
                    break
                context = record["committed_context"][-MAX_CONTEXT_CHARS:]
                context_ids = tokenizer(context, add_special_tokens=False)["input_ids"] if context else []
                record_row = len(batch_records)
                batch_records.append(record)
                for candidate in record["candidates"]:
                    cand_ids = tokenizer(candidate["text"], add_special_tokens=False)["input_ids"]
                    seqs.append(context_ids + cand_ids)
                    seq_targets.append((record_row, candidate["menu_index"], len(cand_ids)))
                    token_count += len(context_ids) + len(cand_ids) + 1
                if token_count >= TOKEN_BUDGET or len(batch_records) >= MAX_MENUS_PER_BATCH:
                    flush()
                    if written and written % 5000 < MAX_MENUS_PER_BATCH:
                        rate = written / (time.time() - started)
                        print(f"labeled {written} menus ({rate:.1f}/s)", flush=True)
    flush()
    print(f"done: {written} menus labeled -> {args.out}")


if __name__ == "__main__":
    main()
