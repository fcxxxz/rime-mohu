#!/usr/bin/env python3
"""Run the isolated librime candidate dump for every model and input mode."""

from __future__ import annotations

import argparse
import binascii
import hashlib
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_PROBE = HERE / "probes" / "rime_candidate_dump_squirrel"
DEFAULT_SHARED = Path("/Library/Input Methods/Squirrel.app/Contents/SharedSupport")
DEFAULT_PLUGINS = Path("/Library/Input Methods/Squirrel.app/Contents/Frameworks/rime-plugins")
MODE_NAMES = ("pure", "sparse", "word1", "char1")
MIN_CANDIDATES = 20
RIME_PROBE_SHA256 = "878055e7509f790c6bea8ca6673bc0bce752919bc5ba437092bee18ad211b639"
RIME_LIBRIME_SHA256 = "abb06aa5b3f53de375bc401512b49a7a31b7ed5ee62b2ef7a438512abee5958f"
RIME_LUA_SHA256 = "a0862901b4d36d35aba7012f05c132dd087890cca564609c5d1ea3ba9de7c12b"
RIME_OCTAGRAM_SHA256 = "70f587ca908e1b857f4180dc50584b8843ec0852dbc2013248badc5fb0571525"
MODEL_SCHEMAS = {
    "bgw": "mohu_zrm_sentence",
    "wx": "mohu_zrm_sentence_wx",
}
STAGING_MARKER = ".mohu-lm-staging-v1"
STAGING_MARKER_CONTENT = "mohu-lm-sentence-benchmark-v1\n"


