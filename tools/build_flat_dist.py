#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from build_split_dist import ROOT, SCHEMES, build_distribution

NATIVE_LUA = (
    "mohu_runtime.lua",
    "mohu_sentence.lua",
    "mohu_tiger_sentence.lua",
)
WINDOWS_ENTRY = "libtigerengine.dll"
WINDOWS_MANIFEST = "runtime-manifest.json"
WINDOWS_PRELOAD = "runtime-preload.txt"


def valid_runtime_name(name: object) -> bool:
    return (
        isinstance(name, str)
        and bool(name)
        and "/" not in name
        and "\\" not in name
        and Path(name).name == name
    )


def validate_windows_runtime(source: Path) -> list[Path]:
    if not source.is_dir():
        raise ValueError(f"Windows runtime directory does not exist: {source}")
    entry = source / WINDOWS_ENTRY
    if not entry.is_file() or entry.is_symlink():
        raise ValueError(f"Windows runtime is missing entry: {entry.name}")

    files = sorted(source.iterdir(), key=lambda path: path.name.casefold())
    invalid = [path for path in files if not path.is_file() or path.is_symlink()]
    if invalid:
        raise ValueError(f"Windows runtime contains non-regular entry: {invalid[0]}")

    manifest_path = source / WINDOWS_MANIFEST
    preload_path = source / WINDOWS_PRELOAD
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError(f"Windows runtime is missing {WINDOWS_MANIFEST}")
    if not preload_path.is_file() or preload_path.is_symlink():
        raise ValueError(f"Windows runtime is missing {WINDOWS_PRELOAD}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Windows runtime has invalid {WINDOWS_MANIFEST}: {error}") from error

    if not isinstance(manifest, dict):
        raise ValueError(f"Windows runtime has invalid {WINDOWS_MANIFEST}")
    records = manifest.get("files")
    if manifest.get("entry") != WINDOWS_ENTRY or not isinstance(records, list):
        raise ValueError(f"Windows runtime has invalid {WINDOWS_MANIFEST}")
    names = [record.get("name") for record in records if isinstance(record, dict)]
    if len(names) != len(records) or any(not valid_runtime_name(name) for name in names):
        raise ValueError(f"Windows runtime has invalid file names in {WINDOWS_MANIFEST}")
    normalized_names = {name.casefold() for name in names}
    if len(normalized_names) != len(names) or WINDOWS_ENTRY not in names:
        raise ValueError(f"Windows runtime has invalid file list in {WINDOWS_MANIFEST}")
    supplied_names = {
        path.name.casefold()
        for path in files
        if path.name not in {WINDOWS_MANIFEST, WINDOWS_PRELOAD}
    }
    supplied_file_count = len(files) - 2
    if len(supplied_names) != supplied_file_count or supplied_names != normalized_names:
        raise ValueError(f"Windows runtime files do not match {WINDOWS_MANIFEST}")

    preload = manifest.get("preload")
    preload_lines = [line.strip() for line in preload_path.read_text(encoding="utf-8").splitlines()]
    if (
        not isinstance(preload, list)
        or preload != preload_lines
        or any(not valid_runtime_name(name) or not name.lower().endswith(".dll") for name in preload)
        or any(name.casefold() == WINDOWS_ENTRY for name in preload)
        or {name.casefold() for name in preload} != normalized_names - {WINDOWS_ENTRY}
    ):
        raise ValueError(f"Windows runtime has invalid {WINDOWS_PRELOAD}")
    return files


def copy_windows_runtime(source: Path, destination: Path) -> None:
    files = validate_windows_runtime(source)

    destination.mkdir(parents=True, exist_ok=True)
    for source_file in files:
        shutil.copy2(source_file, destination / source_file.name)


def build_flat(
    scheme: str, destination: Path, windows_runtime: Path | None = None
) -> None:
    if scheme not in SCHEMES:
        raise ValueError(f"unsupported scheme: {scheme}")

    build_distribution(scheme, destination)

    lua_dir = destination / "lua"
    for filename in NATIVE_LUA:
        shutil.copy2(ROOT / "tiger_sentence_native" / filename, lua_dir / filename)

    lexicon = ROOT / "tiger_sentence_native" / "data" / scheme / f"mohu_{scheme}.lexicon.txt"
    data_dir = destination / "mohu" / "data" / scheme
    data_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(lexicon, data_dir / lexicon.name)

    clear_quarantine = destination / "解除隔离.command"
    shutil.copy2(ROOT / "解除隔离.command", clear_quarantine)
    clear_quarantine.chmod(0o755)

    model_dir = destination / "mohu" / "model"
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "README.md").write_text(
        "# Mohu sentence model\n\n"
        "Place `mohu-sentence-ngram-vN.bin` files in this directory.\n"
        "The runtime selects the highest numeric version, for example `v5.10` or `v6`.\n"
        "Download the model from the GitHub Release asset with the same filename.\n",
        encoding="utf-8",
    )

    runtime_source = ROOT / "tiger_sentence_native" / "libtigerengine.dylib"
    if runtime_source.is_file():
        runtime_dir = destination / "mohu" / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(runtime_source, runtime_dir / runtime_source.name)
    if windows_runtime is not None:
        copy_windows_runtime(windows_runtime, destination / "mohu" / "runtime")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a flat Rime Mohu package")
    parser.add_argument("scheme", choices=sorted(SCHEMES))
    parser.add_argument("destination", type=Path)
    parser.add_argument("--windows-runtime", type=Path)
    args = parser.parse_args()
    build_flat(args.scheme, args.destination, args.windows_runtime)


if __name__ == "__main__":
    main()
