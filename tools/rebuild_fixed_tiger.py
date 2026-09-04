#!/usr/bin/env python3

import argparse
import csv
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fixed_tiger_allocation import (  # noqa: E402
    DoublePinyin,
    SourceEntry,
    TableEntry,
    build_source_entries,
    load_original_character_entries,
    rank_candidates,
    select_records,
)
from flypyify import flypyify1  # noqa: E402
from zrmify import unzrmify1  # noqa: E402
from modern_readings import load_modern_readings  # noqa: E402
from tiger_aux import (  # noqa: E402
    TIGER_EQUIVALENTS,
    load_auxiliary_tsv,
    load_tiger_codes,
)
from zrmify import unzrmify1, zrmify  # noqa: E402

VERSION = "20260816"
LOWERCASE_CODE = re.compile(r"[a-z]+")
EXPECTED_TIGER_CHARACTER_COUNT = 83951
RESEARCH_OUTPUT_DIR = ROOT / "research/tiger_aux/output"
CODE_LENGTH_REPORT = RESEARCH_OUTPUT_DIR / "tiger_zrm_prefix2_all_code_lengths.tsv"
FLYPY_CODE_LENGTH_REPORT = RESEARCH_OUTPUT_DIR / "tiger_flypy_prefix2_all_code_lengths.tsv"
TIGER_RANK_PATH = ROOT / "lua/tiger_rank.txt"
COMPATIBILITY_TARGETS_PATH = ROOT / "tools/data/tiger_compatibility_chars.txt"
SIMPLIFIED_READING_AUDIT_PATH = RESEARCH_OUTPUT_DIR / "simplified_reading_compatibility.tsv"
COMPATIBILITY_CHARSET_PATH = ROOT / "tools/data/simp_chars.txt"
COMPATIBILITY_PROFILE_PATH = ROOT / "tools/data/tiger_race_profile.tsv"
FIXED_CHAR_CODE_OVERRIDES_PATH = (
    ROOT / "tools/data/mohu_fixed_char_code_overrides.tsv"
)
SECONDARY_SHORT_CODES_PATH = ROOT / "tools/data/mohu_fixed_secondary_codes.tsv"
EXPECTED_SIMPLIFIED_READING_CATEGORIES = {
    "all-modern": 8121,
    "mixed": 1,
    "all-compat": 75829,
}
EXPECTED_SIMPLIFIED_COMPATIBILITY_READINGS = 94844
EXPECTED_COMPATIBILITY_CHARACTER_COUNT = 8105
LEGACY_MULTI_SHORT_CODE_MAX_LENGTH = 2
MULTI_CASCADE_CODE_LENGTHS = (3, 4)
GENERATED_CHARACTER_MARKER = "#----------生成单字----------#\n"
WORD_TABLE_MARKER = "#----------词库----------#\n"
PRIORITY_WORD_MARKER = "#----------置顶词----------#\n"

PARENT_TABLES = (
    (
        ROOT / "mohu_zrm_fixed.dict.yaml",
        "mohu_zrm_tiger_fixed",
        ROOT / "tools/data/mohu_fixed_simp_legacy_chars.txt",
    ),
)


@dataclass(frozen=True)
class CollisionGroup:
    code: str
    characters: tuple[str, ...]
    unresolved: tuple[str, ...]


@dataclass(frozen=True)
class CollisionAudit:
    codeable_count: int
    groups: tuple[CollisionGroup, ...]

    @property
    def group_count(self) -> int:
        return len(self.groups)

    @property
    def non_first_count(self) -> int:
        return sum(len(group.unresolved) for group in self.groups)


def split_dictionary(text: str) -> tuple[str, list[str]]:
    marker = "...\n"
    if marker not in text:
        raise ValueError("Rime dictionary is missing the YAML body marker")
    header, body = text.split(marker, 1)
    return header + marker, body.splitlines(keepends=True)


def remove_import(header: str, table_name: str) -> str:
    import_line = f"  - {table_name}\n"
    return header.replace(import_line, "", 1)


def strip_generated_character_block(body: str) -> str:
    if GENERATED_CHARACTER_MARKER not in body:
        return body
    start = body.index(GENERATED_CHARACTER_MARKER)
    end = body.index(WORD_TABLE_MARKER, start)
    return body[:start] + body[end:]


def split_priority_word_block(body: str) -> tuple[str, str]:
    # 置顶词块是手工维护的词库条目，渲染时放在生成单字之前，
    # 使这些词在同码竞争中排在单字前面。
    if PRIORITY_WORD_MARKER not in body:
        return "", body
    start = body.index(PRIORITY_WORD_MARKER)
    ends = [
        index
        for index in (
            body.find(GENERATED_CHARACTER_MARKER, start + 1),
            body.find(WORD_TABLE_MARKER, start + 1),
        )
        if index != -1
    ]
    if not ends:
        raise ValueError("priority word block must precede a known block marker")
    end = min(ends)
    return body[start:end], body[:start] + body[end:]


def rebuild_parent(path: Path, table_name: str) -> tuple[str, list[tuple[int, str]]]:
    source = path.read_text(encoding="utf-8")
    header, body_lines = split_dictionary(source)
    body = strip_generated_character_block("".join(body_lines)).splitlines(
        keepends=True
    )
    header_lines = header.splitlines(keepends=True)
    removed = []
    retained = []
    in_fly_block = False
    for offset, raw_line in enumerate(body, start=len(header_lines) + 1):
        line = raw_line.rstrip("\n")
        # 飞键区块（# 开始飞键 ... # 结束飞键）与词库一样手工维护，
        # 其中的单字飞键条目不参与清扫与重生成。
        if line.startswith("# 开始飞键"):
            in_fly_block = True
        elif line.startswith("# 结束飞键"):
            in_fly_block = False
        fields = line.split("\t")
        if (
            not in_fly_block
            and len(fields) >= 2
            and len(fields[0]) == 1
            and LOWERCASE_CODE.fullmatch(fields[1])
        ):
            removed.append((offset, line))
        else:
            retained.append(raw_line)
    return remove_import(header, table_name) + "".join(retained), removed


