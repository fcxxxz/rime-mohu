#!/usr/bin/env python3
"""Exact-sentence overlap audit against the consumed Mohu training mixes.

The deployed Mohu char-level model lineage was trained on the
``fenci_mix_v*.txt`` line sets under the local training workspace.  Each mix
line is a whitespace-separated segmentation of one training sentence; removing
the spaces yields the exact sentence text the trainer consumed.  This module
builds a 64-bit hash set over the union of all mixes and reports which probe
sentences are members, so downstream corpus builders can exclude them.

The criterion is normalized exact sentence equality.  Substring or near
duplicate containment is not audited here; corpus builders must apply their
own minimum-length rule to mitigate short-sentence containment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

DEFAULT_MIX_DIR = Path("/tmp/mohu_lm_train")
DEFAULT_OUTPUT = Path("/tmp/mohu-tail-benchmark-v1/train-audit.json")
CHUNK_LINES = 4_000_000


def sentence_key(text: str) -> bytes:
    normalized = "".join(text.split())
    return hashlib.blake2b(normalized.encode("utf-8"), digest_size=8).digest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_mix_lines(paths: list[Path]):
    for path in paths:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                yield line


def build_hash_array(paths: list[Path]) -> np.ndarray:
    chunks: list[np.ndarray] = []
    buffer: list[int] = []
    for line in iter_mix_lines(paths):
        key = sentence_key(line)
        buffer.append(int.from_bytes(key, "big"))
        if len(buffer) >= CHUNK_LINES:
            chunks.append(np.array(buffer, dtype=np.uint64))
            buffer.clear()
    if buffer:
        chunks.append(np.array(buffer, dtype=np.uint64))
    if not chunks:
        raise ValueError("training mixes are empty")
    union = np.unique(np.concatenate(chunks))
    return union


def jsonl_texts(path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            text = str(row.get("text", ""))
            identifier = str(row.get("id", f"line-{line_number}"))
            if text and text not in seen:
                seen.add(text)
                rows.append((identifier, text))
    return rows


def membership(union: np.ndarray, texts: list[str]) -> tuple[int, list[int]]:
    if not texts:
        return 0, []
    keys = np.array(
        [int.from_bytes(sentence_key(text), "big") for text in texts], dtype=np.uint64
    )
    order = np.argsort(keys, kind="stable")
    sorted_keys = keys[order]
    position = np.searchsorted(union, sorted_keys)
    position = np.clip(position, 0, len(union) - 1)
    hits = union[position] == sorted_keys
    hit_indices = sorted(int(index) for index in order[hits])
    return int(hits.sum()), hit_indices


def run_audit(
    mix_dir: Path,
    corpora: list[Path],
    output: Path,
    *,
    hashes_path: Path | None = None,
) -> dict[str, object]:
    mix_paths = sorted(mix_dir.glob("fenci_mix_v*.txt"))
    if not mix_paths:
        raise FileNotFoundError(f"no fenci_mix_v*.txt under {mix_dir}")
    union = build_hash_array(mix_paths)
    if hashes_path is not None:
        hashes_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(hashes_path, union)

    report: dict[str, object] = {
        "criterion": "normalized exact sentence equality over the union of consumed training mixes",
        "normalization": "remove all whitespace (mix lines are word-segmented)",
        "mix_files": [
            {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256_file(path)}
            for path in mix_paths
        ],
        "training_union_sentences": int(len(union)),
        "corpora": {},
    }
    for path in corpora:
        rows = jsonl_texts(path)
        count, hit_indices = membership(union, [text for _, text in rows])
        report["corpora"][str(path)] = {
            "sentences": len(rows),
            "training_overlap": count,
            "overlap_ids": [rows[index][0] for index in hit_indices[:200]],
            "overlap_total": count,
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mix-dir", type=Path, default=DEFAULT_MIX_DIR)
    parser.add_argument(
        "--corpus",
        type=Path,
        action="append",
        required=True,
        help="jsonl file with text fields to audit (repeatable)",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--hashes", type=Path, default=None)
    args = parser.parse_args()
    report = run_audit(
        args.mix_dir,
        args.corpus,
        args.output,
        hashes_path=args.hashes,
    )
    summary = {
        "training_union_sentences": report["training_union_sentences"],
        "corpora": {
            name: body["training_overlap"]
            for name, body in report["corpora"].items()
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
