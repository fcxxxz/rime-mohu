#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MOHU_SOURCE = ROOT / "tools/data/mohu_emoji_base.txt"
DEFAULT_TIGER_SOURCE = ROOT / "tools/data/tiger_emoji.txt"
DEFAULT_OUTPUT = ROOT / "opencc/mohu_emoji.txt"

Entries = OrderedDict[str, list[str]]


def append_unique(target: list[str], values: list[str]) -> None:
    seen = set(target)
    for value in values:
        if value and value not in seen:
            target.append(value)
            seen.add(value)


def load_entries(path: Path) -> Entries:
    entries: Entries = OrderedDict()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line or raw_line.startswith("#"):
            continue
        try:
            key, output = raw_line.split("\t", 1)
        except ValueError as error:
            raise ValueError(f"{path}:{line_number}: expected a tab-separated entry") from error
        if not key or not output:
            raise ValueError(f"{path}:{line_number}: key and output must be non-empty")
        append_unique(entries.setdefault(key, []), output.split())
    return entries


def merge_entries(mohu: Entries, tiger: Entries) -> Entries:
    merged: Entries = OrderedDict((key, list(values)) for key, values in mohu.items())
    for key, values in tiger.items():
        append_unique(merged.setdefault(key, []), values)
    return merged


def render_entries(entries: Entries) -> str:
    return "".join(f"{key}\t{' '.join(values)}\n" for key, values in entries.items())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge Mohu and Tiger emoji associations")
    parser.add_argument("--mohu-source", type=Path, default=DEFAULT_MOHU_SOURCE)
    parser.add_argument("--tiger-source", type=Path, default=DEFAULT_TIGER_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="fail if the generated output differs")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output = render_entries(
        merge_entries(load_entries(args.mohu_source), load_entries(args.tiger_source))
    ).encode("utf-8")
    if args.check:
        if not args.output.is_file() or args.output.read_bytes() != output:
            print(f"out of date: {args.output}", file=sys.stderr)
            return 1
        return 0
    args.output.write_bytes(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
