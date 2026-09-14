#!/usr/bin/env python3
"""Run the portable Tiger model-dispatch fixture in isolated processes."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} FIXTURE", file=sys.stderr)
        return 2
    fixture = Path(sys.argv[1])
    if not fixture.is_file() and fixture.suffix.lower() != ".exe":
        executable = fixture.with_name(fixture.name + ".exe")
        if executable.is_file():
            fixture = executable
    if not fixture.is_file():
        print(f"fixture not found: {fixture}", file=sys.stderr)
        return 2
    for case in ("dispatch", "unknown", "lazy", "metadata"):
        completed = subprocess.run(
            [str(fixture), case], text=True, capture_output=True, check=False
        )
        if completed.returncode != 0:
            print(f"{case} failed with exit {completed.returncode}", file=sys.stderr)
            if completed.stdout:
                print(completed.stdout, file=sys.stderr, end="")
            if completed.stderr:
                print(completed.stderr, file=sys.stderr, end="")
            return completed.returncode or 1
    print("pass: portable model dispatch cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
