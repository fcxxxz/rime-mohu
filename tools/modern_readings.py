from pathlib import Path

Reading = tuple[str, str]


def load_modern_readings(path: Path) -> set[Reading]:
    readings = set()
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        fields = raw_line.split("\t")
        if len(fields) >= 2 and len(fields[0]) == 1 and fields[1]:
            readings.add((fields[0], fields[1]))
    return readings


def simplified_reading_weight(
    character: str,
    pinyin: str,
    weight: float | int | str,
    modern_readings: set[Reading],
) -> float | int | str:
    return weight if (character, pinyin) in modern_readings else 0
