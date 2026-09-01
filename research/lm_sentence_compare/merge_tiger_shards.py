#!/usr/bin/env python3
"""Merge and integrity-check sharded Tiger native dumps."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path
from typing import Iterable


def _rows(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines(keepends=True) if line.strip()]


def _raw(line: str) -> str:
    raw = line.rstrip("\n").split("\t", 1)[0]
    if not raw:
        raise ValueError("empty raw in shard")
    return raw


def _atomic_concat(paths: Iterable[Path], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=destination.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            for path in paths:
                for line in _rows(path):
                    stream.write(line if line.endswith("\n") else line + "\n")
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    os.replace(temporary, destination)


def merge_shards(
    shards_root: str | Path,
    output_root: str | Path,
    *,
    modes: tuple[str, ...] = ("pure", "sparse", "word1", "char1"),
    shard_count: int = 4,
    expected_rows: int | None = 20_000,
) -> dict[str, int]:
    if shard_count <= 0:
        raise ValueError("shard_count must be positive")
    root = Path(shards_root)
    output = Path(output_root)
    totals: dict[str, int] = {}
    for mode in modes:
        data_paths = [root / f"tiger_shard{i}" / f"tiger_{mode}_shard{i}.tsv" for i in range(shard_count)]
        latency_paths = [root / f"tiger_shard{i}" / f"tiger_{mode}_shard{i}.latency.tsv" for i in range(shard_count)]
        if any(not path.is_file() for path in (*data_paths, *latency_paths)):
            missing = next(path for path in (*data_paths, *latency_paths) if not path.is_file())
            raise FileNotFoundError(missing)
        all_data = [_rows(path) for path in data_paths]
        all_latency = [_rows(path) for path in latency_paths]
        data_raws: list[str] = []
        latency_raws: list[str] = []
        for shard_data, shard_latency in zip(all_data, all_latency):
            data_raws.extend(_raw(line) for line in shard_data)
            latency_raws.extend(_raw(line) for line in shard_latency)
        if len(data_raws) != len(set(data_raws)):
            raise ValueError(f"duplicate raw in {mode} data shards")
        if len(latency_raws) != len(set(latency_raws)):
            raise ValueError(f"duplicate raw in {mode} latency shards")
        if set(data_raws) != set(latency_raws):
            raise ValueError(f"data/latency raw mismatch in {mode} shards")
        if expected_rows is not None and len(data_raws) != expected_rows:
            raise ValueError(f"{mode} shard row count: expected {expected_rows}, got {len(data_raws)}")
        _atomic_concat(data_paths, output / f"tiger_{mode}.tsv")
        _atomic_concat(latency_paths, output / f"tiger_{mode}.latency.tsv")
        totals[mode] = len(data_raws)
    return totals


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge Tiger native result shards")
    parser.add_argument("--shards-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--shard-count", type=int, default=4)
    parser.add_argument("--expected-rows", type=int, default=20_000)
    parser.add_argument("--modes", default="pure,sparse,word1,char1")
    args = parser.parse_args()
    print(merge_shards(args.shards_root, args.output_root, modes=tuple(args.modes.split(",")), shard_count=args.shard_count, expected_rows=args.expected_rows))


if __name__ == "__main__":
    main()
