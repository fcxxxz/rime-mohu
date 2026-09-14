#!/usr/bin/env python3
"""Evaluate index-preserving qwen-semantic-rerank/v1 menu replay JSONL."""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.evaluate_tiger_reranker import DEFAULT_BOOTSTRAP_SAMPLES, DEFAULT_SEED
from tools.qwen_semantic_rerank import (
    FORMAT_VERSION,
    SemanticMenuValidationError,
    eligible_menu_indices,
    make_scoring_request,
    validate_menu_record,
    validate_scoring_response,
)

EVALUATION_FORMAT_VERSION = "qwen-semantic-rerank-eval/v1"
EVALUATION_ROW_FIELDS = {
    "format_version",
    "menu",
    "model_profile",
    "semantic_response",
    "final_menu_indices",
    "gate",
}
GATE_FIELDS = {"open", "fail_open", "protected_veto"}


class SemanticEvaluationError(ValueError):
    """An evaluation row violates the semantic replay contract."""


def _mapping(value: object, location: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SemanticEvaluationError(f"{location}: must be an object")
    return value


def _reject_unknown_fields(mapping: Mapping[str, object], allowed: set[str], location: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise SemanticEvaluationError(f"{location}: unknown fields {unknown}")


def _required_field(mapping: Mapping[str, object], name: str, location: str) -> object:
    if name not in mapping:
        raise SemanticEvaluationError(f"{location}: missing required field '{name}'")
    return mapping[name]


def _string(value: object, field: str, location: str) -> str:
    if not isinstance(value, str) or not value:
        raise SemanticEvaluationError(f"{location}: field '{field}' must be a non-empty string")
    return value


def _boolean(value: object, field: str, location: str) -> bool:
    if not isinstance(value, bool):
        raise SemanticEvaluationError(f"{location}: field '{field}' must be a boolean")
    return value


def _indexes(value: object, expected: Sequence[int], location: str) -> list[int]:
    if not isinstance(value, list) or any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        raise SemanticEvaluationError(f"{location}: must be an integer array")
    indexes = list(value)
    if len(indexes) != len(expected) or set(indexes) != set(expected) or len(set(indexes)) != len(indexes):
        raise SemanticEvaluationError(f"{location}: must be a full permutation of native menu indexes")
    return indexes


def validate_evaluation_row(
    row: object,
    *,
    model_profile: str,
    location: str = "row",
) -> dict[str, object]:
    mapping = _mapping(row, location)
    _reject_unknown_fields(mapping, EVALUATION_ROW_FIELDS, location)
    version = _required_field(mapping, "format_version", location)
    if version != EVALUATION_FORMAT_VERSION:
        raise SemanticEvaluationError(f"{location}: unsupported format_version {version!r}")
    try:
        menu = validate_menu_record(
            _required_field(mapping, "menu", location), location=f"{location}: menu"
        )
    except SemanticMenuValidationError as error:
        raise SemanticEvaluationError(str(error)) from error
    native_indexes = [int(candidate["menu_index"]) for candidate in menu["candidates"]]
    final_indexes = _indexes(
        _required_field(mapping, "final_menu_indices", location),
        native_indexes,
        f"{location}: final_menu_indices",
    )
    gate = _mapping(_required_field(mapping, "gate", location), f"{location}: gate")
    _reject_unknown_fields(gate, GATE_FIELDS, f"{location}: gate")
    gate_open = _boolean(_required_field(gate, "open", f"{location}: gate"), "open", f"{location}: gate")
    fail_open = _boolean(
        _required_field(gate, "fail_open", f"{location}: gate"),
        "fail_open",
        f"{location}: gate",
    )
    protected_veto = _boolean(
        _required_field(gate, "protected_veto", f"{location}: gate"),
        "protected_veto",
        f"{location}: gate",
    )
    expected_profile = _string(model_profile, "model_profile", "evaluation")
    row_profile = _string(
        _required_field(mapping, "model_profile", location), "model_profile", location
    )
    if row_profile != expected_profile:
        raise SemanticEvaluationError(f"{location}: model_profile does not match trusted evaluation profile")
    raw_response = _required_field(mapping, "semantic_response", location)
    response: dict[str, object] | None = None
    expected_indices = eligible_menu_indices(menu)
    if raw_response is not None:
        try:
            response = validate_scoring_response(
                raw_response,
                make_scoring_request(menu, model_profile=expected_profile),
                location=f"{location}: semantic_response",
            )
        except SemanticMenuValidationError as error:
            raise SemanticEvaluationError(str(error)) from error
    if gate_open and not fail_open and raw_response is None:
        raise SemanticEvaluationError(f"{location}: open gate without fail-open requires a response")
    preserves_native_order = not gate_open or fail_open or protected_veto
    if preserves_native_order:
        if final_indexes != native_indexes:
            raise SemanticEvaluationError(
                f"{location}: closed, fail-open, or protection-veto state must preserve native menu order"
            )
    elif response is not None:
        provided = response.get("permutation")
        if provided is None:
            score_by_index = {int(item["menu_index"]): float(item["score"]) for item in response["scores"]}
            provided = sorted(expected_indices, key=lambda index: (-score_by_index[index], index))
        applied = list(native_indexes)
        iterator = iter(provided)
        eligible_set = set(expected_indices)
        for position, menu_index in enumerate(native_indexes):
            if menu_index in eligible_set:
                applied[position] = next(iterator)
        if final_indexes != applied:
            raise SemanticEvaluationError(f"{location}: final_menu_indices do not match the validated response")
    return {
        "format_version": EVALUATION_FORMAT_VERSION,
        "menu": menu,
        "model_profile": expected_profile,
        "semantic_response": response,
        "final_menu_indices": final_indexes,
        "gate": {
            "open": gate_open,
            "fail_open": fail_open,
            "protected_veto": protected_veto,
        },
    }


def _outcome(row: object, location: str, *, model_profile: str) -> dict[str, object]:
    validated = validate_evaluation_row(row, model_profile=model_profile, location=location)
    menu = validated["menu"]
    candidates = menu["candidates"]
    target_visible = bool(menu["target_visible"])
    target_index = int(menu["oracle_rank"]) - 1 if target_visible else None
    baseline_correct = target_index == 0
    final_indexes = validated["final_menu_indices"]
    reranked_correct = target_index is not None and final_indexes[0] == target_index
    eligible = eligible_menu_indices(menu)
    response = validated["semantic_response"]
    return {
        "source": str(menu["source"]),
        "schema": str(menu["schema"]),
        "candidate_count": str(len(candidates)),
        "raw_length": len(str(menu["raw_input"])),
        "context_length": len(str(menu["committed_context"])),
        "oracle_correct": target_visible,
        "baseline_correct": baseline_correct,
        "reranked_correct": reranked_correct,
        "correction": not baseline_correct and reranked_correct,
        "harm": baseline_correct and not reranked_correct,
        "gate_open": bool(validated["gate"]["open"]),
        "fail_open": bool(validated["gate"]["fail_open"]),
        "protected_veto": bool(validated["gate"]["protected_veto"]),
        "reversal": final_indexes[0] != 0,
        "response_present": response is not None,
        "eligible_count": str(len(eligible)),
    }


def _summary(outcomes: Sequence[Mapping[str, object]]) -> dict[str, int | float]:
    total = len(outcomes)
    count = lambda field: sum(bool(item[field]) for item in outcomes)
    denominator = total or 1
    baseline_correct = count("baseline_correct")
    reranked_correct = count("reranked_correct")
    oracle_correct = count("oracle_correct")
    return {
        "total": total,
        "baseline_correct": baseline_correct,
        "reranked_correct": reranked_correct,
        "oracle_correct": oracle_correct,
        "corrections": count("correction"),
        "harms": count("harm"),
        "candidate_membership_failures": 0,
        "baseline_accuracy": baseline_correct / denominator if total else 0.0,
        "reranked_accuracy": reranked_correct / denominator if total else 0.0,
        "oracle_accuracy": oracle_correct / denominator if total else 0.0,
        "accuracy_delta": (reranked_correct - baseline_correct) / denominator if total else 0.0,
        "gate_open_rate": count("gate_open") / denominator if total else 0.0,
        "fail_open_rate": count("fail_open") / denominator if total else 0.0,
        "protected_veto_rate": count("protected_veto") / denominator if total else 0.0,
        "reversal_rate": count("reversal") / denominator if total else 0.0,
    }


def _slices(outcomes: Sequence[Mapping[str, object]], field: str) -> dict[str, dict[str, int | float]]:
    labels = sorted({str(item[field]) for item in outcomes})
    return {label: _summary([item for item in outcomes if str(item[field]) == label]) for label in labels}


def evaluate_rows(rows: Iterable[object], *, model_profile: str) -> dict[str, Any]:
    outcomes = [
        _outcome(row, f"row[{index}]", model_profile=model_profile)
        for index, row in enumerate(rows)
    ]
    totals = _summary(outcomes)
    return {
        **totals,
        "totals": dict(totals),
        "slices": {
            "source": _slices(outcomes, "source"),
            "schema": _slices(outcomes, "schema"),
            "candidate_count": _slices(outcomes, "candidate_count"),
            "eligible_count": _slices(outcomes, "eligible_count"),
        },
    }


def paired_bootstrap(
    rows: Iterable[object],
    *,
    model_profile: str,
    samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    seed: int = DEFAULT_SEED,
) -> dict[str, object]:
    if isinstance(samples, bool) or not isinstance(samples, int) or samples <= 0:
        raise ValueError("samples must be a positive integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    outcomes = [
        _outcome(row, f"row[{index}]", model_profile=model_profile)
        for index, row in enumerate(rows)
    ]
    if not outcomes:
        raise ValueError("paired bootstrap requires at least one row")
    differences = [int(bool(item["reranked_correct"])) - int(bool(item["baseline_correct"])) for item in outcomes]
    generator = random.Random(seed)
    count = len(differences)
    values = sorted(
        sum(differences[generator.randrange(count)] for _ in range(count)) / count
        for _ in range(samples)
    )

    def percentile(probability: float) -> float:
        position = (len(values) - 1) * probability
        lower, upper = math.floor(position), math.ceil(position)
        if lower == upper:
            return values[lower]
        return values[lower] + (values[upper] - values[lower]) * (position - lower)

    return {
        "observed_delta": sum(differences) / count,
        "bootstrap_mean_delta": sum(values) / samples,
        "confidence_interval_95": [percentile(0.025), percentile(0.975)],
        "probability_improvement": sum(value > 0 for value in values) / samples,
        "samples": samples,
        "seed": seed,
    }


def load_rows(path: str | Path, *, model_profile: str) -> list[dict[str, object]]:
    source = Path(path)
    rows: list[dict[str, object]] = []
    with source.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as error:
                raise SemanticEvaluationError(
                    f"{source}:{number}: invalid JSON at column {error.colno}: {error.msg}"
                ) from error
            rows.append(
                validate_evaluation_row(raw, model_profile=model_profile, location=f"{source}:{number}")
            )
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dump", type=Path)
    parser.add_argument("--model-profile", required=True)
    parser.add_argument("--bootstrap-samples", type=int, default=DEFAULT_BOOTSTRAP_SAMPLES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output", "-o", type=Path)
    arguments = parser.parse_args(argv)
    try:
        rows = load_rows(arguments.dump, model_profile=arguments.model_profile)
        result = evaluate_rows(rows, model_profile=arguments.model_profile)
        result["bootstrap"] = paired_bootstrap(
            rows,
            model_profile=arguments.model_profile,
            samples=arguments.bootstrap_samples,
            seed=arguments.seed,
        )
        rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if arguments.output is None or str(arguments.output) == "-":
            sys.stdout.write(rendered)
        else:
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            arguments.output.write_text(rendered, encoding="utf-8")
    except (OSError, SemanticEvaluationError, SemanticMenuValidationError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
