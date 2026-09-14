"""Validate and merge isolated pipeline-capture shards.

Checks every requested case appears exactly once, every record has one B and
one E row, candidate indexes are contiguous, contexts marked failed have no
candidate rows, and no case ID is duplicated across shards. Only complete,
validated output is written. Candidate/context text remains hex encoded.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def expected_ids(paths: list[Path]) -> set[str]:
    ids: set[str] = set()
    for path in paths:
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                case_id = line.split("\t", 1)[0]
                if case_id in ids:
                    raise SystemExit(f"duplicate input case_id: {case_id}")
                ids.add(case_id)
    return ids


def validate_shards(paths: list[Path], expected: set[str]) -> dict:
    states: dict[str, dict] = {}
    for path in paths:
        with path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                fields = line.rstrip("\n").split("\t")
                if len(fields) < 2 or fields[0] not in {"B", "C", "E"}:
                    raise SystemExit(f"{path}:{line_number}: malformed capture row")
                case_id = fields[1]
                if case_id not in expected:
                    raise SystemExit(f"{path}:{line_number}: unexpected case_id {case_id}")
                state = states.setdefault(case_id, {
                    "begins": 0, "ends": 0, "context_ok": None,
                    "next_index": 0, "end_count": None, "truncated": False,
                })
                if fields[0] == "B":
                    if len(fields) != 4 or fields[2] not in {"0", "1"}:
                        raise SystemExit(f"{path}:{line_number}: malformed B row")
                    state["begins"] += 1
                    state["context_ok"] = fields[2] == "1"
                elif fields[0] == "C":
                    if len(fields) < 4 or int(fields[2]) != state["next_index"]:
                        raise SystemExit(f"{path}:{line_number}: non-contiguous candidate index")
                    state["next_index"] += 1
                else:
                    if len(fields) != 5:
                        raise SystemExit(f"{path}:{line_number}: malformed E row")
                    state["ends"] += 1
                    state["end_count"] = int(fields[2])
                    state["truncated"] = fields[3] == "1"

    missing = expected - states.keys()
    if missing:
        raise SystemExit(f"capture missing {len(missing)} cases; first={sorted(missing)[:3]}")

    context_failed = target_empty = truncated = candidate_rows = 0
    for case_id, state in states.items():
        if state["begins"] != 1 or state["ends"] != 1:
            raise SystemExit(
                f"case {case_id}: expected one B/E, got {state['begins']}/{state['ends']}"
            )
        if state["end_count"] != state["next_index"]:
            raise SystemExit(f"case {case_id}: E count mismatch")
        if not state["context_ok"]:
            context_failed += 1
            if state["next_index"]:
                raise SystemExit(f"case {case_id}: failed context has candidates")
        elif not state["next_index"]:
            target_empty += 1
        if state["truncated"]:
            truncated += 1
        candidate_rows += state["next_index"]

    return {
        "format_version": "mohu-pipeline-capture-merge/v1",
        "expected_cases": len(expected),
        "captured_cases": len(states),
        "context_failed": context_failed,
        "empty_menu_after_context": target_empty,
        "truncated_menus": truncated,
        "candidate_rows": candidate_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, nargs="+", required=True)
    parser.add_argument("--captures", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()

    expected = expected_ids(args.cases)
    manifest = validate_shards(args.captures, expected)

    # Inputs are case-ID-disjoint by validation, so shard order is a stable
    # merge order; stream directly without retaining candidate rows in RAM.
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as sink:
        for path in args.captures:
            with path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    sink.write(line if line.endswith("\n") else line + "\n")
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
