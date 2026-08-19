from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping, Sequence

import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTIVE_WORD_TABLES = (
    "mohu_zrm.base.dict.yaml",
    "mohu_zrm.words.dict.yaml",
    "mohu_zrm.tencent.dict.yaml",
    "mohu_zrm.computer.dict.yaml",
    "mohu_zrm.moe.dict.yaml",
)
FIXED_TABLE = "mohu_zrm_fixed.dict.yaml"
PYTHON_SOURCE = "tools/data/pinyin_simp.txt"
PINYIN_HINTS = "opencc/mohu_pinyinhint.txt"
MAX_OPERATIONS = 200
MAX_PAYLOAD_BYTES = 60 * 1024
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
BATCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


class BatchValidationError(ValueError):
    pass


class BatchConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class BatchOperation:
    kind: str
    word: str = ""
    code: str = ""
    other_word: str = ""
    expected: int | None = None
    desired: int | None = None
    expected_order: tuple[str, ...] = ()
    desired_order: tuple[str, ...] = ()

    @property
    def expected_pronunciations(self) -> tuple[str, ...]:
        return self.expected_order

    @property
    def desired_pronunciations(self) -> tuple[str, ...]:
        return self.desired_order


@dataclass(frozen=True)
class DictionaryBatch:
    version: int
    batch_id: str
    base_sha: str
    operations: tuple[BatchOperation, ...]


@dataclass
class RimeRow:
    fields: list[str]
    line_index: int
    columns: tuple[str, ...]

    @property
    def text(self) -> str:
        return self.fields[self.columns.index("text")] if self.columns.index("text") < len(self.fields) else ""

    @text.setter
    def text(self, value: str) -> None:
        self._set("text", value)

    @property
    def code(self) -> str:
        index = self.columns.index("code") if "code" in self.columns else -1
        return self.fields[index] if index >= 0 and index < len(self.fields) else ""

    @code.setter
    def code(self, value: str) -> None:
        if "code" in self.columns:
            self._set("code", value)

    @property
    def weight(self) -> int:
        if "weight" not in self.columns:
            return 0
        index = self.columns.index("weight")
        if index >= len(self.fields) or not self.fields[index]:
            return 0
        try:
            value = Decimal(self.fields[index])
        except InvalidOperation as exc:
            raise BatchValidationError("dictionary contains a non-numeric weight") from exc
        if not value.is_finite() or value < 0 or value != value.to_integral_value():
            raise BatchValidationError("dictionary contains an invalid weight")
        return int(value)

    @weight.setter
    def weight(self, value: int) -> None:
        if "weight" in self.columns:
            self._set("weight", str(int(value)))

    def _set(self, column: str, value: str) -> None:
        index = self.columns.index(column)
        while len(self.fields) < len(self.columns):
            self.fields.append("")
        self.fields[index] = value


