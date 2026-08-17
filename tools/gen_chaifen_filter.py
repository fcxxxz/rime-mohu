#!/usr/bin/env python3

from pathlib import Path

from tiger_aux import TIGER_EQUIVALENTS, load_tiger_codes, select_longest_codes

ROOT = Path(__file__).resolve().parents[1]


def load_characters(path: Path) -> list[str]:
    characters = []
    seen = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line or raw_line.startswith("#"):
            continue
        char = raw_line.split("\t", 1)[0]
        if len(char) == 1 and char not in seen:
            seen.add(char)
            characters.append(char)
    return sorted(characters)


def load_decompositions(path: Path) -> dict[str, str]:
    decompositions = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw_line or raw_line.startswith("#"):
            continue
        fields = raw_line.split("\t", 1)
        if len(fields) != 2 or len(fields[0]) != 1 or not fields[1]:
            raise ValueError(f"{path}:{line_number}: malformed Tiger decomposition")
        char, decomposition = fields
        if char in decompositions:
            raise ValueError(f"{path}:{line_number}: duplicate Tiger decomposition for {char}")
        decompositions[char] = decomposition.replace("&nbsp;", " ")
    return decompositions


def main() -> None:
    characters = load_characters(ROOT / "tools/data/chars.txt")
    decompositions = load_decompositions(ROOT / "tools/data/tiger_chaifen.txt")
    tiger_codes = load_tiger_codes(ROOT / "tiger.dict.yaml")

    for char in characters:
        source_char = TIGER_EQUIVALENTS.get(char, char)
        decomposition = decompositions.get(source_char)
        if decomposition is None:
            codes = select_longest_codes(tiger_codes.get(source_char, ()))
            if not codes:
                raise ValueError(f"missing Tiger decomposition and code for {char}")
            decomposition = "".join(f"〔{{{char}}} · {code}〕" for code in codes)
        print(f"{char}\t{decomposition}")


if __name__ == "__main__":
    main()
