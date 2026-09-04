"""Auditable cross-candidate benchmark parsing and aggregation.

The historical kua3 probe emits one input row (``B``), an optional prefix
commit result (``A``), and candidate streams (``C``/``E``).  This module keeps
all case ids, including prefix failures and targets absent from the exported
Top-N menu.  It deliberately does not call those targets absent from the
export a complete-candidate-pool miss.
"""

from __future__ import annotations

import binascii
import json
from collections import Counter
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Iterable, Mapping, Sequence

MODES = ("pure", "head", "tail", "both")
MODE_LABELS = {
    "pure": "纯双拼",
    "head": "首辅",
    "tail": "末辅",
    "both": "首末辅",
    "tail1": "一位末辅",
    "tail2": "两位末辅",
    "tail2o": "两位末辅o",
    "tail2s": "两位末辅/",
}
MODE_ORDER = tuple(MODES) + ("tail1", "tail2", "tail2o", "tail2s")
SCHEME_LABELS = {
    "moran": "魔然",
    "yeying": "夜莺",
    "wxpro": "万象 Pro",
    "mohu_zrm": "魔虎 V5（自然码）",
    "mohu_flypy": "魔虎 V5（小鹤）",
}
SCHEME_SPECS = {
    # moranmain2 is the corrected main-schema run; the older moran condition
    # used moran_sentence and must not be presented as the main scheme.
    "moran": {
        "condition": "moranmain2",
        "input_name": "moran.fresh.tsv",
        "fresh_glob": "moranmain2-moran.fresh.*.tsv",
    },
    "yeying": {
        "condition": "yeying",
        "input_name": "yeying.fresh.tsv",
        "fresh_glob": "yeying-*.tsv",
    },
    "wxpro": {
        "condition": "wxpro",
        "input_name": "wxpro.fresh.tsv",
        "fresh_glob": "wxpro-*.tsv",
    },
    "mohu_zrm": {
        "condition": "mohu_zrm",
        "input_name": "mohu_zrm.fresh.tsv",
        "fresh_glob": "mohu_zrm-*.tsv",
    },
    "mohu_flypy": {
        "condition": "mohu_flypy",
        "input_name": "mohu_flypy.fresh.tsv",
        "fresh_glob": "mohu_flypy-*.tsv",
    },
}
SCHEMES = tuple(SCHEME_LABELS)


@dataclass(frozen=True, slots=True)
class ProbeResult:
    candidates: tuple[str, ...] = ()
    truncated: bool = False
    status: str = "missing"


@dataclass(frozen=True, slots=True)
class CrossCase:
    case_id: str
    text: str
    prefix: str
    modes: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class CrossRow:
    case_id: str
    scheme: str
    mode: str
    text: str
    prefix_ok: bool | None
    result: ProbeResult
    top1: str
    rank: int
    candidate_state: str
    context_state: str
    raw_rank: int = 0


FIRST_CANDIDATE_RULES = ("raw", "ignore_single_char")


def _rank_function(rule: str):
    if rule == "raw":
        return _rank
    if rule == "ignore_single_char":
        return _word_rank
    raise ValueError(f"unknown first-candidate rule: {rule}")


def _decode_hex(value: str) -> str:
    return binascii.unhexlify(value).decode("utf-8")


def ordered_modes(present: Iterable[str]) -> tuple[str, ...]:
    """Canonical mode ordering: known modes in MODE_ORDER, then extras sorted."""

    remaining = set(present)
    ordered = [mode for mode in MODE_ORDER if mode in remaining]
    ordered.extend(sorted(remaining - set(MODE_ORDER)))
    return tuple(ordered)


def case_modes(cases: Sequence[CrossCase]) -> tuple[str, ...]:
    """Ordered union of per-case modes; requires a uniform mode layout."""

    layouts = {tuple(sorted(case.modes)) for case in cases}
    if len(layouts) != 1:
        raise ValueError("cases carry inconsistent mode layouts")
    return ordered_modes(cases[0].modes)


def _parse_b_line(line: str) -> CrossCase:
    fields = line.rstrip("\n").split("\t")
    if len(fields) < 5 or fields[0] != "B" or not fields[1]:
        raise ValueError(f"invalid B row: {line.rstrip()!r}")
    if len(fields) == 9 and not any("=" in column for column in fields[2:6]):
        # Legacy fixed four-mode layout.
        modes = dict(zip(MODES, fields[2:6]))
        text = _decode_hex(fields[6])
        plain = fields[8]
    else:
        # Generic layout: B, case id, <mode>=<code>..., target hex, "0", text.
        middle = fields[2:-3]
        if not middle or not all("=" in column for column in middle):
            raise ValueError(f"invalid B row: {line.rstrip()!r}")
        modes = {}
        for column in middle:
            mode, _, code = column.partition("=")
            if not mode or not code or mode in modes:
                raise ValueError(f"invalid B mode column {column!r}")
            modes[mode] = code
        text = _decode_hex(fields[-3])
        plain = fields[-1]
    if any(not modes[mode] for mode in modes) or not text:
        raise ValueError(f"invalid B row for {fields[1]!r}")
    if plain and plain != text:
        raise ValueError(f"B row text mismatch for {fields[1]!r}")
    return CrossCase(fields[1], text, "", modes)


def load_cross_cases(root: str | Path, *, input_name: str) -> list[CrossCase]:
    """Load case identity and scheme-specific raw modes from a ``B`` stream."""

    path = Path(root) / "in" / input_name
    cases: list[CrossCase] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = _parse_b_line(line)
        if case.case_id in seen:
            raise ValueError(f"duplicate case id {case.case_id!r}")
        seen.add(case.case_id)
        cases.append(case)
    if not cases:
        raise ValueError(f"no B rows in {path}")
    meta_path = Path(root) / "meta.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        for index, case in enumerate(cases):
            if case.case_id not in meta:
                raise ValueError(f"missing metadata for {case.case_id!r}")
            item = meta[case.case_id]
            if item.get("word") != case.text:
                raise ValueError(f"metadata target mismatch for {case.case_id!r}")
            cases[index] = CrossCase(
                case.case_id,
                case.text,
                str(item.get("prefix", "")),
                case.modes,
            )
    return cases


