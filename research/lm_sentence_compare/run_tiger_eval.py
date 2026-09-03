#!/usr/bin/env python3
"""用 ctypes 驱动 libtigerengine（TCSKNM 三元模型）解码评测输入。

引擎与线上部署一致：beam=200、>4 键全档位竞争（all_ranks）。
模型取用户目录 ~/Library/Rime/mohu_llm/data/ 下的 sentence-ngram-mobile.bin，
默认 lexicon 使用仓库中的 `tiger_sentence_native/data/zrm/mohu_llm_zrm.lexicon.txt`。

用法:
  uv run research/lm_sentence_compare/run_tiger_eval.py \
      [--mode pure,sparse,word1,char1] [--shard 0/4] [--top 20] [--out results]

输出 results/tiger_<mode>[_shard<i>].tsv：
  case_id \t 候选1文本 \x1f score \t 候选2文本 \x1f score ...
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import sys
import time
from pathlib import Path
from typing import Sequence

HERE = Path(__file__).resolve().parent
REPO = Path(__file__).resolve().parents[2]
DEFAULT_LIB = REPO / "tiger_sentence_native/libtigerengine.dylib"
DEFAULT_MODEL = Path.home() / "Library/Rime/mohu_llm/data/sentence-ngram-mobile.bin"
DEFAULT_LEXICON = REPO / "tiger_sentence_native/data/zrm/mohu_llm_zrm.lexicon.txt"
TIGER_ENGINE_SHA256 = "45eeb32c7d65d24fcf018e0bc8fca90a31d5f6605b254a710c39297f1a0a195b"
TIGER_NGRAM_SHA256 = "c2c148ea7aae3336b745f3f63551c6cf35cc6d0a892078e4bd4e7568a2dfee34"
TIGER_LEXICON_SHA256 = "666a29cfdaaf5566a6431d270275c637e48679042c180cd1cecbd73830a97ee3"
BUF_SIZE = 8 << 20
DEFAULT_TOP = 20
DEFAULT_SQUIRREL_FRAMEWORKS = Path("/Library/Input Methods/Squirrel.app/Contents/Frameworks")
MODE_NAMES = ("pure", "sparse", "word1", "char1")


def load_lua_runtime(frameworks: Path = DEFAULT_SQUIRREL_FRAMEWORKS) -> list[ctypes.CDLL]:
    """Load Squirrel's librime/Lua symbols globally for the native addon."""

    loaded: list[ctypes.CDLL] = []
    if not frameworks.is_dir():
        return loaded
    for path in (frameworks / "librime.1.dylib", frameworks / "rime-plugins" / "librime-lua.dylib"):
        if path.is_file():
            loaded.append(ctypes.CDLL(str(path), mode=ctypes.RTLD_GLOBAL))
    return loaded


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


def resource_metadata(path: Path, expected: str | None, *, label: str) -> dict[str, object]:
    actual = verify_file_hash(path, expected, label=label) if expected is not None else _file_hash(path)
    return {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": actual,
        "pinned": expected is not None,
        "expected_sha256": expected,
    }


