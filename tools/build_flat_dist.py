#!/usr/bin/env python3

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from build_split_dist import ROOT, SCHEMES, build_distribution

NATIVE_LUA = (
    "mohu_runtime.lua",
    "mohu_sentence.lua",
    "mohu_tiger_sentence.lua",
)


def build_flat(scheme: str, destination: Path) -> None:
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a flat Rime Mohu package")
    parser.add_argument("scheme", choices=sorted(SCHEMES))
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    build_flat(args.scheme, args.destination)


if __name__ == "__main__":
    main()