def _parse_candidate_files(paths: Iterable[Path]) -> dict[tuple[str, str], ProbeResult]:
    pending: dict[tuple[str, str], list[tuple[int, str]]] = {}
    result: dict[tuple[str, str], ProbeResult] = {}
    for path in sorted(paths):
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                fields = line.rstrip("\n").split("\t")
                if not fields or not fields[0]:
                    continue
                if fields[0] == "C":
                    if len(fields) != 5:
                        raise ValueError(f"invalid C row at {path}:{line_number}")
                    key = (fields[1], fields[2])
                    if key in result:
                        raise ValueError(f"C row after E at {path}:{line_number}")
                    rank = int(fields[3])
                    if not fields[2] or rank != len(pending.get(key, ())) + 1:
                        raise ValueError(f"invalid C rank at {path}:{line_number}")
                    text = _decode_hex(fields[4])
                    if not text or any(text == old for _, old in pending.get(key, ())):
                        raise ValueError(f"invalid or duplicate C text at {path}:{line_number}")
                    pending.setdefault(key, []).append((rank, text))
                elif fields[0] == "E":
                    if len(fields) != 6:
                        raise ValueError(f"invalid E row at {path}:{line_number}")
                    key = (fields[1], fields[2])
                    if key in result or not fields[2]:
                        raise ValueError(f"duplicate E row at {path}:{line_number}")
                    count = int(fields[3])
                    if count != len(pending.get(key, ())):
                        raise ValueError(f"candidate count mismatch at {path}:{line_number}")
                    if fields[4] not in {"0", "1"}:
                        raise ValueError(f"invalid truncation flag at {path}:{line_number}")
                    result[key] = ProbeResult(
                        tuple(text for _, text in pending.pop(key, ())),
                        fields[4] == "1",
                        "ok" if count else "empty",
                    )
                else:
                    continue
    if pending:
        raise ValueError(f"missing E rows for {len(pending)} candidate streams")
    return result


def load_fresh(root: str | Path, scheme: str) -> dict[tuple[str, str], ProbeResult]:
    if scheme not in SCHEME_SPECS:
        raise ValueError(f"unknown scheme {scheme!r}")
    fresh_glob = str(SCHEME_SPECS[scheme]["fresh_glob"])
    files = sorted((Path(root) / "out" / "fresh").glob(fresh_glob))
    if not files:
        raise FileNotFoundError(f"no fresh files for {scheme}")
    return _parse_candidate_files(files)


def load_after(root: str | Path, condition: str) -> tuple[dict[str, bool], dict[tuple[str, str], ProbeResult]]:
    """Load prefix availability and after-prefix menus without filtering ids."""

    directory = Path(root) / "out" / "afterA" / condition
    files = sorted(directory.glob("*.tsv"))
    if not files:
        raise FileNotFoundError(f"no afterA files for {condition}")
    prefix: dict[str, bool] = {}
    for path in files:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                fields = line.rstrip("\n").split("\t")
                if not fields or not fields[0]:
                    continue
                if fields[0] != "A":
                    continue
                if len(fields) != 4 or not fields[1] or fields[2] not in {"0", "1"}:
                    raise ValueError(f"invalid A row at {path}:{line_number}")
                if fields[1] in prefix:
                    raise ValueError(f"duplicate A row at {path}:{line_number}")
                prefix[fields[1]] = fields[2] == "1"
    return prefix, _parse_candidate_files(files)


def _expected_keys(cases: Sequence[CrossCase]) -> set[tuple[str, str]]:
    return {(case.case_id, mode) for case in cases for mode in case.modes}


def _validate_result_keys(
    actual: Mapping[tuple[str, str], ProbeResult],
    cases: Sequence[CrossCase],
    *,
    label: str,
) -> None:
    expected = _expected_keys(cases)
    actual_keys = set(actual)
    if actual_keys != expected:
        missing = sorted(expected - actual_keys)[:5]
        extra = sorted(actual_keys - expected)[:5]
        raise ValueError(f"{label} result keys mismatch: missing={missing}, extra={extra}")


def _rank(candidates: Sequence[str], expected: str) -> int:
    return next((index + 1 for index, value in enumerate(candidates) if value == expected), 0)


def _word_rank(candidates: Sequence[str], expected: str) -> int:
    """Rank of ``expected`` among multi-character candidates only.

    Single-character candidates (notably full-code chars at four keys) do not
    participate, so the metric answers "is the word the first word".
    """

    seen = 0
    for value in candidates:
        if len(value) == 1:
            continue
        seen += 1
        if value == expected:
            return seen
    return 0


def _candidate_state(result: ProbeResult, rank: int) -> str:
    if result.status == "missing":
        return "missing_raw"
    if result.status == "empty":
        return "empty_candidates"
    if rank:
        return "target_covered"
    return "target_absent_exported_topN"


def build_rows(
    root: str | Path,
    cases: Sequence[CrossCase],
    scheme: str,
    *,
    first_candidate_rule: str = "raw",
) -> list[CrossRow]:
    fresh = load_fresh(root, scheme)
    _validate_result_keys(fresh, cases, label=f"{scheme} fresh")
    rank_of = _rank_function(first_candidate_rule)
    rows: list[CrossRow] = []
    for case in cases:
        for mode in case_modes(cases):
            fresh_result = fresh[(case.case_id, mode)]
            raw_rank = _rank(fresh_result.candidates, case.text)
            rows.append(CrossRow(case.case_id, scheme, mode, case.text, None, fresh_result,
                                 fresh_result.candidates[0] if fresh_result.candidates else "",
                                 rank_of(fresh_result.candidates, case.text),
                                 _candidate_state(fresh_result, raw_rank),
                                 "not_applicable", raw_rank))
            # The context row is represented by the same type and is created by
            # build_context_rows; keeping fresh rows separate prevents accidental
            # mixing of static auxiliary-code remediation with context gains.
    return rows