def _file_hash(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_engine(
    lib_path: Path,
    model: Path,
    lexicon: Path,
    *,
    lua_frameworks: Path = DEFAULT_SQUIRREL_FRAMEWORKS,
    expected_engine_sha256: str | None = TIGER_ENGINE_SHA256,
    expected_model_sha256: str | None = TIGER_NGRAM_SHA256,
    expected_lexicon_sha256: str | None = TIGER_LEXICON_SHA256,
):
    if expected_engine_sha256 is not None:
        verify_file_hash(lib_path, expected_engine_sha256, label="Tiger engine")
    if expected_model_sha256 is not None:
        verify_file_hash(model, expected_model_sha256, label="Tiger n-gram")
    if expected_lexicon_sha256 is not None:
        verify_file_hash(lexicon, expected_lexicon_sha256, label="Tiger lexicon")
    load_lua_runtime(lua_frameworks)
    lib = ctypes.CDLL(str(lib_path), mode=ctypes.RTLD_GLOBAL)
    lib.tiger_engine_free.argtypes = [ctypes.c_int]
    lib.tiger_engine_free.restype = None
    lib.tiger_engine_create.argtypes = [
        ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_int,
        ctypes.c_char_p, ctypes.c_int]
    lib.tiger_engine_create.restype = ctypes.c_int
    lib.tiger_decode.argtypes = [
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
        ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(ctypes.c_double)]
    lib.tiger_decode.restype = ctypes.c_int
    lib.tiger_last_error.argtypes = []
    lib.tiger_last_error.restype = ctypes.c_char_p
    err = ctypes.create_string_buffer(512)
    handle = lib.tiger_engine_create(
        str(model).encode(), str(lexicon).encode(), 200, 1, err, len(err))
    if handle < 0:
        raise SystemExit(f"engine create failed: {err.value.decode()}")
    try:
        if expected_engine_sha256 is not None:
            verify_file_hash(lib_path, expected_engine_sha256, label="Tiger engine after load")
        if expected_model_sha256 is not None:
            verify_file_hash(model, expected_model_sha256, label="Tiger n-gram after load")
        if expected_lexicon_sha256 is not None:
            verify_file_hash(lexicon, expected_lexicon_sha256, label="Tiger lexicon after load")
    except Exception:
        lib.tiger_engine_free(handle)
        raise
    return lib, handle


def decode(lib: ctypes.CDLL, handle: int, raw: str) -> tuple[list[tuple[str, str]], int]:
    buf = ctypes.create_string_buffer(BUF_SIZE)
    ms = ctypes.c_double(0)
    n = lib.tiger_decode(handle, raw.encode(), 0, buf, BUF_SIZE, ctypes.byref(ms))
    if n < 0:
        message = lib.tiger_last_error().decode()
        raise RuntimeError(f"decode failed for {raw!r}: {message}")
    try:
        lines = buf.value.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"native output is not valid UTF-8 for {raw!r}: {exc}") from exc
    if not lines:
        raise RuntimeError(f"native output is empty for {raw!r}")

    header = lines[0].split()
    if len(header) != 10:
        raise RuntimeError(f"invalid native output header for {raw!r}")
    try:
        values = [int(value) for value in header]
    except ValueError as exc:
        raise RuntimeError(f"invalid native output header for {raw!r}") from exc
    truncated, early_truncated, uses_incomplete, prefers_incomplete = values[:4]
    n_final, n_early = values[4:6]
    consensus_complete, consensus_bytes, consensus_raw, visible_consensus = values[6:]
    if any(flag not in (0, 1) for flag in (
        truncated,
        early_truncated,
        uses_incomplete,
        prefers_incomplete,
        consensus_complete,
        visible_consensus,
    )) or any(value < 0 for value in (n_final, n_early, consensus_bytes, consensus_raw)):
        raise RuntimeError(f"invalid native output header for {raw!r}")
    if n != n_final or n_early != 0 or len(lines) - 1 != n_final + n_early:
        raise RuntimeError(
            f"native candidate count mismatch for {raw!r}: "
            f"return={n}, final={n_final}, early={n_early}, rows={len(lines) - 1}"
        )

    rows: list[tuple[str, str]] = []
    seen_texts: set[str] = set()
    for line in lines[1:]:
        fields = line.split("\t", 5)
        if len(fields) != 6 or not fields[0] or not fields[1]:
            raise RuntimeError(f"invalid native candidate row for {raw!r}")
        if fields[0] in seen_texts:
            raise RuntimeError(f"duplicate candidate text for {raw!r}")
        try:
            score = float(fields[2])
            confidence = float(fields[3])
            max_rank = int(fields[4])
        except ValueError as exc:
            raise RuntimeError(f"invalid native candidate row for {raw!r}") from exc
        if not math.isfinite(score) or not math.isfinite(confidence) or max_rank < 1:
            raise RuntimeError(f"invalid native candidate row for {raw!r}")
        seen_texts.add(fields[0])
        rows.append((fields[0], fields[2]))
    return rows, int(round(ms.value * 1000.0))


def validate_top_limit(value: int) -> int:
    if value < 20:
        raise ValueError("--top must be at least 20 for Top-20 metrics")
    return value


