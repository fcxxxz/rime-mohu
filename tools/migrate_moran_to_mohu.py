#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SCHEMA_MAP = {
    "moran_fixed": "mohu_zrm_fixed",
    "moran_sentence": "mohu_zrm_sentence",
    "moran_aux": "mohu_zrm_aux",
    "moran": "mohu_zrm",
}

ZRM_SCHEMA_GROUP = (
    "mohu_zrm",
)

FLYPY_SCHEMA_GROUP = (
    "mohu_flypy",
)

REMOVED_SCHEMA_IDS = (
    "mohu_zrm_fixed",
    "mohu_zrm_fixed_legacy",
    "mohu_zrm_sentence",
    "mohu_zrm_sentence_core",
    "mohu_zrm_aux",
    "mohu_zrm_core",
    "mohu_llm_zrm",
    "mohu_flypy_fixed",
    "mohu_flypy_fixed_legacy",
    "mohu_flypy_sentence",
    "mohu_flypy_sentence_core",
    "mohu_flypy_aux",
    "mohu_flypy_core",
    "mohu_llm_flypy",
)

FILE_MAP = {
    "moran.custom.yaml": "mohu_zrm.custom.yaml",
    "moran_fixed.custom.yaml": "mohu_zrm_fixed.custom.yaml",
    "moran_sentence.custom.yaml": "mohu_zrm_sentence.custom.yaml",
    "moran_aux.custom.yaml": "mohu_zrm_aux.custom.yaml",
}

DB_PREFIX_MAP = {
    "moran_fixed_tiger_prefix2": "mohu_zrm_fixed_tiger_prefix2",
    "moran_sentence_tiger_prefix2": "mohu_zrm_sentence_tiger_prefix2",
    "moran_aux_tiger_prefix2": "mohu_zrm_aux_tiger_prefix2",
    "moran_tiger_prefix2": "mohu_zrm_tiger_prefix2",
    "moran_custom_phrases": "mohu_zrm_custom_phrases",
    "moran_candidate_override": "mohu_zrm_candidate_override",
    "moran_pin": "mohu_zrm_pin",
}

IDENTIFIER_MAP = {
    "moran.extended": "mohu_zrm.extended",
    "moran.chars": "mohu_zrm.chars",
    "moran.base": "mohu_zrm.base",
    "moran.words": "mohu_zrm.words",
    "moran.tencent": "mohu_zrm.tencent",
    "moran.computer": "mohu_zrm.computer",
    "moran.moe": "mohu_zrm.moe",
}

UNKNOWN_PATTERN = re.compile(r"(?<![A-Za-z0-9])moran(?:[_./-][A-Za-z0-9_.-]+)?")


@dataclass
class MigrationPlan:
    text_edits: dict[Path, str] = field(default_factory=dict)
    renames: dict[Path, Path] = field(default_factory=dict)
    unknown_references: list[tuple[Path, str]] = field(default_factory=list)


def _replace_token(text: str, old: str, new: str) -> str:
    pattern = rf"(?<![A-Za-z0-9_]){re.escape(old)}(?![A-Za-z0-9_])"
    return re.sub(pattern, new, text)


def _drop_removed_schema_lines(text: str) -> str:
    # 字词、整句、辅筛及内部编译方案不再作为可选方案发布。
    removed = "|".join(re.escape(schema_id) for schema_id in REMOVED_SCHEMA_IDS)
    line = re.compile(
        r"^[ \t]*-[ \t]*(?:\{[ \t]*)?schema:[ \t]*"
        rf"(?:{removed})"
        r"(?:[ \t\r]*\})?[ \t\r]*\n",
        re.MULTILINE,
    )
    return line.sub("", text)


def _add_flypy_schema_group(text: str) -> str:
    schema_line = re.compile(
        r"^(?P<indent>\s*)-\s*(?P<braced>\{\s*)?schema:\s*(?P<schema>[A-Za-z0-9_]+)"
    )
    lines = text.splitlines(keepends=True)
    entries = []
    for index, line in enumerate(lines):
        match = schema_line.match(line)
        if match:
            entries.append((index, match))

    selected = {match.group("schema") for _, match in entries}
    if not set(ZRM_SCHEMA_GROUP).issubset(selected) or selected.intersection(
        FLYPY_SCHEMA_GROUP
    ):
        return text

    anchor_index, anchor_match = next(
        (index, match)
        for index, match in entries
        if match.group("schema") == "mohu_zrm"
    )
    indent = anchor_match.group("indent")
    newline = "\r\n" if lines[anchor_index].endswith("\r\n") else "\n"
    if anchor_match.group("braced"):
        additions = [
            f"{indent}- {{schema: {schema}}}{newline}" for schema in FLYPY_SCHEMA_GROUP
        ]
    else:
        additions = [f"{indent}- schema: {schema}{newline}" for schema in FLYPY_SCHEMA_GROUP]
    lines[anchor_index + 1 : anchor_index + 1] = additions
    return "".join(lines)