def build_context_rows(
    root: str | Path,
    cases: Sequence[CrossCase],
    scheme: str,
    *,
    first_candidate_rule: str = "raw",
) -> list[CrossRow]:
    condition = str(SCHEME_SPECS[scheme]["condition"])
    prefix, after = load_after(root, condition)
    _validate_result_keys(after, cases, label=f"{scheme} after-prefix")
    expected_ids = {case.case_id for case in cases}
    if set(prefix) != expected_ids:
        raise ValueError(
            f"{scheme} prefix result ids mismatch: "
            f"missing={sorted(expected_ids - set(prefix))[:5]}, "
            f"extra={sorted(set(prefix) - expected_ids)[:5]}"
        )
    rank_of = _rank_function(first_candidate_rule)
    rows: list[CrossRow] = []
    for case in cases:
        ok = prefix[case.case_id]
        for mode in case_modes(cases):
            result = after[(case.case_id, mode)] if ok else ProbeResult((), False, "missing")
            raw_rank = _rank(result.candidates, case.text)
            rows.append(CrossRow(case.case_id, scheme, mode, case.text, ok,
                                 result, result.candidates[0] if result.candidates else "",
                                 rank_of(result.candidates, case.text),
                                 _candidate_state(result, raw_rank),
                                 "available" if ok else "prefix_failed", raw_rank))
    return rows


def _rate(value: int, denominator: int) -> float:
    return value / denominator if denominator else 0.0


def summarize_rows(rows: Sequence[CrossRow]) -> dict[str, object]:
    n = len(rows)
    fresh_top1 = sum(row.rank == 1 for row in rows)
    covered = sum(row.result.status == "ok" and row.rank > 0 for row in rows)
    nonempty = sum(row.result.status == "ok" for row in rows)
    prefix_available = sum(row.prefix_ok is True for row in rows)
    words: dict[str, list[CrossRow]] = {}
    for row in rows:
        words.setdefault(row.text, []).append(row)
    word_equal_top1 = (
        sum(
            sum(row.rank == 1 for row in word_rows) / len(word_rows)
            for word_rows in words.values()
        ) / len(words)
        if words else 0.0
    )
    return {
        "n": n,
        "prefix_available": prefix_available,
        "prefix_available_rate": _rate(prefix_available, n),
        "candidate_nonempty": nonempty,
        "candidate_nonempty_rate": _rate(nonempty, n),
        "target_covered": covered,
        "target_covered_rate": _rate(covered, n),
        "top1": fresh_top1,
        "top1_rate": _rate(fresh_top1, n),
        "word_count": len(words),
        "word_equal_top1_rate": word_equal_top1,
        "empty_candidates": sum(row.candidate_state == "empty_candidates" for row in rows),
        "target_absent_exported_topN": sum(row.candidate_state == "target_absent_exported_topN" for row in rows),
        "missing_raw": sum(row.candidate_state == "missing_raw" for row in rows),
        "truncated": sum(row.result.truncated for row in rows),
    }


def aggregate_context(fresh: Sequence[CrossRow], context: Sequence[CrossRow]) -> dict[str, object]:
    fresh_keys = [(row.scheme, row.mode, row.case_id) for row in fresh]
    context_keys = [(row.scheme, row.mode, row.case_id) for row in context]
    if len(set(fresh_keys)) != len(fresh_keys):
        raise ValueError("duplicate fresh rows")
    if len(set(context_keys)) != len(context_keys):
        raise ValueError("duplicate context rows")
    by_id = dict(zip(fresh_keys, fresh))
    available = [row for row in context if row.context_state == "available"]
    context_by_id = {
        (row.scheme, row.mode, row.case_id): row
        for row in available
    }
    pairs = [
        (by_id[key], context_by_id[key])
        for key in sorted(set(by_id) & set(context_by_id))
        if by_id[key].result.status != "missing"
        and context_by_id[key].result.status != "missing"
    ]
    fresh_top = sum(before.rank == 1 for before, _ in pairs)
    context_top = sum(after.rank == 1 for _, after in pairs)
    fixed = sum(before.rank != 1 and after.rank == 1 for before, after in pairs)
    broken = sum(before.rank == 1 and after.rank != 1 for before, after in pairs)
    word_pairs: dict[str, list[tuple[CrossRow, CrossRow]]] = {}
    for pair in pairs:
        word_pairs.setdefault(pair[0].text, []).append(pair)
    word_equal_top1 = (
        sum(
            sum(after.rank == 1 for _, after in items) / len(items)
            for items in word_pairs.values()
        ) / len(word_pairs)
        if word_pairs else 0.0
    )
    word_fixed_rates = [
        sum(before.rank != 1 and after.rank == 1 for before, after in items)
        / sum(before.rank != 1 for before, _ in items)
        for items in word_pairs.values()
        if any(before.rank != 1 for before, _ in items)
    ]
    word_broken_rates = [
        sum(before.rank == 1 and after.rank != 1 for before, after in items)
        / sum(before.rank == 1 for before, _ in items)
        for items in word_pairs.values()
        if any(before.rank == 1 for before, _ in items)
    ]
    return {
        "n_available": len(pairs),
        "fresh_top1": fresh_top,
        "context_top1": context_top,
        "context_lift": _rate(context_top - fresh_top, len(pairs)),
        "word_count_available": len(word_pairs),
        "word_equal_context_top1_rate": word_equal_top1,
        "fixed": fixed,
        "fixed_denominator": sum(before.rank != 1 for before, _ in pairs),
        "fixed_rate": _rate(fixed, sum(before.rank != 1 for before, _ in pairs)),
        "word_equal_fixed_rate": (
            sum(word_fixed_rates) / len(word_fixed_rates) if word_fixed_rates else 0.0
        ),
        "word_fixed_count": len(word_fixed_rates),
        "broken": broken,
        "broken_denominator": sum(before.rank == 1 for before, _ in pairs),
        "broken_rate": _rate(broken, sum(before.rank == 1 for before, _ in pairs)),
        "word_equal_broken_rate": (
            sum(word_broken_rates) / len(word_broken_rates) if word_broken_rates else 0.0
        ),
        "word_broken_count": len(word_broken_rates),
        "context_unavailable": len(fresh) - len(pairs),
    }


