#!/usr/bin/env python3

import argparse
from pathlib import Path
import re
import shutil


ROOT = Path(__file__).resolve().parents[1]
SCHEMES = {"zrm", "flypy"}
SCHEMA_LINE = re.compile(r"^(\s*)- schema: (\S+)\s*$")

COMMON_ROOT_PATHS = (
    "README.md",
    "LICENSE",
    "etc",
    "mohu.yaml",
    "mohu_defs.yaml",
    "mohu_charset.dict.yaml",
    "mohu_charset.schema.yaml",
    "mohu_fixed.symbols.dict.yaml",
    "mohu_pinyin.dict.yaml",
    "mohu_pinyin.schema.yaml",
    "key_bindings.yaml",
    "punctuation.yaml",
    "symbols.yaml",
    "recipe.yaml",
    "recipes",
    "squirrel.yaml",
    "tiger.dict.yaml",
    "tiger.schema.yaml",
    "zh-hans-t-essay-bgw.gram",
    "Rime皮肤编辑器",
)


def copy_path(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(
            source,
            destination,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def recreate_destination(destination: Path) -> None:
    resolved = destination.resolve()
    root = ROOT.resolve()
    if resolved == root or resolved == Path(resolved.anchor) or resolved in root.parents:
        raise ValueError(f"unsafe destination: {destination}")

    if destination.is_symlink() or destination.is_file():
        destination.unlink()
    elif destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)


def copy_runtime_directories(destination: Path) -> None:
    copy_path(ROOT / "lua", destination / "lua")

    opencc_destination = destination / "opencc"
    opencc_destination.mkdir()
    for pattern in ("*.ocd2", "*.json", "mohu_TSPhrases.txt"):
        for source in sorted((ROOT / "opencc").glob(pattern)):
            copy_path(source, opencc_destination / source.name)


def write_filtered_default(scheme: str, destination: Path) -> None:
    other_scheme = "flypy" if scheme == "zrm" else "zrm"
    output_lines = []
    for line in (ROOT / "default.yaml").read_text(encoding="utf-8").splitlines(
        keepends=True
    ):
        match = SCHEMA_LINE.match(line.rstrip("\r\n"))
        if match and match.group(2).startswith(f"mohu_{other_scheme}"):
            continue
        output_lines.append(line)
    destination.write_text("".join(output_lines), encoding="utf-8")


def build_distribution(scheme: str, destination: Path) -> None:
    if scheme not in SCHEMES:
        raise ValueError(f"unsupported scheme: {scheme}")

    recreate_destination(destination)
    for relative in COMMON_ROOT_PATHS:
        copy_path(ROOT / relative, destination / relative)
    for source in sorted(ROOT.glob(f"mohu_{scheme}*")):
        copy_path(source, destination / source.name)
    copy_runtime_directories(destination)
    write_filtered_default(scheme, destination / "default.yaml")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a scheme-specific Mohu package")
    parser.add_argument("scheme", choices=sorted(SCHEMES))
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    build_distribution(args.scheme, args.destination)


if __name__ == "__main__":
    main()
