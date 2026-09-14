"""Strict parser for isolated Rime candidate-menu observations.

This format records only values exposed by the public librime C API.  It is not a
``qwen-semantic-rerank/v1`` training record: candidate type, spans, candidate
preedit, source/protection evidence, committed context, and verified code
features are unavailable at this boundary and must not be inferred.
"""

from __future__ import annotations

import binascii
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

FORMAT_VERSION = "qwen-semantic-menu-observation/v1"


class SemanticMenuObservationError(ValueError):
    """Raised when a probe observation violates the versioned wire format."""


@dataclass(frozen=True, slots=True)
class CandidateObservation:
    menu_index: int
    text: str
    comment: str


@dataclass(frozen=True, slots=True)
class MenuObservation:
    case_id: str
    raw_input: str
    composition_preedit: str
    composition_length: int
    cursor_pos: int
    selection_start: int
    selection_end: int
    candidates: tuple[CandidateObservation, ...]
    truncated: bool
    elapsed_us: int


def _decode_hex(value: str, *, location: str, name: str) -> str:
    if len(value) % 2:
        raise SemanticMenuObservationError(f"{location}: {name} hex payload has odd length")
    try:
        decoded = binascii.unhexlify(value).decode("utf-8")
    except (UnicodeDecodeError, binascii.Error) as error:
        raise SemanticMenuObservationError(
            f"{location}: {name} is not valid UTF-8 hex"
        ) from error
    if "\x00" in decoded or any(0xD800 <= ord(char) <= 0xDFFF for char in decoded):
        raise SemanticMenuObservationError(f"{location}: {name} contains invalid Unicode")
    return decoded


def _integer(value: str, *, location: str, name: str, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise SemanticMenuObservationError(f"{location}: {name} must be an integer") from error
    if parsed < minimum:
        raise SemanticMenuObservationError(f"{location}: {name} must be at least {minimum}")
    return parsed


def parse_observation_lines(lines: Iterable[str], *, source: str = "observation") -> list[MenuObservation]:
    header_seen = False
    active: dict[str, object] | None = None
    observations: list[MenuObservation] = []
    seen_case_ids: set[str] = set()

    for number, raw_line in enumerate(lines, start=1):
        if not raw_line.strip():
            raise SemanticMenuObservationError(f"{source}:{number}: blank observation row")
        fields = raw_line.rstrip("\n").split("\t")
        location = f"{source}:{number}"
        kind = fields[0] if fields else ""
        if kind == "H":
            if header_seen or active is not None or fields != ["H", FORMAT_VERSION]:
                raise SemanticMenuObservationError(f"{location}: invalid observation header")
            header_seen = True
            continue
        if not header_seen:
            raise SemanticMenuObservationError(f"{location}: missing observation header")
        if kind == "M":
            if active is not None or len(fields) != 8 or not fields[1]:
                raise SemanticMenuObservationError(f"{location}: invalid menu row")
            case_id = fields[1]
            if case_id in seen_case_ids:
                raise SemanticMenuObservationError(f"{location}: duplicate case id")
            raw_input = _decode_hex(fields[2], location=location, name="raw_input")
            if not raw_input:
                raise SemanticMenuObservationError(f"{location}: raw_input must not be empty")
            composition_length = _integer(fields[4], location=location, name="composition_length")
            cursor_pos = _integer(fields[5], location=location, name="cursor_pos")
            selection_start = _integer(fields[6], location=location, name="selection_start")
            selection_end = _integer(fields[7], location=location, name="selection_end")
            if selection_start > selection_end or cursor_pos > composition_length:
                raise SemanticMenuObservationError(f"{location}: invalid composition positions")
            active = {
                "case_id": case_id,
                "raw_input": raw_input,
                "composition_preedit": _decode_hex(
                    fields[3], location=location, name="composition_preedit"
                ),
                "composition_length": composition_length,
                "cursor_pos": cursor_pos,
                "selection_start": selection_start,
                "selection_end": selection_end,
                "candidates": [],
            }
            continue
        if kind == "C":
            if active is None or len(fields) != 5 or fields[1] != active["case_id"]:
                raise SemanticMenuObservationError(f"{location}: invalid candidate row")
            menu_index = _integer(fields[2], location=location, name="menu_index")
            candidates = active["candidates"]
            assert isinstance(candidates, list)
            if menu_index != len(candidates):
                raise SemanticMenuObservationError(f"{location}: non-contiguous menu_index")
            text = _decode_hex(fields[3], location=location, name="candidate text")
            if not text:
                raise SemanticMenuObservationError(f"{location}: candidate text must not be empty")
            candidates.append(
                CandidateObservation(
                    menu_index=menu_index,
                    text=text,
                    comment=_decode_hex(fields[4], location=location, name="candidate comment"),
                )
            )
            continue
        if kind == "E":
            if active is None or len(fields) != 5 or fields[1] != active["case_id"]:
                raise SemanticMenuObservationError(f"{location}: invalid end row")
            count = _integer(fields[2], location=location, name="candidate count")
            if fields[3] not in {"0", "1"}:
                raise SemanticMenuObservationError(f"{location}: invalid truncation flag")
            if fields[3] != "0":
                raise SemanticMenuObservationError(f"{location}: truncated menus are not complete observations")
            elapsed_us = _integer(fields[4], location=location, name="elapsed_us")
            candidates = active["candidates"]
            assert isinstance(candidates, list)
            if count != len(candidates):
                raise SemanticMenuObservationError(f"{location}: candidate count mismatch")
            observations.append(
                MenuObservation(
                    case_id=str(active["case_id"]),
                    raw_input=str(active["raw_input"]),
                    composition_preedit=str(active["composition_preedit"]),
                    composition_length=int(active["composition_length"]),
                    cursor_pos=int(active["cursor_pos"]),
                    selection_start=int(active["selection_start"]),
                    selection_end=int(active["selection_end"]),
                    candidates=tuple(candidates),
                    truncated=fields[3] == "1",
                    elapsed_us=elapsed_us,
                )
            )
            seen_case_ids.add(str(active["case_id"]))
            active = None
            continue
        raise SemanticMenuObservationError(f"{location}: unknown observation row")

    if not header_seen:
        raise SemanticMenuObservationError(f"{source}: missing observation header")
    if active is not None:
        raise SemanticMenuObservationError(f"{source}: menu observation missing end row")
    return observations


def load_observations(path: str | Path) -> list[MenuObservation]:
    source = Path(path)
    with source.open(encoding="utf-8") as stream:
        return parse_observation_lines(stream, source=str(source))
