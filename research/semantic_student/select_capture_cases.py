"""Select deterministic production-menu capture cases for C-stage training.

Keep every raw-decoder error (all correction opportunities) and a stable 1/N
hash sample of raw-correct menus (preservation/anti-regression examples).
Selection is deterministic by case_id and records the exact counts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def keep_preserve(case_id: str, modulus: int) -> bool:
    digest = hashlib.blake2b(case_id.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % modulus == 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--preserve-modulus", type=int, default=8)
    args = parser.parse_args()

    ranks: dict[str, int] = {}
    for shard in sorted(args.dataset.glob("train-*.jsonl")):
        with shard.open("r", encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    row = json.loads(line)
                    ranks[row["case_id"]] = row["oracle_rank"]

    total = wrong = preserve = missing = 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.cases.open("r", encoding="utf-8") as source, \
         args.out.open("w", encoding="utf-8") as sink:
        for line in source:
            if not line.strip():
                continue
            case_id = line.split("\t", 1)[0]
            rank = ranks.get(case_id)
            if rank is None:
                missing += 1
                continue
            selected = rank > 1 or keep_preserve(case_id, args.preserve_modulus)
            if not selected:
                continue
            sink.write(line)
            total += 1
            if rank > 1:
                wrong += 1
            else:
                preserve += 1
    print(json.dumps({
        "selected": total,
        "raw_wrong_all": wrong,
        "raw_right_hash_sample": preserve,
        "preserve_modulus": args.preserve_modulus,
        "missing_case_id": missing,
    }, indent=2))


if __name__ == "__main__":
    main()