class RimeTable:
    def __init__(self, header: str, body_lines: list[str], columns: tuple[str, ...], rows: list[RimeRow]):
        self.header = header
        self.body_lines = body_lines
        self.columns = columns
        self.rows = rows
        self.removed_indices: set[int] = set()

    @classmethod
    def parse(cls, text: str) -> RimeTable:
        lines = text.splitlines(keepends=True)
        try:
            marker = next(index for index, line in enumerate(lines) if line.strip() == "...")
        except StopIteration as exc:
            raise BatchValidationError("dictionary has no body marker") from exc
        header_text = "".join(lines[: marker + 1])
        try:
            header = yaml.safe_load(header_text)
        except yaml.YAMLError as exc:
            raise BatchValidationError("dictionary header is malformed") from exc
        if not isinstance(header, dict):
            raise BatchValidationError("dictionary header is invalid")
        columns_raw = header.get("columns", ["text", "code", "weight"])
        if not isinstance(columns_raw, list) or not columns_raw or any(not isinstance(item, str) for item in columns_raw):
            raise BatchValidationError("dictionary columns are invalid")
        columns = tuple(columns_raw)
        if "text" not in columns or len(columns) != len(set(columns)):
            raise BatchValidationError("dictionary columns are invalid")
        body = lines[marker + 1 :]
        rows: list[RimeRow] = []
        for index, line in enumerate(body):
            stripped = line.rstrip("\r\n")
            if not stripped or stripped.lstrip().startswith("#"):
                continue
            rows.append(RimeRow(stripped.split("\t"), index, columns))
        return cls(header_text, body, columns, rows)

    @classmethod
    def from_path(cls, path: Path) -> RimeTable:
        try:
            return cls.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as exc:
            raise BatchValidationError(f"cannot read dictionary: {path.name}") from exc

    def rows_for(self, word: str) -> list[RimeRow]:
        return [row for row in self.rows if row.text == word]

    def rows_for_code(self, code: str) -> list[RimeRow]:
        return [row for row in self.rows if row.code == code]

    def remove_rows(self, rows: Sequence[RimeRow]) -> None:
        removed = {row.line_index for row in rows}
        self.removed_indices.update(removed)
        self.rows = [row for row in self.rows if row.line_index not in removed]

    def render(self) -> str:
        by_index = {row.line_index: row for row in self.rows}
        rendered: list[str] = []
        for index, line in enumerate(self.body_lines):
            if index in self.removed_indices:
                continue
            row = by_index.get(index)
            if row is None:
                rendered.append(line)
                continue
            ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
            rendered.append("\t".join(row.fields) + ending)
        return self.header + "".join(rendered)


class DictionaryIndex:
    def __init__(self, tables: Mapping[str, RimeTable]):
        self.tables = dict(tables)

    @classmethod
    def load(cls, root: Path) -> DictionaryIndex:
        root = Path(root).resolve()
        names = (FIXED_TABLE, *ACTIVE_WORD_TABLES)
        return cls({name: RimeTable.from_path(root / name) for name in names})

    def effective_weight(self, word: str) -> int | None:
        weights = [row.weight for name, table in self.tables.items() if name in ACTIVE_WORD_TABLES for row in table.rows_for(word)]
        return max(weights) if weights else None

    def fixed_order(self, code: str) -> tuple[str, ...]:
        return tuple(row.text for row in self.tables[FIXED_TABLE].rows_for_code(code))


