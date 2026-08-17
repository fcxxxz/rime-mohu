#!/usr/bin/env python3

import sys
from pathlib import Path

from tiger_aux import build_auxiliary_map, write_auxiliary_tsv


def load_characters(path: Path) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line or raw_line.startswith("#"):
            continue
        char = raw_line.split("\t", 1)[0]
        if len(char) == 1 and char not in seen:
            seen.add(char)
            result.append(char)
    return result


def main() -> None:
    characters = load_characters(Path("tools/data/chars.txt"))
    seen = set(characters)
    for char in load_characters(Path("tools/data/chars.dict.yaml")):
        if char not in seen:
            seen.add(char)
            characters.append(char)
    mapping = build_auxiliary_map(Path("tiger.dict.yaml"), characters)
    write_auxiliary_tsv({char: mapping[char] for char in characters}, sys.stdout)


if __name__ == "__main__":
    main()
