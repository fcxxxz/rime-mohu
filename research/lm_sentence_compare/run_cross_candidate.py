#!/usr/bin/env python3
"""Build and run the isolated five-scheme cross-candidate benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from research.lm_sentence_compare.cross_candidate import (
    SCHEME_SPECS,
    SCHEMES,
    _validate_result_keys,
    build_report,
    case_modes,
    load_after,
    load_cross_cases,
    load_fresh,
)

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PROBE_SOURCE = HERE / "probes" / "rime_wordgroup_dump.cc"
PROBE_BINARY = HERE / "probes" / "rime_wordgroup_dump_squirrel"
SQUIRREL = Path("/Library/Input Methods/Squirrel.app/Contents")
RIME_DEPLOYER = SQUIRREL / "MacOS" / "rime_deployer"
FRAMEWORKS = SQUIRREL / "Frameworks"
PLUGINS = FRAMEWORKS / "rime-plugins"
LIBRIME = FRAMEWORKS / "librime.1.dylib"
LUA_PLUGIN = PLUGINS / "librime-lua.dylib"
OCTAGRAM_PLUGIN = PLUGINS / "librime-octagram.dylib"
DEFAULT_MODEL = REPO / "tiger_sentence_native" / "sentence-ngram-mobile.bin"
EXPECTED_MODEL_SHA256 = "c2c148ea7aae3336b745f3f63551c6cf35cc6d0a892078e4bd4e7568a2dfee34"
EXPECTED_RUNTIME_HASHES = {
    "librime": "abb06aa5b3f53de375bc401512b49a7a31b7ed5ee62b2ef7a438512abee5958f",
    "lua": "a0862901b4d36d35aba7012f05c132dd087890cca564609c5d1ea3ba9de7c12b",
    "octagram": "70f587ca908e1b857f4180dc50584b8843ec0852dbc2013248badc5fb0571525",
}
RUN_MARKER = ".mohu-cross-candidate-run-v1"
RUN_MARKER_CONTENT = "mohu-cross-candidate-run-v1\n"


@dataclass(frozen=True, slots=True)
class SchemeRun:
    scheme: str
    condition: str
    template: Path
    schema_file: str
    schema_id: str


COMPETITOR_RUNS = {
    "moran": SchemeRun(
        "moran", "moranmain2", Path("/tmp/kua-templates/moran"), "moran.schema.yaml", "moran"
    ),
    "yeying": SchemeRun(
        "yeying", "yeying", Path("/tmp/kua-templates/yeying"), "yeying.schema.yaml", "yeying"
    ),
    "wxpro": SchemeRun(
        "wxpro", "wxpro", Path("/tmp/kua-templates/wxpro"), "wanxiang_pro.schema.yaml", "wanxiang_pro"
    ),
}
COMPETITOR_PATCHES = {
    "moran": {
        "smart/enable_user_dict": False,
        "english/enable_user_dict": False,
        "japanese/enable_user_dict": False,
        "custom_phrase/enable_user_dict": False,
    },
    "yeying": {
        "translator/enable_user_dict": False,
        "custom_phrase/enable_user_dict": False,
    },
    "wxpro": {
        "user_dict_set/enable_user_dict": False,
        "add_user_dict/enable_user_dict": False,
        "add_user_dict/enable_auto_phrase": False,
        "wanxiang_english/enable_user_dict": False,
        "custom_phrase/enable_user_dict": False,
    },
}
REQUIRED_BUILD_ARTIFACTS = {
    "moran": (
        "moran.extended.table.bin",
        "moran.prism.bin",
        "moran_fixed_simp.table.bin",
        "moran_english.table.bin",
    ),
    "yeying": (
        "yeying.table.bin",
        "yeying.prism.bin",
    ),
    "wxpro": (
        "wanxiang_pro.table.bin",
        "wanxiang_pro.prism.bin",
        "wanxiang_english.table.bin",
        "wanxiang_mixedcode.table.bin",
        "wanxiang_reverse.table.bin",
    ),
    "mohu_zrm": (
        "mohu_zrm.extended.table.bin",
        "mohu_zrm.prism.bin",
        "mohu_zrm_fixed.table.bin",
        "mohu_zrm_fixed_legacy.table.bin",
        "mohu_charset.reverse.bin",
    ),
    "mohu_flypy": (
        "mohu_flypy.extended.table.bin",
        "mohu_flypy.prism.bin",
        "mohu_flypy_fixed.table.bin",
        "mohu_flypy_fixed_legacy.table.bin",
        "mohu_charset.reverse.bin",
    ),
}


@dataclass(frozen=True, slots=True)
class WorkerJob:
    run: SchemeRun
    mode: str
    shard: int
    input_path: Path
    output_path: Path
    run_dir: Path
    log_path: Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_metadata(path: Path, *, expected: str | None = None) -> dict[str, object]:
    if not path.is_file():
        raise FileNotFoundError(path)
    actual = sha256(path)
    if expected is not None and actual != expected:
        raise ValueError(f"SHA-256 mismatch for {path}: expected {expected}, got {actual}")
    return {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": actual}


def validate_root(root: Path) -> Path:
    root = root.expanduser().absolute().resolve()
    live_rime = (Path.home() / "Library/Rime").resolve()
    forbidden = {Path("/"), Path("/tmp").resolve(), Path.home().resolve(), REPO.resolve(), live_rime}
    if root in forbidden or live_rime in root.parents or root == REPO or REPO in root.parents:
        raise ValueError(f"unsafe benchmark root: {root}")
    if not (root / "manifest.json").is_file() or not (root / "meta.json").is_file():
        raise ValueError(f"benchmark root does not contain generated inputs: {root}")
    return root


def validate_model_path(model: Path) -> Path:
    resolved = model.expanduser().resolve()
    live_rime = (Path.home() / "Library/Rime").resolve()
    if resolved == live_rime or live_rime in resolved.parents:
        raise ValueError(f"refusing to read benchmark model from live Rime: {resolved}")
    return resolved


def ensure_run_marker(root: Path) -> None:
    marker = root / RUN_MARKER
    if marker.exists() and marker.read_text(encoding="utf-8") != RUN_MARKER_CONTENT:
        raise ValueError(f"invalid benchmark run marker: {marker}")
    marker.write_text(RUN_MARKER_CONTENT, encoding="utf-8")


def remove_owned(path: Path, root: Path) -> None:
    if not (root / RUN_MARKER).is_file() or root not in path.resolve().parents:
        raise ValueError(f"refusing to remove unowned path: {path}")
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def compile_probe(probe: Path = PROBE_BINARY) -> dict[str, object]:
    for path in (PROBE_SOURCE, LIBRIME):
        if not path.is_file():
            raise FileNotFoundError(path)
    include_candidates = [Path("/opt/homebrew/include"), FRAMEWORKS / "Headers"]
    include = next((path for path in include_candidates if (path / "rime_api.h").is_file()), None)
    if include is None:
        raise FileNotFoundError("no rime_api.h compatible with Squirrel was found")
    probe.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "clang++", "-std=c++17", "-O2", f"-I{include}", str(PROBE_SOURCE), str(LIBRIME),
        f"-Wl,-rpath,{FRAMEWORKS}", "-o", str(probe),
    ]
    subprocess.run(command, cwd=REPO, check=True)
    inspected = subprocess.run(["otool", "-L", str(probe)], capture_output=True, text=True, check=True)
    if "@rpath/librime.1.dylib" not in inspected.stdout:
        raise ValueError("probe is not linked against Squirrel @rpath/librime.1.dylib")
    return {"command": command, **file_metadata(probe)}


def _copytree(source: Path, destination: Path, *, exclude_volatile: bool) -> None:
    ignored = {"build", "sync", "log", "logs"}

    def ignore(_directory: str, names: list[str]) -> set[str]:
        if not exclude_volatile:
            return set()
        return {
            name for name in names
            if name in ignored
            or name == "user.yaml"
            or name.endswith(".userdb")
            or name.endswith(".trash")
        }

    shutil.copytree(source, destination, ignore=ignore)


def _clone_tree(source: Path, destination: Path) -> None:
    if sys.platform == "darwin":
        completed = subprocess.run(
            ["cp", "-cR", str(source), str(destination)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            return
    shutil.copytree(source, destination)


def _copy_file(source: Path, destination: Path, *, executable: bool = False) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    destination.chmod(0o755 if executable else 0o644)


def write_custom_patch(path: Path, patch: dict[str, bool]) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing custom patch: {path}")
    rows = ["patch:"]
    rows.extend(f"  {key}: {'true' if value else 'false'}" for key, value in patch.items())
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def write_benchmark_schema_list(path: Path, schema_id: str) -> None:
    path.write_text(
        f"patch:\n  schema_list:\n    - schema: {schema_id}\n",
        encoding="utf-8",
    )


def verify_build_artifacts(run: SchemeRun) -> dict[str, object]:
    required = REQUIRED_BUILD_ARTIFACTS[run.scheme]
    build_dir = run.template / "build"
    missing = [name for name in required if not (build_dir / name).is_file()]
    if missing:
        raise RuntimeError(
            f"incomplete Rime deployment for {run.scheme}: missing {', '.join(missing)}"
        )
    return {
        name: file_metadata(build_dir / name)
        for name in required
    }


def deploy_template(run: SchemeRun) -> dict[str, object]:
    if not RIME_DEPLOYER.is_file():
        raise FileNotFoundError(RIME_DEPLOYER)
    write_benchmark_schema_list(run.template / "default.custom.yaml", run.schema_id)
    environment = os.environ.copy()
    environment.update({
        "HOME": str(run.template),
        "XDG_CACHE_HOME": str(run.template / ".cache"),
        "XDG_CONFIG_HOME": str(run.template / ".config"),
        "XDG_DATA_HOME": str(run.template / ".local/share"),
    })
    command = [
        str(RIME_DEPLOYER),
        "--build",
        str(run.template),
        str(run.template),
        str(run.template / "build"),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(
            f"Rime deployment failed for {run.scheme}: exit={completed.returncode}; {detail}"
        )
    artifacts = verify_build_artifacts(run)
    remove_personal_state(run.template)
    return {
        "command": command,
        "deployer": file_metadata(RIME_DEPLOYER),
        "artifacts": artifacts,
    }


def remove_personal_state(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.name == "user.yaml" or path.name.endswith(".userdb"):
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
    leftovers = [
        path for path in root.rglob("*")
        if path.name == "user.yaml" or path.name.endswith(".userdb")
    ]
    if leftovers:
        raise ValueError(f"personal state remains in staged template: {leftovers[:3]}")


def verify_codesign(path: Path) -> dict[str, object]:
    completed = subprocess.run(
        ["codesign", "--verify", "--strict", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise ValueError(f"codesign verification failed for {path}: {detail}")
    return {"verified": True, "command": ["codesign", "--verify", "--strict", str(path)]}


def strip_missing_import(destination: Path, dict_name: str, import_name: str) -> bool:
    """Drop an import_tables entry whose dict file is absent in the staging copy.

    The working tree may reference not-yet-generated lexicons (for example the
    wanxiang sync).  The benchmark keeps the previously measured dictionary
    instead of fetching new data, and records the strip in the run manifest.
    """

    dict_path = destination / f"{dict_name}.dict.yaml"
    if not dict_path.is_file() or (destination / f"{import_name}.dict.yaml").is_file():
        return False
    text = dict_path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"^[ \t]*-[ \t]+{re.escape(import_name)}[ \t]*(?:#[^\n]*)?\n?",
        re.MULTILINE,
    )
    patched, count = pattern.subn("", text)
    if not count:
        return False
    dict_path.write_text(patched, encoding="utf-8")
    return True


def stage_mohu_template(
    scheme: str,
    report_name: str,
    destination: Path,
    model: Path,
    notes: list[str],
) -> SchemeRun:
    if scheme not in {"zrm", "flypy"}:
        raise ValueError(f"unsupported Mohu scheme: {scheme}")
    build_script = REPO / "tools/build_split_dist.py"
    subprocess.run([sys.executable, str(build_script), scheme, str(destination)], cwd=REPO, check=True)

    schema_id = f"mohu_llm_{scheme}"
    schema_file = f"{schema_id}.schema.yaml"
    _copy_file(REPO / schema_file, destination / schema_file)
    native_lua = (
        "mohu_llm_runtime.lua",
        "mohu_sentence.lua",
        "mohu_tiger_sentence.lua",
        "mohu_tiger_reranker.lua",
        "mohu_tiger_reranker_profile.lua",
        "mohu_tiger_model_catalog.lua",
        "mohu_tiger_model_menu.lua",
    )
    for name in native_lua:
        _copy_file(REPO / "tiger_sentence_native" / name, destination / "lua" / name)
    _copy_file(
        REPO / "tiger_sentence_native/libtigerengine.dylib",
        destination / "mohu_llm/runtime/libtigerengine.dylib",
    )
    _copy_file(model, destination / "mohu_llm/data/sentence-ngram-mobile.bin")
    _copy_file(
        REPO / f"tiger_sentence_native/data/{scheme}/{schema_id}.lexicon.txt",
        destination / f"mohu_llm/data/{scheme}/{schema_id}.lexicon.txt",
    )
    (destination / "mohu_llm/config").mkdir(parents=True, exist_ok=True)
    if strip_missing_import(destination, f"mohu_{scheme}.extended", f"mohu_{scheme}.wanxiang"):
        notes.append(
            f"{report_name}: dropped missing import mohu_{scheme}.wanxiang from the "
            "staged extended dictionary (untracked sync output absent; benchmark "
            "keeps the previously measured dictionary)"
        )
    write_custom_patch(
        destination / f"{schema_id}.custom.yaml",
        {
            "smart/enable_user_dict": False,
            "custom_phrase/enable_user_dict": False,
            "tiger/user_model": False,
        },
    )
    remove_personal_state(destination)
    return SchemeRun(
        scheme=report_name,
        condition=report_name,
        template=destination,
        schema_file=schema_file,
        schema_id=schema_id,
    )


def stage_templates(root: Path, model: Path) -> tuple[dict[str, SchemeRun], list[str]]:
    templates = root / "templates"
    remove_owned(templates, root)
    templates.mkdir(parents=True)
    runs: dict[str, SchemeRun] = {}
    notes: list[str] = []
    for scheme, source_run in COMPETITOR_RUNS.items():
        if not source_run.template.is_dir():
            raise FileNotFoundError(source_run.template)
        destination = templates / scheme
        _copytree(source_run.template, destination, exclude_volatile=True)
        remove_personal_state(destination)
        write_custom_patch(
            destination / f"{source_run.schema_id}.custom.yaml",
            COMPETITOR_PATCHES[scheme],
        )
        runs[scheme] = SchemeRun(
            scheme, source_run.condition, destination, source_run.schema_file, source_run.schema_id
        )
    for short_name, report_name in (("zrm", "mohu_zrm"), ("flypy", "mohu_flypy")):
        runs[report_name] = stage_mohu_template(
            short_name,
            report_name,
            templates / report_name,
            model,
            notes,
        )
    return runs, notes


def _input_units(path: Path, mode: str) -> list[list[str]]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    units: list[list[str]] = []
    if mode == "fresh":
        for line_number, line in enumerate(lines, start=1):
            if not line.startswith("B\t"):
                raise ValueError(f"expected B row at {path}:{line_number}")
            units.append([line])
    else:
        if len(lines) % 2:
            raise ValueError(f"after-prefix input has an odd row count: {path}")
        for index in range(0, len(lines), 2):
            pair = lines[index:index + 2]
            if not pair[0].startswith("W\t") or not pair[1].startswith("B\t"):
                raise ValueError(f"invalid W/B pair at {path}:{index + 1}")
            if pair[0].split("\t", 2)[1] != pair[1].split("\t", 2)[1]:
                raise ValueError(f"W/B case mismatch at {path}:{index + 1}")
            units.append(pair)
    return units


def write_shards(source: Path, mode: str, destination: Path, units_per_shard: int) -> list[Path]:
    units = _input_units(source, mode)
    destination.mkdir(parents=True, exist_ok=True)
    shards: list[Path] = []
    for offset in range(0, len(units), units_per_shard):
        path = destination / f"{source.name}.{len(shards):02d}.tsv"
        rows = [line for unit in units[offset:offset + units_per_shard] for line in unit]
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        shards.append(path)
    if not shards:
        raise ValueError(f"no input units in {source}")
    return shards


def make_jobs(root: Path, runs: dict[str, SchemeRun], units_per_shard: int) -> list[WorkerJob]:
    chunks = root / "chunks"
    outputs = root / "out"
    run_dirs = root / "runs"
    logs = root / "logs"
    for path in (chunks, outputs, run_dirs, logs):
        remove_owned(path, root)
    jobs: list[WorkerJob] = []
    for scheme in SCHEMES:
        run = runs[scheme]
        for mode, input_suffix in (("fresh", "fresh"), ("afterA", "afterA")):
            source = root / "in" / f"{scheme}.{input_suffix}.tsv"
            shards = write_shards(source, mode, chunks / scheme / mode, units_per_shard)
            for index, input_path in enumerate(shards):
                if mode == "fresh":
                    output = outputs / "fresh" / f"{run.condition}-{input_path.stem}.tsv"
                else:
                    output = outputs / "afterA" / run.condition / f"{input_path.stem}.tsv"
                jobs.append(WorkerJob(
                    run=run,
                    mode=mode,
                    shard=index,
                    input_path=input_path,
                    output_path=output,
                    run_dir=run_dirs / f"{scheme}-{mode}-{index:02d}",
                    log_path=logs / f"{scheme}-{mode}-{index:02d}.log",
                ))
    return jobs


def validate_candidate_output(path: Path) -> dict[str, int]:
    stream_count = 0
    nonempty_count = 0
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.startswith("E\t"):
            continue
        fields = line.split("\t")
        if len(fields) != 6:
            raise RuntimeError(f"malformed E row at {path}:{line_number}")
        try:
            candidate_count = int(fields[3])
        except ValueError as exc:
            raise RuntimeError(f"invalid candidate count at {path}:{line_number}") from exc
        stream_count += 1
        nonempty_count += candidate_count > 0
    if stream_count == 0:
        raise RuntimeError(f"probe output contains no candidate stream records: {path}")
    if nonempty_count == 0:
        raise RuntimeError(f"every candidate stream is empty: {path}")
    return {"candidate_streams": stream_count, "nonempty_candidate_streams": nonempty_count}


def run_job(job: WorkerJob, probe: Path, max_candidates: int) -> dict[str, object]:
    _clone_tree(job.run.template, job.run_dir)
    job.output_path.parent.mkdir(parents=True, exist_ok=True)
    job.log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(probe), str(LUA_PLUGIN), str(OCTAGRAM_PLUGIN), str(FRAMEWORKS),
        str(job.run_dir), str(job.run_dir / job.run.schema_file), job.run.schema_id,
        str(job.input_path), str(job.output_path), str(max_candidates), job.mode,
    ]
    environment = os.environ.copy()
    environment.update({
        "HOME": str(job.run_dir),
        "XDG_CACHE_HOME": str(job.run_dir / ".cache"),
        "XDG_CONFIG_HOME": str(job.run_dir / ".config"),
        "XDG_DATA_HOME": str(job.run_dir / ".local/share"),
    })
    started = time.monotonic()
    try:
        with job.log_path.open("w", encoding="utf-8") as log:
            completed = subprocess.run(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
                env=environment,
            )
        if completed.returncode != 0:
            raise RuntimeError(
                f"probe failed for {job.run.scheme}/{job.mode}/{job.shard}: "
                f"exit={completed.returncode}; log={job.log_path}"
            )
        if not job.output_path.is_file() or job.output_path.stat().st_size == 0:
            raise RuntimeError(f"probe produced no output: {job.output_path}")
        output_validation = validate_candidate_output(job.output_path)
        return {
            "scheme": job.run.scheme,
            "condition": job.run.condition,
            "mode": job.mode,
            "shard": job.shard,
            "input": file_metadata(job.input_path),
            "output": file_metadata(job.output_path),
            "log": file_metadata(job.log_path),
            "output_validation": output_validation,
            "elapsed_seconds": time.monotonic() - started,
            "command": command,
        }
    finally:
        if job.run_dir.exists():
            shutil.rmtree(job.run_dir)


def validate_complete(root: Path) -> dict[str, object]:
    counts: dict[str, object] = {}
    for scheme in SCHEMES:
        cases = load_cross_cases(root, input_name=str(SCHEME_SPECS[scheme]["input_name"]))
        fresh = load_fresh(root, scheme)
        _validate_result_keys(fresh, cases, label=f"{scheme} direct")
        condition = str(SCHEME_SPECS[scheme]["condition"])
        prefix, after = load_after(root, condition)
        _validate_result_keys(after, cases, label=f"{scheme} after-prefix")
        expected_ids = {case.case_id for case in cases}
        if set(prefix) != expected_ids:
            raise ValueError(f"{scheme} prefix IDs are incomplete")
        mode_count = len(case_modes(cases))
        counts[scheme] = {
            "cases": len(cases),
            "modes": mode_count,
            "expected_candidate_streams_per_condition": len(cases) * mode_count,
            "direct_candidate_streams": len(fresh),
            "after_prefix_candidate_streams": len(after),
            "prefix_records": len(prefix),
            "prefix_success": sum(prefix.values()),
        }
    build_report(root)
    return counts


def runtime_metadata(probe: Path, model: Path) -> dict[str, object]:
    signed_paths = (LIBRIME, LUA_PLUGIN, OCTAGRAM_PLUGIN, REPO / "tiger_sentence_native/libtigerengine.dylib")
    signatures = {path.name: verify_codesign(path) for path in signed_paths}
    return {
        "probe": file_metadata(probe),
        "probe_source": file_metadata(PROBE_SOURCE),
        "librime": file_metadata(LIBRIME, expected=EXPECTED_RUNTIME_HASHES["librime"]),
        "lua_plugin": file_metadata(LUA_PLUGIN, expected=EXPECTED_RUNTIME_HASHES["lua"]),
        "octagram_plugin": file_metadata(OCTAGRAM_PLUGIN, expected=EXPECTED_RUNTIME_HASHES["octagram"]),
        "mohu_model": file_metadata(model, expected=EXPECTED_MODEL_SHA256),
        "mohu_engine": file_metadata(REPO / "tiger_sentence_native/libtigerengine.dylib"),
        "mohu_zrm_lexicon": file_metadata(REPO / "tiger_sentence_native/data/zrm/mohu_llm_zrm.lexicon.txt"),
        "mohu_flypy_lexicon": file_metadata(REPO / "tiger_sentence_native/data/flypy/mohu_llm_flypy.lexicon.txt"),
        "codesign": signatures,
    }


def write_manifest(root: Path, payload: dict[str, object]) -> None:
    (root / "run-manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_benchmark(
    root: Path,
    *,
    model: Path = DEFAULT_MODEL,
    workers: int = 5,
    units_per_shard: int = 1200,
    max_candidates: int = 5,
) -> dict[str, object]:
    root = validate_root(root)
    ensure_run_marker(root)
    if workers < 1 or units_per_shard < 1:
        raise ValueError("workers and units_per_shard must be positive")
    if max_candidates != 5:
        raise ValueError("cross-candidate reports require exactly five exported candidates")
    model = validate_model_path(model)
    probe_build = compile_probe()
    runtime = runtime_metadata(PROBE_BINARY, model)
    runs, staging_notes = stage_templates(root, model)
    deployments = {
        scheme: deploy_template(run)
        for scheme, run in runs.items()
    }
    jobs = make_jobs(root, runs, units_per_shard)
    manifest: dict[str, object] = {
        "version": 1,
        "root": str(root),
        "started_at_unix": int(time.time()),
        "max_candidates": max_candidates,
        "units_per_shard": units_per_shard,
        "workers": workers,
        "isolation": {
            "user_directory": "one cloned directory per scheme, condition, and shard",
            "session": "one Rime session per case",
            "adaptive_user_dictionaries": "disabled by benchmark-only schema custom patches",
            "wanxiang_context_reorder": "enabled; cold-start protocol commits prefix only and never commits target",
        },
        "staging_patches": staging_notes,
        "probe_build": probe_build,
        "runtime": runtime,
        "schemes": {
            scheme: {
                "condition": run.condition,
                "schema_file": run.schema_file,
                "schema_id": run.schema_id,
                "schema": file_metadata(run.template / run.schema_file),
                "deployment": deployments[scheme],
            }
            for scheme, run in runs.items()
        },
        "jobs": [],
    }
    write_manifest(root, manifest)
    failures: list[BaseException] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(run_job, job, PROBE_BINARY, max_candidates): job for job in jobs}
        for future in as_completed(futures):
            try:
                manifest["jobs"].append(future.result())
                write_manifest(root, manifest)
            except BaseException as exc:
                failures.append(exc)
                for pending in futures:
                    pending.cancel()
                break
    if failures:
        raise failures[0]
    manifest["jobs"] = sorted(
        manifest["jobs"], key=lambda item: (str(item["scheme"]), str(item["mode"]), int(item["shard"]))
    )
    manifest["validation"] = validate_complete(root)
    manifest["finished_at_unix"] = int(time.time())
    write_manifest(root, manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--units-per-shard", type=int, default=1200)
    parser.add_argument("--max-candidates", type=int, default=5)
    args = parser.parse_args()
    manifest = run_benchmark(
        args.root,
        model=args.model,
        workers=args.workers,
        units_per_shard=args.units_per_shard,
        max_candidates=args.max_candidates,
    )
    print(json.dumps(manifest["validation"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
