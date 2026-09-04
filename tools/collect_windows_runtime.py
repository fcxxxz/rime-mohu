#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

IMPORT_LINE = re.compile(r"^\s*DLL Name:\s*(\S+)\s*$", re.MULTILINE)
SYSTEM_DLLS = frozenset(
    {
        "advapi32.dll",
        "bcrypt.dll",
        "combase.dll",
        "crypt32.dll",
        "gdi32.dll",
        "imm32.dll",
        "kernel32.dll",
        "kernelbase.dll",
        "msvcrt.dll",
        "ntdll.dll",
        "ole32.dll",
        "oleaut32.dll",
        "rpcrt4.dll",
        "secur32.dll",
        "shell32.dll",
        "shlwapi.dll",
        "user32.dll",
        "ucrtbase.dll",
        "version.dll",
        "winhttp.dll",
        "winmm.dll",
        "ws2_32.dll",
    }
)
HOST_DLLS = frozenset({"rime.dll"})
SYSTEM_DLL_PREFIXES = ("api-ms-win-", "ext-ms-win-")


class RuntimeClosureError(RuntimeError):
    """A package-owned Windows dependency could not be resolved."""


@dataclass(frozen=True)
class Closure:
    files: tuple[Path, ...]
    imports: tuple[tuple[str, str], ...]
    entry: Path


def parse_imports(output: str) -> list[str]:
    return IMPORT_LINE.findall(output)


def is_host_dependency(name: str) -> bool:
    normalized = name.casefold()
    return (
        normalized in SYSTEM_DLLS
        or normalized in HOST_DLLS
        or normalized.startswith(SYSTEM_DLL_PREFIXES)
    )


def default_import_reader(objdump: str) -> Callable[[Path], list[str]]:
    def read_imports(path: Path) -> list[str]:
        process = subprocess.run(
            [objdump, "-p", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if process.returncode:
            raise RuntimeClosureError(
                f"cannot inspect {path}: {process.stderr.strip() or process.stdout.strip()}"
            )
        return parse_imports(process.stdout)

    return read_imports


def index_search_dirs(search_dirs: Iterable[Path]) -> dict[str, Path]:
    indexed: dict[str, Path] = {}
    for directory in search_dirs:
        if not directory.is_dir():
            raise RuntimeClosureError(f"runtime search directory does not exist: {directory}")
        for candidate in sorted(directory.iterdir(), key=lambda path: path.name.casefold()):
            if candidate.is_file():
                indexed.setdefault(candidate.name.casefold(), candidate)
    return indexed


def dependency_chain(
    predecessors: dict[str, str | None], missing: str
) -> str:
    names = [missing]
    current = missing
    while (parent := predecessors.get(current)) is not None:
        names.append(parent)
        current = parent
    return " -> ".join(reversed(names))


def dependency_preload_order(
    entry: Path, imports: set[tuple[str, str]]
) -> tuple[str, ...]:
    dependencies: dict[str, list[str]] = {}
    names: dict[str, str] = {entry.name.casefold(): entry.name}
    for importer, imported in imports:
        importer_key = importer.casefold()
        imported_key = imported.casefold()
        names.setdefault(importer_key, importer)
        names.setdefault(imported_key, imported)
        dependencies.setdefault(importer_key, []).append(imported_key)

    visiting: set[str] = set()
    visited: set[str] = set()
    ordered: list[str] = []

    def visit(name: str) -> None:
        if name in visited or name in visiting:
            return
        visiting.add(name)
        for dependency in sorted(dependencies.get(name, [])):
            visit(dependency)
        visiting.remove(name)
        visited.add(name)
        ordered.append(names[name])

    visit(entry.name.casefold())
    return tuple(name for name in ordered if name.casefold() != entry.name.casefold())


def collect_runtime(
    entry: Path,
    search_dirs: list[Path],
    output: Path,
    import_reader: Callable[[Path], list[str]] | None = None,
) -> Closure:
    entry = entry.resolve()
    if not entry.is_file():
        raise RuntimeClosureError(f"runtime entry does not exist: {entry}")
    if output.exists():
        raise RuntimeClosureError(f"runtime output already exists: {output}")

    reader = import_reader or default_import_reader("objdump")
    available = index_search_dirs(search_dirs)
    available.setdefault(entry.name.casefold(), entry)
    entry_name = entry.name.casefold()
    discovered = {entry_name: entry}
    predecessors: dict[str, str | None] = {entry_name: None}
    imports: set[tuple[str, str]] = set()
    pending = deque([entry_name])

    while pending:
        name = pending.popleft()
        source = discovered[name]
        for imported in reader(source):
            target = imported.casefold()
            if is_host_dependency(target):
                continue
            candidate = available.get(target)
            if candidate is None:
                predecessors.setdefault(target, name)
                raise RuntimeClosureError(
                    "unresolved Windows runtime dependency: "
                    + dependency_chain(predecessors, target)
                )
            imports.add((source.name, candidate.name))
            if target not in discovered:
                discovered[target] = candidate
                predecessors[target] = name
                pending.append(target)

    files = tuple(sorted(discovered.values(), key=lambda path: path.name.casefold()))
    ordered_imports = tuple(sorted(imports, key=lambda edge: (edge[0].casefold(), edge[1].casefold())))
    preload = dependency_preload_order(entry, imports)
    output.mkdir()
    for source in files:
        shutil.copy2(source, output / source.name)
    manifest = {
        "entry": entry.name,
        "files": [
            {"name": source.name, "source": str(source)} for source in files
        ],
        "imports": [
            {"from": importer, "to": imported} for importer, imported in ordered_imports
        ],
        "preload": list(preload),
    }
    (output / "runtime-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output / "runtime-preload.txt").write_text("\n".join(preload) + ("\n" if preload else ""), encoding="utf-8")
    return Closure(files=files, imports=ordered_imports, entry=entry)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect the non-host dependency closure for a Windows DLL"
    )
    parser.add_argument("--entry", type=Path, required=True)
    parser.add_argument("--search-dir", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--objdump", default="objdump")
    args = parser.parse_args()
    closure = collect_runtime(
        args.entry,
        args.search_dir,
        args.output,
        import_reader=default_import_reader(args.objdump),
    )
    print(f"collected {len(closure.files)} Windows runtime file(s) in {args.output}")


if __name__ == "__main__":
    main()