def load_input_rows(lines: Sequence[str]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    ids: set[str] = set()
    raws: set[str] = set()
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != 2 or not fields[0] or not fields[1]:
            raise ValueError(f"invalid input row at line {line_number}")
        case_id, raw = fields
        if case_id in ids:
            raise ValueError(f"duplicate input id at line {line_number}")
        if raw in raws:
            raise ValueError(f"duplicate input raw at line {line_number}")
        ids.add(case_id)
        raws.add(raw)
        rows.append((case_id, raw))
    return rows


def raw_digest(raws: Sequence[str]) -> str:
    return hashlib.sha256("\n".join(sorted(raws)).encode("utf-8")).hexdigest()


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
        REPO.resolve(),
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="pure,sparse,word1,char1")
    parser.add_argument("--inputs", type=Path, default=HERE / "staging/inputs")
    parser.add_argument("--out", type=Path, default=HERE / "results")
    parser.add_argument("--shard", default="0/1", help="i/n 分片")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP)
    parser.add_argument("--lib", type=Path, default=DEFAULT_LIB)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--lexicon", type=Path, default=DEFAULT_LEXICON)
    parser.add_argument("--model-sha256", default=TIGER_NGRAM_SHA256)
    parser.add_argument("--lexicon-sha256", default=TIGER_LEXICON_SHA256)
    parser.add_argument("--engine-sha256", default=TIGER_ENGINE_SHA256)
    parser.add_argument("--allow-unpinned-resources", action="store_true")
    parser.add_argument("--lua-frameworks", type=Path, default=DEFAULT_SQUIRREL_FRAMEWORKS)
    args = parser.parse_args()
    try:
        top_limit = validate_top_limit(args.top)
    except ValueError as exc:
        parser.error(str(exc))

    shard_index, shard_count = (int(x) for x in args.shard.split("/"))
    if not (0 <= shard_index < shard_count):
        raise SystemExit("bad shard spec")

    # Validate the destination before loading native resources so a rejected
    # path cannot leave a live engine handle behind.
    args.out = validate_output_root(args.out)
    args.out.mkdir(parents=True, exist_ok=True)
    suffix = "" if shard_count == 1 else f"_shard{shard_index}"
    requested_modes = tuple(args.mode.split(","))
    if not requested_modes or any(not mode for mode in requested_modes):
        raise SystemExit("at least one mode is required")
    if len(set(requested_modes)) != len(requested_modes):
        raise SystemExit("duplicate mode in --mode")
    for mode in requested_modes:
        if mode not in MODE_NAMES:
            raise SystemExit(f"unknown mode: {mode}")
        validate_output_file(args.out / f"tiger_{mode}{suffix}.tsv", args.out)
        validate_output_file(args.out / f"tiger_{mode}{suffix}.latency.tsv", args.out)
    validate_output_file(args.out / "tiger-run-manifest.json", args.out)
    expected_model_sha256 = None if args.allow_unpinned_resources else args.model_sha256
    expected_lexicon_sha256 = None if args.allow_unpinned_resources else args.lexicon_sha256
    expected_engine_sha256 = None if args.allow_unpinned_resources else args.engine_sha256
    lib, handle = load_engine(
        args.lib,
        args.model,
        args.lexicon,
        lua_frameworks=args.lua_frameworks,
        expected_engine_sha256=expected_engine_sha256,
        expected_model_sha256=expected_model_sha256,
        expected_lexicon_sha256=expected_lexicon_sha256,
    )
    try:
        run_manifest: dict[str, object] = {
            "beam": 200,
            "all_ranks": True,
            "top": top_limit,
            "latency_scope": "native tiger_decode only",
            "shard": {"index": shard_index, "count": shard_count},
            "allow_unpinned_resources": args.allow_unpinned_resources,
            "resources": {
                "engine": resource_metadata(args.lib, expected_engine_sha256, label="Tiger engine"),
                "model": resource_metadata(args.model, expected_model_sha256, label="Tiger n-gram"),
                "lexicon": resource_metadata(args.lexicon, expected_lexicon_sha256, label="Tiger lexicon"),
            },
            "modes": [],
        }
        for mode in requested_modes:
            src = args.inputs / f"{mode}.tsv"
            rows = load_input_rows(src.read_text(encoding="utf-8").splitlines())
            shard_rows = [r for i, r in enumerate(rows) if i % shard_count == shard_index]
            dst = args.out / f"tiger_{mode}{suffix}.tsv"
            latency_dst = args.out / f"tiger_{mode}{suffix}.latency.tsv"
            t0 = time.time()
            with dst.open("w", encoding="utf-8") as out, latency_dst.open("w", encoding="utf-8") as latency_out:
                for done, (_case_id, raw) in enumerate(shard_rows, 1):
                    cands, elapsed_us = decode(lib, handle, raw)
                    cands = cands[:top_limit]
                    cells = "\t".join(
                        f"{text}\x1f{score}" for text, score in cands)
                    out.write(f"{raw}\t{cells}\n")
                    latency_out.write(f"{raw}\t{elapsed_us}\n")
                    if done % 2000 == 0:
                        rate = done / max(time.time() - t0, 1e-6)
                        print(f"[tiger {mode}{suffix}] {done}/{len(shard_rows)} "
                              f"({rate:.0f}/s)", file=sys.stderr)
            print(f"[tiger {mode}{suffix}] wrote {len(shard_rows)} rows -> {dst} "
                  f"({time.time()-t0:.0f}s)", file=sys.stderr)
            output_raws = [
                line.split("\t", 1)[0]
                for line in dst.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            latency_raws = [
                line.split("\t", 1)[0]
                for line in latency_dst.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            expected_raws = [raw for _case_id, raw in shard_rows]
            if output_raws != expected_raws:
                raise RuntimeError(f"Tiger output raw order mismatch for {mode}{suffix}")
            if latency_raws != expected_raws:
                raise RuntimeError(f"Tiger latency raw order mismatch for {mode}{suffix}")
            run_manifest["modes"].append({
                "mode": mode,
                "input_rows": len(shard_rows),
                "output_rows": len(shard_rows),
                "input_raw_sha256": raw_digest(expected_raws),
                "output_raw_sha256": raw_digest(output_raws),
                "latency_raw_sha256": raw_digest(latency_raws),
                "output": str(dst),
                "latency": str(latency_dst),
            })
        manifest_path = validate_output_file(args.out / "tiger-run-manifest.json", args.out)
        manifest_path.write_text(
            json.dumps(run_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    finally:
        free = getattr(lib, "tiger_engine_free", None)
        if free is not None:
            free.argtypes = [ctypes.c_int]
            free.restype = None
            free(handle)


if __name__ == "__main__":
    main()
