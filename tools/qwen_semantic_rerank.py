"""Versioned candidate-menu protocol for semantic reranking.

The protocol identifies candidates by their original menu indexes.  Display text is
not an identifier: the same text may occur in multiple Rime candidates.
"""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

FORMAT_VERSION = "qwen-semantic-rerank/v1"
MAX_CANDIDATES = 64
MAX_RAW_INPUT_CHARS = 128
MAX_CONTEXT_CHARS = 512
MAX_CANDIDATE_TEXT_CHARS = 128
MAX_SYLLABLES = 32
MAX_EVIDENCE_ITEMS = 64
MENU_FIELDS = {
    "format_version", "case_id", "source", "schema", "raw_input", "preedit", "committed_context",
    "target_text", "target_visible", "oracle_rank", "engine_capabilities", "candidates",
}
CANDIDATE_FIELDS = {
    "menu_index", "text", "type", "start", "end", "consumes_current_input", "protected", "preedit",
    "native_rank", "native_score", "native_score_kind", "source_flags", "dictionary_evidence", "code_evidence",
}
DICTIONARY_EVIDENCE_FIELDS = {"known_phrase_spans", "personal_phrase_spans", "fixed_phrase_spans"}
CODE_EVIDENCE_FIELDS = {"syllables", "auxiliary_constraints", "reading_constraints"}
PHRASE_SPAN_FIELDS = {"text", "start_char", "end_char", "source"}
REQUEST_FIELDS = {"format_version", "model_profile", "protocol_checksum", "eligible_menu_indices", "menu"}
RESPONSE_FIELDS = {"format_version", "protocol_checksum", "model_profile", "scores", "permutation"}
SCORE_FIELDS = {"menu_index", "score"}
ENGINE_CAPABILITY_FIELDS = {"engine_id", "engine_version", "capabilities"}


class SemanticMenuValidationError(ValueError):
    """A semantic-menu record or scorer response violates the protocol."""


