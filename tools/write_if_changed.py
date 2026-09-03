#!/usr/bin/env python3
"""Write stdin to a target file only when the content would change.

Generated Rime dictionaries embed a build-date version stamp, so rebuilding
on another machine (fresh checkout mtimes every run) would otherwise dirty
tracked files even though the dictionary content is identical. With
--ignore-version, a candidate that differs only in `version: "..."` header
lines keeps the existing file untouched; any other difference rewrites it.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path


def normalize_version_lines(text: str) -> str:
    return "\n".join(
        'version: "<stamp>"' if line.startswith('version: "') else line
        for line in text.splitlines()
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    parser.add_argument(
        "--ignore-version",
        action="store_true",
        help="keep the existing file when only version header lines differ",
    )
    args = parser.parse_args(argv)

    candidate = sys.stdin.read()
    if args.target.exists():
        current = args.target.read_text(encoding="utf-8")
        if args.ignore_version:
            unchanged = normalize_version_lines(current) == normalize_version_lines(
                candidate
            )
        else:
            unchanged = current == candidate
        if unchanged:
            return 0
    args.target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=args.target.parent, delete=False
    ) as handle:
        handle.write(candidate)
        handle.flush()
        temporary = Path(handle.name)
    temporary.replace(args.target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
