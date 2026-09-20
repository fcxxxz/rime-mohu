#!/usr/bin/env python3
"""Synchronize the deploy-time fly-key TSV into Rime algebra definitions."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import fly_keys

ROOT = Path(__file__).resolve().parents[1]
DEFS = ROOT / "mohu_defs.yaml"
MOHU = ROOT / "mohu.yaml"


def render_defs_section(name: str, pairs: dict[str, str]) -> str:
    lines = [f"{name}:\n", ""]
    for source, target in pairs.items():
        lines.extend(
            [
                f"  {source}_{target}:\n",
                "    __append:\n",
                f"      - derive/{source};/{target};/\n",
            ]
        )
    return "".join(lines)


def render_mohu_section(name: str, defs_name: str, pairs: dict[str, str]) -> str:
    lines = [f"  {name}:\n", "    __append:\n", "      __patch:\n"]
    for source, target in pairs.items():
        lines.append(f"        - mohu_defs:/{defs_name}/{source}_{target}\n")
    return "".join(lines)


def replace_section(text: str, pattern: str, replacement: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE | re.DOTALL)
    if count != 1:
        raise ValueError(f"could not locate configuration section: {pattern}")
    return updated


def render_defs(text: str, pairs: dict[str, dict[str, str]]) -> str:
    for name, scheme in (("fly", "zrm"), ("fly_flypy", "flypy")):
        replacement = render_defs_section(name, pairs[scheme])
        pattern = rf"^{re.escape(name)}:\n.*?(?=^[A-Za-z_][A-Za-z0-9_]*:\n|\Z)"
        text = replace_section(text, pattern, replacement)
    return text


def render_mohu(text: str, pairs: dict[str, dict[str, str]]) -> str:
    for name, defs_name, scheme in (
        ("fly_zrm", "fly", "zrm"),
        ("fly_flypy", "fly_flypy", "flypy"),
    ):
        replacement = render_mohu_section(name, defs_name, pairs[scheme])
        pattern = rf"^  {re.escape(name)}:\n.*?(?=^  [A-Za-z_][A-Za-z0-9_]*:\n|\Z)"
        text = replace_section(text, pattern, replacement)
    return text


def build_outputs() -> dict[Path, str]:
    pairs = fly_keys.load_pairs()
    return {
        DEFS: render_defs(DEFS.read_text(encoding="utf-8"), pairs),
        MOHU: render_mohu(MOHU.read_text(encoding="utf-8"), pairs),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write synchronized files")
    parser.add_argument("--check", action="store_true", help="fail when files are out of date")
    args = parser.parse_args()
    changed = False
    for path, expected in build_outputs().items():
        current = path.read_text(encoding="utf-8")
        if current == expected:
            print(f"== {path.name}: consistent")
            continue
        changed = True
        print(f"== {path.name}: out of date" + (" (writing)" if args.apply else ""))
        if args.apply:
            path.write_text(expected, encoding="utf-8")
    if args.check and changed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