def _mapping(value: object, location: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SemanticMenuValidationError(f"{location}: must be an object")
    return value


def _reject_unknown_fields(mapping: Mapping[str, object], allowed: set[str], location: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise SemanticMenuValidationError(f"{location}: unknown fields {unknown}")


def _field(mapping: Mapping[str, object], name: str, location: str) -> object:
    if name not in mapping:
        raise SemanticMenuValidationError(f"{location}: missing required field '{name}'")
    return mapping[name]


def _string(value: object, name: str, location: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise SemanticMenuValidationError(f"{location}: field '{name}' must be a string")
    normalized = unicodedata.normalize("NFC", value)
    if not allow_empty and not normalized:
        raise SemanticMenuValidationError(f"{location}: field '{name}' must not be empty")
    if "\x00" in normalized:
        raise SemanticMenuValidationError(f"{location}: field '{name}' must not contain NUL")
    if any(0xD800 <= ord(character) <= 0xDFFF for character in normalized):
        raise SemanticMenuValidationError(
            f"{location}: field '{name}' must contain valid Unicode scalar values"
        )
    return normalized


def _boolean(value: object, name: str, location: str) -> bool:
    if not isinstance(value, bool):
        raise SemanticMenuValidationError(f"{location}: field '{name}' must be a boolean")
    return value


def _integer(value: object, name: str, location: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SemanticMenuValidationError(
            f"{location}: field '{name}' must be an integer >= {minimum}"
        )
    return value


def _finite_number_or_null(value: object, name: str, location: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SemanticMenuValidationError(
            f"{location}: field '{name}' must be a finite number or null"
        )
    try:
        number = float(value)
    except OverflowError as error:
        raise SemanticMenuValidationError(
            f"{location}: field '{name}' must be a finite number or null"
        ) from error
    if not math.isfinite(number):
        raise SemanticMenuValidationError(
            f"{location}: field '{name}' must be a finite number or null"
        )
    return number


def _string_list(
    value: object,
    name: str,
    location: str,
    *,
    limit: int,
    preserve_order: bool = False,
    allow_duplicates: bool = False,
) -> list[str]:
    if not isinstance(value, list):
        raise SemanticMenuValidationError(f"{location}: field '{name}' must be an array")
    if len(value) > limit:
        raise SemanticMenuValidationError(f"{location}: field '{name}' exceeds limit {limit}")
    normalized = [_string(item, name, location) for item in value]
    if not allow_duplicates and len(set(normalized)) != len(normalized):
        raise SemanticMenuValidationError(f"{location}: field '{name}' must not contain duplicates")
    return normalized if preserve_order else sorted(normalized)


def _validate_phrase_spans(value: object, name: str, text: str, location: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise SemanticMenuValidationError(f"{location}: field '{name}' must be an array")
    if len(value) > MAX_EVIDENCE_ITEMS:
        raise SemanticMenuValidationError(f"{location}: field '{name}' exceeds limit {MAX_EVIDENCE_ITEMS}")
    spans: list[dict[str, object]] = []
    for index, raw_span in enumerate(value):
        span_location = f"{location}: {name}[{index}]"
        span = _mapping(raw_span, span_location)
        _reject_unknown_fields(span, PHRASE_SPAN_FIELDS, span_location)
        span_text = _string(_field(span, "text", span_location), "text", span_location)
        start = _integer(_field(span, "start_char", span_location), "start_char", span_location)
        end = _integer(_field(span, "end_char", span_location), "end_char", span_location)
        source = _string(_field(span, "source", span_location), "source", span_location)
        if start >= end or end > len(text) or text[start:end] != span_text:
            raise SemanticMenuValidationError(
                f"{span_location}: phrase span must match candidate text using Unicode code points"
            )
        spans.append({"text": span_text, "start_char": start, "end_char": end, "source": source})
    unique = {(item["text"], item["start_char"], item["end_char"], item["source"]) for item in spans}
    if len(unique) != len(spans):
        raise SemanticMenuValidationError(f"{location}: field '{name}' must not contain duplicates")
    return sorted(spans, key=lambda item: (item["start_char"], item["end_char"], item["source"], item["text"]))


def _validate_engine_capabilities(value: object, location: str) -> dict[str, object]:
    capabilities = _mapping(value, location)
    _reject_unknown_fields(capabilities, ENGINE_CAPABILITY_FIELDS, location)
    return {
        "engine_id": _string(_field(capabilities, "engine_id", location), "engine_id", location),
        "engine_version": _string(
            _field(capabilities, "engine_version", location), "engine_version", location
        ),
        "capabilities": _string_list(
            _field(capabilities, "capabilities", location),
            "capabilities",
            location,
            limit=MAX_EVIDENCE_ITEMS,
        ),
    }


def _validate_candidate(
    raw_candidate: object,
    *,
    location: str,
    expected_index: int,
    raw_input_length: int,
) -> dict[str, object]:
    candidate = _mapping(raw_candidate, location)
    _reject_unknown_fields(candidate, CANDIDATE_FIELDS, location)
    menu_index = _integer(_field(candidate, "menu_index", location), "menu_index", location)
    if menu_index != expected_index:
        raise SemanticMenuValidationError(
            f"{location}: menu_index must equal original array position {expected_index}"
        )
    text = _string(_field(candidate, "text", location), "text", location)
    if len(text) > MAX_CANDIDATE_TEXT_CHARS:
        raise SemanticMenuValidationError(f"{location}: candidate text exceeds limit {MAX_CANDIDATE_TEXT_CHARS}")
    start = _integer(_field(candidate, "start", location), "start", location)
    end = _integer(_field(candidate, "end", location), "end", location)
    if start > end or end > raw_input_length:
        raise SemanticMenuValidationError(
            f"{location}: start/end must be Unicode code-point offsets within raw_input"
        )
    consumes_current_input = _boolean(
        _field(candidate, "consumes_current_input", location), "consumes_current_input", location
    )
    if consumes_current_input != (end == raw_input_length):
        raise SemanticMenuValidationError(
            f"{location}: consumes_current_input must exactly match end == raw_input length"
        )
    protected = _boolean(_field(candidate, "protected", location), "protected", location)
    native_rank = _integer(_field(candidate, "native_rank", location), "native_rank", location, minimum=1)
    if native_rank != expected_index + 1:
        raise SemanticMenuValidationError(
            f"{location}: native_rank must equal original menu rank {expected_index + 1}"
        )
    evidence = _mapping(_field(candidate, "dictionary_evidence", location), f"{location}: dictionary_evidence")
    _reject_unknown_fields(evidence, DICTIONARY_EVIDENCE_FIELDS, f"{location}: dictionary_evidence")
    code_evidence = _mapping(_field(candidate, "code_evidence", location), f"{location}: code_evidence")
    _reject_unknown_fields(code_evidence, CODE_EVIDENCE_FIELDS, f"{location}: code_evidence")
    result: dict[str, object] = {
        "menu_index": menu_index,
        "text": text,
        "type": _string(_field(candidate, "type", location), "type", location),
        "start": start,
        "end": end,
        "consumes_current_input": consumes_current_input,
        "protected": protected,
        "preedit": _string(_field(candidate, "preedit", location), "preedit", location),
        "native_rank": native_rank,
        "native_score": _finite_number_or_null(
            _field(candidate, "native_score", location), "native_score", location
        ),
        "native_score_kind": _string(
            _field(candidate, "native_score_kind", location), "native_score_kind", location
        ),
        "source_flags": _string_list(
            _field(candidate, "source_flags", location), "source_flags", location, limit=MAX_EVIDENCE_ITEMS
        ),
        "dictionary_evidence": {
            "known_phrase_spans": _validate_phrase_spans(
                _field(evidence, "known_phrase_spans", location),
                "known_phrase_spans",
                text,
                location,
            ),
            "personal_phrase_spans": _validate_phrase_spans(
                _field(evidence, "personal_phrase_spans", location),
                "personal_phrase_spans",
                text,
                location,
            ),
            "fixed_phrase_spans": _validate_phrase_spans(
                _field(evidence, "fixed_phrase_spans", location),
                "fixed_phrase_spans",
                text,
                location,
            ),
        },
        "code_evidence": {
            "syllables": _string_list(
                _field(code_evidence, "syllables", location),
                "syllables",
                location,
                limit=MAX_SYLLABLES,
                preserve_order=True,
                allow_duplicates=True,
            ),
            "auxiliary_constraints": _string_list(
                _field(code_evidence, "auxiliary_constraints", location),
                "auxiliary_constraints",
                location,
                limit=MAX_EVIDENCE_ITEMS,
            ),
            "reading_constraints": _string_list(
                _field(code_evidence, "reading_constraints", location),
                "reading_constraints",
                location,
                limit=MAX_EVIDENCE_ITEMS,
            ),
        },
    }
    return result


def validate_menu_record(record: object, *, location: str = "menu") -> dict[str, object]:
    """Validate and canonicalize a complete `qwen-semantic-rerank/v1` menu.

    Character and raw-input spans use NFC-normalized Unicode code-point offsets.
    Candidate array order is the native menu order and cannot be inferred later.
    """

    mapping = _mapping(record, location)
    _reject_unknown_fields(mapping, MENU_FIELDS, location)
    format_version = _string(_field(mapping, "format_version", location), "format_version", location)
    if format_version != FORMAT_VERSION:
        raise SemanticMenuValidationError(f"{location}: unsupported format_version {format_version!r}")
    raw_input = _string(_field(mapping, "raw_input", location), "raw_input", location)
    if len(raw_input) > MAX_RAW_INPUT_CHARS:
        raise SemanticMenuValidationError(f"{location}: raw_input exceeds limit {MAX_RAW_INPUT_CHARS}")
    committed_context = _string(
        _field(mapping, "committed_context", location), "committed_context", location, allow_empty=True
    )
    if len(committed_context) > MAX_CONTEXT_CHARS:
        raise SemanticMenuValidationError(
            f"{location}: committed_context exceeds limit {MAX_CONTEXT_CHARS}"
        )
    target_visible = _boolean(_field(mapping, "target_visible", location), "target_visible", location)
    raw_candidates = _field(mapping, "candidates", location)
    if not isinstance(raw_candidates, list) or not raw_candidates:
        raise SemanticMenuValidationError(f"{location}: field 'candidates' must be a non-empty array")
    if len(raw_candidates) > MAX_CANDIDATES:
        raise SemanticMenuValidationError(f"{location}: candidates exceeds limit {MAX_CANDIDATES}")
    candidates = [
        _validate_candidate(
            item,
            location=f"{location}: candidates[{index}]",
            expected_index=index,
            raw_input_length=len(raw_input),
        )
        for index, item in enumerate(raw_candidates)
    ]
    oracle_rank_value = _field(mapping, "oracle_rank", location)
    if target_visible:
        oracle_rank = _integer(oracle_rank_value, "oracle_rank", location, minimum=1)
        if oracle_rank > len(candidates):
            raise SemanticMenuValidationError(f"{location}: oracle_rank is outside candidates")
    else:
        if oracle_rank_value is not None:
            raise SemanticMenuValidationError(
                f"{location}: oracle_rank must be null when target_visible is false"
            )
        oracle_rank = None
    target_text = _string(_field(mapping, "target_text", location), "target_text", location, allow_empty=not target_visible)
    if target_visible and candidates[oracle_rank - 1]["text"] != target_text:
        raise SemanticMenuValidationError(
            f"{location}: target_text must match the candidate at oracle_rank"
        )
    any_native_score_missing = any(candidate["native_score"] is None for candidate in candidates)
    if any_native_score_missing:
        engine_capabilities = _validate_engine_capabilities(
            _field(mapping, "engine_capabilities", location), f"{location}: engine_capabilities"
        )
    elif _field(mapping, "engine_capabilities", location) is not None:
        engine_capabilities = _validate_engine_capabilities(
            mapping["engine_capabilities"], f"{location}: engine_capabilities"
        )
    else:
        engine_capabilities = None
    return {
        "format_version": FORMAT_VERSION,
        "case_id": _string(_field(mapping, "case_id", location), "case_id", location),
        "source": _string(_field(mapping, "source", location), "source", location),
        "schema": _string(_field(mapping, "schema", location), "schema", location),
        "raw_input": raw_input,
        "preedit": _string(_field(mapping, "preedit", location), "preedit", location),
        "committed_context": committed_context,
        "target_text": target_text,
        "target_visible": target_visible,
        "oracle_rank": oracle_rank,
        "engine_capabilities": engine_capabilities,
        "candidates": candidates,
    }


def canonical_menu_bytes(record: object) -> bytes:
    """Return the exact UTF-8 bytes used for protocol checksums and scorer input."""

    validated = validate_menu_record(record)
    return json.dumps(
        validated, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False
    ).encode("utf-8")


def menu_checksum(record: object) -> str:
    return hashlib.sha256(canonical_menu_bytes(record)).hexdigest()


def eligible_menu_indices(record: object) -> list[int]:
    validated = validate_menu_record(record)
    return [
        int(candidate["menu_index"])
        for candidate in validated["candidates"]
        if bool(candidate["consumes_current_input"]) and not bool(candidate["protected"])
    ]


def make_scoring_request(record: object, *, model_profile: str) -> dict[str, object]:
    menu = validate_menu_record(record)
    profile = _string(model_profile, "model_profile", "request")
    return {
        "format_version": FORMAT_VERSION,
        "model_profile": profile,
        "protocol_checksum": menu_checksum(menu),
        "eligible_menu_indices": eligible_menu_indices(menu),
        "menu": menu,
    }


def _validate_index_permutation(value: object, expected: Sequence[int], location: str) -> list[int]:
    if not isinstance(value, list):
        raise SemanticMenuValidationError(f"{location}: must be an array")
    indexes = [_integer(item, "menu_index", location) for item in value]
    if len(indexes) != len(expected) or set(indexes) != set(expected):
        raise SemanticMenuValidationError(
            f"{location}: must be a full permutation of eligible menu indexes"
        )
    if len(set(indexes)) != len(indexes):
        raise SemanticMenuValidationError(f"{location}: must not contain duplicate indexes")
    return indexes


def _validate_scoring_request(
    request: object, *, location: str = "request"
) -> tuple[dict[str, object], str, str, list[int]]:
    mapping = _mapping(request, location)
    _reject_unknown_fields(mapping, REQUEST_FIELDS, location)
    version = _string(_field(mapping, "format_version", location), "format_version", location)
    if version != FORMAT_VERSION:
        raise SemanticMenuValidationError(f"{location}: unsupported format_version {version!r}")
    menu = validate_menu_record(_field(mapping, "menu", location), location=f"{location}: menu")
    profile = _string(_field(mapping, "model_profile", location), "model_profile", location)
    checksum = _string(
        _field(mapping, "protocol_checksum", location), "protocol_checksum", location
    )
    computed_checksum = menu_checksum(menu)
    if checksum != computed_checksum:
        raise SemanticMenuValidationError(f"{location}: protocol checksum does not match menu")
    eligible = eligible_menu_indices(menu)
    supplied_eligible = _validate_index_permutation(
        _field(mapping, "eligible_menu_indices", location),
        eligible,
        f"{location}: eligible_menu_indices",
    )
    if supplied_eligible != eligible:
        raise SemanticMenuValidationError(
            f"{location}: eligible_menu_indices must use native menu order"
        )
    return menu, profile, checksum, eligible


def validate_scoring_response(response: object, request: object, *, location: str = "response") -> dict[str, object]:
    """Validate a complete score array or permutation for one bound request."""

    menu, expected_profile, expected_checksum, expected = _validate_scoring_request(request)
    mapping = _mapping(response, location)
    _reject_unknown_fields(mapping, RESPONSE_FIELDS, location)
    version = _string(_field(mapping, "format_version", location), "format_version", location)
    if version != FORMAT_VERSION:
        raise SemanticMenuValidationError(f"{location}: response-version mismatch")
    checksum = _string(_field(mapping, "protocol_checksum", location), "protocol_checksum", location)
    if checksum != expected_checksum:
        raise SemanticMenuValidationError(f"{location}: protocol checksum mismatch")
    profile = _string(_field(mapping, "model_profile", location), "model_profile", location)
    if profile != expected_profile:
        raise SemanticMenuValidationError(f"{location}: model profile mismatch")
    has_scores = "scores" in mapping
    has_permutation = "permutation" in mapping
    if has_scores == has_permutation:
        raise SemanticMenuValidationError(f"{location}: provide exactly one of scores or permutation")
    if has_permutation:
        return {
            "format_version": FORMAT_VERSION,
            "protocol_checksum": checksum,
            "model_profile": profile,
            "permutation": _validate_index_permutation(
                mapping["permutation"], expected, f"{location}: permutation"
            ),
        }
    raw_scores = mapping["scores"]
    if not isinstance(raw_scores, list):
        raise SemanticMenuValidationError(f"{location}: scores must be an array")
    indexes: list[int] = []
    scores: list[dict[str, object]] = []
    for index, raw_score in enumerate(raw_scores):
        score_location = f"{location}: scores[{index}]"
        score_mapping = _mapping(raw_score, score_location)
        _reject_unknown_fields(score_mapping, SCORE_FIELDS, score_location)
        menu_index = _integer(_field(score_mapping, "menu_index", score_location), "menu_index", score_location)
        score = _finite_number_or_null(_field(score_mapping, "score", score_location), "score", score_location)
        if score is None:
            raise SemanticMenuValidationError(f"{score_location}: score must be a finite number")
        indexes.append(menu_index)
        scores.append({"menu_index": menu_index, "score": score})
    try:
        _validate_index_permutation(indexes, expected, f"{location}: scores")
    except SemanticMenuValidationError as error:
        if any(index not in expected for index in indexes):
            raise SemanticMenuValidationError(
                f"{location}: scores include a protected or ineligible menu index"
            ) from error
        raise
    return {
        "format_version": FORMAT_VERSION,
        "protocol_checksum": checksum,
        "model_profile": profile,
        "scores": scores,
    }


def apply_semantic_response(
    record: object,
    response: object,
    *,
    model_profile: str,
) -> list[int]:
    """Return the whole native menu index sequence after a valid stable rewrite.

    Only eligible slots move. Scores are descending; equal scores retain original
    menu order. Callers must still apply their own calibrated gate and protection
    policy before presenting this sequence.
    """

    menu = validate_menu_record(record)
    expected_profile = _string(model_profile, "model_profile", "apply")
    response_mapping = _mapping(response, "response")
    validated_response = validate_scoring_response(
        response_mapping, make_scoring_request(menu, model_profile=expected_profile)
    )
    eligible = eligible_menu_indices(menu)
    if "permutation" in validated_response:
        ordered = list(validated_response["permutation"])
    else:
        score_by_index = {
            int(item["menu_index"]): float(item["score"])
            for item in validated_response["scores"]
        }
        ordered = sorted(eligible, key=lambda index: (-score_by_index[index], index))
    positions = set(eligible)
    result: list[int] = []
    iterator = iter(ordered)
    for candidate in menu["candidates"]:
        index = int(candidate["menu_index"])
        result.append(next(iterator) if index in positions else index)
    return result
