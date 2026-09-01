from __future__ import annotations

import binascii
import math
import random
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class CandidateResult:
    candidates: tuple[str, ...]
    elapsed_us: int | None = None
    status: str = "ok"
    truncated: bool = False
    error: str | None = None


def _decode_hex(value: str) -> str:
    return binascii.unhexlify(value).decode("utf-8")


def parse_rime_dump(path: str | Path) -> dict[str, CandidateResult]:
    """Parse the C/E stream emitted by ``rime_candidate_dump``."""

    pending: dict[str, list[str]] = {}
    completed: set[str] = set()
    result: dict[str, CandidateResult] = {}
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            fields = line.rstrip("\n").split("\t")
            kind = fields[0]
            if kind == "C":
                if len(fields) != 5:
                    raise ValueError(f"invalid Rime row at line {line_number}: exactly 5 fields required")
                raw = fields[1]
                if not raw or raw in completed:
                    raise ValueError(f"invalid Rime row at line {line_number}: candidate after end")
                try:
                    text = _decode_hex(fields[3])
                    _decode_hex(fields[4])
                except (binascii.Error, UnicodeDecodeError) as exc:
                    raise ValueError(f"invalid Rime candidate encoding at line {line_number}: {exc}") from exc
                try:
                    rank = int(fields[2])
                except ValueError as exc:
                    raise ValueError(f"invalid Rime candidate rank at line {line_number}") from exc
                if rank != len(pending.get(raw, ())) + 1:
                    raise ValueError(f"invalid Rime candidate rank at line {line_number}")
                if not text:
                    raise ValueError(f"invalid Rime candidate text at line {line_number}")
                if text in pending.get(raw, ()):
                    raise ValueError(f"duplicate Rime candidate text at line {line_number}")
                pending.setdefault(raw, []).append(text)
            elif kind == "E":
                if len(fields) != 5:
                    raise ValueError(f"invalid Rime row at line {line_number}: exactly 5 fields required")
                raw = fields[1]
                if not raw or raw in completed:
                    raise ValueError(f"duplicate end record for raw {raw!r} at line {line_number}")
                try:
                    count = int(fields[2])
                    elapsed_value = int(fields[4])
                except ValueError as exc:
                    raise ValueError(f"invalid Rime end record at line {line_number}: {exc}") from exc
                if count < 0:
                    raise ValueError(f"invalid Rime end count at line {line_number}")
                if fields[3] not in {"0", "1"}:
                    raise ValueError(f"invalid Rime truncation flag at line {line_number}")
                if elapsed_value < 0:
                    raise ValueError(f"Rime latency must be non-negative at line {line_number}")
                candidates = tuple(pending.get(raw, ()))
                if count != len(candidates):
                    raise ValueError(
                        f"candidate count mismatch: declared={count}, parsed={len(candidates)}"
                    )
                result[raw] = CandidateResult(
                    candidates=candidates,
                    elapsed_us=elapsed_value,
                    status="ok" if candidates else "empty",
                    truncated=fields[3] == "1",
                )
                pending.pop(raw, None)
                completed.add(raw)
            else:
                raise ValueError(f"invalid Rime row at line {line_number}")
    for raw, candidates in pending.items():
        raise ValueError(f"missing end record for raw {raw!r}")
    return result


