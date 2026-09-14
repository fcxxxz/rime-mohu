"""Train the shared-context semantic candidate ranker on native-replay menus.

Data path is streaming: JSONL shards are encoded record-by-record into fixed
shape tensors (padded to MAX_CANDIDATES x MAX_CANDIDATE_CHARS at encode time),
so peak memory stays a few GB even with millions of menus and DataLoader
batching needs no padding. The vocabulary is built from a prefix sample
(common chars are covered; tail chars map to <unk>).

Loss is listwise cross-entropy over the full candidate menu (the oracle index
is the ground-truth user intent). Metrics compare against the native baseline
on the same records: Top-1, MRR, corrections (native wrong -> model right) and
regressions (native right -> model wrong).

Usage:
  python train.py --dataset <dir> [--epochs 3] [--batch-size 64] \
      [--device cuda] [--amp] [--out runs/student-v1]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter
from pathlib import Path

import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from model import PAD, UNK, SharedContextRanker, count_parameters

MAX_CONTEXT = 96
MAX_CANDIDATE_CHARS = 16
MAX_CANDIDATES = 20
VOCAB_SAMPLE_RECORDS = 50_000


def iter_shard_lines(paths: list[Path]):
    for path in paths:
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    yield line


def build_vocab(paths: list[Path]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    seen = 0
    for line in iter_shard_lines(paths):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        counts.update(record["committed_context"])
        for candidate in record["candidates"]:
            counts.update(candidate["text"])
        seen += 1
        if seen >= VOCAB_SAMPLE_RECORDS:
            break
    vocab = {"<pad>": PAD, "<unk>": UNK, "<bos>": 2, "<eos>": 3}
    for char, _count in counts.most_common():
        if char not in vocab:
            vocab[char] = len(vocab)
    return vocab


def encode_dataset(paths: list[Path], vocab: dict[str, int], *,
                   score_mean: float, score_std: float, limit: int = 0,
                   teacher_labels: dict[str, list[float]] | None = None) -> dict[str, torch.Tensor]:
    n = MAX_CANDIDATES
    length = MAX_CANDIDATE_CHARS
    packs: dict[str, list] = {
        "context_ids": [], "candidate_ids": [], "candidate_mask": [],
        "native_rank": [], "native_score": [], "syllable_count": [],
        "char_len": [], "target": [], "native_first_correct": [],
    }
    use_teacher = teacher_labels is not None
    if use_teacher:
        packs["teacher_log_probs"] = []
        packs["teacher_mask"] = []
    seen = 0
    unk_hits = 0
    unk_total = 0
    for line in iter_shard_lines(paths):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        context_ids = torch.full((MAX_CONTEXT,), PAD, dtype=torch.long)
        encoded = [vocab.get(char, UNK) for char in record["committed_context"][:MAX_CONTEXT]]
        unk_hits += encoded.count(UNK)
        unk_total += len(encoded)
        if encoded:
            context_ids[: len(encoded)] = torch.tensor(encoded, dtype=torch.long)

        candidate_ids = torch.full((n, length), PAD, dtype=torch.long)
        mask = torch.zeros(n, dtype=torch.bool)
        rank = torch.ones(n, dtype=torch.long)
        score = torch.zeros(n)
        syllable = torch.ones(n)
        chars = torch.ones(n)
        for column, candidate in enumerate(record["candidates"][:n]):
            ids = [vocab.get(char, UNK) for char in candidate["text"][:length]]
            unk_hits += ids.count(UNK)
            unk_total += len(ids)
            if ids:
                candidate_ids[column, : len(ids)] = torch.tensor(ids, dtype=torch.long)
            mask[column] = True
            rank[column] = candidate["native_rank"]
            raw_score = candidate["native_score"]
            score[column] = (raw_score - score_mean) / score_std if raw_score is not None else 0.0
            syllable[column] = len(candidate["code_evidence"]["syllables"])
            chars[column] = len(candidate["text"])

        packs["context_ids"].append(context_ids)
        packs["candidate_ids"].append(candidate_ids)
        packs["candidate_mask"].append(mask)
        packs["native_rank"].append(rank)
        packs["native_score"].append(score)
        packs["syllable_count"].append(syllable)
        packs["char_len"].append(chars)
        packs["target"].append(torch.tensor(record["oracle_rank"] - 1, dtype=torch.long))
        packs["native_first_correct"].append(torch.tensor(record["oracle_rank"] == 1))
        if use_teacher:
            label = teacher_labels.get(record["case_id"])
            if label is not None and len(label) >= len(record["candidates"]):
                packs["teacher_log_probs"].append(torch.tensor(label[:n], dtype=torch.float))
                packs["teacher_mask"].append(torch.tensor(True))
            else:
                packs["teacher_log_probs"].append(torch.zeros(n))
                packs["teacher_mask"].append(torch.tensor(False))
        seen += 1
        if limit and seen >= limit:
            break
    tensors = {key: torch.stack(value) for key, value in packs.items()}
    tensors["native_first_correct"] = tensors["native_first_correct"].bool()
    if unk_total:
        print(f"unk rate: {unk_hits / unk_total:.4%}")
    print(f"encoded {seen} records")
    return tensors


def native_score_stats(paths: list[Path], sample: int = 200_000) -> tuple[float, float]:
    values: list[float] = []
    for line in iter_shard_lines(paths):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        for candidate in record["candidates"]:
            if candidate["native_score"] is not None:
                values.append(candidate["native_score"])
        if len(values) >= sample:
            break
    mean = sum(values) / len(values)
    var = sum((value - mean) ** 2 for value in values) / len(values)
    return mean, math.sqrt(var) or 1.0


def load_teacher_labels(paths_text: str) -> dict[str, list[float]]:
    """Comma-separated teacher_score.py outputs -> case_id -> per-index scores."""

    labels: dict[str, list[float]] = {}
    for part in paths_text.split(","):
        path = Path(part.strip())
        if not path.is_file():
            raise SystemExit(f"teacher label file not found: {path}")
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                row = json.loads(line)
                if "header" in row:
                    continue
                scores = [0.0] * MAX_CANDIDATES
                for menu_index, score in row.get("scores", []):
                    if 0 <= menu_index < MAX_CANDIDATES:
                        scores[menu_index] = score
                labels[row["case_id"]] = scores
    return labels


class MenuTensorDataset(Dataset):
    def __init__(self, packs: dict[str, torch.Tensor]):
        self.packs = packs
        self.size = packs["target"].shape[0]

    def __len__(self) -> int:
        return self.size

    def __getitem__(self, index: int) -> dict:
        return {key: value[index] for key, value in self.packs.items()}


@torch.no_grad()
def evaluate(model: SharedContextRanker, loader: DataLoader, device: str) -> dict:
    model.eval()
    top1 = 0
    mrr_sum = 0.0
    corrections = 0
    regressions = 0
    native_top1 = 0
    total = 0
    for batch in loader:
        batch = {key: value.to(device) for key, value in batch.items()}
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
        top1 += (rank == 1).sum().item()
        mrr_sum += (1.0 / rank.float()).sum().item()
        corrections += ((~native_first) & (model_first == target)).sum().item()
        regressions += (native_first & (model_first != target)).sum().item()
        native_top1 += native_first.sum().item()
        total += target.numel()
    return {
        "top1": top1 / total,
        "mrr": mrr_sum / total,
        "native_top1": native_top1 / total,
        "corrections": corrections,
        "regressions": regressions,
        "total": total,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--eval-batch-size", type=int, default=128)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--warmup-steps", type=int, default=2000)
    parser.add_argument("--device", type=str, default="cuda" if torch.cuda.is_available() else "cpu")
    parser.add_argument("--out", type=Path, default=HERE / "runs/student-v1")
    parser.add_argument("--limit", type=int, default=0, help="cap training records (smoke runs)")
    parser.add_argument("--eval-limit", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--amp", action="store_true", help="bfloat16 autocast on CUDA")
    parser.add_argument("--teacher-labels", type=str, default=None,
                        help="comma-separated teacher_score.py outputs for KL distillation")
    parser.add_argument("--kl-weight", type=float, default=0.5)
    parser.add_argument("--d-model", type=int, default=512)
    parser.add_argument("--context-layers", type=int, default=6)
    parser.add_argument("--candidate-layers", type=int, default=4)
    parser.add_argument("--cross-layers", type=int, default=2)
    args = parser.parse_args()

    torch.manual_seed(args.seed)
    args.out.mkdir(parents=True, exist_ok=True)

    train_paths = sorted(args.dataset.glob("train-*.jsonl"))
    dev_paths = sorted(args.dataset.glob("dev-*.jsonl"))
    if not train_paths or not dev_paths:
        raise SystemExit(f"no train-*/dev-* shards under {args.dataset}")

    print("building vocabulary from sample...", flush=True)
    vocab = build_vocab(train_paths)
    (args.out / "vocab.json").write_text(json.dumps(vocab, ensure_ascii=False), encoding="utf-8")
    print(f"vocab size: {len(vocab)}", flush=True)

    print("estimating native score scale...", flush=True)
    score_mean, score_std = native_score_stats(train_paths)
    print(f"native score mean {score_mean:.3f} std {score_std:.3f}", flush=True)

    print("encoding training shards...", flush=True)
    teacher_labels = load_teacher_labels(args.teacher_labels) if args.teacher_labels else None
    if teacher_labels is not None:
        print(f"teacher labels loaded: {len(teacher_labels)} menus", flush=True)
    train_packs = encode_dataset(train_paths, vocab, score_mean=score_mean, score_std=score_std,
                                 limit=args.limit, teacher_labels=teacher_labels)
    print("encoding dev shards...", flush=True)
    dev_packs = encode_dataset(dev_paths, vocab, score_mean=score_mean, score_std=score_std,
                               limit=args.eval_limit)

    train_loader = DataLoader(
        MenuTensorDataset(train_packs), batch_size=args.batch_size, shuffle=True,
        num_workers=2, pin_memory=args.device.startswith("cuda"), drop_last=True,
    )
    dev_loader = DataLoader(
        MenuTensorDataset(dev_packs), batch_size=args.eval_batch_size, shuffle=False,
        num_workers=1, pin_memory=args.device.startswith("cuda"),
    )

    model = SharedContextRanker(
        len(vocab), d_model=args.d_model, context_layers=args.context_layers,
        candidate_layers=args.candidate_layers, cross_layers=args.cross_layers,
    ).to(args.device)
    print(f"parameters: {count_parameters(model) / 1e6:.1f}M", flush=True)

    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    total_steps = len(train_loader) * args.epochs

    def lr_at(step: int) -> float:
        if step < args.warmup_steps:
            return args.lr * step / max(1, args.warmup_steps)
        progress = (step - args.warmup_steps) / max(1, total_steps - args.warmup_steps)
        return 0.1 * args.lr + 0.45 * args.lr * (1 + math.cos(math.pi * progress))

    metrics: dict[str, object] = {"config": {key: str(value) for key, value in vars(args).items()}}
    step = 0
    skipped_nan = 0
    for epoch in range(args.epochs):
        model.train()
        epoch_loss = 0.0
        epoch_start = time.time()
        for batch in train_loader:
            for group in optimizer.param_groups:
                group["lr"] = lr_at(step)
            batch = {key: value.to(args.device) for key, value in batch.items()}
            with torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=args.amp):
                logits = model.score_menu(
                    batch["context_ids"], batch["candidate_ids"], batch["candidate_mask"],
                    batch["native_rank"], batch["native_score"], batch["syllable_count"],
                    batch["char_len"],
                )
                log_prob = F.log_softmax(logits, dim=-1)
            loss = F.nll_loss(log_prob.float(), batch["target"])
            if teacher_labels is not None:
                teacher_mask = batch["teacher_mask"]
                if teacher_mask.any():
                    teacher_logits = batch["teacher_log_probs"].masked_fill(
                        ~batch["candidate_mask"], float("-inf")
                    )
                    teacher_log_prob = F.log_softmax(teacher_logits, dim=-1)
                    teacher_prob = teacher_log_prob.exp()
                    # Multiply only over real candidate slots: padded slots
                    # would give 0 * (-inf) = NaN and poison the whole loss.
                    kl_terms = (teacher_log_prob - log_prob.float()).masked_fill(
                        ~batch["candidate_mask"], 0.0
                    )
                    kl = (teacher_prob * kl_terms).sum(dim=-1)
                    loss = loss + args.kl_weight * kl[teacher_mask].mean()
            if not torch.isfinite(loss):
                # Never backpropagate a non-finite loss; count and skip.
                skipped_nan += 1
                step += 1
                continue
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            epoch_loss += loss.item()
            step += 1
            if step % 500 == 0:
                rate = step / (time.time() - epoch_start)
                print(f"epoch {epoch} step {step}/{total_steps} loss {loss.item():.4f} "
                      f"({rate:.1f} steps/s)", flush=True)
        evaluation = evaluate(model, dev_loader, args.device)
        evaluation["epoch"] = epoch
        evaluation["train_loss"] = epoch_loss / max(1, len(train_loader))
        evaluation["skipped_nan_losses"] = skipped_nan
        print(f"epoch {epoch}: {json.dumps(evaluation)}", flush=True)
        metrics[f"epoch{epoch}"] = evaluation
        torch.save(
            {"model": model.state_dict(), "vocab": vocab, "config": {
                "d_model": args.d_model, "context_layers": args.context_layers,
                "candidate_layers": args.candidate_layers, "cross_layers": args.cross_layers,
                "score_mean": score_mean, "score_std": score_std,
            }},
            args.out / f"checkpoint-epoch{epoch}.pt",
        )

    (args.out / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