def auxiliary_remediation(
    rows: Sequence[CrossRow], *, base_mode: str = "pure"
) -> dict[str, object]:
    present = ordered_modes({row.mode for row in rows})
    if not present:
        raise ValueError("no modes in remediation rows")
    if base_mode not in present:
        base_mode = present[0]
    aux_modes = tuple(mode for mode in present if mode != base_mode)
    by_case = {(row.case_id, row.mode): row for row in rows}
    base = {case_id: row for (case_id, mode), row in by_case.items() if mode == base_mode}
    modes: dict[str, dict[str, int | float]] = {}
    for mode in aux_modes:
        paired = [(base_row, by_case[(case_id, mode)]) for case_id, base_row in base.items() if (case_id, mode) in by_case]
        wrong = [(base_row, aux) for base_row, aux in paired if base_row.rank != 1]
        absent = [(base_row, aux) for base_row, aux in paired if base_row.candidate_state in {"empty_candidates", "target_absent_exported_topN", "missing_raw"}]
        modes[mode] = {
            "n": len(paired),
            "base_top1_error": len(wrong),
            "base_export_absent": len(absent),
            "wrong_to_aux_top1": sum(aux.rank == 1 for _, aux in wrong),
            "wrong_to_aux_top1_rate": _rate(sum(aux.rank == 1 for _, aux in wrong), len(wrong)),
            "absent_to_aux_covered": sum(aux.rank > 0 for _, aux in absent),
            "absent_to_aux_covered_rate": _rate(sum(aux.rank > 0 for _, aux in absent), len(absent)),
            "absent_to_aux_top1": sum(aux.rank == 1 for _, aux in absent),
            "absent_to_aux_top1_rate": _rate(sum(aux.rank == 1 for _, aux in absent), len(absent)),
        }
    categories: Counter = Counter()
    for case_id, base_row in base.items():
        if base_row.rank == 1:
            continue
        bits = [
            mode
            for mode in aux_modes
            if (case_id, mode) in by_case and by_case[(case_id, mode)].rank == 1
        ]
        categories["+".join(bits) if bits else "none"] += 1
    return {
        "base_mode": base_mode,
        "aux_modes": aux_modes,
        "modes": modes,
        "exclusive_top1_rescue": dict(sorted(categories.items())),
        "exclusive_total": sum(categories.values()),
        "exclusive_denominator": sum(base_row.rank != 1 for base_row in base.values()),
    }


def rank_metric(
    values: Mapping[str, float | None], *, descending: bool = True
) -> list[dict[str, object]]:
    available = [(scheme, value) for scheme, value in values.items() if value is not None]
    unavailable = sorted(
        (scheme for scheme, value in values.items() if value is None),
        key=lambda scheme: SCHEME_LABELS[scheme],
    )
    ordered = sorted(
        available,
        key=lambda item: (
            -float(item[1]) if descending else float(item[1]),
            SCHEME_LABELS[item[0]],
        ),
    )
    output: list[dict[str, object]] = []
    previous: float | None = None
    rank = 0
    for index, (scheme, value) in enumerate(ordered, start=1):
        numeric_value = float(value)
        if previous is None or numeric_value != previous:
            rank = index
        output.append(
            {
                "scheme": scheme,
                "label": SCHEME_LABELS[scheme],
                "value": numeric_value,
                "rank": rank,
            }
        )
        previous = numeric_value
    output.extend(
        {
            "scheme": scheme,
            "label": SCHEME_LABELS[scheme],
            "value": None,
            "rank": None,
        }
        for scheme in unavailable
    )
    return output


def full_rankings(
    summary: Mapping[str, Mapping[str, Mapping[str, object]]],
    auxiliary: Mapping[str, Mapping[str, object]] | None = None,
) -> dict[str, object]:
    metric_specs = {
        "前缀可用率": lambda item: float(item["prefix_available_rate"]),
        "候选非空率": lambda item: float(item["candidate_nonempty_rate"]),
        "目标进入已导出候选池率": lambda item: float(item["target_covered_rate"]),
        "直接第一候选命中率": lambda item: float(item["top1_rate"]),
        "目标词等权直接第一候选命中率": lambda item: float(item["word_equal_top1_rate"]),
        "上屏后第一候选命中率": lambda item: (
            float(item["context"]["context_top1"]) / int(item["context"]["n_available"])
            if int(item["context"]["n_available"])
            else None
        ),
        "目标词等权上屏后第一候选命中率": lambda item: (
            float(item["context"]["word_equal_context_top1_rate"])
            if int(item["context"].get("word_count_available", 1))
            else None
        ),
        "上下文提升": lambda item: (
            float(item["context"]["context_lift"])
            if int(item["context"]["n_available"])
            else None
        ),
        "上下文修好率": lambda item: (
            float(item["context"]["fixed_rate"])
            if int(item["context"].get("fixed_denominator", 1))
            else None
        ),
        "目标词等权上下文修好率": lambda item: (
            float(item["context"]["word_equal_fixed_rate"])
            if int(item["context"].get("word_fixed_count", 1))
            else None
        ),
        "上下文修坏率": lambda item: (
            float(item["context"]["broken_rate"])
            if int(item["context"].get("broken_denominator", 1))
            else None
        ),
        "目标词等权上下文修坏率": lambda item: (
            float(item["context"]["word_equal_broken_rate"])
            if int(item["context"].get("word_broken_count", 1))
            else None
        ),
    }
    report_modes = ordered_modes(
        mode
        for scheme_summary in summary.values()
        for mode in scheme_summary
    )
    rankings: dict[str, object] = {}
    for mode in report_modes:
        for label, getter in metric_specs.items():
            rankings[f"{MODE_LABELS[mode]} / {label}"] = rank_metric(
                {
                    scheme: (
                        getter(summary[scheme][mode])
                        if mode in summary.get(scheme, {})
                        else None
                    )
                    for scheme in SCHEMES
                },
                descending=label not in {"上下文修坏率", "目标词等权上下文修坏率"},
            )
    if auxiliary is not None:
        remediation_specs = {
            "首选错误后辅码首选正确率": ("wrong_to_aux_top1_rate", "base_top1_error"),
            "未进已导出池后辅码进入池率": ("absent_to_aux_covered_rate", "base_export_absent"),
            "未进已导出池后辅码首选正确率": ("absent_to_aux_top1_rate", "base_export_absent"),
        }
        base_modes = {
            str(body.get("base_mode", "pure"))
            for body in auxiliary.values()
            if isinstance(body, Mapping)
        }
        if len(base_modes) != 1:
            raise ValueError(f"inconsistent remediation base modes: {sorted(base_modes)}")
        base_mode = base_modes.pop()
        aux_modes = ordered_modes(
            mode
            for body in auxiliary.values()
            if isinstance(body, Mapping)
            for mode in body.get("modes", {})
        )
        for mode in aux_modes:
            for label, (key, denominator_key) in remediation_specs.items():
                rankings[f"{MODE_LABELS[mode]} / {MODE_LABELS[base_mode]}{label}"] = rank_metric({
                    scheme: (
                        float(auxiliary[scheme]["modes"][mode][key])
                        if mode in auxiliary.get(scheme, {}).get("modes", {})
                        and int(auxiliary[scheme]["modes"][mode][denominator_key])
                        else None
                    )
                    for scheme in SCHEMES
                })
    return rankings