def render_archive(source_name: str, removed: list[tuple[int, str]]) -> str:
    lines = [
        f"# Lowercase single-character rows removed from {source_name}.\n",
        "# source_line\toriginal_row\n",
    ]
    lines.extend(f"{line_number}\t{row}\n" for line_number, row in removed)
    return "".join(lines)


def render_table(name: str, rows: list[tuple[str, str, str]]) -> str:
    lines = [
        "# Generated by tools/rebuild_fixed_tiger.py. DO NOT EDIT.\n",
        "---\n",
        f"name: {name}\n",
        f'version: "{VERSION}"\n',
        "sort: by_weight\n",
        "columns:\n",
        "  - text\n",
        "  - code\n",
        "  - weight\n",
        "...\n",
        "\n",
    ]
    lines.extend(f"{char}\t{code}\t{weight}\n" for char, code, weight in rows)
    return "".join(lines)


def render_parent_variant(
    source: str,
    name: str,
    source_table: str,
    target_table: str,
) -> str:
    """Rename the fixed-word parent before embedding another character table."""
    header, body = split_dictionary(source)
    header = re.sub(r"(?m)^name:\s+\S+\s*$", f"name: {name}", header, count=1)
    return header + "".join(body)


def render_parent_with_characters(
    source: str,
    rows: list[tuple[str, str, str]],
) -> str:
    header, body = split_dictionary(source)
    priority_block, remaining = split_priority_word_block("".join(body))
    character_rows = "".join(
        f"{char}\t{code}\t\t{weight}\n" for char, code, weight in rows
    )
    remaining = refresh_fly_characters(remaining, rows)
    return (
        header
        + "\n"
        + priority_block
        + GENERATED_CHARACTER_MARKER
        + character_rows
        + "\n"
        + remaining.lstrip("\n")
    )


FLY_SUBSTITUTIONS = {"wz": "wk", "xq": "xo", "qx": "qo"}


def refresh_fly_characters(
    body: str,
    rows: list[tuple[str, str, str]],
) -> str:
    """按当前字表重生成飞键区块中的单字行（先剔除旧单字行，再按码序注入）。

    单字飞键随码表分配走：zrm 与 legacy 的单字简码不同，
    渲染每个变体时都以各自的字表为准，避免继承另一方案的飞键单字。
    """
    start_mark = "# 开始飞键 "
    end_mark = "# 结束飞键"
    fly_targets = {new: [] for new in FLY_SUBSTITUTIONS.values()}
    for char, code, weight in rows:
        if len(code) >= 2 and code[:2] in FLY_SUBSTITUTIONS:
            new = FLY_SUBSTITUTIONS[code[:2]]
            flycode = new + code[2:]
            fly_targets[new].append((flycode, f"{char}\t{flycode}\t\t{weight}\n"))

    # 第一遍：剔除飞键区块内既有的单字行
    stripped_lines = []
    block_new = None
    for raw in body.splitlines(keepends=True):
        stripped = raw.rstrip("\n")
        if stripped.startswith(start_mark):
            block_new = stripped.split("->")[-1].strip()
        elif stripped.startswith(end_mark):
            block_new = None
        elif block_new is not None:
            fields = stripped.split("\t")
            if (len(fields) >= 2 and len(fields[0]) == 1
                    and LOWERCASE_CODE.fullmatch(fields[1])):
                continue
        stripped_lines.append(raw)

    # 第二遍：按码序把生成的单字行注入对应块（同码保持字表顺序）
    result = []
    pending = None
    for raw in stripped_lines:
        stripped = raw.rstrip("\n")
        if stripped.startswith(start_mark):
            label_new = stripped.split("->")[-1].strip()
            pending = list(fly_targets.get(label_new, []))
            result.append(raw)
            continue
        if stripped.startswith(end_mark):
            result.extend(line for _, line in pending or [])
            pending = None
            result.append(raw)
            continue
        if pending:
            fields = stripped.split("\t")
            code = (fields[1] if len(fields) >= 2
                    and LOWERCASE_CODE.fullmatch(fields[1] or "") else None)
            while pending and code is not None and pending[0][0] <= code:
                result.append(pending.pop(0)[1])
        result.append(raw)
    result.extend(line for _, line in pending or [])
    return "".join(result)


def load_production_pinyin_table(path: Path) -> dict[str, list[tuple[str, float]]]:
    weights: dict[str, dict[str, float]] = defaultdict(dict)
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        parts = raw_line.split("\t")
        if len(parts) < 3:
            continue
        text, pinyin = parts[0].strip(), parts[1].strip()
        if len(text) != 1 or not pinyin:
            continue
        frequencies = []
        for value in parts[2:]:
            try:
                frequencies.append(float(value.strip()))
            except ValueError:
                continue
        if not frequencies:
            continue
        weight = max(frequencies)
        weights[text][pinyin] = max(weight, weights[text].get(pinyin, weight))
    return {
        text: list(readings.items())
        for text, readings in weights.items()
    }


def load_compatibility_order(
    charset_path: Path,
    profile_path: Path,
) -> list[str]:
    charset = [
        line.strip()
        for line in charset_path.read_text(encoding="utf-8-sig").splitlines()
        if len(line.strip()) == 1 and not line.startswith("#")
    ]
    try:
        rows = list(
            csv.DictReader(
                profile_path.read_text(encoding="utf-8-sig").splitlines(),
                delimiter="\t",
            )
        )
        order = [row["char"] for row in rows]
        ranks = [int(row["rank"]) for row in rows]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid compatibility profile") from error
    if (
        len(charset) != len(set(charset))
        or len(order) != len(set(order))
        or any(len(char) != 1 for char in order)
        or ranks != list(range(1, len(order) + 1))
        or set(order) != set(charset)
    ):
        raise ValueError("compatibility profile does not match charset")
    return order