def verify_file_hash(path: Path, expected: str, *, label: str = "file") -> str:
    if not path.is_file():
        raise FileNotFoundError(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != expected:
        raise ValueError(f"{label} SHA-256 mismatch: expected {expected}, got {actual}")
    return actual


def file_metadata(path: Path, expected: str | None, *, label: str) -> dict[str, object]:
    if not path.is_file():
        raise FileNotFoundError(path)
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if expected is not None and actual != expected:
        raise ValueError(f"{label} SHA-256 mismatch: expected {expected}, got {actual}")
    return {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": actual,
        "pinned": expected is not None,
        "expected_sha256": expected,
    }


def validate_output_root(path: Path) -> Path:
    lexical = path.expanduser().absolute()
    allowed_system_aliases = {
        Path("/tmp"),
        Path("/private/tmp"),
        Path("/var"),
        Path("/var/tmp"),
    }
    current = lexical
    while True:
        if current.is_symlink() and current not in allowed_system_aliases:
            raise ValueError(f"refusing unsafe output root: symlinked ancestor {current}")
        if current == current.parent:
            break
        current = current.parent
    root = lexical.resolve()
    live_rime = (Path.home() / "Library" / "Rime").resolve()
    forbidden = {
        Path("/").resolve(),
        Path.home().resolve(),
        Path("/tmp").resolve(),
        Path("/private/tmp").resolve(),
        Path("/var/tmp").resolve(),
        Path(__file__).resolve().parents[2],
    }
    if lexical.is_symlink() or root in forbidden or root == live_rime or live_rime in root.parents:
        raise ValueError(f"refusing unsafe output root: {root}")
    if root.exists() and not root.is_dir():
        raise ValueError(f"output root is not a directory: {root}")
    return root


def validate_output_file(path: Path, root: Path) -> Path:
    """Reject output targets that could follow a pre-existing symlink."""

    lexical = path.expanduser().absolute()
    if lexical.is_symlink():
        raise ValueError(f"refusing symlinked output file: {lexical}")
    if lexical.resolve().parent != root:
        raise ValueError(f"refusing unsafe output file: {lexical}")
    if lexical.exists() and not lexical.is_file():
        raise ValueError(f"output target is not a file: {lexical}")
    return lexical


def validate_staging_data_dir(path: Path) -> Path:
    """Accept only the owned ``<staging>/data`` tree produced by prepare_staging."""

    lexical = path.expanduser().absolute()
    allowed_system_aliases = {
        Path("/tmp"),
        Path("/private/tmp"),
        Path("/var"),
        Path("/var/tmp"),
    }
    current = lexical
    while True:
        if current.is_symlink() and current not in allowed_system_aliases:
            raise ValueError(f"refusing symlinked Rime staging path: {current}")
        if current == current.parent:
            break
        current = current.parent
    root = lexical.resolve()
    live_dir = (Path.home() / "Library" / "Rime").resolve()
    if root == live_dir or live_dir in root.parents:
        raise ValueError(f"refusing to use live Rime user directory: {root}")
    if root.name != "data":
        raise ValueError(f"Rime data directory must be named data: {root}")
    marker = root.parent / STAGING_MARKER
    if marker.is_symlink() or not marker.is_file() or marker.read_text(encoding="utf-8") != STAGING_MARKER_CONTENT:
        raise ValueError(f"refusing unmarked Rime staging data: {root}")
    if not root.is_dir():
        raise ValueError(f"Rime staging data is not a directory: {root}")
    return root


def validate_probe_dependencies(output: str) -> None:
    """Require a probe linked against Squirrel's rpath librime on macOS."""

    if sys.platform != "darwin":
        return
    lines = output.splitlines()
    librime_lines = [line.strip() for line in lines if "librime" in line]
    if any("/opt/homebrew/" in line or "/usr/local/" in line for line in librime_lines):
        raise ValueError("Rime probe links a non-Squirrel Homebrew/local librime")
    if not any("@rpath/librime.1.dylib" in line for line in librime_lines):
        raise ValueError("Rime probe is not linked against Squirrel @rpath/librime.1.dylib")


def validate_probe_abi(probe: Path) -> None:
    if sys.platform != "darwin":
        return
    try:
        completed = subprocess.run(
            ["otool", "-L", str(probe)],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("otool is required to verify the Squirrel Rime probe ABI") from exc
    if completed.returncode != 0:
        raise RuntimeError(f"failed to inspect Rime probe dependencies: {probe}")
    validate_probe_dependencies(completed.stdout)


def _input_raws(path: Path) -> set[str]:
    raws: set[str] = set()
    ids: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != 2 or not fields[0] or not fields[1]:
            raise ValueError(f"invalid input row at {path}:{line_number}")
        case_id, raw = fields
        if case_id in ids:
            raise ValueError(f"duplicate input id at {path}:{line_number}")
        if raw in raws:
            raise ValueError(f"duplicate input raw at {path}:{line_number}")
        ids.add(case_id)
        raws.add(raw)
    return raws


def _raw_digest(raws: set[str]) -> str:
    payload = "\n".join(sorted(raws)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_candidate_limit(value: int) -> int:
    if value < MIN_CANDIDATES:
        raise ValueError("max_candidates must be at least 20 for Top-20 metrics")
    return value


def _validate_rime_output(path: Path, expected_raws: set[str]) -> set[str]:
    candidate_counts: Counter[str] = Counter()
    candidate_texts: dict[str, set[str]] = {}
    end_counts: dict[str, int] = {}
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            fields = line.rstrip("\n").split("\t")
            kind = fields[0]
            if kind == "C":
                if len(fields) != 5:
                    raise ValueError(
                        f"invalid candidate row at {path}:{line_number}: exactly 5 fields required"
                    )
                raw = fields[1]
                if raw not in expected_raws:
                    raise ValueError(f"unexpected output raw at {path}:{line_number}")
                if raw in end_counts:
                    raise ValueError(f"candidate row after end record at {path}:{line_number}")
                try:
                    rank = int(fields[2])
                except ValueError as exc:
                    raise ValueError(f"invalid candidate rank at {path}:{line_number}") from exc
                if rank != candidate_counts[raw] + 1:
                    raise ValueError(f"candidate rank mismatch at {path}:{line_number}")
                try:
                    text = binascii.unhexlify(fields[3]).decode("utf-8")
                    binascii.unhexlify(fields[4])
                except (binascii.Error, ValueError) as exc:
                    raise ValueError(f"invalid candidate encoding at {path}:{line_number}") from exc
                if not text:
                    raise ValueError(f"invalid candidate text at {path}:{line_number}")
                seen_texts = candidate_texts.setdefault(raw, set())
                if text in seen_texts:
                    raise ValueError(f"duplicate candidate text at {path}:{line_number}")
                seen_texts.add(text)
                candidate_counts[raw] += 1
            elif kind == "E":
                if len(fields) != 5:
                    raise ValueError(
                        f"invalid end row at {path}:{line_number}: exactly 5 fields required"
                    )
                raw = fields[1]
                if raw in end_counts:
                    raise ValueError(f"duplicate end raw at {path}:{line_number}")
                try:
                    end_counts[raw] = int(fields[2])
                except ValueError as exc:
                    raise ValueError(f"invalid end count at {path}:{line_number}") from exc
                if end_counts[raw] < 0:
                    raise ValueError(f"invalid end count at {path}:{line_number}")
                if fields[3] not in {"0", "1"}:
                    raise ValueError(f"invalid truncation flag at {path}:{line_number}")
                try:
                    elapsed_us = int(fields[4])
                except ValueError as exc:
                    raise ValueError(f"invalid end latency at {path}:{line_number}") from exc
                if elapsed_us < 0:
                    raise ValueError(f"end latency must be non-negative at {path}:{line_number}")
            else:
                raise ValueError(f"unknown output row at {path}:{line_number}")
    if set(end_counts) != expected_raws:
        raise ValueError(
            f"output raw set mismatch for {path}: "
            f"expected {len(expected_raws)}, got {len(end_counts)}"
        )
    for raw, declared in end_counts.items():
        actual = candidate_counts[raw]
        if actual != declared:
            raise ValueError(
                f"candidate count mismatch for {path} raw {raw}: "
                f"declared={declared}, parsed={actual}"
            )
    return set(end_counts)


def run_rime(
    *,
    data_dir: Path,
    inputs_dir: Path,
    results_dir: Path,
    probe: Path = DEFAULT_PROBE,
    shared_dir: Path = DEFAULT_SHARED,
    plugins_dir: Path = DEFAULT_PLUGINS,
    models: tuple[str, ...] = ("bgw", "wx"),
    modes: tuple[str, ...] = MODE_NAMES,
    max_candidates: int = 20,
    expected_probe_sha256: str | None = None,
    expected_librime_sha256: str | None = RIME_LIBRIME_SHA256,
    expected_lua_sha256: str | None = RIME_LUA_SHA256,
    expected_octagram_sha256: str | None = RIME_OCTAGRAM_SHA256,
) -> dict[str, object]:
    if not modes or any(not mode for mode in modes):
        raise ValueError("at least one mode is required")
    if len(set(modes)) != len(modes):
        raise ValueError("duplicate mode in modes")
    data_dir = validate_staging_data_dir(data_dir)
    max_candidates = validate_candidate_limit(max_candidates)
    results_dir = validate_output_root(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = validate_output_file(results_dir / "rime-run-manifest.json", results_dir)
    lua_plugin = plugins_dir / "librime-lua.dylib"
    octagram_plugin = plugins_dir / "librime-octagram.dylib"
    librime = plugins_dir.parent / "librime.1.dylib"
    if not probe.is_file() or not lua_plugin.is_file() or not octagram_plugin.is_file():
        raise FileNotFoundError("probe or Squirrel plugins are missing")
    file_metadata(probe, expected_probe_sha256, label="Rime probe")
    file_metadata(librime, expected_librime_sha256, label="Squirrel librime")
    file_metadata(lua_plugin, expected_lua_sha256, label="Squirrel Lua plugin")
    file_metadata(octagram_plugin, expected_octagram_sha256, label="Squirrel octagram plugin")
    validate_probe_abi(probe)
    validate_probe_abi(lua_plugin)
    validate_probe_abi(octagram_plugin)

    manifest: dict[str, object] = {
        "probe": str(probe),
        "data_dir": str(data_dir),
        "max_candidates": max_candidates,
        "allow_unpinned_runtime": all(
            expected is None
            for expected in (
                expected_librime_sha256,
                expected_lua_sha256,
                expected_octagram_sha256,
            )
        ),
        "probe_hash_pinned": expected_probe_sha256 is not None,
        "resources": {
            "probe": file_metadata(probe, expected_probe_sha256, label="Rime probe"),
            "librime": file_metadata(librime, expected_librime_sha256, label="Squirrel librime"),
            "lua": file_metadata(lua_plugin, expected_lua_sha256, label="Squirrel Lua plugin"),
            "octagram": file_metadata(octagram_plugin, expected_octagram_sha256, label="Squirrel octagram plugin"),
        },
        "latency_scope": "clear composition + candidate iteration + probe output write",
        "runs": [],
    }
    for model in models:
        if model not in MODEL_SCHEMAS:
            raise ValueError(f"unknown model {model!r}")
        schema = MODEL_SCHEMAS[model]
        schema_file = data_dir / f"{schema}.schema.yaml"
        if not schema_file.is_file():
            raise FileNotFoundError(schema_file)
        for mode in modes:
            if mode not in MODE_NAMES:
                raise ValueError(f"unknown mode {mode!r}")
            input_file = inputs_dir / f"{mode}.tsv"
            output_file = results_dir / f"rime_{model}_{mode}.tsv"
            log_file = results_dir / f"rime_{model}_{mode}.log"
            validate_output_file(output_file, results_dir)
            validate_output_file(log_file, results_dir)
            expected_raws = _input_raws(input_file)
            expected = len(expected_raws)
            command = [
                str(probe), str(lua_plugin), str(octagram_plugin), str(shared_dir),
                str(data_dir), str(schema_file), schema, str(input_file),
                str(output_file), str(max_candidates),
            ]
            started = time.monotonic()
            with log_file.open("w", encoding="utf-8") as log:
                completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False)
            elapsed = time.monotonic() - started
            if completed.returncode != 0:
                raise RuntimeError(f"Rime probe failed for {model}/{mode}; see {log_file}")
            if not output_file.is_file():
                raise RuntimeError(f"Rime probe output is missing for {model}/{mode}")
            try:
                actual_raws = _validate_rime_output(output_file, expected_raws)
            except ValueError as exc:
                raise RuntimeError(f"invalid Rime output for {model}/{mode}: {exc}") from exc
            actual = len(actual_raws)
            if actual != expected:
                raise RuntimeError(f"Rime probe row mismatch for {model}/{mode}: expected {expected}, got {actual}")
            manifest["runs"].append({
                "model": model,
                "schema": schema,
                "mode": mode,
                "input_rows": expected,
                "output_rows": actual,
                "input_raw_sha256": _raw_digest(expected_raws),
                "output_raw_sha256": _raw_digest(actual_raws),
                "output": str(output_file),
                "log": str(log_file),
                "elapsed_seconds": elapsed,
            })
            print(f"{model}/{mode}: {actual} rows in {elapsed:.1f}s")
    manifest_path = validate_output_file(results_dir / "rime-run-manifest.json", results_dir)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Run isolated Rime model evaluations")
    parser.add_argument("--data-dir", type=Path, default=HERE / "staging" / "data")
    parser.add_argument("--inputs-dir", type=Path, default=HERE / "staging" / "inputs")
    parser.add_argument("--results-dir", type=Path, default=HERE / "results")
    parser.add_argument("--probe", type=Path, default=DEFAULT_PROBE)
    parser.add_argument("--shared-dir", type=Path, default=DEFAULT_SHARED)
    parser.add_argument("--plugins-dir", type=Path, default=DEFAULT_PLUGINS)
    parser.add_argument("--models", default="bgw,wx")
    parser.add_argument("--modes", default=",".join(MODE_NAMES))
    parser.add_argument("--max-candidates", type=int, default=20)
    parser.add_argument("--probe-sha256")
    parser.add_argument("--librime-sha256")
    parser.add_argument("--lua-sha256")
    parser.add_argument("--octagram-sha256")
    parser.add_argument("--allow-unpinned-runtime", action="store_true")
    args = parser.parse_args()
    runtime_pinned = not args.allow_unpinned_runtime
    run_rime(
        data_dir=args.data_dir,
        inputs_dir=args.inputs_dir,
        results_dir=args.results_dir,
        probe=args.probe,
        shared_dir=args.shared_dir,
        plugins_dir=args.plugins_dir,
        models=tuple(args.models.split(",")),
        modes=tuple(args.modes.split(",")),
        max_candidates=args.max_candidates,
        expected_probe_sha256=args.probe_sha256,
        expected_librime_sha256=(args.librime_sha256 or RIME_LIBRIME_SHA256)
        if runtime_pinned
        else None,
        expected_lua_sha256=(args.lua_sha256 or RIME_LUA_SHA256)
        if runtime_pinned
        else None,
        expected_octagram_sha256=(args.octagram_sha256 or RIME_OCTAGRAM_SHA256)
        if runtime_pinned
        else None,
    )


if __name__ == "__main__":
    main()
