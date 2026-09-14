"""Pair a validated production capture with raw record metadata, streaming.

Candidate identity is the production ``menu_index`` only. Public C-API menu
observations cannot be joined to raw-decode evidence by display text, so all
production candidates carry explicit unknown score/source fields; raw records
are retained separately for lineage diagnostics only.

A temporary SQLite index holds only records referenced by the capture, avoiding
loading the 10GB native-replay dataset or hundreds of thousands of menus into
RAM. Capture rows must be grouped B, C*, E per case as emitted by the probe and
validated by ``merge_pipeline_capture.py``.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import sqlite3
import tempfile
from pathlib import Path

import sys as _sys

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in _sys.path:
    _sys.path.insert(0, str(_REPO))

from native_menu import NativeMenuDecoder


def capture_case_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            fields = line.rstrip("\n").split("\t")
            if fields and fields[0] == "E":
                if len(fields) != 5 or fields[1] in ids:
                    raise SystemExit(f"duplicate or malformed E row for {fields[1] if len(fields) > 1 else '?'}")
                ids.add(fields[1])
    return ids


def _canonical(payload: str) -> str:
    """Duplicate raw rows may differ only in key order; compare semantics."""
    return json.dumps(json.loads(payload), sort_keys=True, ensure_ascii=False)


def build_index(dataset: Path, split: str, wanted: set[str], db_path: Path) -> dict[str, int]:
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE records (case_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
    found: dict[str, str] = {}
    duplicate_rows = conflicting = 0
    batch: list[tuple[str, str]] = []
    for shard in sorted(dataset.glob(f"{split}-*.jsonl")):
        with shard.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                row = json.loads(line)
                case_id = row["case_id"]
                if case_id not in wanted:
                    continue
                canonical = _canonical(line)
                if case_id in found:
                    duplicate_rows += 1
                    if found[case_id] != canonical:
                        conflicting += 1
                    continue
                found[case_id] = canonical
                batch.append((case_id, line.rstrip("\n")))
                if len(batch) >= 1000:
                    connection.executemany("INSERT INTO records VALUES (?, ?)", batch)
                    connection.commit()
                    batch.clear()
    if batch:
        connection.executemany("INSERT INTO records VALUES (?, ?)", batch)
        connection.commit()
    connection.close()
    missing = wanted - found.keys()
    if missing:
        raise SystemExit(f"record index coverage mismatch: missing={len(missing)}")
    if conflicting:
        raise SystemExit(f"conflicting duplicate raw records: {conflicting}")
    return {"indexed_unique": len(found), "duplicate_raw_rows": duplicate_rows}


def decode_hex(value: str) -> str:
    try:
        return bytes.fromhex(value).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise SystemExit("capture contains invalid UTF-8 hex") from exc


def make_char_scorer():
    """V5 context char scores by final menu_index; the same signal the
    deployed gate filter computes, valid for fixed/smart/native candidates
    alike and never joined across menus by display text."""
    decoder = NativeMenuDecoder()
    lib = decoder.lib
    lib.tiger_engine_context_char_scores.restype = ctypes.c_int
    lib.tiger_engine_context_char_scores.argtypes = [
        ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int,
        ctypes.POINTER(ctypes.c_double),
    ]
    def score(context: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        scores = (ctypes.c_double * len(texts))()
        n = lib.tiger_engine_context_char_scores(
            decoder.handle, context.encode(), "\n".join(texts).encode(),
            len(texts), scores)
        if n != len(texts):
            raise SystemExit("context_char_scores count mismatch")
        return list(scores)
    return score, decoder.close


def emit_case(connection: sqlite3.Connection, sink, begin: list[str], candidates: list[list[str]],
              end: list[str], char_score) -> dict[str, int]:
    case_id = begin[1]
    result = connection.execute("SELECT payload FROM records WHERE case_id = ?", (case_id,)).fetchone()
    if result is None:
        raise SystemExit(f"capture case not found in record index: {case_id}")
    raw_record = json.loads(result[0])
    production = []
    texts = []
    for expected_index, fields in enumerate(candidates):
        if len(fields) < 4 or int(fields[2]) != expected_index:
            raise SystemExit(f"case {case_id}: invalid production candidate index")
        texts.append(decode_hex(fields[3]))
    context_ok = begin[2] == "1"
    context = decode_hex(begin[3]) if len(begin) > 3 else ""
    char_scores = char_score(context, texts) if context_ok and texts else []
    for expected_index, fields in enumerate(candidates):
        if len(fields) < 4 or int(fields[2]) != expected_index:
            raise SystemExit(f"case {case_id}: invalid production candidate index")
        production.append({
            "menu_index": expected_index,
            "text": texts[expected_index],
            "comment": decode_hex(fields[4]) if len(fields) > 4 else "",
            "native_score": char_scores[expected_index] if char_scores else None,
            "score_kind": "v5_context_char" if char_scores else "unavailable",
            "has_score": bool(char_scores),
            "evidence_alignment": "menu_index_context_char_scores",
        })
    if len(end) != 5 or int(end[2]) != len(production):
        raise SystemExit(f"case {case_id}: end count mismatch")
    context_ok = begin[2] == "1"
    context = decode_hex(begin[3]) if len(begin) > 3 else ""
    texts = [item["text"] for item in production]
    try:
        production_rank = texts.index(raw_record["target_text"]) + 1
    except ValueError:
        production_rank = None
    sink.write(json.dumps({
        "format_version": "mohu-production-menu-pair/v1",
        "case_id": case_id,
        "context_ok": context_ok,
        "context_committed": context,
        "raw_input": raw_record["raw_input"],
        "target_text": raw_record["target_text"],
        "production": production,
        "production_oracle_rank": production_rank,
        "raw_oracle_rank": raw_record["oracle_rank"],
        "raw_record_checksum_source": "native-replay/v2",
    }, ensure_ascii=False, separators=(",", ":")) + "\n")
    return {
        "paired": 1,
        "context_failed": int(not context_ok),
        "target_missing": int(production_rank is None),
        "production_top1": int(production_rank == 1),
        "raw_top1": int(raw_record["oracle_rank"] == 1),
    }


def pair_capture(capture: Path, db_path: Path, out: Path, char_score) -> dict[str, int]:
    connection = sqlite3.connect(db_path)
    totals = {"paired": 0, "context_failed": 0, "target_missing": 0,
              "production_top1": 0, "raw_top1": 0}
    begin: list[str] | None = None
    candidates: list[list[str]] = []
    out.parent.mkdir(parents=True, exist_ok=True)
    with capture.open("r", encoding="utf-8") as stream, out.open("w", encoding="utf-8") as sink:
        for line_number, line in enumerate(stream, 1):
            fields = line.rstrip("\n").split("\t")
            if fields[0] == "B":
                if begin is not None:
                    raise SystemExit(f"line {line_number}: nested B row")
                begin, candidates = fields, []
            elif fields[0] == "C":
                if begin is None or fields[1] != begin[1]:
                    raise SystemExit(f"line {line_number}: C outside current case")
                candidates.append(fields)
            elif fields[0] == "E":
                if begin is None or fields[1] != begin[1]:
                    raise SystemExit(f"line {line_number}: E outside current case")
                delta = emit_case(connection, sink, begin, candidates, fields, char_score)
                for key, value in delta.items():
                    totals[key] += value
                begin, candidates = None, []
            else:
                raise SystemExit(f"line {line_number}: unknown capture row")
    connection.close()
    if begin is not None:
        raise SystemExit("capture ends with incomplete case")
    return totals


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--split", required=True)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()

    wanted = capture_case_ids(args.capture)
    char_score, close_scorer = make_char_scorer()
    try:
        with tempfile.TemporaryDirectory(prefix="mohu-pair-") as directory:
            db_path = Path(directory) / "records.sqlite3"
            indexed = build_index(args.dataset, args.split, wanted, db_path)
            totals = pair_capture(args.capture, db_path, args.out, char_score)
    finally:
        close_scorer()
    manifest = {
        "format_version": "mohu-production-menu-pair-manifest/v1",
        "capture": str(args.capture.resolve()),
        "dataset": str(args.dataset.resolve()),
        "split": args.split,
        "index_stats": indexed,
        **totals,
        "production_top1_rate": totals["production_top1"] / totals["paired"] if totals["paired"] else None,
        "raw_top1_rate": totals["raw_top1"] / totals["paired"] if totals["paired"] else None,
        "candidate_alignment": "menu_index + v5_context_char_scores (per-slot, same signal as deployed gate filter)",
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