def load_reading_evidence(
    path: Path,
) -> dict[str, list[tuple[str, float, float]]]:
    result: dict[str, list[tuple[str, float, float]]] = defaultdict(list)
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        parts = raw_line.split("\t")
        if len(parts) < 2 or len(parts[0]) != 1 or not parts[1]:
            continue
        try:
            trad_weight = float(parts[2]) if len(parts) >= 3 else 0.0
            simp_weight = float(parts[3]) if len(parts) >= 4 else 0.0
        except ValueError:
            continue
        result[parts[0]].append((parts[1], trad_weight, simp_weight))
    return dict(result)


def load_fixed_char_code_overrides(
    path: Path,
    double_pinyin: DoublePinyin,
) -> dict[str, str]:
    if double_pinyin not in {"zrm", "flypy"}:
        raise ValueError(f"unsupported double-pinyin scheme: {double_pinyin}")

    records: dict[str, tuple[str, str]] = {}
    code_owners: dict[str, dict[str, str]] = {"zrm": {}, "flypy": {}}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(),
        1,
    ):
        if not raw_line.strip() or raw_line.startswith("#"):
            continue
        fields = [field.strip() for field in raw_line.split("\t")]
        if len(fields) != 3:
            raise ValueError(
                f"invalid fixed character override at {path}:{line_number}: "
                "expected character, natural code, and Flypy code"
            )
        text, zrm_code, flypy_code = fields
        if len(text) != 1:
            raise ValueError(
                f"invalid fixed character override at {path}:{line_number}: "
                "character must contain exactly one code point"
            )
        if text in records:
            raise ValueError(f"duplicate character override at {path}:{line_number}: {text}")
        for scheme, code in (("zrm", zrm_code), ("flypy", flypy_code)):
            if re.fullmatch(r"[a-z]{3}", code) is None:
                raise ValueError(
                    f"invalid {scheme} override at {path}:{line_number}: "
                    "code must contain exactly three lowercase letters"
                )
            if code in code_owners[scheme]:
                raise ValueError(
                    f"duplicate {scheme} code at {path}:{line_number}: {code} "
                    f"({code_owners[scheme][code]} and {text})"
                )
            code_owners[scheme][code] = text
        records[text] = (zrm_code, flypy_code)

    index = 0 if double_pinyin == "zrm" else 1
    return {text: codes[index] for text, codes in records.items()}


def validate_fixed_char_codes(
    fixed_codes: dict[str, str],
    full_codes: dict[str, list[str]],
    allowed: set[str],
) -> None:
    for text, code in fixed_codes.items():
        if text not in allowed:
            raise ValueError(f"fixed character override is outside Tiger order: {text}")
        if not any(full_code.startswith(code) for full_code in full_codes[text]):
            raise ValueError(
                f"fixed character override does not prefix a current full code: "
                f"{text} {code}"
            )


def build_tiger_character_order(
    tiger_path: Path,
    pinyin_table: dict[str, list[tuple[str, float]]],
    *,
    expected_count: int = EXPECTED_TIGER_CHARACTER_COUNT,
) -> list[str]:
    order = [char for char in load_tiger_codes(tiger_path) if char in pinyin_table]
    if len(order) != expected_count:
        raise ValueError(
            f"expected {expected_count} Tiger characters with pronunciations, "
            f"got {len(order)}"
        )
    return order


def reserve_shortest_prefix(
    text: str,
    candidate_full_codes: list[str],
    occupied: dict[str, str],
    encoded: dict[str, list[str]],
    *,
    start_length: int = 1,
) -> str | None:
    uncovered = [
        code
        for code in dict.fromkeys(candidate_full_codes)
        if not any(code.startswith(existing) for existing in encoded[text])
    ]
    for length in range(max(1, start_length), 4):
        for full_code in uncovered:
            if len(full_code) <= length:
                continue
            prefix = full_code[:length]
            if prefix in occupied:
                continue
            occupied[prefix] = text
            encoded[text].append(prefix)
            return prefix
    return None


def validate_unique_short_codes(rows: list[TableEntry]) -> None:
    owners: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        if 1 <= len(row.code) <= 3 and row.text not in owners[row.code]:
            owners[row.code].append(row.text)
    for code, characters in sorted(owners.items()):
        if len(characters) > 1:
            raise ValueError(f"duplicate short code {code}: {' '.join(characters)}")


def allocate_reading_ordered_codes(
    entries: list[SourceEntry],
    tiger_order: list[str],
    legacy_entries: list[SourceEntry] | None = None,
    *,
    fixed_codes: dict[str, str] | None = None,
) -> list[TableEntry]:
    fixed_codes = fixed_codes or {}
    blocked_codes = {code[:2] for code in fixed_codes.values()}
    occupied: dict[str, str] = {code: "<fixed-word-slot>" for code in blocked_codes}
    encoded: dict[str, list[str]] = defaultdict(list)
    rows: list[TableEntry] = []
    seen: set[tuple[str, str]] = set()
    seen_requests: set[tuple[str, str]] = set()
    full_codes: dict[str, list[str]] = defaultdict(list)
    weights: dict[str, float] = defaultdict(float)
    for entry in entries:
        full_codes[entry.text].append(entry.code.strip().lower())
        weights[entry.text] = max(weights[entry.text], entry.weight)

    tiger_chars = set(tiger_order)
    validate_fixed_char_codes(fixed_codes, full_codes, tiger_chars)
    for text, code in fixed_codes.items():
        if code in occupied:
            raise ValueError(f"fixed character override code is already occupied: {text} {code}")
        occupied[code] = text
        encoded[text].append(code)

    for entry in legacy_entries or []:
        code = entry.code.strip().lower()
        request = (entry.text, code)
        matching_full_codes = [
            full for full in full_codes[entry.text] if full.startswith(code)
        ]
        if (
            request in seen_requests
            or entry.text in fixed_codes
            or not 1 <= len(code) <= 3
            or not matching_full_codes
        ):
            continue
        seen_requests.add(request)
        assigned = reserve_shortest_prefix(
            entry.text,
            matching_full_codes,
            occupied,
            encoded,
            start_length=len(code),
        )
        if assigned is None:
            continue
        value = (entry.text, assigned)
        if value in seen:
            continue
        seen.add(value)
        rows.append(
            TableEntry(
                entry.text,
                assigned,
                weights[entry.text],
                len(rows),
                source="original",
            )
        )

    for text, code in fixed_codes.items():
        value = (text, code)
        if value in seen:
            continue
        seen.add(value)
        rows.append(
            TableEntry(
                text,
                code,
                weights[text],
                len(rows),
                source="original",
            )
        )

    for entry in entries:
        if entry.text not in tiger_chars or entry.text in fixed_codes:
            continue
        code = entry.code.strip().lower()
        if not code or any(code.startswith(existing) for existing in encoded[entry.text]):
            continue
        assigned = reserve_shortest_prefix(
            entry.text,
            [code],
            occupied,
            encoded,
        )
        if assigned is not None:
            value = (entry.text, assigned)
            if value not in seen:
                seen.add(value)
                rows.append(
                    TableEntry(
                        entry.text,
                        assigned,
                        entry.weight,
                        len(rows),
                        source=entry.source,
                    )
                )
    validate_unique_short_codes(rows)
    return rank_candidates(rows)