def parse_tiger_dump(path: str | Path, *, latency_path: str | Path | None = None) -> dict[str, CandidateResult]:
    """Parse ``raw<TAB>text\x1fscore...`` native Tiger output."""

    result: dict[str, CandidateResult] = {}
    latencies: dict[str, int] = {}
    latency_file = Path(latency_path) if latency_path is not None else None
    if latency_file is not None:
        if not latency_file.is_file():
            raise FileNotFoundError(latency_file)
        with latency_file.open(encoding="utf-8") as latency_stream:
            for line in latency_stream:
                fields = line.rstrip("\n").split("\t")
                if len(fields) != 2 or not fields[0]:
                    raise ValueError("invalid Tiger latency row")
                if fields[0] in latencies:
                    raise ValueError(f"duplicate Tiger latency raw {fields[0]!r}")
                try:
                    value = int(fields[1])
                except ValueError:
                    raise ValueError("invalid Tiger latency value") from None
                if value < 0:
                    raise ValueError("Tiger latency must be non-negative")
                latencies[fields[0]] = value
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            fields = line.rstrip("\n").split("\t")
            if not fields or not fields[0]:
                continue
            raw = fields[0]
            if raw in result:
                raise ValueError(f"duplicate Tiger raw {raw!r} at line {line_number}")
            candidates: list[str] = []
            for cell in fields[1:]:
                if not cell:
                    continue
                if cell.count("\x1f") != 1:
                    raise ValueError(f"invalid Tiger candidate cell at line {line_number}")
                text, score_text = cell.split("\x1f", 1)
                if not text:
                    raise ValueError(f"invalid Tiger candidate cell at line {line_number}")
                try:
                    score = float(score_text)
                except ValueError as exc:
                    raise ValueError(f"invalid Tiger candidate score at line {line_number}") from exc
                if not math.isfinite(score):
                    raise ValueError(f"invalid Tiger candidate score at line {line_number}")
                if text in candidates:
                    raise ValueError(f"duplicate Tiger candidate text at line {line_number}")
                candidates.append(text)
            result[raw] = CandidateResult(
                candidates=tuple(candidates),
                elapsed_us=latencies.get(raw),
                status="ok" if candidates else "empty",
                error=None if candidates else f"no valid candidates at line {line_number}",
            )
    if latency_file is not None:
        result_raws = set(result)
        latency_raws = set(latencies)
        if result_raws != latency_raws:
            raise ValueError(
                f"latency raw set mismatch: results={len(result_raws)}, "
                f"latency={len(latency_raws)}"
            )
    return result


def _coerce_result(value: object) -> CandidateResult:
    if isinstance(value, CandidateResult):
        return value
    if isinstance(value, Mapping):
        candidates = tuple(str(item) for item in value.get("candidates", ()))
        elapsed = value.get("elapsed_us")
        return CandidateResult(
            candidates=candidates,
            elapsed_us=int(elapsed) if elapsed is not None else None,
            status=str(value.get("status", "ok" if candidates else "empty")),
            truncated=bool(value.get("truncated", False)),
            error=str(value["error"]) if value.get("error") is not None else None,
        )
    raise TypeError(f"unsupported candidate result: {type(value).__name__}")


def edit_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for i, char_left in enumerate(left, start=1):
        current = [i]
        for j, char_right in enumerate(right, start=1):
            current.append(min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + (char_left != char_right),
            ))
        previous = current
    return previous[-1]


def _percentile(values: Sequence[float], probability: float) -> float | None:
    if len(values) == 0:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _new_stat() -> dict[str, float | int | None]:
    return {
        "n": 0,
        "top1": 0,
        "top5": 0,
        "top10": 0,
        "top20": 0,
        "mrr": 0.0,
        "top1_ci_low": 0.0,
        "top1_ci_high": 0.0,
        "coverage": 0,
        "missing": 0,
        "empty": 0,
        "errors": 0,
        "char_accuracy": 0.0,
        "latency_mean_us": None,
        "latency_p50_us": None,
        "latency_p95_us": None,
        "latency_p99_us": None,
    }


def _finish_stat(stat: dict[str, float | int | None], latencies: list[float]) -> None:
    n = int(stat["n"])
    stat["char_accuracy"] = float(stat["char_accuracy"]) / n if n else 0.0
    stat["mrr"] = float(stat["mrr"]) / n if n else 0.0
    if n:
        # Wilson interval is stable at 0%/100% and avoids the misleading
        # negative/over-one normal interval for small slices.
        z = 1.959963984540054
        p = int(stat["top1"]) / n
        denominator = 1.0 + z * z / n
        centre = (p + z * z / (2.0 * n)) / denominator
        radius = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denominator
        stat["top1_ci_low"] = max(0.0, centre - radius)
        stat["top1_ci_high"] = min(1.0, centre + radius)
    stat["latency_mean_us"] = mean(latencies) if latencies else None
    stat["latency_p50_us"] = _percentile(latencies, 0.50)
    stat["latency_p95_us"] = _percentile(latencies, 0.95)
    stat["latency_p99_us"] = _percentile(latencies, 0.99)


