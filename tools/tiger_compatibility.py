from collections.abc import Iterable
from pathlib import Path

from tiger_aux import load_tiger_codes, select_longest_codes


def derive_compatibility_auxiliaries(codes: Iterable[str]) -> list[str]:
    result: list[str] = []
    for code in select_longest_codes(codes):
        if len(code) >= 3:
            result.append(code[0] + code[2])
        if len(code) >= 4:
            result.append(code[0] + code[3])
    return list(dict.fromkeys(result))


def build_compatibility_auxiliary_map(path: Path) -> dict[str, list[str]]:
    result = {}
    for char, codes in load_tiger_codes(path).items():
        auxiliaries = derive_compatibility_auxiliaries(codes)
        if auxiliaries:
            result[char] = auxiliaries
    return result