def load_secondary_short_codes(path: Path) -> list[tuple[str, str]]:
    """Load hand-curated low-priority quick codes (secondary short codes).

    These rows are appended to the multi-short-code table at low priority
    without occupying the exclusive allocation; they also count as coverage
    so the character's prefix-matched auto-assigned codes are suppressed.
    Codes are natural-code (zrm); Flypy tables are converted downstream.
    """
    records: list[tuple[str, str]] = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(),
        1,
    ):
        if not raw_line.strip() or raw_line.startswith("#"):
            continue
        fields = [field.strip() for field in raw_line.split("\t")]
        if (
            len(fields) != 2
            or len(fields[0]) != 1
            or not LOWERCASE_CODE.fullmatch(fields[1])
        ):
            raise ValueError(
                f"invalid secondary short code at {path}:{line_number}: "
                "expected a character and a lowercase code"
            )
        records.append((fields[0], fields[1]))
    return records


def allocate_legacy_codes(
    cascade_entries: list[SourceEntry] | list[TableEntry],
    tiger_order: list[str],
    legacy_entries: list[SourceEntry],
    legacy_full_entries: list[SourceEntry] | list[TableEntry] | None = None,
    fallback_rows: list[TableEntry] | None = None,
    *,
    fixed_codes: dict[str, str] | None = None,
    secondary_codes: list[tuple[str, str]] | None = None,
) -> list[TableEntry]:
    """Keep legacy collisions, then cascade three- and four-key rows.

    One- and two-key codes preserve rime-moran's multi-short-code behavior and
    the existing unique-mode fallback rows. Longer rows use current full codes
    and skip characters already covered by a matching shorter prefix.
    Secondary short codes are registered as coverage first (suppressing the
    character's prefix-matched fallback/cascade codes) and emitted last so
    they render after existing rows sharing the same code.
    """
    fixed_codes = fixed_codes or {}
    blocked_codes = {code[:2] for code in fixed_codes.values()}
    allowed = set(tiger_order)
    tiger_rank = {char: rank for rank, char in enumerate(tiger_order)}
    current_full_codes: dict[str, list[str]] = defaultdict(list)
    weights: dict[str, float] = defaultdict(float)
    for entry in legacy_full_entries or cascade_entries:
        current_full_codes[entry.text].append(entry.code.strip().lower())
        weights[entry.text] = max(weights[entry.text], entry.weight)
    validate_fixed_char_codes(fixed_codes, current_full_codes, allowed)
    rows: list[TableEntry] = []
    assigned: dict[str, list[str]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for entry in legacy_entries:
        code = entry.code.strip().lower()
        value = (entry.text, code)
        if (
            entry.text not in allowed
            or entry.text in fixed_codes
            or value in seen
            or not re.fullmatch(r"[a-z]+", code)
            or not 1 <= len(code) <= LEGACY_MULTI_SHORT_CODE_MAX_LENGTH
            or code in blocked_codes
            or not any(
                full_code.startswith(code)
                for full_code in current_full_codes[entry.text]
            )
        ):
            continue
        seen.add(value)
        assigned[entry.text].append(code)
        rows.append(
            TableEntry(
                entry.text,
                code,
                entry.weight,
                len(rows),
                source="original",
            )
        )

    pending_secondary: list[tuple[str, str]] = []
    for text, code in secondary_codes or []:
        value = (text, code)
        if (
            text not in allowed
            or text in fixed_codes
            or value in seen
            or code in blocked_codes
        ):
            raise ValueError(f"invalid secondary short code: {text} {code}")
        seen.add(value)
        assigned[text].append(code)
        pending_secondary.append(value)

    legacy_texts = set(assigned)
    for entry in fallback_rows or []:
        code = entry.code.strip().lower()
        value = (entry.text, code)
        if (
            entry.text not in allowed
            or entry.text in fixed_codes
            or entry.text in legacy_texts
            or value in seen
            or not LOWERCASE_CODE.fullmatch(code)
            or not 1 <= len(code) <= LEGACY_MULTI_SHORT_CODE_MAX_LENGTH
            or code in blocked_codes
            or not any(
                full_code.startswith(code)
                for full_code in current_full_codes[entry.text]
            )
        ):
            continue
        seen.add(value)
        assigned[entry.text].append(code)
        rows.append(
            TableEntry(
                entry.text,
                code,
                entry.weight,
                len(rows),
                source=entry.source,
            )
        )

    for text, code in fixed_codes.items():
        value = (text, code)
        if value in seen:
            continue
        seen.add(value)
        assigned[text].append(code)
        rows.append(
            TableEntry(
                text,
                code,
                weights[text],
                len(rows),
                source="original",
            )
        )

    for length in MULTI_CASCADE_CODE_LENGTHS:
        candidates: dict[str, list[tuple[float, int, int, str]]] = defaultdict(list)
        for source_order, entry in enumerate(cascade_entries):
            code = entry.code.strip().lower()
            if (
                entry.text not in allowed
                or entry.text in fixed_codes
                or len(code) < length
            ):
                continue
            candidates[code[:length]].append(
                (-entry.weight, tiger_rank[entry.text], source_order, entry.text)
            )

        for prefix in sorted(candidates):
            if prefix in fixed_codes.values():
                continue
            checked: set[str] = set()
            for negative_weight, _, source_order, text in sorted(candidates[prefix]):
                if text in checked:
                    continue
                checked.add(text)
                if any(
                    len(shorter) < length and prefix.startswith(shorter)
                    for shorter in assigned[text]
                ):
                    continue
                seen.add((text, prefix))
                assigned[text].append(prefix)
                rows.append(
                    TableEntry(
                        text,
                        prefix,
                        -negative_weight,
                        len(rows),
                        source=cascade_entries[source_order].source,
                    )
                )
                break

    for text, code in pending_secondary:
        if any(row.text == text and row.code == code for row in rows):
            continue
        rows.append(
            TableEntry(
                text,
                code,
                weights[text],
                len(rows),
                source="original",
            )
        )
    return rank_candidates(rows)


def find_compatibility_target_characters(
    base_rows: list[TableEntry],
    primary_entries: list[SourceEntry] | list[TableEntry],
    visible_order: list[str],
) -> list[str]:
    """Find four-key collision losers without any one-to-three-key shortcut."""
    visible_rank = {char: rank for rank, char in enumerate(visible_order)}
    visible = set(visible_rank)
    short_texts = {
        row.text
        for row in base_rows
        if row.text in visible and 1 <= len(row.code) <= 3
    }
    exact_owner_by_code = {
        row.code: row.text
        for row in base_rows
        if len(row.code) == 4 and row.text in visible
    }
    remaining_by_code: dict[str, set[str]] = defaultdict(set)
    for entry in primary_entries:
        if (
            entry.text in visible
            and entry.text not in short_texts
            and len(entry.code) == 4
        ):
            remaining_by_code[entry.code].add(entry.text)

    targets = set()
    for full_code, characters in remaining_by_code.items():
        if len(characters) < 2:
            continue
        exact_owner = exact_owner_by_code.get(full_code)
        owner = (
            exact_owner
            if exact_owner in characters
            else min(characters, key=visible_rank.__getitem__)
        )
        targets.update(characters - {owner})
    return [char for char in visible_order if char in targets]


def allocate_compatibility_codes(
    base_rows: list[TableEntry],
    primary_entries: list[SourceEntry] | list[TableEntry],
    compatibility_entries: list[SourceEntry] | list[TableEntry],
    visible_order: list[str],
) -> list[TableEntry]:
    """Append fixed Natural-code compatibility rows over a visible charset."""
    visible_rank = {char: rank for rank, char in enumerate(visible_order)}
    visible = set(visible_rank)
    occupied: dict[str, set[str]] = defaultdict(set)
    for row in base_rows:
        if row.text in visible:
            occupied[row.code].add(row.text)
    unresolved = find_compatibility_target_characters(
        base_rows, primary_entries, visible_order
    )
    unresolved_set = set(unresolved)

    candidates_by_code: dict[str, list[tuple[int, int, SourceEntry | TableEntry]]] = (
        defaultdict(list)
    )
    input_entries: dict[tuple[str, str], SourceEntry | TableEntry] = {}
    edges_by_text: dict[str, list[str]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for source_order, entry in enumerate(compatibility_entries):
        value = (entry.text, entry.code)
        if (
            entry.text not in unresolved_set
            or value in seen
        ):
            continue
        seen.add(value)
        input_entries[value] = entry
        if occupied[entry.code]:
            continue
        candidates_by_code[entry.code].append(
            (visible_rank[entry.text], source_order, entry)
        )
        edges_by_text[entry.text].append(entry.code)

    owner_by_code: dict[str, str] = {}

    def augment(text: str, visited: set[str]) -> bool:
        for code in edges_by_text.get(text, []):
            if code in visited:
                continue
            visited.add(code)
            owner = owner_by_code.get(code)
            if owner is None or augment(owner, visited):
                owner_by_code[code] = text
                return True
        return False

    for text in unresolved:
        augment(text, set())

    for code in sorted(candidates_by_code):
        if code not in owner_by_code:
            remaining = [
                item
                for item in candidates_by_code[code]
                if item[2].text in unresolved_set
            ]
            if remaining:
                owner_by_code[code] = min(remaining)[2].text

    selected_entries: dict[tuple[str, str], SourceEntry | TableEntry] = {}
    for code, text in owner_by_code.items():
        matching = [
            item
            for item in candidates_by_code[code]
            if item[2].text == text
        ]
        _, _, entry = min(matching)
        selected_entries[(text, code)] = entry

    rows = list(base_rows)
    for text, code in sorted(
        selected_entries,
        key=lambda value: (value[1], visible_rank[value[0]]),
    ):
        entry = selected_entries[(text, code)]
        rows.append(
            TableEntry(
                text,
                code,
                entry.weight,
                len(rows),
                source=entry.source,
            )
        )
    fallback_entries = {
        value: entry
        for value, entry in input_entries.items()
        if value[0] in unresolved_set and value not in selected_entries
    }
    for text, code in sorted(
        fallback_entries,
        key=lambda value: (value[1], visible_rank[value[0]]),
    ):
        entry = fallback_entries[(text, code)]
        rows.append(
            TableEntry(
                text,
                code,
                0.0,
                len(rows),
                source="pinyin_fallback",
            )
        )
    return rank_candidates(rows)


def audit_full_code_collisions(
    primary_entries: list[SourceEntry] | list[TableEntry],
    baseline_rows: list[TableEntry],
    compatibility_entries: list[SourceEntry] | list[TableEntry],
    visible_order: list[str],
    thresholds: tuple[int, ...],
) -> dict[int, CollisionAudit]:
    """Audit original full-code groups after shorter and compatibility exits."""
    invalid_thresholds = [
        threshold
        for threshold in thresholds
        if threshold < 0 or threshold > len(visible_order)
    ]
    if invalid_thresholds:
        raise ValueError(f"invalid collision threshold: {invalid_thresholds[0]}")

    baseline_pairs = {(row.text, row.code) for row in baseline_rows}
    result = {}
    for threshold in thresholds:
        scoped_order = visible_order[:threshold]
        visible_rank = {
            char: rank for rank, char in enumerate(scoped_order)
        }
        visible = set(visible_rank)
        short_texts = {
            row.text
            for row in baseline_rows
            if row.text in visible and 1 <= len(row.code) <= 3
        }
        exact_owner_by_code = {
            row.code: row.text
            for row in baseline_rows
            if len(row.code) == 4 and row.text in visible
        }
        rescued_paths: dict[str, set[str]] = defaultdict(set)
        if compatibility_entries:
            allocated_rows = allocate_compatibility_codes(
                baseline_rows,
                primary_entries,
                compatibility_entries,
                scoped_order,
            )
            for row in allocated_rows:
                if (
                    row.text in visible
                    and (row.text, row.code) not in baseline_pairs
                    and row.source != "pinyin_fallback"
                ):
                    rescued_paths[row.text].add(row.code[:-2])

        characters_by_code: dict[str, set[str]] = defaultdict(set)
        codeable = set()
        for entry in primary_entries:
            if (
                entry.text not in visible
                or entry.text in short_texts
                or len(entry.code) != 4
            ):
                continue
            codeable.add(entry.text)
            characters_by_code[entry.code].add(entry.text)

        groups = []
        for code, characters in characters_by_code.items():
            if len(characters) < 2:
                continue
            exact_owner = exact_owner_by_code.get(code)
            owner = (
                exact_owner
                if exact_owner in characters
                else min(characters, key=visible_rank.__getitem__)
            )
            losers = characters - {owner}
            phonetic_path = code[:-2]
            unresolved = tuple(
                sorted(
                    (
                        char
                        for char in losers
                        if phonetic_path not in rescued_paths.get(char, set())
                    ),
                    key=visible_rank.__getitem__,
                )
            )
            if not unresolved:
                continue
            ordered_characters = (
                owner,
                *sorted(losers, key=visible_rank.__getitem__),
            )
            groups.append(
                CollisionGroup(code, ordered_characters, unresolved)
            )
        groups.sort(key=lambda group: group.code)
        result[threshold] = CollisionAudit(
            codeable_count=len(codeable),
            groups=tuple(groups),
        )
    return result


def convert_legacy_entries(
    entries: list[SourceEntry],
    double_pinyin: DoublePinyin,
) -> list[SourceEntry]:
    if double_pinyin == "zrm":
        return entries
    converted = []
    for entry in entries:
        code = entry.code.strip().lower()
        if len(code) > 1:
            try:
                code = flypyify1(unzrmify1(code[:2])) + code[2:]
            except (AssertionError, IndexError, ValueError):
                pass
        converted.append(
            SourceEntry(entry.text, code, entry.weight, entry.source)
        )
    return converted


def build_full_character_allocation(
    tiger_path: Path,
    chars_path: Path,
    auxiliary_path: Path,
    *,
    expected_count: int = EXPECTED_TIGER_CHARACTER_COUNT,
    legacy_path: Path | None = None,
    shortcut_readings: set[tuple[str, str]] | None = None,
    double_pinyin: DoublePinyin = "zrm",
    fixed_codes: dict[str, str] | None = None,
    secondary_codes: list[tuple[str, str]] | None = None,
    compatibility_auxiliary_codes: dict[str, list[str]] | None = None,
    compatibility_order: list[str] | None = None,
    compatibility_targets_out: list[str] | None = None,
) -> tuple[
    list[str],
    list[TableEntry],
    list[TableEntry],
    list[TableEntry],
    dict[str, float],
]:
    pinyin_table = load_production_pinyin_table(chars_path)
    tiger_order = build_tiger_character_order(
        tiger_path,
        pinyin_table,
        expected_count=expected_count,
    )
    auxiliary = load_auxiliary_tsv(auxiliary_path)
    missing = [char for char in tiger_order if char not in auxiliary]
    if missing:
        raise ValueError(f"missing Tiger auxiliary codes: {' '.join(missing[:20])}")
    # 正常辅码只用首选主码（12 位），镜像主码不再参与简快码分配。
    primary_auxiliary = {
        char: [entry.normal] for char, entry in auxiliary.items()
    }

    tiger_chars = set(tiger_order)
    aliases = [
        alias
        for alias in TIGER_EQUIVALENTS
        if alias in pinyin_table and alias not in tiger_chars
    ]
    source_entries = build_source_entries(
        [*tiger_order, *aliases],
        pinyin_table,
        primary_auxiliary,
        double_pinyin=double_pinyin,
    )
    shortcut_pinyin_table = pinyin_table
    if shortcut_readings is not None:
        shortcut_pinyin_table = {
            char: [
                (pinyin, weight)
                for pinyin, weight in readings
                if (char, pinyin) in shortcut_readings
            ]
            for char, readings in pinyin_table.items()
        }
    shortcut_source_entries = build_source_entries(
        [*tiger_order, *aliases],
        shortcut_pinyin_table,
        primary_auxiliary,
        double_pinyin=double_pinyin,
    )
    legacy_entries = (
        load_original_character_entries([legacy_path])
        if legacy_path is not None
        else []
    )
    legacy_entries = convert_legacy_entries(legacy_entries, double_pinyin)
    short_rows = allocate_reading_ordered_codes(
        shortcut_source_entries,
        tiger_order,
        legacy_entries,
        fixed_codes=fixed_codes,
    )
    multi_rows = allocate_legacy_codes(
        shortcut_source_entries,
        tiger_order,
        legacy_entries,
        legacy_full_entries=source_entries,
        fallback_rows=short_rows,
        fixed_codes=fixed_codes,
        secondary_codes=secondary_codes,
    )
    if (compatibility_auxiliary_codes is None) != (compatibility_order is None):
        raise ValueError(
            "compatibility auxiliary codes and order must be provided together"
        )
    compatibility_targets = []
    if compatibility_auxiliary_codes is not None and compatibility_order is not None:
        compatibility_entries = build_source_entries(
            compatibility_order,
            shortcut_pinyin_table,
            compatibility_auxiliary_codes,
            double_pinyin=double_pinyin,
        )
        compatibility_targets = find_compatibility_target_characters(
            multi_rows,
            shortcut_source_entries,
            compatibility_order,
        )
        multi_rows = allocate_compatibility_codes(
            multi_rows,
            shortcut_source_entries,
            compatibility_entries,
            compatibility_order,
        )
    if compatibility_targets_out is not None:
        compatibility_targets_out.extend(compatibility_targets)
    full_rows = []
    for entry in source_entries:
        full_rows.append(
            TableEntry(
                entry.text,
                entry.code,
                entry.weight,
                len(full_rows),
                source=entry.source,
            )
        )
    full_rows = rank_candidates(full_rows)

    invalid = [
        row
        for row in [*short_rows, *multi_rows, *full_rows]
        if not LOWERCASE_CODE.fullmatch(row.code) or not 1 <= len(row.code) <= 4
    ]
    if invalid:
        sample = invalid[0]
        raise ValueError(f"invalid allocated code: {sample.text} {sample.code}")

    weights = {
        char: max(weight for _, weight in pinyin_table[char])
        for char in tiger_order
    }
    return tiger_order, short_rows, multi_rows, full_rows, weights


def render_code_length_report(
    tiger_order: list[str],
    short_rows: list[TableEntry],
    full_rows: list[TableEntry],
    weights: dict[str, float],
) -> str:
    selected = select_records([*short_rows, *full_rows], tiger_order, weights)
    if len(selected) != len(tiger_order):
        raise ValueError(
            f"expected {len(tiger_order)} selected characters, got {len(selected)}"
        )
    lines = ["tiger_rank\tchar\tshort_code\tcode_length\n"]
    lines.extend(
        f"{record['rank']}\t{record['char']}\t{record['short_code']}\t"
        f"{len(str(record['short_code']))}\n"
        for record in selected
    )
    return "".join(lines)


def render_tiger_rank(tiger_order: list[str]) -> str:
    return "".join(
        f"{char}\t{rank}\n"
        for rank, char in enumerate(tiger_order, 1)
    )


def render_compatibility_targets(characters: list[str]) -> str:
    return (
        "# Generated by tools/rebuild_fixed_tiger.py. DO NOT EDIT.\n"
        + "".join(f"{char}\n" for char in characters)
    )


def render_simplified_reading_audit(
    tiger_order: list[str],
    evidence: dict[str, list[tuple[str, float, float]]],
    modern_readings: set[tuple[str, str]],
    short_rows: list[TableEntry],
    auxiliary: dict[str, list[str]],
) -> str:
    shortcuts: dict[str, list[str]] = defaultdict(list)
    for row in short_rows:
        if row.code not in shortcuts[row.text]:
            shortcuts[row.text].append(row.code)

    categories = defaultdict(int)
    compatibility_count = 0
    lines = [
        "tiger_rank\tchar\tpinyin\ttrad_weight\tsimp_weight\t"
        "classification\tshortcut_codes\tfull_codes\n"
    ]
    for rank, char in enumerate(tiger_order, 1):
        readings = evidence[char]
        modern_count = sum((char, pinyin) in modern_readings for pinyin, _, _ in readings)
        if modern_count == len(readings):
            categories["all-modern"] += 1
        elif modern_count:
            categories["mixed"] += 1
        else:
            categories["all-compat"] += 1

        for pinyin, trad_weight, simp_weight in readings:
            classification = (
                "modern" if (char, pinyin) in modern_readings else "compatibility"
            )
            full_codes = list(
                dict.fromkeys(zrmify(pinyin) + code for code in auxiliary[char].codes())
            )
            matching_shortcuts = []
            if classification == "modern":
                matching_shortcuts = [
                    code
                    for code in shortcuts.get(char, [])
                    if any(full_code.startswith(code) for full_code in full_codes)
                ]
            if classification == "compatibility":
                compatibility_count += 1
            lines.append(
                f"{rank}\t{char}\t{pinyin}\t{trad_weight:g}\t{simp_weight:g}\t"
                f"{classification}\t{' '.join(matching_shortcuts)}\t"
                f"{' '.join(full_codes)}\n"
            )

    if dict(categories) != EXPECTED_SIMPLIFIED_READING_CATEGORIES:
        raise ValueError(f"unexpected simplified reading categories: {dict(categories)}")
    if compatibility_count != EXPECTED_SIMPLIFIED_COMPATIBILITY_READINGS:
        raise ValueError(
            f"expected {EXPECTED_SIMPLIFIED_COMPATIBILITY_READINGS} compatibility "
            f"readings, got {compatibility_count}"
        )
    return "".join(lines)


def write_or_check(path: Path, expected: str, check: bool) -> bool:
    actual = path.read_text(encoding="utf-8") if path.exists() else None
    if actual == expected:
        return True
    if check:
        print(f"out of date: {path.relative_to(ROOT)}")
        return False
    path.write_text(expected, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if generated files differ")
    args = parser.parse_args()

    outputs = []
    parent_variants = []
    for parent, table_name, archive in PARENT_TABLES:
        parent_text, removed = rebuild_parent(parent, table_name)
        legacy_parent = parent.with_name(
            parent.name.replace("_fixed.dict.yaml", "_fixed_legacy.dict.yaml")
        )
        parent_variants.append(
            (parent, legacy_parent, table_name, parent_text)
        )
        if removed:
            outputs.append((archive, render_archive(parent.name, removed)))
        elif not archive.exists():
            raise ValueError(f"missing legacy archive for {parent.name}")

    tiger_path = ROOT / "tiger.dict.yaml"
    chars_path = ROOT / "tools/data/chars.txt"
    auxiliary_path = ROOT / "tools/data/tiger_aux.txt"
    modern_readings = load_modern_readings(ROOT / "tools/data/pinyin_simp.txt")
    compatibility_order = load_compatibility_order(
        COMPATIBILITY_CHARSET_PATH,
        COMPATIBILITY_PROFILE_PATH,
    )
    if len(compatibility_order) != EXPECTED_COMPATIBILITY_CHARACTER_COUNT:
        raise ValueError(
            f"expected {EXPECTED_COMPATIBILITY_CHARACTER_COUNT} compatibility "
            f"characters, got {len(compatibility_order)}"
        )
    # 兼容打法救援：13/14 位兼容码优先取 14（首个四码的 1+4 位），再取 13。
    auxiliary_records = load_auxiliary_tsv(auxiliary_path)
    compatibility_auxiliary_codes = {
        char: entry.compat_codes()
        for char, entry in auxiliary_records.items()
        if entry.compat_codes()
    }
    zrm_fixed_codes = load_fixed_char_code_overrides(
        FIXED_CHAR_CODE_OVERRIDES_PATH,
        "zrm",
    )
    flypy_fixed_codes = load_fixed_char_code_overrides(
        FIXED_CHAR_CODE_OVERRIDES_PATH,
        "flypy",
    )
    compatibility_targets: list[str] = []
    zrm_secondary_codes = load_secondary_short_codes(SECONDARY_SHORT_CODES_PATH)
    flypy_secondary_codes = [
        (text, flypyify1(unzrmify1(code[:2])) + code[2:])
        for text, code in zrm_secondary_codes
    ]
    tiger_order, zrm_rows, zrm_legacy_rows, zrm_full_rows, weights = (
        build_full_character_allocation(
            tiger_path,
            chars_path,
            auxiliary_path,
            legacy_path=PARENT_TABLES[0][2],
            shortcut_readings=modern_readings,
            double_pinyin="zrm",
            fixed_codes=zrm_fixed_codes,
            secondary_codes=zrm_secondary_codes,
            compatibility_auxiliary_codes=compatibility_auxiliary_codes,
            compatibility_order=compatibility_order,
            compatibility_targets_out=compatibility_targets,
        )
    )
    flypy_order, flypy_rows, flypy_legacy_rows, flypy_full_rows, flypy_weights = (
        build_full_character_allocation(
            tiger_path,
            chars_path,
            auxiliary_path,
            legacy_path=PARENT_TABLES[0][2],
            shortcut_readings=modern_readings,
            double_pinyin="flypy",
            fixed_codes=flypy_fixed_codes,
            secondary_codes=flypy_secondary_codes,
        )
    )
    if flypy_order != tiger_order or flypy_weights != weights:
        raise ValueError("double-pinyin allocations use inconsistent character data")

    zrm_dictionary_rows = [
        (row.text, row.code, "0" if row.source == "original" else f"{row.weight:g}")
        for row in zrm_rows
    ]
    flypy_dictionary_rows = [
        (row.text, row.code, "0" if row.source == "original" else f"{row.weight:g}")
        for row in flypy_rows
    ]
    zrm_legacy_dictionary_rows = [
        (row.text, row.code, "0" if row.source == "original" else f"{row.weight:g}")
        for row in zrm_legacy_rows
    ]
    flypy_legacy_dictionary_rows = [
        (row.text, row.code, "0" if row.source == "original" else f"{row.weight:g}")
        for row in flypy_legacy_rows
    ]
    for parent, legacy_parent, table_name, parent_text in parent_variants:
        if table_name != "mohu_zrm_tiger_fixed":
            raise ValueError(f"unsupported fixed parent table: {table_name}")
        outputs.append(
            (
                parent,
                render_parent_with_characters(parent_text, zrm_dictionary_rows),
            )
        )
        legacy_base = render_parent_variant(
            parent_text,
            legacy_parent.stem.removesuffix(".dict"),
            table_name,
            f"{table_name}_legacy",
        )
        outputs.append(
            (
                legacy_parent,
                render_parent_with_characters(
                    legacy_base, zrm_legacy_dictionary_rows
                ),
            )
        )
    outputs.extend(
        (
            (
                ROOT / "mohu_zrm_tiger_fixed.dict.yaml",
                render_table("mohu_zrm_tiger_fixed", zrm_dictionary_rows),
            ),
            (
                ROOT / "mohu_flypy_tiger_fixed.dict.yaml",
                render_table("mohu_flypy_tiger_fixed", flypy_dictionary_rows),
            ),
            (
                ROOT / "mohu_zrm_tiger_fixed_legacy.dict.yaml",
                render_table(
                    "mohu_zrm_tiger_fixed_legacy", zrm_legacy_dictionary_rows
                ),
            ),
            (
                ROOT / "mohu_flypy_tiger_fixed_legacy.dict.yaml",
                render_table(
                    "mohu_flypy_tiger_fixed_legacy", flypy_legacy_dictionary_rows
                ),
            ),
            (TIGER_RANK_PATH, render_tiger_rank(tiger_order)),
            (
                COMPATIBILITY_TARGETS_PATH,
                render_compatibility_targets(compatibility_targets),
            ),
        )
    )
    research_outputs = (
        (
            CODE_LENGTH_REPORT,
            render_code_length_report(
                tiger_order,
                zrm_rows,
                zrm_full_rows,
                weights,
            ),
        ),
        (
            FLYPY_CODE_LENGTH_REPORT,
            render_code_length_report(
                flypy_order,
                flypy_rows,
                flypy_full_rows,
                flypy_weights,
            ),
        ),
        (
            SIMPLIFIED_READING_AUDIT_PATH,
            render_simplified_reading_audit(
                tiger_order,
                load_reading_evidence(chars_path),
                modern_readings,
                zrm_rows,
                load_auxiliary_tsv(auxiliary_path),
            ),
        ),
    )
    if RESEARCH_OUTPUT_DIR.is_dir():
        outputs.extend(research_outputs)

    return 0 if all(write_or_check(path, text, args.check) for path, text in outputs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