def migrate_text(text: str) -> tuple[str, list[str]]:
    migrated = text
    for old, new in sorted(DB_PREFIX_MAP.items(), key=lambda item: -len(item[0])):
        migrated = migrated.replace(old, new)
    for old, new in sorted(IDENTIFIER_MAP.items(), key=lambda item: -len(item[0])):
        migrated = migrated.replace(old, new)
    for old, new in sorted(SCHEMA_MAP.items(), key=lambda item: -len(item[0])):
        migrated = _replace_token(migrated, old, new)
    migrated = migrated.replace("moran/", "mohu/")
    migrated = _drop_removed_schema_lines(migrated)
    migrated = _add_flypy_schema_group(migrated)
    unknown = sorted(set(UNKNOWN_PATTERN.findall(migrated)))
    return migrated, unknown


def _renamed_entry(entry: Path) -> Path | None:
    if entry.name in FILE_MAP:
        return entry.with_name(FILE_MAP[entry.name])
    for old, new in sorted(DB_PREFIX_MAP.items(), key=lambda item: -len(item[0])):
        if entry.name == old or entry.name.startswith(old + "."):
            return entry.with_name(new + entry.name[len(old) :])
    return None


def plan_migration(root: Path) -> MigrationPlan:
    root = root.absolute()
    if not root.is_dir():
        raise ValueError(f"Rime user directory does not exist: {root}")

    plan = MigrationPlan()
    for entry in sorted(root.iterdir()):
        if entry.name.startswith("mohu-migration-backup-"):
            continue
        target = _renamed_entry(entry)
        if target is not None:
            plan.renames[entry] = target
        if not entry.is_file() or entry.suffix not in {".yaml", ".yml", ".txt"}:
            continue
        try:
            original = entry.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        migrated, unknown = migrate_text(original)
        if migrated != original:
            plan.text_edits[entry] = migrated
        plan.unknown_references.extend((entry, token) for token in unknown)
    return plan


def _affected_sources(plan: MigrationPlan) -> list[Path]:
    return sorted(set(plan.text_edits) | set(plan.renames), key=lambda path: path.name)


def apply_migration(
    root: Path,
    plan: MigrationPlan,
    *,
    timestamp: str | None = None,
) -> Path:
    if plan.unknown_references:
        details = ", ".join(f"{path.name}:{token}" for path, token in plan.unknown_references)
        raise ValueError(f"unknown legacy references: {details}")

    for source, target in plan.renames.items():
        if target.exists() and target != source:
            raise FileExistsError(f"migration target already exists: {target}")

    timestamp = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = root.absolute() / f"mohu-migration-backup-{timestamp}"
    if backup.exists():
        raise FileExistsError(f"backup already exists: {backup}")

    sources = _affected_sources(plan)
    backup.mkdir(parents=False)
    for source in sources:
        destination = backup / source.name
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)

    for source, content in plan.text_edits.items():
        target = plan.renames.get(source, source)
        temporary = target.with_name(target.name + ".mohu-migration.tmp")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, target)
        if target != source and source.exists():
            source.unlink()

    for source, target in plan.renames.items():
        if source in plan.text_edits:
            continue
        source.rename(target)

    return backup


def describe(plan: MigrationPlan) -> str:
    lines = []
    for source in sorted(plan.text_edits, key=lambda path: path.name):
        lines.append(f"edit   {source.name}")
    for source, target in sorted(plan.renames.items(), key=lambda item: item[0].name):
        lines.append(f"rename {source.name} -> {target.name}")
    for source, token in plan.unknown_references:
        lines.append(f"block  {source.name}: {token}")
    return "\n".join(lines) if lines else "No migration changes found."


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate Moran user data to Mohu natural-code schemas")
    parser.add_argument("rime_user_dir", type=Path)
    parser.add_argument("--apply", action="store_true", help="back up and apply the migration")
    args = parser.parse_args()

    plan = plan_migration(args.rime_user_dir)
    print(describe(plan))
    if plan.unknown_references:
        print("Migration blocked by unknown legacy references.")
        return 2
    if args.apply:
        backup = apply_migration(args.rime_user_dir, plan)
        print(f"Backup: {backup}")
        print("Migration complete. Redeploy Rime before using Mohu.")
    else:
        print("Dry run only. Re-run with --apply after quitting the input method.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