def _control_free(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or any(unicodedata.category(char).startswith("C") for char in value):
        raise BatchValidationError(f"{label} contains a control character or is empty")
    if len(value) > 128:
        raise BatchValidationError(f"{label} is too long")
    return value


def _number(value: object, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BatchValidationError(f"{label} must be a non-negative integer or null")
    return value


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise BatchValidationError(f"{label} must be a non-empty list")
    return tuple(_control_free(item, label) for item in value)


def _optional_string_tuple(value: object, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise BatchValidationError(f"{label} must be a list")
    return tuple(_control_free(item, label) for item in value)


def load_batch(payload: Mapping[str, object]) -> DictionaryBatch:
    if not isinstance(payload, Mapping):
        raise BatchValidationError("batch must be an object")
    allowed = {"version", "batch_id", "base_sha", "operations"}
    unknown = set(payload) - allowed
    if unknown:
        raise BatchValidationError(f"unknown batch field: {sorted(unknown)[0]}")
    if payload.get("version") != 1:
        raise BatchValidationError("unsupported batch version")
    batch_id = str(payload.get("batch_id") or "")
    if BATCH_RE.fullmatch(batch_id) is None:
        raise BatchValidationError("invalid batch ID")
    base_sha = str(payload.get("base_sha") or "")
    if SHA_RE.fullmatch(base_sha) is None:
        raise BatchValidationError("invalid base SHA")
    raw_operations = payload.get("operations")
    if not isinstance(raw_operations, list) or not raw_operations or len(raw_operations) > MAX_OPERATIONS:
        raise BatchValidationError("operations must contain 1 to 200 items")
    operations: list[BatchOperation] = []
    kinds = {"fixed_add", "fixed_delete", "fixed_reorder", "word_add", "word_delete", "word_frequency", "pronunciation_set"}
    for raw in raw_operations:
        if not isinstance(raw, Mapping):
            raise BatchValidationError("operation must be an object")
        kind = str(raw.get("kind") or "")
        if kind not in kinds:
            raise BatchValidationError(f"unknown operation kind: {kind}")
        common_keys = {"kind", "word"}
        if kind.startswith("fixed"):
            allowed_keys = common_keys | {"code", "other_word", "expected_order", "desired_order"}
        elif kind == "pronunciation_set":
            allowed_keys = common_keys | {"expected", "desired"}
        else:
            allowed_keys = common_keys | {"expected", "desired"}
        unknown_operation_fields = set(raw) - allowed_keys
        if unknown_operation_fields:
            raise BatchValidationError(
                f"unknown operation field: {sorted(unknown_operation_fields)[0]}"
            )
        word = _control_free(raw.get("word"), "word")
        code = str(raw.get("code") or "")
        other = str(raw.get("other_word") or "")
        if other:
            other = _control_free(other, "other_word")
        if kind.startswith("fixed"):
            if not re.fullmatch(r"[a-z]{1,32}", code) or (kind == "fixed_reorder" and not other):
                raise BatchValidationError("fixed operation has invalid code or other word")
        if kind == "pronunciation_set":
            expected_values = _string_tuple(raw.get("expected"), "expected pronunciation")
            desired_values = _string_tuple(raw.get("desired"), "desired pronunciation")
            operations.append(BatchOperation(kind, word=word, expected_order=expected_values, desired_order=desired_values))
            continue
        expected = _number(raw.get("expected"), "expected")
        desired = _number(raw.get("desired"), "desired")
        expected_order = _optional_string_tuple(raw.get("expected_order"), "expected_order")
        desired_order = _optional_string_tuple(raw.get("desired_order"), "desired_order")
        operations.append(BatchOperation(kind, word, code, other, expected, desired, expected_order, desired_order))
    canonical = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(canonical) > MAX_PAYLOAD_BYTES:
        raise BatchValidationError("batch payload is too large")
    return DictionaryBatch(1, batch_id, base_sha, tuple(operations))


def _natural_code(pinyin: str) -> str:
    sys.path.insert(0, str(ROOT / "tools"))
    import zrmify

    try:
        return zrmify.zrmify(pinyin).split()
    except Exception as exc:
        raise BatchValidationError(f"unsupported pronunciation: {pinyin}") from exc


def _toneless(value: str) -> str:
    marks = {
        **dict.fromkeys("āáǎà", "a"), **dict.fromkeys("ēéěè", "e"),
        **dict.fromkeys("īíǐì", "i"), **dict.fromkeys("ōóǒò", "o"),
        **dict.fromkeys("ūúǔù", "u"), **dict.fromkeys("ǖǘǚǜ", "v"), "ü": "v",
    }
    return "".join(marks.get(char, char) for char in value)


def _replace_word_code(row: RimeRow, desired: str) -> None:
    old_tokens = row.code.split()
    new_tokens = _natural_code(" ".join(_toneless(part) for part in desired.split()))
    if len(old_tokens) != len(new_tokens):
        raise BatchConflict("pronunciation syllable count does not match dictionary code")
    merged: list[str] = []
    for old, new in zip(old_tokens, new_tokens, strict=True):
        suffix = old.split(";", 1)[1] if ";" in old else ""
        merged.append(f"{new};{suffix}" if suffix else new)
    row.code = " ".join(merged)


def _apply_pronunciation(
    root: Path,
    operation: BatchOperation,
    tables: dict[str, RimeTable],
    pending_files: dict[Path, str],
) -> None:
    word = operation.word
    expected = operation.expected_order
    desired = operation.desired_order
    desired_reading = desired[0]
    expected_bases = {_toneless(value) for value in expected}
    if len(word) == 1:
        source = root / PYTHON_SOURCE
        source_text = pending_files.get(source, source.read_text(encoding="utf-8"))
        lines = source_text.splitlines(keepends=True)
        matches = [line for line in lines if line.split("\t", 1)[0] == word]
        if not matches:
            raise BatchConflict("character pronunciation source is missing")
        current = {line.split("\t")[1] for line in matches if "\t" in line}
        if expected and not current.intersection(expected_bases):
            raise BatchConflict("upstream pronunciation changed")
        wanted = [_toneless(item.split()[0]) for item in desired]
        max_weight = max((int(line.rstrip("\r\n").split("\t")[2]) for line in matches if len(line.rstrip("\r\n").split("\t")) > 2 and line.rstrip("\r\n").split("\t")[2].isdigit()), default=0)
        replacement = [f"{word}\t{reading}\t{max_weight}\n" for reading in dict.fromkeys(wanted)]
        new_lines = [line for line in lines if line.split("\t", 1)[0] != word]
        insert_at = next((i for i, line in enumerate(new_lines) if line.split("\t", 1)[0] > word), len(new_lines))
        new_lines[insert_at:insert_at] = replacement
        pending_files[source] = "".join(new_lines)
        _update_pronunciation_hint(root, word, desired, pending_files)
        return
    matches: list[tuple[str, RimeRow]] = []
    for name in ACTIVE_WORD_TABLES:
        for row in tables[name].rows_for(word):
            matches.append((name, row))
    if not matches:
        raise BatchConflict("word pronunciation source is missing")
    current_codes = {" ".join(_toneless(_code_to_pinyin(token.split(";", 1)[0])) for token in row.code.split()) for _, row in matches}
    if expected and not current_codes.intersection(expected_bases):
        raise BatchConflict("upstream pronunciation changed")
    for index, (_, row) in enumerate(matches):
        _replace_word_code(row, desired[index] if index < len(desired) else desired_reading)
    _update_pronunciation_hint(root, word, desired, pending_files)


def _update_pronunciation_hint(
    root: Path,
    word: str,
    desired: Sequence[str],
    pending_files: dict[Path, str],
) -> None:
    hint_path = root / PINYIN_HINTS
    if not hint_path.exists() and hint_path not in pending_files:
        return
    hint_text = pending_files.get(hint_path, hint_path.read_text(encoding="utf-8"))
    lines = hint_text.splitlines(keepends=True)
    replacement = f"{word}\t" + "".join(f"〔{value.replace(' ', '')}〕" for value in desired) + "\n"
    found = False
    output: list[str] = []
    for line in lines:
        if line.split("\t", 1)[0] == word:
            if not found:
                output.append(replacement)
                found = True
        else:
            output.append(line)
    if not found:
        output.append(replacement)
    pending_files[hint_path] = "".join(output)


def _code_to_pinyin(code: str) -> str:
    # The repository's natural-code reverse helper is authoritative.
    sys.path.insert(0, str(ROOT / "tools"))
    import zrmify

    try:
        return zrmify.unzrmify1(code)
    except Exception:
        return code


def _apply_tables(root: Path, batch: DictionaryBatch) -> tuple[Path, ...]:
    tables = {name: RimeTable.from_path(root / name) for name in (FIXED_TABLE, *ACTIVE_WORD_TABLES)}
    index = DictionaryIndex(tables)
    for operation in batch.operations:
        if operation.kind == "word_add":
            if index.effective_weight(operation.word) is not None:
                raise BatchConflict("word already exists")
        elif operation.kind == "word_delete":
            if index.effective_weight(operation.word) is None:
                raise BatchConflict("word does not exist")
        elif operation.kind == "word_frequency":
            if index.effective_weight(operation.word) != operation.expected:
                raise BatchConflict("word frequency changed")
        elif operation.kind.startswith("fixed"):
            order = index.fixed_order(operation.code)
            if operation.expected_order and tuple(operation.expected_order) != order:
                raise BatchConflict("fixed order changed")
            if operation.kind == "fixed_add" and operation.word in order:
                raise BatchConflict("fixed word already exists")
            if operation.kind == "fixed_delete" and operation.word not in order:
                raise BatchConflict("fixed word does not exist")
            if operation.kind == "fixed_reorder" and tuple(operation.expected_order) != order:
                raise BatchConflict("fixed order changed")
        elif operation.kind == "pronunciation_set":
            # Validation and mutation are performed together after all simple operations.
            pass

    changed: set[str] = set()
    pending_files: dict[Path, str] = {}
    for operation in batch.operations:
        if operation.kind == "word_add":
            table = tables[ACTIVE_WORD_TABLES[1]]
            fields = [operation.word]
            for column in table.columns[1:]:
                fields.append("1" if column == "weight" else "")
            table.body_lines.append("\t".join(fields) + "\n")
            table.rows.append(RimeRow(fields, len(table.body_lines) - 1, table.columns))
            changed.add(ACTIVE_WORD_TABLES[1])
        elif operation.kind in {"word_delete", "word_frequency"}:
            for name in ACTIVE_WORD_TABLES:
                table = tables[name]
                if operation.kind == "word_delete":
                    table.remove_rows(table.rows_for(operation.word))
                else:
                    for row in table.rows_for(operation.word):
                        row.weight = operation.desired or 0
                if operation.kind == "word_delete" or table.rows_for(operation.word):
                    changed.add(name)
        elif operation.kind.startswith("fixed"):
            table = tables[FIXED_TABLE]
            if operation.kind == "fixed_add":
                code_rows = table.rows_for_code(operation.code)
                fields = [operation.word if column == "text" else operation.code if column == "code" else "" for column in table.columns]
                table.body_lines.append("\t".join(fields) + "\n")
                table.rows.append(RimeRow(fields, len(table.body_lines) - 1, table.columns))
            elif operation.kind == "fixed_delete":
                table.remove_rows(
                    [row for row in table.rows if row.text == operation.word and row.code == operation.code]
                )
            else:
                rows = table.rows_for_code(operation.code)
                positions = {row.text: list(row.fields) for row in rows}
                desired_order = list(operation.desired_order)
                for row, word in zip(rows, desired_order, strict=True):
                    row.fields = list(positions[word])
            changed.add(FIXED_TABLE)
        elif operation.kind == "pronunciation_set":
            _apply_pronunciation(root, operation, tables, pending_files)
            changed.update(ACTIVE_WORD_TABLES if len(operation.word) > 1 else {PYTHON_SOURCE, PINYIN_HINTS})

    for name in (FIXED_TABLE, *ACTIVE_WORD_TABLES):
        if name in changed:
            _atomic_write(root / name, tables[name].render())
    for path, content in pending_files.items():
        _atomic_write(path, content)
    return tuple(sorted(root / name for name in changed))


def apply_batch(root: Path, batch: DictionaryBatch) -> tuple[Path, ...]:
    root = Path(root).resolve()
    if not root.is_dir() or any(not (root / name).is_file() for name in (FIXED_TABLE, *ACTIVE_WORD_TABLES)):
        raise BatchValidationError("repository root is incomplete")
    return _apply_tables(root, batch)


def _atomic_write(path: Path, content: str) -> None:
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", required=True, type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--result", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.batch.read_text(encoding="utf-8"))
        batch = load_batch(payload)
        changed = apply_batch(args.root, batch)
    except BatchConflict as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (BatchValidationError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 3
    result = {"batch_id": batch.batch_id, "changed_files": [str(path.relative_to(args.root.resolve())) for path in changed]}
    if args.result:
        args.result.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    else:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