def evaluate_cases(
    cases: Sequence[Mapping[str, object]],
    results: Mapping[str, Mapping[str, Mapping[str, object]]],
) -> tuple[list[dict[str, object]], dict[str, dict[str, dict[str, float | int | None]]]]:
    """Join expected cases with per-model/per-mode candidate maps."""

    rows: list[dict[str, object]] = []
    summary: dict[str, dict[str, dict[str, float | int | None]]] = {}
    latency: dict[tuple[str, str], list[float]] = {}
    for model, mode_maps in results.items():
        summary[model] = {}
        for mode, raw_map in mode_maps.items():
            summary[model][mode] = _new_stat()
            latency[(model, mode)] = []

    for case in cases:
        case_id = str(case["id"])
        source = str(case.get("source", ""))
        label = str(case.get("label", ""))
        expected = str(case["text"])
        modes = case.get("modes", {})
        if not isinstance(modes, Mapping):
            raise ValueError(f"case {case_id} has invalid modes")
        for model, mode_maps in results.items():
            for mode, raw_map in mode_maps.items():
                raw = str(modes.get(mode, ""))
                candidate_value = raw_map.get(raw)
                candidate_result = (
                    _coerce_result(candidate_value)
                    if candidate_value is not None
                    else CandidateResult((), status="missing", error="raw not found")
                )
                candidates = candidate_result.candidates
                top = candidates[0] if candidates else ""
                rank = next((index + 1 for index, item in enumerate(candidates) if item == expected), 0)
                distance = edit_distance(top, expected) if top else len(expected)
                denominator = max(len(top), len(expected), 1)
                stat = summary[model][mode]
                stat["n"] = int(stat["n"]) + 1
                stat["top1"] = int(stat["top1"]) + int(rank == 1)
                stat["top5"] = int(stat["top5"]) + int(0 < rank <= 5)
                stat["top10"] = int(stat["top10"]) + int(0 < rank <= 10)
                stat["top20"] = int(stat["top20"]) + int(0 < rank <= 20)
                stat["coverage"] = int(stat["coverage"]) + int(rank > 0)
                stat["mrr"] = float(stat["mrr"]) + (1.0 / rank if rank else 0.0)
                stat["char_accuracy"] = float(stat["char_accuracy"]) + (1.0 - distance / denominator)
                if candidate_result.status == "missing":
                    stat["missing"] = int(stat["missing"]) + 1
                if candidate_result.status == "empty":
                    stat["empty"] = int(stat["empty"]) + 1
                if candidate_result.status not in {"ok", "empty", "missing"}:
                    stat["errors"] = int(stat["errors"]) + 1
                if candidate_result.elapsed_us is not None:
                    latency[(model, mode)].append(float(candidate_result.elapsed_us))
                rows.append({
                    "id": case_id,
                    "source": source,
                    "label": label,
                    "text": expected,
                    "mode": mode,
                    "model": model,
                    "raw": raw,
                    "top1": top,
                    "rank": rank,
                    "candidates": list(candidates),
                    "status": candidate_result.status,
                    "elapsed_us": candidate_result.elapsed_us,
                })
    for model, mode_maps in summary.items():
        for mode, stat in mode_maps.items():
            _finish_stat(stat, latency[(model, mode)])
    return rows, summary


def paired_bootstrap_delta(
    baseline: Sequence[bool], challenger: Sequence[bool], *, samples: int = 10_000, seed: int = 20260829
) -> dict[str, float | int]:
    if len(baseline) != len(challenger) or not baseline:
        raise ValueError("paired samples must have equal non-zero length")
    if samples <= 0:
        raise ValueError("samples must be positive")
    differences = [int(new) - int(old) for old, new in zip(baseline, challenger)]
    observed = sum(differences) / len(differences)
    plus = differences.count(1)
    minus = differences.count(-1)
    zero = len(differences) - plus - minus
    try:
        import numpy as np  # type: ignore
    except ImportError:  # pragma: no cover - numpy ships with the project stack
        rng = random.Random(seed)
        draws = [
            sum(differences[rng.randrange(len(differences))] for _ in differences) / len(differences)
            for _ in range(samples)
        ]
    else:
        # A paired bootstrap draw only depends on the counts of -1, 0 and +1
        # differences.  Sampling those three categories avoids allocating a
        # samples-by-n index matrix (and is mathematically identical to row
        # resampling).
        rng = np.random.default_rng(seed)
        counts = rng.multinomial(
            len(differences),
            [plus / len(differences), minus / len(differences), zero / len(differences)],
            size=samples,
        )
        draws = (counts[:, 0] - counts[:, 1]) / len(differences)
    return {
        "n": len(differences),
        "samples": samples,
        "observed_delta": observed,
        "ci_low": float(_percentile(draws, 0.025)),
        "ci_high": float(_percentile(draws, 0.975)),
    }
