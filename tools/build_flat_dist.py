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
MACOS_ENGINE = "libtigerengine.dylib"
# libtigerengine.dylib 以 @loader_path 解析这些依赖，必须和它放在同一个
# runtime/ 目录里（见 tiger_sentence_native/README.md「魔虎语义」一节）。
MACOS_ENGINE_DEPENDENCIES = ("libonnxruntime.1.dylib",)


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


def copy_macos_engine_dependencies(destination: Path) -> None:
    """把 native 引擎的 macOS 动态库依赖一并放进 runtime/。

    `libtigerengine.dylib` 用 `-Wl,-rpath,@loader_path` 解析
    `libonnxruntime.1.dylib`（魔虎语义的进程内 ONNX 推理）。漏拷它时 dlopen
    直接失败、引擎静默 fail-open，表现为「模型已安装但首选仍走 smart 词典」；
    任何以 `source_dir` 指向 dist-* 的 mira 运行也会稳定复现。缺文件时显式报错，
    而不是发出一个装不上的包。
    """
    for name in MACOS_ENGINE_DEPENDENCIES:
        source = ROOT / "tiger_sentence_native" / name
        if not source.is_file():
            raise ValueError(
                f"macOS runtime is missing {name}; {MACOS_ENGINE} resolves it via "
                f"@loader_path (see tiger_sentence_native/README.md)"
            )
        shutil.copy2(source, destination / source.name)


def copy_semantic_model(destination: Path) -> None:
    """语义模型一律不进默认包（体积考虑，全走按需下载）。

    主路径：本机 Qwen HTTP 重排（tiger/semantic_http_url），模型从
    release `semantic-models-v1` 下载（见 docs/semantic-rerank.md）。
    轻量回退：进程内 C2 ONNX 同在该 release（mohu-semantic-c2-onnx.zip），
    解压到用户目录 mohu_semantic/ 即生效。包内两者皆无时「魔虎语义开」
    自动关闭，不影响其他功能。
    """
    print("note: semantic models excluded from package "
          "(optional download from release semantic-models-v1; "
          "see docs/semantic-rerank.md)")
    return


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
        "Place `mohu-sentence-ngram-v5.bin` in this directory.\n"
        "The runtime uses this fixed model filename; package preparation checks the asset separately.\n"
        "Download the model from the GitHub Release asset with the same filename.\n",
        encoding="utf-8",
    )

    runtime_source = ROOT / "tiger_sentence_native" / MACOS_ENGINE
    if runtime_source.is_file():
        runtime_dir = destination / "mohu" / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(runtime_source, runtime_dir / runtime_source.name)
        copy_macos_engine_dependencies(runtime_dir)
    if windows_runtime is not None:
        copy_windows_runtime(windows_runtime, destination / "mohu" / "runtime")
    copy_semantic_model(destination)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a flat Rime Mohu package")
    parser.add_argument("scheme", choices=sorted(SCHEMES))
    parser.add_argument("destination", type=Path)
    parser.add_argument("--windows-runtime", type=Path)
    args = parser.parse_args()
    build_flat(args.scheme, args.destination, args.windows_runtime)


if __name__ == "__main__":
    main()
