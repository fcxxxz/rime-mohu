"""Native Tiger menu decoder for semantic reranker data generation.

Drives ``libtigerengine`` over ctypes with the same production decode call the
Rime translator uses (beam 200, all_ranks). Every artifact hash is recorded in
the dataset manifest instead of being pinned here: research datasets pin
lineage in their manifests, not in code.

Output rows keep the full native decode payload (text, segmented code, score,
confidence, max_rank, raw_lengths) so dataset builders can carry only fields
that came from this exact decode invocation.
"""

from __future__ import annotations

import ctypes
import hashlib
import math
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_LIB = REPO / "tiger_sentence_native" / "libtigerengine.dylib"
_MODEL_CANDIDATES = (
    Path.home() / "Library/Rime/mohu/model/mohu-sentence-ngram-v5.bin",
    Path("/tmp/mohu-sentence-ngram-v5.bin"),
)
DEFAULT_MODEL = next((path for path in _MODEL_CANDIDATES if path.is_file()), _MODEL_CANDIDATES[0])
DEFAULT_LEXICON = Path.home() / "Library/Rime/mohu/data/zrm/mohu_zrm.lexicon.txt"
DEFAULT_SQUIRREL_FRAMEWORKS = Path("/Library/Input Methods/Squirrel.app/Contents/Frameworks")
BUF_SIZE = 8 << 20


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_lua_runtime(frameworks: Path) -> None:
    if not frameworks.is_dir():
        return
    for path in (frameworks / "librime.1.dylib", frameworks / "rime-plugins" / "librime-lua.dylib"):
        if path.is_file():
            ctypes.CDLL(str(path), mode=ctypes.RTLD_GLOBAL)


@dataclass(frozen=True)
class NativeCandidate:
    text: str
    segmented: str
    score: float
    confidence: float
    max_rank: int
    path_map: dict[int, int]
    personal: bool


@dataclass(frozen=True)
class NativeMenu:
    raw: str
    truncated: bool
    early_truncated: bool
    uses_incomplete: bool
    prefers_incomplete: bool
    consensus_bytes: int
    consensus_raw: int
    candidates: list[NativeCandidate]


class NativeMenuDecoder:
    def __init__(
        self,
        lib_path: Path = DEFAULT_LIB,
        model_path: Path = DEFAULT_MODEL,
        lexicon_path: Path = DEFAULT_LEXICON,
        *,
        beam: int = 200,
        all_ranks: bool = True,
        word_edge_weight: float = 0.0,
        lua_frameworks: Path = DEFAULT_SQUIRREL_FRAMEWORKS,
    ) -> None:
        for label, path in (("engine", lib_path), ("model", model_path), ("lexicon", lexicon_path)):
            if not Path(path).is_file():
                raise FileNotFoundError(f"{label} not found: {path}")
        _load_lua_runtime(lua_frameworks)
        self.lib = ctypes.CDLL(str(lib_path), mode=ctypes.RTLD_GLOBAL)
        self.lib.tiger_engine_free.argtypes = [ctypes.c_int]
        self.lib.tiger_engine_free.restype = None
        self.lib.tiger_engine_create.argtypes = [
            ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_int,
            ctypes.c_char_p, ctypes.c_int,
        ]
        self.lib.tiger_engine_create.restype = ctypes.c_int
        self.lib.tiger_decode.argtypes = [
            ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
            ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(ctypes.c_double),
        ]
        self.lib.tiger_decode.restype = ctypes.c_int
        self.lib.tiger_last_error.argtypes = []
        self.lib.tiger_last_error.restype = ctypes.c_char_p
        err = ctypes.create_string_buffer(512)
        self.handle = self.lib.tiger_engine_create(
            str(model_path).encode(), str(lexicon_path).encode(),
            beam, 1 if all_ranks else 0, err, len(err),
        )
        if self.handle < 0:
            raise RuntimeError(f"engine create failed: {err.value.decode()}")
        if word_edge_weight > 0.0:
            # 词边先验形态的菜单捕获（C3 训练分布＝部署分布）。旧 dylib
            # 无该 setter 时显式失败，避免静默拿到 w=0 分布。
            setter = getattr(self.lib, "tiger_engine_set_word_edge_weight", None)
            if setter is None:
                raise RuntimeError(
                    "word_edge_weight requested but engine lacks the setter")
            setter.argtypes = [ctypes.c_int, ctypes.c_double]
            setter.restype = ctypes.c_int
            rc = setter(self.handle, ctypes.c_double(word_edge_weight))
            if rc < 0:
                raise RuntimeError(
                    f"set word edge weight failed: {self.lib.tiger_last_error().decode()}")
        self.manifest = {
            "engine": str(lib_path),
            "engine_sha256": file_sha256(lib_path),
            "model": str(model_path),
            "model_sha256": file_sha256(model_path),
            "lexicon": str(lexicon_path),
            "lexicon_sha256": file_sha256(lexicon_path),
            "beam": beam,
            "all_ranks": all_ranks,
            "word_edge_weight": word_edge_weight,
        }

    def close(self) -> None:
        if getattr(self, "handle", -1) >= 0:
            self.lib.tiger_engine_free(self.handle)
            self.handle = -1

    def __enter__(self) -> "NativeMenuDecoder":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def decode(self, raw: str) -> NativeMenu:
        if not raw or not raw.isascii() or not raw.isalpha():
            raise ValueError(f"raw decode input must be ASCII letters: {raw!r}")
        buf = ctypes.create_string_buffer(BUF_SIZE)
        ms = ctypes.c_double(0)
        n = self.lib.tiger_decode(self.handle, raw.encode(), 0, buf, BUF_SIZE, ctypes.byref(ms))
        if n < 0:
            raise RuntimeError(f"decode failed for {raw!r}: {self.lib.tiger_last_error().decode()}")
        lines = buf.value.decode("utf-8").splitlines()
        if not lines:
            raise RuntimeError(f"native output is empty for {raw!r}")
        header = [int(value) for value in lines[0].split()]
        if len(header) != 10:
            raise RuntimeError(f"invalid native header for {raw!r}")
        truncated, early_truncated, uses_incomplete, prefers_incomplete = header[:4]
        n_final, n_early = header[4:6]
        consensus_complete, consensus_bytes, consensus_raw, _visible = header[6:]
        if n != n_final or n_early != 0 or len(lines) - 1 != n_final:
            raise RuntimeError(f"candidate count mismatch for {raw!r}")
        candidates: list[NativeCandidate] = []
        for line in lines[1:]:
            fields = line.split("\t", 6)
            if len(fields) != 7 or not fields[0] or not fields[1]:
                raise RuntimeError(f"invalid candidate row for {raw!r}")
            score = float(fields[2])
            confidence = float(fields[3])
            max_rank = int(fields[4])
            path_map: dict[int, int] = {}
            if fields[5]:
                for item in fields[5].split(","):
                    start_text, _, length_text = item.partition(":")
                    path_map[int(start_text)] = int(length_text)
            personal = fields[6] == "1"
            if not math.isfinite(score) or not math.isfinite(confidence) or max_rank < 1:
                raise RuntimeError(f"invalid candidate values for {raw!r}")
            candidates.append(
                NativeCandidate(fields[0], fields[1], score, confidence, max_rank, path_map, personal)
            )
        return NativeMenu(
            raw=raw,
            truncated=bool(truncated),
            early_truncated=bool(early_truncated),
            uses_incomplete=bool(uses_incomplete),
            prefers_incomplete=bool(prefers_incomplete),
            consensus_bytes=consensus_bytes,
            consensus_raw=consensus_raw,
            candidates=candidates,
        )
