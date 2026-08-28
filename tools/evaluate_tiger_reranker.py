#!/usr/bin/env python3
"""Validate and evaluate sentence-reranker JSONL dumps."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

DEFAULT_SEED = 20260826
DEFAULT_BOOTSTRAP_SAMPLES = 10_000
CASE_FIELDS = ("id", "source", "mode", "raw", "expected")
CANDIDATE_FIELDS = ("text", "base_score", "confidence", "segmented")
RAW_LENGTH_BANDS = (
    ("0-5", 0, 5),
    ("6-9", 6, 9),
    ("10-15", 10, 15),
    ("16+", 16, None),
)


class RowValidationError(ValueError):
    """A JSONL row does not satisfy the evaluation contract."""


def _require_mapping(value: object, location: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RowValidationError(f"{location}: row must be a JSON object")
    return value


def _require_field(row: Mapping[str, object], field: str, location: str) -> object:
    if field not in row:
        raise RowValidationError(f"{location}: missing required field '{field}'")
    return row[field]


def _require_non_empty_string(value: object, field: str, location: str) -> str:
    if not isinstance(value, str):
        raise RowValidationError(f"{location}: field '{field}' must be a string")
    if not value:
        raise RowValidationError(f"{location}: field '{field}' must not be empty")
    return value


def _require_finite_number(value: object, field: str, location: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RowValidationError(f"{location}: field '{field}' must be a finite number")
    try:
        number = float(value)
    except OverflowError as error:
        raise RowValidationError(f"{location}: field '{field}' must be a finite number") from error
    if not math.isfinite(number):
        raise RowValidationError(f"{location}: field '{field}' must be a finite number")
    return number


def validate_case_row(row: object, *, location: str = "row") -> dict[str, object]:
    """Validate a case row and return a shallow, JSON-compatible copy."""

    mapping = _require_mapping(row, location)
    validated = dict(mapping)
    for field in CASE_FIELDS:
        value = _require_field(mapping, field, location)
        validated[field] = _require_non_empty_string(value, field, location)
    return validated


def validate_dump_row(row: object, *, location: str = "row") -> dict[str, object]:
    """Validate a decoder dump row, including its explicit reranked order."""

    validated = validate_case_row(row, location=location)
    mapping = _require_mapping(row, location)

    raw_candidates = _require_field(mapping, "candidates", location)
    if not isinstance(raw_candidates, list) or not raw_candidates:
        raise RowValidationError(f"{location}: field 'candidates' must be a non-empty array")

    candidates: list[dict[str, object]] = []
    candidate_texts: list[str] = []
    for index, raw_candidate in enumerate(raw_candidates):
        candidate_location = f"{location}: candidates[{index}]"
        candidate_mapping = _require_mapping(raw_candidate, candidate_location)
        candidate = dict(candidate_mapping)

        for field in CANDIDATE_FIELDS:
            _require_field(candidate_mapping, field, candidate_location)
        candidate["text"] = _require_non_empty_string(
            candidate_mapping["text"], "text", candidate_location
        )
        candidate["base_score"] = _require_finite_number(
            candidate_mapping["base_score"], "base_score", candidate_location
        )
        candidate["confidence"] = _require_finite_number(
            candidate_mapping["confidence"], "confidence", candidate_location
        )
        if not isinstance(candidate_mapping["segmented"], str):
            raise RowValidationError(f"{candidate_location}: field 'segmented' must be a string")
        candidate["segmented"] = candidate_mapping["segmented"]
        candidate_texts.append(candidate["text"])
        candidates.append(candidate)

    if len(set(candidate_texts)) != len(candidate_texts):
        raise RowValidationError(f"{location}: candidate texts must be unique")

    raw_reranked = _require_field(mapping, "reranked", location)
    if not isinstance(raw_reranked, list) or not raw_reranked:
        raise RowValidationError(f"{location}: field 'reranked' must be a non-empty array")
    reranked: list[str] = []
    for index, value in enumerate(raw_reranked):
        item_location = f"{location}: reranked[{index}]"
        if not isinstance(value, str):
            raise RowValidationError(f"{item_location} must be a string")
        if not value:
            raise RowValidationError(f"{item_location} must not be empty")
        reranked.append(value)

    if len(set(reranked)) != len(reranked):
        duplicate_indices = [
            index for index, value in enumerate(reranked) if reranked.count(value) > 1
        ]
        raise RowValidationError(
            f"{location}: field 'reranked' must be a full permutation without duplicates; "
            f"duplicate_indices={duplicate_indices}"
        )
    candidate_text_set = set(candidate_texts)
    reranked_text_set = set(reranked)
    if len(reranked) != len(candidate_texts) or reranked_text_set != candidate_text_set:
        missing_indices = [
            index for index, value in enumerate(candidate_texts) if value not in reranked_text_set
        ]
        unknown_indices = [
            index for index, value in enumerate(reranked) if value not in candidate_text_set
        ]
        raise RowValidationError(
            f"{location}: field 'reranked' must be a full permutation of candidate texts; "
            f"candidate_count={len(candidate_texts)}, reranked_count={len(reranked)}, "
            f"missing_candidate_indices={missing_indices}, "
            f"unknown_reranked_indices={unknown_indices}"
        )

    validated["candidates"] = candidates
    validated["reranked"] = reranked
    return validated


def _load_jsonl(path: str | Path, *, dump: bool) -> list[dict[str, object]]:
    source_path = Path(path)
    rows: list[dict[str, object]] = []
    seen_ids: dict[str, int] = {}
    with source_path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            location = f"{source_path}:{line_number}"
            try:
                raw_row = json.loads(line)
            except json.JSONDecodeError as error:
                raise RowValidationError(
                    f"{location}: invalid JSON at column {error.colno}: {error.msg}"
                ) from error
            validator = validate_dump_row if dump else validate_case_row
            row = validator(raw_row, location=location)
            case_id = str(row["id"])
            if case_id in seen_ids:
                raise RowValidationError(
                    f"{location}: duplicate id {case_id!r}; first seen on line {seen_ids[case_id]}"
                )
            seen_ids[case_id] = line_number
            rows.append(row)
    return rows


def load_case_rows(path: str | Path) -> list[dict[str, object]]:
    """Load and validate case-contract rows from a JSONL file."""

    return _load_jsonl(path, dump=False)


def load_dump_rows(path: str | Path) -> list[dict[str, object]]:
    """Load and validate dump-contract rows from a JSONL file."""

    return _load_jsonl(path, dump=True)


def _raw_length_band(raw_length: int) -> str:
    for label, lower, upper in RAW_LENGTH_BANDS:
        if raw_length >= lower and (upper is None or raw_length <= upper):
            return label
    raise AssertionError(f"unreachable raw length: {raw_length}")


def _row_outcome(row: object, location: str) -> dict[str, object]:
    validated = validate_dump_row(row, location=location)
    candidates = validated["candidates"]
    assert isinstance(candidates, list)
    baseline = candidates[0]
    reranked = validated["reranked"]
    assert isinstance(reranked, list)
    expected = str(validated["expected"])
    candidate_texts = [str(candidate["text"]) for candidate in candidates]
    baseline_correct = baseline["text"] == expected
    reranked_correct = reranked[0] == expected
    return {
        "source": validated["source"],
        "mode": validated["mode"],
        "raw_length_band": _raw_length_band(len(str(validated["raw"]))),
        "baseline_correct": baseline_correct,
        "reranked_correct": reranked_correct,
        "oracle_correct": expected in candidate_texts,
        "correction": not baseline_correct and reranked_correct,
        "harm": baseline_correct and not reranked_correct,
    }


def _collect_outcomes(rows: Iterable[object]) -> list[dict[str, object]]:
    return [_row_outcome(row, f"row[{index}]") for index, row in enumerate(rows)]


def _summary(outcomes: Sequence[Mapping[str, object]]) -> dict[str, int | float]:
    total = len(outcomes)
    baseline_correct = sum(bool(item["baseline_correct"]) for item in outcomes)
    reranked_correct = sum(bool(item["reranked_correct"]) for item in outcomes)
    oracle_correct = sum(bool(item["oracle_correct"]) for item in outcomes)
    corrections = sum(bool(item["correction"]) for item in outcomes)
    harms = sum(bool(item["harm"]) for item in outcomes)
    denominator = total or 1
    baseline_accuracy = baseline_correct / denominator if total else 0.0
    reranked_accuracy = reranked_correct / denominator if total else 0.0
    oracle_accuracy = oracle_correct / denominator if total else 0.0
    return {
        "total": total,
        "baseline_correct": baseline_correct,
        "reranked_correct": reranked_correct,
        "oracle_correct": oracle_correct,
        "corrections": corrections,
        "harms": harms,
        "baseline_accuracy": baseline_accuracy,
        "reranked_accuracy": reranked_accuracy,
        "oracle_accuracy": oracle_accuracy,
        "accuracy_delta": reranked_accuracy - baseline_accuracy,
    }


def _categorical_slices(
    outcomes: Sequence[Mapping[str, object]], field: str
) -> dict[str, dict[str, int | float]]:
    labels = sorted({str(item[field]) for item in outcomes})
    return {
        label: _summary([item for item in outcomes if str(item[field]) == label])
        for label in labels
    }


def evaluate_rows(rows: Iterable[object]) -> dict[str, Any]:
    """Evaluate validated decoder dumps and return aggregate and slice metrics."""

    outcomes = _collect_outcomes(rows)
    totals = _summary(outcomes)
    raw_length_band = {
        label: _summary([item for item in outcomes if item["raw_length_band"] == label])
        for label, _, _ in RAW_LENGTH_BANDS
    }
    return {
        **totals,
        "totals": dict(totals),
        "slices": {
            "source": _categorical_slices(outcomes, "source"),
            "mode": _categorical_slices(outcomes, "mode"),
            "raw_length_band": raw_length_band,
        },
    }


def _percentile(sorted_values: Sequence[float], probability: float) -> float:
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def paired_bootstrap(
    rows: Iterable[object],
    *,
    samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    seed: int = DEFAULT_SEED,
) -> dict[str, object]:
    """Bootstrap the paired reranked-minus-baseline accuracy delta."""

    if isinstance(samples, bool) or not isinstance(samples, int) or samples <= 0:
        raise ValueError("samples must be a positive integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    outcomes = _collect_outcomes(rows)
    if not outcomes:
        raise ValueError("paired bootstrap requires at least one row")

    differences = [
        int(bool(item["reranked_correct"])) - int(bool(item["baseline_correct"]))
        for item in outcomes
    ]
    count = len(differences)
    observed_delta = sum(differences) / count
    generator = random.Random(seed)
    bootstrap_deltas = [
        sum(differences[generator.randrange(count)] for _ in range(count)) / count
        for _ in range(samples)
    ]
    bootstrap_deltas.sort()
    return {
        "observed_delta": observed_delta,
        "bootstrap_mean_delta": sum(bootstrap_deltas) / samples,
        "confidence_interval_95": [
            _percentile(bootstrap_deltas, 0.025),
            _percentile(bootstrap_deltas, 0.975),
        ],
        "probability_improvement": sum(delta > 0 for delta in bootstrap_deltas) / samples,
        "samples": samples,
        "seed": seed,
    }


def _metadata_values(
    values: Sequence[str] | None,
    count: int,
    *,
    option: str,
    defaults: Sequence[str],
) -> list[str]:
    if values is None:
        return list(defaults)
    if isinstance(values, str) or len(values) != count:
        raise ValueError(f"{option} must be supplied once for each corpus path")
    result = list(values)
    if any(not value for value in result):
        raise ValueError(f"{option} values must not be empty")
    return result


def _manifest_path(path: Path, root: Path | None) -> str:
    resolved = path.resolve()
    if root is not None:
        try:
            return resolved.relative_to(root.resolve()).as_posix()
        except ValueError as error:
            raise ValueError(f"corpus path is outside manifest root: {path}") from error
    if path.is_absolute():
        return f"<external>/{resolved.name}"
    return path.as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _non_empty_line_count(path: Path) -> int:
    with path.open(encoding="utf-8") as stream:
        return sum(1 for line in stream if line.strip())


def build_manifest(
    paths: Sequence[str | Path],
    *,
    source_labels: Sequence[str] | None = None,
    license_notes: Sequence[str] | None = None,
    generated_date: str | None = None,
    root: str | Path | None = None,
) -> dict[str, object]:
    """Build metadata for local corpora without copying any corpus bodies."""

    corpus_paths = [Path(path) for path in paths]
    if not corpus_paths:
        raise ValueError("manifest requires at least one corpus path")
    labels = _metadata_values(
        source_labels,
        len(corpus_paths),
        option="source label",
        defaults=[path.stem for path in corpus_paths],
    )
    notes = _metadata_values(
        license_notes,
        len(corpus_paths),
        option="license note",
        defaults=["License not specified; verify source terms before use."] * len(corpus_paths),
    )
    manifest_date = generated_date or date.today().isoformat()
    try:
        date.fromisoformat(manifest_date)
    except ValueError as error:
        raise ValueError("generated date must use YYYY-MM-DD format") from error
    root_path = Path(root) if root is not None else None
    sources = []
    for path, label, note in zip(corpus_paths, labels, notes, strict=True):
        if not path.is_file():
            raise ValueError(f"corpus path is not a file: {path}")
        sources.append(
            {
                "source": label,
                "path": _manifest_path(path, root_path),
                "line_count": _non_empty_line_count(path),
                "sha256": _sha256(path),
                "license_note": note,
            }
        )
    return {"version": 1, "generated_date": manifest_date, "sources": sources}


def _write_json(payload: Mapping[str, object], output: Path | None) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output is None or str(output) == "-":
        sys.stdout.write(rendered)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")


def _add_evaluate_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "evaluate",
        aliases=["metrics"],
        help="evaluate a reranker dump JSONL file",
    )
    parser.add_argument("dump", type=Path, help="dump-contract JSONL file")
    parser.add_argument("--bootstrap-samples", type=int, default=DEFAULT_BOOTSTRAP_SAMPLES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output", "-o", type=Path)
    parser.set_defaults(handler=_run_evaluate)


def _run_evaluate(arguments: argparse.Namespace) -> None:
    rows = load_dump_rows(arguments.dump)
    result = evaluate_rows(rows)
    result["bootstrap"] = paired_bootstrap(
        rows,
        samples=arguments.bootstrap_samples,
        seed=arguments.seed,
    )
    _write_json(result, arguments.output)


def _run_manifest(arguments: argparse.Namespace) -> None:
    manifest = build_manifest(
        arguments.paths,
        source_labels=arguments.source_labels,
        license_notes=arguments.license_notes,
        generated_date=arguments.generated_date,
        root=arguments.root,
    )
    _write_json(manifest, arguments.output)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_evaluate_parser(subparsers)

    manifest = subparsers.add_parser(
        "manifest", help="hash local corpus files and record provenance metadata"
    )
    manifest.add_argument("paths", nargs="+", type=Path, help="local corpus files")
    manifest.add_argument(
        "--source-label",
        dest="source_labels",
        action="append",
        required=True,
        help="source label; repeat once per corpus path",
    )
    manifest.add_argument(
        "--license-note",
        dest="license_notes",
        action="append",
        required=True,
        help="license/provenance note; repeat once per corpus path",
    )
    manifest.add_argument("--generated-date", default=date.today().isoformat())
    manifest.add_argument("--root", type=Path, default=Path.cwd())
    manifest.add_argument("--output", "-o", type=Path)
    manifest.set_defaults(handler=_run_manifest)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        arguments.handler(arguments)
    except (OSError, RowValidationError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