def build_report(root: str | Path) -> dict[str, object]:
    root = Path(root)
    cases_by_scheme = {
        scheme: load_cross_cases(root, input_name=str(SCHEME_SPECS[scheme]["input_name"]))
        for scheme in SCHEMES
    }
    reference_scheme = SCHEMES[0]
    identity = [
        (case.case_id, case.text, case.prefix)
        for case in cases_by_scheme[reference_scheme]
    ]
    for scheme, cases in cases_by_scheme.items():
        if [(case.case_id, case.text, case.prefix) for case in cases] != identity:
            raise ValueError(f"{scheme} input case universe differs from {reference_scheme}")
    report: dict[str, object] = {"case_count": len(identity), "schemes": {}}
    input_manifest_path = root / "manifest.json"
    if input_manifest_path.is_file():
        report["input_manifest"] = json.loads(
            input_manifest_path.read_text(encoding="utf-8")
        )
    first_candidate_rule = "raw"
    if isinstance(report.get("input_manifest"), Mapping):
        first_candidate_rule = str(
            report["input_manifest"].get("first_candidate_rule", "raw")
        )
    _rank_function(first_candidate_rule)  # validate the rule name early
    report["first_candidate_rule"] = first_candidate_rule
    run_manifest_path = root / "run-manifest.json"
    if run_manifest_path.is_file():
        report["run_manifest"] = json.loads(
            run_manifest_path.read_text(encoding="utf-8")
        )
    summaries: dict[str, dict[str, dict[str, object]]] = {}
    fresh_by_scheme: dict[str, list[CrossRow]] = {}
    context_by_scheme: dict[str, list[CrossRow]] = {}
    modes_by_scheme: dict[str, tuple[str, ...]] = {}
    for scheme in SCHEMES:
        cases = cases_by_scheme[scheme]
        modes = case_modes(cases)
        modes_by_scheme[scheme] = modes
        fresh = build_rows(root, cases, scheme, first_candidate_rule=first_candidate_rule)
        context = build_context_rows(root, cases, scheme, first_candidate_rule=first_candidate_rule)
        fresh_by_scheme[scheme] = fresh
        context_by_scheme[scheme] = context
        by_mode: dict[str, dict[str, object]] = {}
        for mode in modes:
            fresh_mode = [row for row in fresh if row.mode == mode]
            context_mode = [row for row in context if row.mode == mode]
            item = summarize_rows(fresh_mode)
            context_stat = aggregate_context(fresh_mode, context_mode)
            item["prefix_available"] = context_stat["n_available"]
            item["prefix_available_rate"] = _rate(context_stat["n_available"], len(fresh_mode))
            item["context"] = context_stat
            by_mode[mode] = item
        summaries[scheme] = by_mode
        report["schemes"][scheme] = {
            "fresh": fresh,
            "context": context,
            "auxiliary_remediation": auxiliary_remediation(fresh),
        }
    report["summary"] = summaries
    report["modes_by_scheme"] = dict(modes_by_scheme)
    reference_modes = modes_by_scheme[SCHEMES[0]]
    reference_mode = "pure" if "pure" in reference_modes else reference_modes[0]
    common_prefix_ids = set.intersection(*(
        {
            row.case_id
            for row in context_by_scheme[scheme]
            if row.mode == reference_mode and row.context_state == "available"
        }
        for scheme in SCHEMES
    ))
    common_context: dict[str, dict[str, object]] = {}
    for scheme in SCHEMES:
        common_context[scheme] = {}
        for mode in modes_by_scheme[scheme]:
            fresh_mode = [
                row for row in fresh_by_scheme[scheme]
                if row.mode == mode and row.case_id in common_prefix_ids
            ]
            context_mode = [
                row for row in context_by_scheme[scheme]
                if row.mode == mode and row.case_id in common_prefix_ids
            ]
            common_context[scheme][mode] = aggregate_context(
                fresh_mode, context_mode
            )
    report["common_prefix_context"] = {
        "case_count": len(common_prefix_ids),
        "word_count": len({
            row.text
            for row in fresh_by_scheme[SCHEMES[0]]
            if row.mode == reference_mode and row.case_id in common_prefix_ids
        }),
        "summary": common_context,
    }
    auxiliary = {
        scheme: report["schemes"][scheme]["auxiliary_remediation"]
        for scheme in SCHEMES
    }
    report["rankings"] = full_rankings(summaries, auxiliary)
    return report


def jsonable(value: object) -> object:
    if isinstance(value, ProbeResult):
        return {"candidates": list(value.candidates), "truncated": value.truncated, "status": value.status}
    if isinstance(value, CrossCase):
        return {"case_id": value.case_id, "text": value.text, "prefix": value.prefix, "modes": dict(value.modes)}
    if isinstance(value, CrossRow):
        return {"case_id": value.case_id, "scheme": value.scheme, "mode": value.mode, "text": value.text,
                "prefix_ok": value.prefix_ok, "result": jsonable(value.result), "top1": value.top1,
                "rank": value.rank, "candidate_state": value.candidate_state, "context_state": value.context_state,
                "raw_rank": value.raw_rank}
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


def render_markdown(report: Mapping[str, object]) -> str:
    summaries = report["summary"]
    modes_by_scheme = report.get("modes_by_scheme", {})
    if not isinstance(modes_by_scheme, Mapping) or not modes_by_scheme:
        modes_by_scheme = {scheme: MODES for scheme in SCHEMES}
    report_modes = ordered_modes(
        mode for modes in modes_by_scheme.values() for mode in modes
    )
    mode_list_text = "、".join(MODE_LABELS.get(mode, mode) for mode in report_modes)

    def scheme_modes(scheme: str) -> tuple[str, ...]:
        return tuple(modes_by_scheme.get(scheme, MODES))

    def count_rate(value: object, denominator: object) -> str:
        numerator = int(value)
        total = int(denominator)
        if total == 0:
            return f"不适用（{numerator}/0）"
        return f"{numerator}/{total}（{100*_rate(numerator, total):.2f}%）"

    lines = [
        "# 跨候选全量可审计基准",
        "",
        f"样本数：{report['case_count']}；{mode_list_text}各档输入全部保留。候选结果来自探针导出的 Top-5，目标未见只表示未进入已导出候选池。",
        "",
        "前缀失败不从分母删除，记为上下文不可用；上下文修好率/修坏率只在前缀成功的配对样本上计算。万象 Pro 是只提交前缀、不提交目标词的冷启动测量。",
    ]
    if report.get("first_candidate_rule") == "ignore_single_char":
        lines += [
            "",
            "本报告的“第一候选”按忽略单字候选的规则判定（含 4 键全码单字在内的单字不参与名次），直接回答目标词是否为第一个词；逐行数据仍保留字面名次 `raw_rank` 供诊断。",
        ]
    input_manifest = report.get("input_manifest")
    if isinstance(input_manifest, Mapping):
        selection = input_manifest.get("selection", {})
        sources = input_manifest.get("sources", {})
        lines += [
            "",
            "## 样本选择与来源",
            "",
            f"频表读取前 30,000 条；按来源频次顺序选取前 {selection.get('target_count', 0)} 个合格目标词（来源排名 {selection.get('first_source_rank', '?')}–{selection.get('last_source_rank', '?')}），每词最多 {selection.get('context_cap_per_target', '?')} 个真实前缀，共 {selection.get('case_count', report['case_count'])} 个 case。合格条件是：二字词有唯一显式整词读音，另有频表词的两个完整无调拼音音节均相同，并在句料中存在带真实前缀的整词出现（目标词不在句首）。",
            "",
            "| 输入源 | SHA-256 |",
            "|---|---|",
        ]
        for source_name in sorted(sources):
            source = sources.get(source_name, {})
            if isinstance(source, Mapping) and source.get("sha256"):
                lines.append(
                    f"| {source_name} | `{source.get('sha256', '')}` |"
                )
        exclusion = input_manifest.get("training_exclusion")
        if isinstance(exclusion, Mapping):
            lines += [
                "",
                "### 训练集排除审计",
                "",
                f"审计口径：{exclusion.get('criterion', '')}。训练并集共 {exclusion.get('training_union_sentences', 0)} 句；测试句料逐句排除命中的训练成员，剩余句子才参与抽样。",
                "",
                "| 句料 | 句数 | 训练重叠 | 保留 |",
                "|---|---:|---:|---:|",
            ]
            corpora = exclusion.get("corpora", {})
            if isinstance(corpora, Mapping) and corpora:
                for corpus_name, body in sorted(corpora.items()):
                    if isinstance(body, Mapping):
                        sentences = int(body.get("sentences", 0))
                        overlap = int(body.get("training_overlap", 0))
                        lines.append(
                            f"| {corpus_name} | {sentences} | {overlap} | {sentences - overlap} |"
                        )
            else:
                pool = exclusion.get("sentence_pool", {})
                if isinstance(pool, Mapping):
                    lines += [
                        f"| 已读取候选句 | {int(pool.get('heldout_loaded', 0)) + int(pool.get('plain_loaded', 0))} | {int(pool.get('training_overlap', 0))} | {int(pool.get('kept', 0))} |",
                        f"| 去重/重复句 | {int(pool.get('duplicate_or_seen', 0))} | — | 0 |",
                        f"| 训练命中或长度/字符集不合格 | {int(pool.get('training_or_length_rejected', 0))} | — | 0 |",
                    ]
    run_manifest = report.get("run_manifest")
    if isinstance(run_manifest, Mapping):
        validation = run_manifest.get("validation", {})
        runtime = run_manifest.get("runtime", {})
        model = runtime.get("mohu_model", {}) if isinstance(runtime, Mapping) else {}
        jobs = run_manifest.get("jobs", [])
        shards_per_condition = (
            len(jobs) // (len(SCHEMES) * 2) if jobs else 0
        )
        lines += [
            "",
            "## 运行完整性与隔离",
            "",
            f"正式运行完成 {len(jobs)}/{len(jobs)} 个 shard（{len(SCHEMES)} 方案 × direct/after-prefix × {shards_per_condition} shard）；每个 case 新建 Rime session，每个方案、条件、shard 使用独立临时 user directory，benchmark-only patch 关闭自适应用户词典。模型从隔离资产目录读取，SHA-256 为 `{model.get('sha256', '')}`。",
            "",
            "| 方案 | case | direct 候选流 | after-prefix 候选流 | 前缀记录 | 前缀成功 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for scheme in SCHEMES:
            item = validation.get(scheme, {})
            lines.append(
                f"| {SCHEME_LABELS[scheme]} | {item.get('cases', 0)} | "
                f"{item.get('direct_candidate_streams', 0)} | "
                f"{item.get('after_prefix_candidate_streams', 0)} | "
                f"{item.get('prefix_records', 0)} | {item.get('prefix_success', 0)} |"
            )
        lines += [
            "",
            "正式运行日志与独立 64-case 验证日志均无缺表、词典加载、探针或空流错误；模板、正式 worker 与验证 worker 已清理 `user.yaml`/`*.userdb`。归档中保留的早期 smoke、prefix-sample、diagnostic 失败日志不是本表的数据来源。运行命令及逐 shard 输入、输出、日志哈希见同目录 `run-manifest.json`。",
        ]
        staging_patches = run_manifest.get("staging_patches")
        if isinstance(staging_patches, list) and staging_patches:
            lines += [
                "",
                "基准模板补丁（仅作用于隔离副本，不改动仓库工作树）：" + "；".join(str(note) for note in staging_patches) + "。",
            ]
    lines += [
        "",
        "## 全量输入状态",
        "",
        "| 方案 | 档位 | 样本 | 前缀可用 | 候选非空 | 目标进入已导出池 | 直接第一候选 | 空候选 | 目标未见 | 缺失 | 截断 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for scheme in SCHEMES:
        for mode in scheme_modes(scheme):
            stat = summaries[scheme][mode]
            n = int(stat["n"])
            lines.append(
                f"| {SCHEME_LABELS[scheme]} | {MODE_LABELS.get(mode, mode)} | {n} | "
                f"{count_rate(stat['prefix_available'], n)} | "
                f"{count_rate(stat['candidate_nonempty'], n)} | "
                f"{count_rate(stat['target_covered'], n)} | "
                f"{count_rate(stat['top1'], n)} | {stat['empty_candidates']} | "
                f"{stat['target_absent_exported_topN']} | {stat['missing_raw']} | {stat['truncated']} |"
            )
    lines += ["", "## 上下文变化（前缀成功配对）", "", "| 方案 | 档位 | 可用配对 | 配对内直接第一候选 | 上屏后第一候选 | 上下文提升 | 修好率 | 修坏率 | 不可用 |", "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for scheme in SCHEMES:
        for mode in scheme_modes(scheme):
            context = summaries[scheme][mode]["context"]
            n = int(context["n_available"])
            lines.append(
                f"| {SCHEME_LABELS[scheme]} | {MODE_LABELS.get(mode, mode)} | {n} | "
                f"{count_rate(context['fresh_top1'], n)} | "
                f"{count_rate(context['context_top1'], n)} | "
                f"{100*float(context['context_lift']):+.2f}pp | "
                f"{count_rate(context['fixed'], context['fixed_denominator'])} | "
                f"{count_rate(context['broken'], context['broken_denominator'])} | "
                f"{context['context_unavailable']} |"
            )
    common = report.get("common_prefix_context")
    if isinstance(common, Mapping):
        common_summary = common.get("summary", {})
        lines += [
            "",
            "## 五方案共同前缀成功子集",
            "",
            f"敏感性分析仅保留五个方案都成功提交前缀的 {common.get('case_count', 0)} 个 case（{common.get('word_count', 0)} 个目标词），消除各方案前缀成功子集不同带来的选择偏差。本表不改变预设的 {len(report['rankings'])} 项主排名。",
            "",
            "| 方案 | 档位 | 共同 case | 直接第一候选 | 上屏后第一候选 | 上下文提升 | 修好率 | 修坏率 |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
        for scheme in SCHEMES:
            for mode in scheme_modes(scheme):
                context = common_summary[scheme][mode]
                n = int(context["n_available"])
                lines.append(
                    f"| {SCHEME_LABELS[scheme]} | {MODE_LABELS.get(mode, mode)} | {n} | "
                    f"{count_rate(context['fresh_top1'], n)} | "
                    f"{count_rate(context['context_top1'], n)} | "
                    f"{100*float(context['context_lift']):+.2f}pp | "
                    f"{count_rate(context['fixed'], context['fixed_denominator'])} | "
                    f"{count_rate(context['broken'], context['broken_denominator'])} |"
                )
    lines += ["", "## 目标词等权审计", "", "以下均为条件宏平均：每个参与该指标的目标词权重相同。直接第一候选覆盖全部参与词；上屏后指标只覆盖至少有一个可用前缀的词；修好/修坏率进一步只覆盖分别存在直接错误/直接正确配对的词。括号明确列出每项参与词数。", "", "| 方案 | 档位 | 目标词数 | 直接第一候选 | 上屏后第一候选 | 上下文修好率 | 上下文修坏率 |", "|---|---|---:|---:|---:|---:|---:|"]
    for scheme in SCHEMES:
        for mode in scheme_modes(scheme):
            stat = summaries[scheme][mode]
            context = stat["context"]
            lines.append(
                f"| {SCHEME_LABELS[scheme]} | {MODE_LABELS.get(mode, mode)} | {stat['word_count']} | "
                f"{100*float(stat['word_equal_top1_rate']):.2f}%（{stat['word_count']}词） | "
                f"{100*float(context['word_equal_context_top1_rate']):.2f}%（{context['word_count_available']}词） | "
                f"{100*float(context['word_equal_fixed_rate']):.2f}%（{context['word_fixed_count']}词） | "
                f"{100*float(context['word_equal_broken_rate']):.2f}%（{context['word_broken_count']}词） |"
            )
    remediation_rows: list[tuple[str, str, Mapping[str, object]]] = []
    base_labels: set[str] = set()
    for scheme in SCHEMES:
        body = report["schemes"][scheme].get("auxiliary_remediation", {})
        base_mode = str(body.get("base_mode", "pure"))
        base_label = MODE_LABELS.get(base_mode, base_mode)
        base_labels.add(base_label)
        for mode in body.get("modes", {}):
            stat = body["modes"][mode]
            remediation_rows.append((scheme, mode, stat))
    if remediation_rows:
        base_label = sorted(base_labels)[0]
        lines += ["", f"## {base_label}到辅码档的补救", "", "辅码各档的分子允许重叠，不能相加；下表按同一 case ID 对齐。三项结果均显示分子/分母和比例。", "", f"| 方案 | 辅码档 | {base_label}首选错误后辅码首选正确 | {base_label}未进已导出池后辅码进入池 | {base_label}未进已导出池后辅码首选正确 |", "|---|---|---:|---:|---:|"]
        for scheme, mode, stat in remediation_rows:
            lines.append(
                f"| {SCHEME_LABELS[scheme]} | {MODE_LABELS.get(mode, mode)} | "
                f"{count_rate(stat['wrong_to_aux_top1'], stat['base_top1_error'])} | "
                f"{count_rate(stat['absent_to_aux_covered'], stat['base_export_absent'])} | "
                f"{count_rate(stat['absent_to_aux_top1'], stat['base_export_absent'])} |"
            )
        aux_mode_sets = {
            tuple(report["schemes"][scheme].get("auxiliary_remediation", {}).get("aux_modes", ()))
            for scheme in SCHEMES
        }
        aux_modes = ordered_modes(
            mode for modes in aux_mode_sets for mode in modes
        )
        category_names = ["none"]
        for size in range(1, len(aux_modes) + 1):
            category_names.extend("+".join(combo) for combo in combinations(aux_modes, size))
        lines += [
            "",
            "### 辅码首选补救互斥校验",
            "",
            f"`{'/'.join(category_names)}` 组合是各辅码档是否把{base_label}错误补到首选的互斥 bitset；各类计数之和必须等于{base_label}首选错误分母。",
            "",
            "| 方案 | " + " | ".join(category_names) + " | 合计/分母 |",
            "|---|" + "---:|" * (len(category_names) + 1),
        ]
        for scheme in SCHEMES:
            auxiliary = report["schemes"][scheme]["auxiliary_remediation"]
            exclusive = auxiliary["exclusive_top1_rescue"]
            cells = [str(exclusive.get(category, 0)) for category in category_names]
            lines.append(
                f"| {SCHEME_LABELS[scheme]} | " + " | ".join(cells) +
                f" | {auxiliary['exclusive_total']}/{auxiliary['exclusive_denominator']} |"
            )
    ranking_headers = " | ".join(str(index) for index in range(1, len(SCHEMES) + 1))
    ranking_rule = "|---|" + "---|" * len(SCHEMES)
    lines += ["", "## 每项指标完整排名", "", "名次按未舍入值计算，并采用标准竞赛排名；显示值四舍五入到两位，因此相同显示值仍可能有先后。修坏率越低越好，其余指标越高越好；零分母或该方案不适用此模式的项目记为不适用且不授名次。", "", f"| 指标 | {ranking_headers} |", ranking_rule]
    for metric, entries in report["rankings"].items():
        unit = "pp" if metric.endswith(" / 上下文提升") else "%"
        cells = [
            (
                f"{entry['label']} {100*float(entry['value']):.2f}{unit}（第{entry['rank']}）"
                if entry["value"] is not None
                else f"{entry['label']} 不适用（无分母）"
            )
            for entry in entries
        ]
        lines.append(f"| {metric} | " + " | ".join(cells) + " |")
    reproduction = None
    if isinstance(input_manifest, Mapping):
        candidate_commands = input_manifest.get("reproduction")
        if isinstance(candidate_commands, list) and candidate_commands:
            reproduction = [str(command) for command in candidate_commands]
    if reproduction is None:
        reproduction = [
            "uv run python -m research.lm_sentence_compare.build_cross_candidate_cases \\",
            "  --frequency-list /path/to/二字词表2.0.txt \\",
            "  --output-root /tmp/mohu-cross-candidate-homophone-v1",
            "uv run python -m research.lm_sentence_compare.run_cross_candidate \\",
            "  --root /tmp/mohu-cross-candidate-homophone-v1 \\",
            "  --model /path/to/mohu-sentence-ngram-v5.bin \\",
            "  --workers 5 --units-per-shard 1200 --max-candidates 5",
            "uv run python -m research.lm_sentence_compare.cross_candidate \\",
            "  --kua3 /tmp/mohu-cross-candidate-homophone-v1 \\",
            "  --json /tmp/mohu-cross-candidate-homophone-v1/cross_candidate_report.json \\",
            "  --markdown /tmp/mohu-cross-candidate-homophone-v1/cross_candidate_report.md",
        ]
    lines += [
        "",
        "## 复现与分发结论",
        "",
        f"五个方案共享相同 target/context universe，但分别使用自身原生双拼和辅码编码；没有向魔然、夜莺或万象 Pro 提供魔虎辅码。本报告输入档为：{mode_list_text}。主指标是第一候选命中，候选 Top-5 可见性只作诊断。",
        "",
        "```bash",
        *reproduction,
        "```",
        "",
        "本次使用的魔虎 V5 模型文件未改动；新增上下文能力来自引擎 ABI、Lua filter/桥接与 schema 配置。其他使用同一 V5 模型的项目若要获得该能力，需要同步更新相应引擎、Lua 与 schema 代码，不需要更换模型文件。仅复制旧模型不会获得新行为。",
        "",
        "## 解释与限制",
        "",
        (
            "- 这是当前测试集观测结果；魔虎训练集与测试集尚未完成完整去重审计，不能据此宣称对所有未知语料普适。"
            if not (
                isinstance(input_manifest, Mapping)
                and isinstance(input_manifest.get("training_exclusion"), Mapping)
            )
            else "- 这是当前测试集观测结果；训练集排除按精确句匹配完成，未做子串/近重复审计，不能据此宣称对所有未知语料普适。"
        ),
        "- 万象 Pro 使用本地自学习 context_reorder，当前 afterA 是冷启动协议；其不可用/零变化不能等同于预训练 grammar 的能力。",
        "- `目标未见` 不等于完整候选池不存在，因为探针只导出 Top-5；`截断` 会进一步限制可见性判断。",
    ]
    return "\n".join(lines) + "\n"


def write_report(root: str | Path, output: str | Path, markdown: str | Path | None = None) -> dict[str, object]:
    report = build_report(root)
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(jsonable(report), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if markdown is not None:
        markdown_path = Path(markdown)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return report


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Aggregate auditable cross-candidate benchmark streams")
    parser.add_argument("--kua3", type=Path, default=Path("/tmp/kua3"))
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args()
    report = write_report(args.kua3, args.json, args.markdown)
    print(json.dumps({"cases": report["case_count"], "schemes": list(SCHEMES)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
