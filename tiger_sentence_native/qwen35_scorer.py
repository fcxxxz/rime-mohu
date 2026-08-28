"""Local Qwen3.5 sentence scorer for the native Mohu sentence schema.

The module intentionally keeps MLX optional at import time.  The Rime host does
not import this module; a separate service process loads ``mlx-lm`` and the
checkpoint only after the first scoring request.  The service protocol is
newline-delimited JSON over a private Unix domain socket:

    {"op":"score","request_id":"...","context":"...",
     "candidates":["...", "..."]}

Responses contain one ``sum_logp`` and ``predicted_tokens`` object per input
candidate.  No prompt or text generation is involved.  A small localhost HTTP
adapter is provided for diagnostics and clients that cannot use Unix sockets.

The implementation follows the public ``mlx-lm==0.31.3`` API.  Qwen3.5's
vision-language checkpoint is loaded through its text branch by mlx-lm; vision
weights are ignored by that loader's ``qwen3_5`` model adapter.  Plain text
checkpoints such as Qwen3-0.6B expose the transformer directly and are scored
through the same batched likelihood path.
"""

from __future__ import annotations

import argparse
import hashlib
import http.server
import inspect
import json
import os
import platform
import re
import signal
import socket
import socketserver
import sys
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from numbers import Integral
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

PROTOCOL_VERSION = 1


@dataclass(frozen=True)
class ModelSpec:
    """One pinned production checkpoint the scorer is allowed to load.

    ``is_vlm`` checkpoints (Qwen3.5) load as a vision-language wrapper whose
    text model hangs off ``language_model``; plain text checkpoints expose the
    transformer directly.  ``text_branch`` is reported in health metadata.
    """

    repo_id: str
    basename: str
    model_type: str
    text_branch: str
    is_vlm: bool


# The production load path is registry-driven: a model reference must pin to
# exactly one registered checkpoint (by canonical basename or repo id) before
# quantization and architecture checks run.
MODEL_SPECS: tuple[ModelSpec, ...] = (
    ModelSpec(
        repo_id="mlx-community/Qwen3.5-0.8B-MLX-4bit",
        basename="Qwen3.5-0.8B-MLX-4bit",
        model_type="qwen3_5",
        text_branch="qwen3_5.language_model",
        is_vlm=True,
    ),
    ModelSpec(
        repo_id="mlx-community/Qwen3-0.6B-4bit",
        basename="Qwen3-0.6B-4bit",
        model_type="qwen3",
        text_branch="qwen3",
        is_vlm=False,
    ),
)
DEFAULT_MODEL = MODEL_SPECS[0].repo_id
_REGISTERED_MODEL_TYPES = {spec.model_type: spec for spec in MODEL_SPECS}
DEFAULT_SOCKET = "/tmp/mohu-qwen35.sock"
DEFAULT_IDLE_TIMEOUT = 900.0
MAX_JSON_LINE_BYTES = 256 * 1024
MAX_HTTP_HEADER_BYTES = 32 * 1024
MAX_REQUEST_ID_BYTES = 128
MAX_CONTEXT_BYTES = 100_000
MAX_CANDIDATE_BYTES = 16_000
# The wire contract accepts the full native candidate pool.  MLX's quantized
# kernels use a stable five-row shape for short requests; larger requests are
# padded to the stable twenty-row shape internally while only real candidates
# are returned on the wire.  Scores are therefore comparable within one
# request, while shape-dependent numerical drift across sequence buckets is an
# implementation detail of the quantized backend.
MAX_REQUEST_CANDIDATES = 20
FIXED_BATCH_ROWS = 5
# A normal Lua request writes a complete frame in one small burst.  These
# bounds protect the long-lived Unix endpoint from a same-user slowloris while
# still allowing a persistent client to idle between keystrokes.
DEFAULT_PARTIAL_FRAME_TIMEOUT = 2.0
DEFAULT_CONNECTION_IDLE_TIMEOUT = 30.0
DEFAULT_MAX_CONNECTIONS = 32
MIN_SCORING_SEQUENCE_TOKENS = 8
MIN_SCORING_TARGET_TOKENS = 8
FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
WARMUP_CANDIDATES = (
    "那就没什么可犹豫地了",
    "那就没什么可犹豫的了",
    "那就没什么可犹豫的嘞",
    "那就没什么可犹豫得了",
    "那就没什么可犹豫的乐",
)


def summarize_latencies(latencies_ms: Sequence[float]) -> dict[str, float | int]:
    """Return deterministic nearest-rank latency percentiles.

    ``time.perf_counter_ns`` callers pass milliseconds here.  Nearest-rank
    percentiles avoid interpolating measurements and make small smoke runs
    easy to compare with the service's p50/p95/p99 targets.
    """

    if not latencies_ms:
        raise ValueError("at least one latency sample is required")
    import math

    values = [float(value) for value in latencies_ms]
    if any(not math.isfinite(value) or value < 0 for value in values):
        raise ValueError("latency samples must be finite and non-negative")
    values.sort()

    def percentile(fraction: float) -> float:
        index = max(0, math.ceil(fraction * len(values)) - 1)
        return values[index]

    return {
        "count": len(values),
        "min_ms": values[0],
        "p50_ms": percentile(0.50),
        "p95_ms": percentile(0.95),
        "p99_ms": percentile(0.99),
        "max_ms": values[-1],
        "mean_ms": sum(values) / len(values),
    }


def benchmark_scorer(
    scorer: Scorer,
    *,
    context: str,
    candidates: Sequence[str],
    warmup: int = 5,
    runs: int = 100,
) -> dict[str, Any]:
    """Benchmark a scorer using monotonic wall-clock measurements."""

    if warmup < 0 or runs < 1:
        raise ValueError("warmup must be non-negative and runs must be positive")
    # Validate once up front so a benchmark cannot silently measure failures.
    request = validate_request({"op": "score", "context": context, "candidates": list(candidates)})
    for _ in range(warmup):
        scorer.score(request.context, request.candidates)
    samples: list[float] = []
    last_scores: Sequence[Score] = ()
    for _ in range(runs):
        started = time.perf_counter_ns()
        last_scores = scorer.score(request.context, request.candidates)
        elapsed_ns = time.perf_counter_ns() - started
        samples.append(elapsed_ns / 1_000_000.0)
    summary = summarize_latencies(samples)
    summary["scores"] = [
        wire for score in last_scores if (wire := _score_to_wire(score)) is not None
    ]
    try:
        summary["health"] = dict(scorer.health())
    except Exception:
        summary["health"] = {"status": "unavailable", "ready": False}
    return summary


class ScorerError(RuntimeError):
    """Base class for errors which can be safely returned to a client."""

    code = "scorer_error"
    public_message = "scorer request failed"

    def __init__(self, message: str | None = None):
        super().__init__(message or self.public_message)
        if message:
            self.public_message = message


class ProtocolError(ScorerError):
    code = "invalid_request"
    public_message = "invalid request"


class IncompleteModelError(ScorerError):
    code = "model_incomplete"
    public_message = "model checkpoint is missing or incomplete"


class UnsupportedModelError(ScorerError):
    code = "model_unsupported"
    public_message = "model is not a supported checkpoint"


class ModelUnavailableError(ScorerError):
    code = "model_unavailable"
    public_message = "model is not ready"


def _validate_expected_fingerprint(actual: str, expected: str | None) -> None:
    """Enforce the exact checkpoint identity requested by a launcher."""

    if expected is None:
        return
    if not isinstance(expected, str) or not FINGERPRINT_RE.fullmatch(expected):
        raise UnsupportedModelError("expected model fingerprint is invalid")
    if actual != expected:
        raise UnsupportedModelError("model fingerprint does not match expected checkpoint")


@dataclass(frozen=True)
class Score:
    """A candidate's conditional token log likelihood."""

    sum_logp: float
    predicted_tokens: int


@dataclass(frozen=True)
class ScoreRequest:
    op: str
    request_id: str
    context: str
    candidates: tuple[str, ...]
    normalize: str = "sum_logp"
    candidate_mode: str = "suffix"


class Scorer(Protocol):
    def score(
        self,
        context: str,
        candidates: Sequence[str],
        *,
        candidate_mode: str = "suffix",
    ) -> Sequence[Score]: ...

    def health(self) -> Mapping[str, Any]: ...


def _validate_utf8_text(value: Any, *, field: str, max_bytes: int, nonempty: bool) -> str:
    """Validate text without putting user-provided text in an error message."""

    if not isinstance(value, str):
        raise ProtocolError(f"{field} must be a string")
    if nonempty and not value:
        raise ProtocolError(f"{field} must not be empty")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        # Do not include the offending value.  Surrogate-containing JSON is a
        # common way for an untrusted client to make a service leak input.
        raise ProtocolError(f"{field} must be valid UTF-8") from exc
    if b"\x00" in encoded:
        raise ProtocolError(f"{field} contains a forbidden NUL byte")
    if len(encoded) > max_bytes:
        raise ProtocolError(f"{field} exceeds the maximum size")
    return value


def validate_request(payload: Any) -> ScoreRequest:
    """Validate and normalize one wire request.

    ``health`` requests do not need a request id or candidate list.  A score
    request has a deliberately small upper bound because the socket is local
    but still reachable by any process owned by the same user.
    """

    if not isinstance(payload, Mapping):
        raise ProtocolError("request must be a JSON object")
    # Early Lua clients predate the explicit ``op`` field; a frame containing
    # candidates is unambiguously a score request when it is omitted.
    op = payload.get("op", "score")
    version = payload.get("version", PROTOCOL_VERSION)
    if (
        isinstance(version, bool)
        or not isinstance(version, Integral)
        or version != PROTOCOL_VERSION
    ):
        raise ProtocolError("unsupported protocol version")
    if op == "health":
        request_id = payload.get("request_id", "")
        if request_id != "":
            _validate_utf8_text(
                request_id,
                field="request_id",
                max_bytes=MAX_REQUEST_ID_BYTES,
                nonempty=False,
            )
        return ScoreRequest("health", str(request_id), "", (), "sum_logp", "suffix")
    if op != "score":
        raise ProtocolError("op must be 'score' or 'health'")

    request_id = payload.get("request_id", "")
    request_id = _validate_utf8_text(
        request_id,
        field="request_id",
        max_bytes=MAX_REQUEST_ID_BYTES,
        nonempty=False,
    )
    # ``raw`` is the historical Lua field and may contain pinyin/code, while
    # ``context_text`` is the readable committed Chinese prefix.  Prefer the
    # latter; only treat ``raw`` as context when no readable field is present.
    if "context_text" in payload:
        context_value = payload.get("context_text")
        if "context" in payload and payload.get("context") != context_value:
            raise ProtocolError("context and context_text must match")
    elif "context" in payload:
        context_value = payload.get("context")
    else:
        context_value = payload.get("raw", "")
    context = _validate_utf8_text(
        context_value,
        field="context",
        max_bytes=MAX_CONTEXT_BYTES,
        nonempty=False,
    )
    normalize = payload.get("normalize", "sum_logp")
    if normalize not in ("sum_logp", "mean_logp"):
        raise ProtocolError("normalize must be 'sum_logp' or 'mean_logp'")
    candidate_mode = payload.get("candidate_mode", "suffix")
    if candidate_mode not in ("suffix", "complete"):
        raise ProtocolError("candidate_mode must be 'suffix' or 'complete'")
    candidates_value = payload.get("candidates")
    if not isinstance(candidates_value, (list, tuple)):
        raise ProtocolError("candidates must be an array")
    if not 1 <= len(candidates_value) <= MAX_REQUEST_CANDIDATES:
        raise ProtocolError("candidates must contain 1..20 items")
    candidates: list[str] = []
    for candidate in candidates_value:
        candidates.append(
            _validate_utf8_text(
                candidate,
                field="candidate",
                max_bytes=MAX_CANDIDATE_BYTES,
                nonempty=True,
            )
        )
    return ScoreRequest("score", request_id, context, tuple(candidates), normalize, candidate_mode)


def _build_metadata(model: Mapping[str, Any], build: Mapping[str, Any]) -> dict[str, Any]:
    """Copy metadata through a JSON-safe boundary."""

    return {"model": dict(model), "build": dict(build)}


def _safe_request_id(value: Any) -> str:
    """Return a bounded, JSON-encodable request id for error responses."""

    if not isinstance(value, str):
        return ""
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        return ""
    if len(encoded) > MAX_REQUEST_ID_BYTES or b"\x00" in encoded:
        return ""
    return value


def _error_response(error: ScorerError, request_id: str = "") -> dict[str, Any]:
    return {
        "ok": False,
        "version": PROTOCOL_VERSION,
        "status": "error",
        "request_id": _safe_request_id(request_id),
        "error": {"code": error.code, "message": error.public_message},
    }


_SERIALIZATION_ERROR_LINE = (
    b'{"ok":false,"version":1,"status":"error","request_id":"",'
    b'"error":{"code":"serialization","message":"response failed"}}\n'
)


def _encode_json_line(payload: Mapping[str, Any]) -> bytes:
    """Encode one response frame without letting malformed metadata kill a thread."""

    try:
        return (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
                "utf-8"
            )
            + b"\n"
        )
    except (TypeError, ValueError, UnicodeError, RecursionError, MemoryError):
        return _SERIALIZATION_ERROR_LINE


def _score_to_wire(score: Any) -> dict[str, Any] | None:
    raw_count: Any
    if isinstance(score, Score):
        value = score
        raw_count = score.predicted_tokens
    elif isinstance(score, Mapping):
        try:
            raw_count = score["predicted_tokens"]
            if isinstance(raw_count, bool) or not isinstance(raw_count, Integral):
                return None
            value = Score(float(score["sum_logp"]), int(raw_count))
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
    else:
        try:
            raw_count = score.predicted_tokens
            if isinstance(raw_count, bool) or not isinstance(raw_count, Integral):
                return None
            value = Score(float(score.sum_logp), int(raw_count))
        except (AttributeError, TypeError, ValueError, OverflowError):
            return None
    import math

    if not math.isfinite(value.sum_logp) or value.predicted_tokens <= 0:
        return None
    # bool is an int subclass, but is not a valid token count on the wire.
    if isinstance(raw_count, bool) or not isinstance(raw_count, Integral):
        return None
    return {
        "sum_logp": float(value.sum_logp),
        "predicted_tokens": int(value.predicted_tokens),
    }


def handle_request(payload: Any, scorer: Scorer) -> dict[str, Any]:
    """Handle a decoded JSON object and return a JSON-serializable response."""

    try:
        request = validate_request(payload)
    except ProtocolError as exc:
        return _error_response(exc)

    if request.op == "health":
        try:
            health = dict(scorer.health())
        except Exception:
            return _error_response(ScorerError("health check failed"), request.request_id)
        response: dict[str, Any] = {
            "ok": True,
            "version": PROTOCOL_VERSION,
            "status": "ok",
            "request_id": request.request_id,
        }
        response.update(health)
        # Keep the health contract stable even for a minimal fake scorer.
        response.setdefault("status", "ok")
        response.setdefault("ready", True)
        response.setdefault("model", {})
        response.setdefault("build", {})
        return response

    try:
        raw_scores = _score_with_request(scorer, request)
        scores = [_score_to_wire(score) for score in raw_scores]
    except ScorerError as exc:
        return _error_response(exc, request.request_id)
    except Exception:
        # Do not expose tokenizer/model internals or candidate text over the
        # protocol.  Full tracebacks are intentionally not logged either.
        return _error_response(ScorerError("internal scoring error"), request.request_id)
    if len(scores) != len(request.candidates) or any(score is None for score in scores):
        return _error_response(ScorerError("scorer returned invalid scores"), request.request_id)
    response = {
        "ok": True,
        "version": PROTOCOL_VERSION,
        "status": "ok",
        "request_id": request.request_id,
        "normalize": request.normalize,
        "scores": scores,
    }
    # Include compact model identity on score responses as well as health.  A
    # client can reject a stale result without issuing a second round trip.
    try:
        model = scorer.health().get("model")
    except Exception:
        model = None
    if isinstance(model, Mapping):
        model_id = model.get("id", model.get("name"))
        model_sha = model.get("sha256", model.get("model_sha256"))
        if model_id is not None or model_sha is not None:
            response["model"] = {
                "id": str(model_id) if model_id is not None else "unknown",
                "sha256": str(model_sha) if model_sha is not None else "unknown",
            }
    return response


def _score_with_request(scorer: Scorer, request: ScoreRequest) -> Sequence[Score]:
    """Call old two-argument test/fallback scorers as well as mode-aware ones."""

    try:
        signature = inspect.signature(scorer.score)
        parameters = signature.parameters.values()
        supports_mode = "candidate_mode" in signature.parameters or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters
        )
    except (TypeError, ValueError):
        supports_mode = True
    if supports_mode:
        return scorer.score(
            request.context,
            request.candidates,
            candidate_mode=request.candidate_mode,
        )
    return scorer.score(request.context, request.candidates)


class StubScorer:
    """Deterministic backend used by protocol tests and smoke checks."""

    _fingerprint = hashlib.sha256(b"mohu-qwen35-stub\n").hexdigest()

    def score(
        self,
        context: str,
        candidates: Sequence[str],
        *,
        candidate_mode: str = "suffix",
    ) -> Sequence[Score]:
        # A length-normalized deterministic value is enough to exercise the
        # transport without pretending to be a language model.
        del context, candidate_mode
        return tuple(Score(-float(len(candidate)), len(candidate)) for candidate in candidates)

    def health(self) -> Mapping[str, Any]:
        return {
            "status": "ok",
            "ready": True,
            "model": {
                "id": "stub",
                "name": "stub",
                "revision": "test",
                "sha256": self._fingerprint,
                "model_sha256": self._fingerprint,
            },
            "build": _runtime_build_metadata("stub"),
        }


def _runtime_build_metadata(mlx_version: str | None = None) -> dict[str, Any]:
    version = mlx_version
    if version is None:
        try:
            from importlib.metadata import version as package_version

            version = package_version("mlx-lm")
        except Exception:
            version = "unavailable"
    return {
        "implementation": "mohu-qwen35-mlx",
        "mlx_lm": str(version),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "pid": os.getpid(),
    }


def _iter_model_files(model_dir: Path) -> Iterable[tuple[Path, str]]:
    """Yield deterministic, regular files while excluding cache internals."""

    root = model_dir.resolve()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or ".cache" in path.parts:
            continue
        try:
            relative = path.relative_to(root)
        except ValueError:
            # ``rglob`` should only return descendants; retain the guard for
            # unusual filesystem implementations.
            continue
        yield path, relative.as_posix()


def compute_model_fingerprint(model_dir: str | os.PathLike[str]) -> str:
    """Compute a stable SHA-256 over model files and their relative names.

    The hash is intentionally content-based (rather than a directory name or
    Hugging Face revision) so a health response can identify a partially
    replaced checkpoint.  The load boundary recomputes it if health metadata
    was collected earlier.
    """

    root = Path(model_dir)
    digest = hashlib.sha256()
    for path, relative in _iter_model_files(root):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        size = path.stat().st_size
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except FileNotFoundError as exc:
        raise IncompleteModelError(f"missing {label}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise IncompleteModelError(f"invalid {label}") from exc
    if not isinstance(value, dict):
        raise IncompleteModelError(f"invalid {label}")
    return value


def validate_model_directory(
    model_dir: str | os.PathLike[str], spec: ModelSpec | None = None
) -> dict[str, Any]:
    """Validate the local files needed by mlx-lm before allocating the model.

    Without ``spec`` the check stays format-focused: any registered model
    family is acceptable.  The production load path passes the exact spec so
    the checkpoint cannot silently masquerade as a different registered model.
    """

    root = Path(model_dir)
    if not root.is_dir():
        raise IncompleteModelError("model directory does not exist")
    config = _read_json(root / "config.json", "config.json")
    model_type = config.get("model_type")
    if spec is not None:
        if model_type != spec.model_type:
            raise UnsupportedModelError(f"config model_type must be {spec.model_type}")
        is_vlm = spec.is_vlm
    else:
        resolved = _REGISTERED_MODEL_TYPES.get(model_type)
        if resolved is None:
            registered = ", ".join(sorted(_REGISTERED_MODEL_TYPES))
            raise UnsupportedModelError(
                f"config model_type must be one of the registered families: {registered}"
            )
        is_vlm = resolved.is_vlm
    if is_vlm:
        text_config = config.get("text_config")
        if not isinstance(text_config, dict):
            raise UnsupportedModelError("Qwen3.5 VLM text_config is missing")

    index_path = root / "model.safetensors.index.json"
    weight_files = sorted(root.glob("model*.safetensors"))
    if not weight_files:
        raise IncompleteModelError("no safetensors weights found")
    expected_files: set[Path] = set(weight_files)
    if index_path.exists():
        index = _read_json(index_path, "model.safetensors.index.json")
        weight_map = index.get("weight_map")
        if not isinstance(weight_map, dict) or not weight_map:
            raise IncompleteModelError("safetensors index has no weight_map")
        expected_files = set()
        for filename in weight_map.values():
            if not isinstance(filename, str):
                raise IncompleteModelError("safetensors index contains an invalid file")
            relative = Path(filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise IncompleteModelError("safetensors index escapes model directory")
            candidate = root / relative
            expected_files.add(candidate)
        missing = [path.name for path in sorted(expected_files) if not path.is_file()]
        if missing:
            raise IncompleteModelError("safetensors shard is missing")
        if any(path.stat().st_size <= 0 for path in expected_files):
            raise IncompleteModelError("safetensors shard is empty")
        metadata = index.get("metadata")
        expected_size = metadata.get("total_size") if isinstance(metadata, dict) else None
        if isinstance(expected_size, int) and expected_size > 0:
            actual_size = sum(path.stat().st_size for path in expected_files)
            if actual_size < expected_size:
                raise IncompleteModelError("safetensors shards are incomplete")
    elif any(path.stat().st_size <= 0 for path in expected_files):
        raise IncompleteModelError("safetensors weight is empty")

    tokenizer_candidates = (
        root / "tokenizer.json",
        root / "tokenizer.model",
        root / "vocab.json",
    )
    if not any(path.is_file() and path.stat().st_size > 0 for path in tokenizer_candidates):
        raise IncompleteModelError("tokenizer files are missing")
    if not (root / "tokenizer_config.json").is_file():
        raise IncompleteModelError("tokenizer_config.json is missing")
    return config


def _resolve_model_spec(model_reference: str) -> ModelSpec:
    """Pin a model reference to exactly one registered checkpoint.

    A reference matches either the canonical Hugging Face repo id or a local
    directory whose own (or a resolved ancestor's) name is the canonical
    basename or the hub cache directory ``models--<org>--<name>``.  Anything
    else is rejected before any weight is read.
    """

    if _is_hf_repo_reference(model_reference):
        for spec in MODEL_SPECS:
            if model_reference == spec.repo_id:
                return spec
        raise UnsupportedModelError("model is not a registered checkpoint")
    reference = Path(model_reference).expanduser()
    names = [reference.name]
    try:
        resolved = reference.resolve()
        names.append(resolved.name)
        names.extend(parent.name for parent in resolved.parents)
    except OSError:
        pass
    names.extend(parent.name for parent in reference.parents)
    for name in names:
        for spec in MODEL_SPECS:
            cache_directory = "models--" + spec.repo_id.replace("/", "--")
            if name == spec.basename or name == cache_directory:
                return spec
    raise UnsupportedModelError("model is not a registered checkpoint")


def _validate_production_quantization(
    config: Mapping[str, Any],
    *,
    model_reference: str,
    spec: ModelSpec | None = None,
) -> None:
    """Reject checkpoints other than the pinned 4-bit production models.

    Some older test fixtures omit quantization metadata, so the low-level
    directory validator remains format-focused.  The production load path is
    strict: explicit metadata must say 4-bit, and metadata-free local paths
    must use a registered canonical 4-bit directory name.  Hugging Face
    references are pinned to the canonical repository before download.
    """

    if spec is None:
        spec = _resolve_model_spec(model_reference)

    if _is_hf_repo_reference(model_reference):
        if model_reference != spec.repo_id:
            raise UnsupportedModelError("model is not a registered checkpoint")
    elif Path(model_reference).name != spec.basename:
        # A local checkpoint with no quantization declaration is ambiguous and
        # must not silently run as bf16 or another unsupported format.
        quantization = config.get("quantization")
        quantization_config = config.get("quantization_config")
        if not isinstance(quantization, Mapping) and not isinstance(quantization_config, Mapping):
            raise UnsupportedModelError("model quantization is unknown")

    declarations = [config.get("quantization"), config.get("quantization_config")]
    present = [item for item in declarations if isinstance(item, Mapping)]
    if not present:
        if Path(model_reference).name != spec.basename:
            raise UnsupportedModelError("model quantization is unknown")
        return
    for declaration in present:
        bits = declaration.get("bits")
        if isinstance(bits, bool) or not isinstance(bits, Integral) or bits != 4:
            raise UnsupportedModelError("only 4-bit model quantization is supported")


def _discover_revision(model_dir: Path) -> str:
    """Find a Hugging Face snapshot ref when the local cache exposes one."""

    # A snapshot directory normally looks like .../snapshots/<commit> and a
    # sibling refs/main file contains the same commit.  Check both forms.
    if re.fullmatch(r"[0-9a-f]{7,64}", model_dir.name, re.IGNORECASE):
        return model_dir.name
    for ancestor in (model_dir, *model_dir.parents):
        for ref_name in ("main", "master"):
            ref = ancestor / "refs" / ref_name
            try:
                value = ref.read_text(encoding="ascii").strip()
            except (OSError, UnicodeDecodeError):
                continue
            if re.fullmatch(r"[0-9a-f]{7,64}", value, re.IGNORECASE):
                return value
    for filename in (".revision", "revision.txt"):
        try:
            value = (model_dir / filename).read_text(encoding="ascii").strip()
        except (OSError, UnicodeDecodeError):
            continue
        if value:
            return value
    return "unknown"


def _is_hf_repo_reference(value: str) -> bool:
    return (
        "/" in value
        and not value.startswith(("/", "./", "../", "~"))
        and not re.match(r"^[A-Za-z]:[\\/]", value)
    )


def _resolve_model_reference(reference: str, revision: str | None) -> Path:
    del revision
    path = Path(reference).expanduser()
    if path.exists():
        return path
    if _is_hf_repo_reference(reference):
        raise ModelUnavailableError("model checkpoint must be installed locally")
    raise IncompleteModelError("model path does not exist")


class MLXScorer:
    """Batched conditional log-likelihood scorer backed by mlx-lm.

    ``mlx_lm`` and the model are imported/loaded only inside ``_ensure_loaded``
    so protocol tests and installations without MLX continue to work.  Calls
    are serialized because one MLX model instance is shared by all socket
    clients; each request still uses exactly one batched forward pass.
    """

    def __init__(
        self,
        model: str | os.PathLike[str] = DEFAULT_MODEL,
        *,
        revision: str | None = None,
        expected_fingerprint: str | None = None,
        max_context_tokens: int = 256,
        max_candidate_tokens: int = 128,
        cache_size: int = 256,
        use_gpu: bool = True,
    ) -> None:
        if max_context_tokens < 0 or max_candidate_tokens < 1:
            raise ValueError("token limits must be non-negative and positive")
        if cache_size < 0:
            raise ValueError("cache_size must be non-negative")
        self.model_reference = str(model)
        self.configured_revision = revision
        if expected_fingerprint is not None:
            _validate_expected_fingerprint(expected_fingerprint, expected_fingerprint)
        self.expected_fingerprint = expected_fingerprint
        self.max_context_tokens = max_context_tokens
        self.max_candidate_tokens = max_candidate_tokens
        self.cache_size = cache_size
        self.use_gpu = use_gpu
        self._model: Any = None
        self._tokenizer: Any = None
        self._config: dict[str, Any] | None = None
        self._model_dir: Path | None = None
        self._model_spec: ModelSpec | None = None
        self._load_error: ScorerError | None = None
        self._load_lock = threading.Lock()
        self._metadata_lock = threading.Lock()
        self._score_lock = threading.Lock()
        self._cache: OrderedDict[tuple[str, tuple[str, ...], str], tuple[Score, ...]] = (
            OrderedDict()
        )
        self._fingerprint: str | None = None
        self._revision = revision or "unknown"
        self._mlx_version = "unavailable"
        self._warmed = False
        self._build_metadata: dict[str, Any] | None = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        if self._load_error is not None:
            raise self._load_error
        with self._load_lock:
            if self._model is not None:
                return
            if self._load_error is not None:
                raise self._load_error
            try:
                model_dir = _resolve_model_reference(self.model_reference, self.configured_revision)
                spec = _resolve_model_spec(self.model_reference)
                config = validate_model_directory(model_dir, spec)
                _validate_production_quantization(
                    config, model_reference=self.model_reference, spec=spec
                )
                # Recompute at the load boundary.  A prior health call may have
                # cached metadata while the checkpoint is being replaced.
                fingerprint = compute_model_fingerprint(model_dir)
                _validate_expected_fingerprint(fingerprint, self.expected_fingerprint)
                import mlx.core as mx
                import mlx_lm

                self._mlx_version = str(getattr(mlx_lm, "__version__", "unknown"))
                with self._metadata_lock:
                    self._build_metadata = None
                if self.use_gpu:
                    try:
                        mx.set_default_device(mx.gpu)
                    except Exception:
                        # CPU-only MLX builds have no GPU device; loading still
                        # works and the health response reports the architecture.
                        pass
                else:
                    # MLX defaults to the GPU on Apple Silicon; skipping the
                    # GPU call alone would not actually select the CPU device.
                    try:
                        mx.set_default_device(mx.cpu)
                    except Exception:
                        pass
                loaded = mlx_lm.load(
                    str(model_dir),
                    lazy=False,
                    return_config=True,
                )
                if not isinstance(loaded, tuple) or len(loaded) != 3:
                    raise ModelUnavailableError("mlx-lm returned an invalid model tuple")
                model_obj, tokenizer, loaded_config = loaded
                if not hasattr(model_obj, "__call__"):
                    raise ModelUnavailableError("mlx-lm returned an invalid model")
                if spec.is_vlm:
                    # qwen3_5.Model is a VLM wrapper whose __call__ delegates to
                    # language_model; checking this catches accidentally loaded
                    # vision-only or incompatible custom checkpoints early.
                    if not hasattr(model_obj, "language_model"):
                        raise UnsupportedModelError(
                            "Qwen3.5 text language model branch is unavailable"
                        )
                elif not hasattr(model_obj, "model"):
                    raise UnsupportedModelError("model text transformer branch is unavailable")
                model_obj.eval()
                self._tokenizer = tokenizer
                self._config = dict(loaded_config or config)
                self._model_dir = model_dir
                self._model_spec = spec
                self._fingerprint = fingerprint
                self._revision = self.configured_revision or _discover_revision(model_dir)
                # Publish the model last so a concurrent health request never
                # observes ``ready`` before tokenizer/config metadata exists.
                self._model = model_obj
            except ScorerError as exc:
                self._load_error = exc
                raise
            except Exception as exc:
                error = ModelUnavailableError("unable to load the MLX model")
                self._load_error = error
                raise error from exc

    @staticmethod
    def _encode(tokenizer: Any, text: str, *, special: bool) -> list[int]:
        try:
            encoded = tokenizer.encode(text, add_special_tokens=special)
        except TypeError:
            encoded = tokenizer.encode(text)
        if hasattr(encoded, "tolist"):
            encoded = encoded.tolist()
        if not isinstance(encoded, (list, tuple)):
            raise ModelUnavailableError("tokenizer returned an invalid token sequence")
        result: list[int] = []
        for token in encoded:
            if isinstance(token, bool) or not isinstance(token, Integral) or token < 0:
                raise ModelUnavailableError("tokenizer returned an invalid token id")
            result.append(int(token))
        return result

    def _boundary_token(self) -> int | None:
        tokenizer = self._tokenizer
        for name in ("bos_token_id", "pad_token_id", "eos_token_id"):
            value = getattr(tokenizer, name, None)
            if isinstance(value, Integral) and not isinstance(value, bool) and value >= 0:
                return value
        return None

    def _prepare_batch(
        self,
        context: str,
        candidates: Sequence[str],
        *,
        candidate_mode: str = "suffix",
    ) -> tuple[list[list[int]], list[list[float]], int]:
        if candidate_mode not in ("suffix", "complete"):
            raise ProtocolError("candidate_mode must be 'suffix' or 'complete'")
        if not 1 <= len(candidates) <= MAX_REQUEST_CANDIDATES:
            raise ProtocolError("candidates must contain 1..20 items")
        assert self._tokenizer is not None
        original_context_tokens = self._encode(self._tokenizer, context, special=False)
        if len(original_context_tokens) > self.max_context_tokens:
            raise ProtocolError("context token count exceeds the maximum")
        context_tokens = original_context_tokens
        if not context_tokens:
            boundary = self._boundary_token()
            if boundary is not None:
                context_tokens = [boundary]

        rows: list[list[int]] = []
        masks: list[list[float]] = []
        row_prefix_lengths: list[int] = []
        candidate_lengths: list[int] = []
        for candidate in candidates:
            tokens = self._encode(self._tokenizer, candidate, special=False)
            if not tokens:
                raise ProtocolError("candidate tokenization produced no tokens")
            if len(tokens) > self.max_candidate_tokens:
                raise ProtocolError("candidate token count exceeds the maximum")

            prefix = context_tokens
            candidate_tokens = tokens
            if candidate_mode == "complete":
                boundary = self._boundary_token()
                if original_context_tokens and candidate.startswith(context):
                    # Encode the known prefix and unseen suffix separately.
                    # Fast/BPE tokenizers are allowed to merge a token across
                    # the character boundary when the whole candidate is
                    # encoded; using that merged token would charge prefix
                    # likelihood to one candidate but not another.  Separate
                    # encoding makes the comparison explicitly conditional on
                    # the same context for every candidate.
                    suffix_text = candidate[len(context) :]
                    suffix_tokens = self._encode(self._tokenizer, suffix_text, special=False)
                    if suffix_tokens:
                        prefix = context_tokens
                        candidate_tokens = suffix_tokens
                    else:
                        # A candidate equal to the context has no unseen
                        # continuation.  Score it independently so the wire
                        # contract still receives a positive token count.
                        prefix = [boundary] if boundary is not None else []
                        candidate_tokens = tokens
                else:
                    # Complete candidates not beginning with the stated context
                    # are scored independently instead of duplicating context.
                    prefix = [boundary] if boundary is not None else []
            # The split suffix is encoded independently, so its token count
            # must be checked after the split as well as before it.
            if len(candidate_tokens) > self.max_candidate_tokens:
                raise ProtocolError("candidate token count exceeds the maximum")
            row = prefix + candidate_tokens
            rows.append(row)
            row_prefix_lengths.append(len(prefix))
            candidate_lengths.append(len(candidate_tokens))

        max_len = max(len(row) for row in rows)
        # Right padding keeps real causal positions independent from padding,
        # including Qwen3.5's recurrent linear-attention layers.
        pad_id = getattr(self._tokenizer, "pad_token_id", None)
        if not isinstance(pad_id, int) or isinstance(pad_id, bool) or pad_id < 0:
            pad_id = self._boundary_token() or 0
        for row, prefix_length, candidate_length in zip(
            rows, row_prefix_lengths, candidate_lengths
        ):
            row.extend([pad_id] * (max_len - len(row)))
            mask = [0.0] * max(0, max_len - 1)
            start = prefix_length - 1
            if start < 0:
                start = 0
            # There are max_len-1 logit positions.  A target at global index k
            # is predicted by the logit at k-1.
            for position in range(start, min(start + candidate_length, len(mask))):
                mask[position] = 1.0
            masks.append(mask)

        return rows, masks, len(context_tokens)

    @staticmethod
    def _pad_short_batch(
        rows: list[list[int]], masks: list[list[float]]
    ) -> tuple[list[list[int]], list[list[float]]]:
        """Pad requests to a stable kernel row shape without changing wire count.

        Five rows are the low-latency shape for the normal shortlist.  Once an
        adaptive request exceeds five candidates, use the full twenty-row
        shape so expanding from 8/12/20 does not switch quantized matmul paths.
        """

        if not rows or len(rows) != len(masks):
            raise ModelUnavailableError("MLX received an invalid batch")
        if len(rows) > MAX_REQUEST_CANDIDATES:
            raise ProtocolError("candidates must contain 1..20 items")
        target_rows = (
            FIXED_BATCH_ROWS if len(rows) <= FIXED_BATCH_ROWS else MAX_REQUEST_CANDIDATES
        )
        if len(rows) == target_rows:
            return rows, masks
        max_len = len(rows[0])
        template_row = list(rows[0])
        template_mask = [0.0] * max(0, max_len - 1)
        padded_rows = [list(row) for row in rows]
        padded_masks = [list(mask) for mask in masks]
        while len(padded_rows) < target_rows:
            padded_rows.append(list(template_row))
            padded_masks.append(list(template_mask))
        return padded_rows, padded_masks

    @staticmethod
    def _pad_sequence_rows(
        rows: list[list[int]],
        masks: list[list[float]],
        *,
        pad_id: int,
        min_length: int = MIN_SCORING_SEQUENCE_TOKENS,
    ) -> tuple[list[list[int]], list[list[float]]]:
        """Right-pad short sequences to reduce quantized shape switching."""

        if not rows or len(rows) != len(masks):
            raise ModelUnavailableError("MLX received an invalid sequence batch")
        if isinstance(pad_id, bool) or not isinstance(pad_id, int) or pad_id < 0:
            raise ModelUnavailableError("MLX received an invalid padding token")
        if isinstance(min_length, bool) or not isinstance(min_length, int) or min_length < 2:
            raise ValueError("min_length must be an integer >= 2")
        max_length = max(len(row) for row in rows)
        target_length = max(max_length, min_length)
        padded_rows = [list(row) + [pad_id] * (target_length - len(row)) for row in rows]
        padded_masks = [
            list(mask) + [0.0] * max(0, target_length - 1 - len(mask)) for mask in masks
        ]
        return padded_rows, padded_masks

    def _score_batch(self, rows: list[list[int]], masks: list[list[float]]) -> tuple[Score, ...]:
        assert self._model is not None
        try:
            import mlx.core as mx

            # MLX's quantized kernels can choose a different numerical path
            # for different batch dimensions.  Short requests use a stable
            # five-row shape; adaptive requests use a stable twenty-row shape
            # so expanding from 8/12/20 does not switch row tiling paths.
            # Zero masks ensure padding rows never contribute a score;
            # ``score`` trims them back before returning.
            rows, masks = self._pad_short_batch(rows, masks)

            pad_id = getattr(self._tokenizer, "pad_token_id", None)
            if not isinstance(pad_id, int) or isinstance(pad_id, bool) or pad_id < 0:
                pad_id = self._boundary_token() or 0
            rows, masks = self._pad_sequence_rows(rows, masks, pad_id=pad_id)

            input_ids = mx.array(rows, dtype=mx.int32)
            if input_ids.shape[1] <= 1:
                raise ModelUnavailableError("MLX produced no predictable candidate token")

            # Most Qwen3.5 checkpoints tie the output embedding to the input
            # embedding.  Run the text transformer once, then project only the
            # positions that predict candidate tokens.  This avoids a costly
            # vocab projection for every context/padding position.  Older or
            # custom MLX models fall back to their regular full-logit call.
            active_positions = [
                [position for position, value in enumerate(mask) if value > 0] for mask in masks
            ]
            max_targets = max(
                MIN_SCORING_TARGET_TOKENS,
                max((len(positions) for positions in active_positions), default=0),
            )
            if max_targets == 0:
                raise ModelUnavailableError("MLX produced no candidate token positions")
            position_rows: list[list[int]] = []
            position_masks: list[list[float]] = []
            for positions in active_positions:
                fallback_position = positions[-1] if positions else 0
                position_rows.append(
                    positions + [fallback_position] * (max_targets - len(positions))
                )
                position_masks.append(
                    [1.0] * len(positions) + [0.0] * (max_targets - len(positions))
                )

            # Qwen3.5 checkpoints nest the text model under ``language_model``
            # (VLM wrapper); plain text checkpoints expose it directly.
            language_model = getattr(self._model, "language_model", None)
            if language_model is None:
                language_model = self._model
            text_model = getattr(language_model, "model", None)
            embedding = getattr(text_model, "embed_tokens", None)
            tie_word_embeddings = bool(
                getattr(
                    getattr(language_model, "args", None),
                    "tie_word_embeddings",
                    False,
                )
            )
            lm_head = getattr(language_model, "lm_head", None)
            use_selected_projection = bool(
                text_model is not None
                and (
                    (tie_word_embeddings and hasattr(embedding, "as_linear")) or lm_head is not None
                )
            )
            if use_selected_projection:
                hidden = text_model(input_ids)
                positions = mx.array(position_rows, dtype=mx.int32)
                selected_hidden = mx.take_along_axis(
                    hidden,
                    positions[..., None],
                    axis=1,
                )
                if tie_word_embeddings:
                    logits = embedding.as_linear(selected_hidden)
                else:
                    logits = lm_head(selected_hidden)
                targets = mx.take_along_axis(input_ids, positions + 1, axis=1)
            else:
                logits = self._model(input_ids)
                if isinstance(logits, tuple):
                    logits = logits[0]
                elif hasattr(logits, "logits"):
                    logits = logits.logits
                if not hasattr(logits, "shape") or len(logits.shape) != 3:
                    raise ModelUnavailableError("model returned invalid logits")
                next_logits = logits[:, :-1, :]
                targets = input_ids[:, 1:]
                # Keep only the requested positions in the fallback path too.
                positions = mx.array(position_rows, dtype=mx.int32)
                logits = mx.take_along_axis(
                    next_logits,
                    positions[..., None],
                    axis=1,
                )
                targets = mx.take_along_axis(input_ids, positions + 1, axis=1)

            # ``logits`` now has shape [batch, requested_targets, vocab].  Gather
            # target logits and compute logsumexp without materializing a full
            # log-probability tensor.
            target_logits = mx.take_along_axis(
                logits,
                targets[..., None],
                axis=-1,
            ).squeeze(-1)
            log_norm = mx.logsumexp(logits.astype(mx.float32), axis=-1)
            token_logp = target_logits.astype(mx.float32) - log_norm
            mask_array = mx.array(position_masks, dtype=mx.float32)
            sums = mx.sum(token_logp * mask_array, axis=1)
            counts = mx.sum(mask_array, axis=1)
            # MLX is lazy; explicitly evaluate before crossing into Python.
            mx.eval(sums, counts)
            sum_values = sums.tolist()
            count_values = counts.tolist()
        except ScorerError:
            raise
        except Exception as exc:
            raise ModelUnavailableError("MLX forward pass failed") from exc

        import math

        result: list[Score] = []
        for total, count in zip(sum_values, count_values):
            total_float = float(total)
            count_int = int(count)
            if count_int == 0:
                # Internal fixed-batch padding rows are deliberately masked
                # out.  They are trimmed before a score response is emitted.
                result.append(Score(0.0, 0))
                continue
            if not math.isfinite(total_float) or count_int < 0:
                raise ModelUnavailableError("MLX returned invalid token counts")
            result.append(Score(total_float, count_int))
        return tuple(result)

    def _warmup(self) -> None:
        if self._warmed:
            return
        # Warm up the fixed five-row batch with representative sentence lengths.
        # This pays the common fast-path kernel compilation once in the service
        # process rather than on a later keypress.
        try:
            rows, masks, _ = self._prepare_batch(
                "", WARMUP_CANDIDATES, candidate_mode="complete"
            )
            self._score_batch(rows, masks)
        except Exception:
            # A warmup failure is surfaced by the real request, not hidden in
            # health; retaining ``_warmed=False`` allows a retry.
            return
        self._warmed = True
        # Also compile the internal twenty-row adaptive shape before publishing
        # the socket.  The second pass is best effort; a later request can
        # still compile an unusual sequence bucket under its full deadline.
        try:
            full_candidates = tuple(
                WARMUP_CANDIDATES[index % len(WARMUP_CANDIDATES)]
                for index in range(MAX_REQUEST_CANDIDATES)
            )
            rows, masks, _ = self._prepare_batch(
                "", full_candidates, candidate_mode="complete"
            )
            self._score_batch(rows, masks)
        except Exception:
            pass

    def score(
        self,
        context: str,
        candidates: Sequence[str],
        *,
        candidate_mode: str = "suffix",
    ) -> Sequence[Score]:
        # Reuse the same validation limits as the wire layer for direct users.
        request = validate_request(
            {
                "op": "score",
                "context": context,
                "candidate_mode": candidate_mode,
                "candidates": list(candidates),
            }
        )
        key = (request.context, request.candidates, request.candidate_mode)
        with self._score_lock:
            if self.cache_size:
                cached = self._cache.get(key)
                if cached is not None:
                    self._cache.move_to_end(key)
                    return cached
            self._ensure_loaded()
            self._warmup()
            rows, masks, _ = self._prepare_batch(
                request.context,
                request.candidates,
                candidate_mode=request.candidate_mode,
            )
            scores = self._score_batch(rows, masks)
            if len(scores) < len(request.candidates):
                raise ModelUnavailableError("MLX returned an incorrect score count")
            scores = scores[: len(request.candidates)]
            if self.cache_size:
                self._cache[key] = scores
                self._cache.move_to_end(key)
                while len(self._cache) > self.cache_size:
                    self._cache.popitem(last=False)
            return scores

    def health(self) -> Mapping[str, Any]:
        self._ensure_local_metadata()
        model_dir = self._model_dir
        fingerprint = self._fingerprint
        ready = self._model is not None
        model_metadata = {
            "id": self.model_reference,
            "name": self.model_reference,
            "revision": self._revision,
            "sha256": fingerprint or "unknown",
            "model_sha256": fingerprint or "unknown",
            "format": "mlx",
            "text_branch": self._model_spec.text_branch if self._model_spec else "unknown",
        }
        if model_dir is not None:
            model_metadata["path"] = str(model_dir)
        status = "ok" if ready else ("error" if self._load_error else "starting")
        result: dict[str, Any] = {
            "status": status,
            "ready": ready,
            "model": model_metadata,
            "build": self._runtime_build(),
        }
        if self._load_error is not None:
            result["error_code"] = self._load_error.code
        return result

    def _runtime_build(self) -> dict[str, Any]:
        # Build metadata is immutable for the lifetime of a scorer.  Avoid
        # repeating package/platform discovery on every health or score call.
        with self._metadata_lock:
            if self._build_metadata is None:
                self._build_metadata = _runtime_build_metadata(self._mlx_version)
            return dict(self._build_metadata)

    def _ensure_local_metadata(self) -> None:
        """Populate health metadata without downloading or loading weights."""

        with self._metadata_lock:
            if self._model_dir is not None and self._fingerprint is not None:
                return
            path = Path(self.model_reference).expanduser()
            if not path.is_dir():
                return
            self._model_dir = path
            if self._revision == "unknown":
                self._revision = self.configured_revision or _discover_revision(path)
            if self._fingerprint is None:
                try:
                    self._fingerprint = compute_model_fingerprint(path)
                except (OSError, ValueError):
                    self._fingerprint = None


class UnixScorerClient:
    """Persistent JSONL client with fail-fast reconnect behavior."""

    def __init__(self, socket_path: str = DEFAULT_SOCKET, timeout: float = 0.02):
        self.socket_path = socket_path
        self.timeout = max(0.001, float(timeout))
        self._socket: socket.socket | None = None
        self._reader: Any = None
        self._lock = threading.Lock()

    def _close_unlocked(self) -> None:
        reader, sock = self._reader, self._socket
        self._reader = None
        self._socket = None
        if reader is not None:
            try:
                reader.close()
            except OSError:
                pass
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def close(self) -> None:
        with self._lock:
            self._close_unlocked()

    def _connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.socket_path)
            self._socket = sock
            self._reader = sock.makefile("rb")
        except OSError:
            sock.close()
            raise

    def _request(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        encoded = (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
                "utf-8"
            )
            + b"\n"
        )
        with self._lock:
            last_error: Exception | None = None
            # A persistent peer may have reaped this connection while the
            # client was idle.  Reconnect once inside the same call so the
            # first post-idle composition does not silently fail-open.
            for attempt in range(2):
                if self._socket is None:
                    self._connect()
                assert self._socket is not None and self._reader is not None
                try:
                    self._socket.sendall(encoded)
                    line = self._reader.readline(MAX_JSON_LINE_BYTES + 1)
                    if not line:
                        # A peer that reaped an idle connection may close it
                        # cleanly after the send.  Treat EOF as reconnectable,
                        # while oversized/non-JSON frames remain hard errors.
                        raise ConnectionResetError("scorer peer closed the connection")
                    if len(line) > MAX_JSON_LINE_BYTES:
                        raise OSError("invalid scorer response frame")
                    response = json.loads(line.decode("utf-8"))
                    if not isinstance(response, dict):
                        raise OSError("invalid scorer response")
                    expected_request_id = payload.get("request_id")
                    if (
                        expected_request_id is not None
                        and response.get("request_id") != expected_request_id
                    ):
                        raise OSError("scorer response request id mismatch")
                    return response
                except (OSError, ValueError, UnicodeError, RecursionError) as exc:
                    # ``_request`` already owns ``_lock``; calling ``close``
                    # here would deadlock on a malformed/late response.
                    last_error = exc
                    self._close_unlocked()
                    retryable = isinstance(exc, (BrokenPipeError, ConnectionResetError))
                    if attempt == 1 or not retryable:
                        raise
            assert last_error is not None
            raise last_error

    def health(self) -> dict[str, Any]:
        return self._request({"op": "health", "request_id": uuid.uuid4().hex})

    def score(
        self,
        context: str,
        candidates: Sequence[str],
        *,
        request_id: str | None = None,
        candidate_mode: str = "suffix",
    ) -> tuple[Score, ...]:
        request_id = request_id or uuid.uuid4().hex
        response = self._request(
            {
                "op": "score",
                "request_id": request_id,
                "context": context,
                "candidate_mode": candidate_mode,
                "candidates": list(candidates),
            }
        )
        if not response.get("ok"):
            error = response.get("error")
            message = (
                error.get("message") if isinstance(error, Mapping) else "scorer request failed"
            )
            raise ScorerError(str(message))
        raw_scores = response.get("scores")
        if not isinstance(raw_scores, list) or len(raw_scores) != len(candidates):
            raise ScorerError("scorer returned an incorrect score count")
        parsed: list[Score] = []
        for raw in raw_scores:
            wire = _score_to_wire(raw)
            if wire is None:
                raise ScorerError("scorer returned invalid scores")
            parsed.append(Score(wire["sum_logp"], wire["predicted_tokens"]))
        return tuple(parsed)


class _UnixServer:
    """Thread-per-connection Unix JSONL server.

    MLX calls remain serialized by ``MLXScorer._score_lock`` while independent
    connections can still issue health requests and wait for their own frame.
    """

    def __init__(
        self,
        scorer: Scorer,
        socket_path: str,
        *,
        idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
        parent_pid: int | None = None,
        partial_frame_timeout: float = DEFAULT_PARTIAL_FRAME_TIMEOUT,
        connection_idle_timeout: float = DEFAULT_CONNECTION_IDLE_TIMEOUT,
        max_connections: int = DEFAULT_MAX_CONNECTIONS,
    ) -> None:
        self.scorer = scorer
        self.socket_path = socket_path
        self.idle_timeout = max(0.0, float(idle_timeout))
        self.parent_pid = parent_pid
        self.partial_frame_timeout = max(0.01, float(partial_frame_timeout))
        self.connection_idle_timeout = max(0.01, float(connection_idle_timeout))
        if isinstance(max_connections, bool) or not isinstance(max_connections, int) or max_connections < 1:
            raise ValueError("max_connections must be a positive integer")
        self.max_connections = max_connections
        self._stop = threading.Event()
        self._listener: socket.socket | None = None
        self._threads: set[threading.Thread] = set()
        self._threads_lock = threading.Lock()
        self._connection_slots = threading.BoundedSemaphore(max_connections)
        # A single MLX model is serialized.  Reject a concurrent score frame
        # immediately instead of letting it wait behind a request whose Rime
        # client may already have timed out.
        self._score_gate = threading.Lock()
        self._last_activity = time.monotonic()
        self._ready = threading.Event()

    def stop(self) -> None:
        self._stop.set()
        listener = self._listener
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass

    def wait_ready(self, timeout: float | None = None) -> bool:
        """Wait until the Unix socket is bound and accepting connections."""

        return self._ready.wait(timeout)

    def _connection(self, conn: socket.socket) -> None:
        partial_started: float | None = None
        last_activity = time.monotonic()
        try:
            conn.settimeout(1.0)
            buffer = bytearray()
            while not self._stop.is_set():
                now = time.monotonic()
                if partial_started is not None and now - partial_started >= self.partial_frame_timeout:
                    return
                if partial_started is None and now - last_activity >= self.connection_idle_timeout:
                    return
                if partial_started is not None:
                    wait_timeout = self.partial_frame_timeout - (now - partial_started)
                else:
                    wait_timeout = self.connection_idle_timeout - (now - last_activity)
                conn.settimeout(max(0.001, min(1.0, wait_timeout)))
                try:
                    chunk = conn.recv(64 * 1024)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                now = time.monotonic()
                last_activity = now
                if partial_started is None:
                    partial_started = now
                buffer.extend(chunk)
                # The Unix endpoint is deliberately JSONL-only.  HTTP is
                # available only through the separately bound diagnostic TCP
                # adapter, so a curl request cannot bypass the local protocol.
                if buffer.startswith((b"POST ", b"GET ")):
                    return
                while True:
                    newline = buffer.find(b"\n")
                    if newline < 0:
                        if len(buffer) > MAX_JSON_LINE_BYTES:
                            response = _error_response(ProtocolError("request frame is too large"))
                            try:
                                conn.sendall(_encode_json_line(response))
                            except OSError:
                                pass
                            return
                        break
                    line = bytes(buffer[:newline])
                    del buffer[: newline + 1]
                    self._last_activity = time.monotonic()
                    partial_started = time.monotonic() if buffer else None
                    if len(line) > MAX_JSON_LINE_BYTES:
                        response = _error_response(ProtocolError("request frame is too large"))
                    else:
                        try:
                            payload = json.loads(line.decode("utf-8"))
                        except (UnicodeDecodeError, ValueError, RecursionError, MemoryError):
                            response = _error_response(ProtocolError("request is not valid JSON"))
                        else:
                            if (
                                isinstance(payload, Mapping)
                                and payload.get("op", "score") == "score"
                            ):
                                request_id = payload.get("request_id", "")
                                if not self._score_gate.acquire(blocking=False):
                                    response = _error_response(
                                        ModelUnavailableError("scorer is busy"),
                                        _safe_request_id(request_id),
                                    )
                                else:
                                    try:
                                        response = handle_request(payload, self.scorer)
                                    finally:
                                        self._score_gate.release()
                            else:
                                response = handle_request(payload, self.scorer)
                    try:
                        encoded = _encode_json_line(response)
                        conn.sendall(encoded)
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        return
        finally:
            self._connection_slots.release()
            try:
                conn.close()
            except OSError:
                pass

    def _spawn_connection(self, conn: socket.socket) -> None:
        if not self._connection_slots.acquire(blocking=False):
            try:
                conn.close()
            except OSError:
                pass
            return
        thread = threading.Thread(target=self._connection, args=(conn,), daemon=True)
        with self._threads_lock:
            self._threads.add(thread)
        thread.start()

    def _prune_threads(self) -> None:
        with self._threads_lock:
            self._threads = {thread for thread in self._threads if thread.is_alive()}

    @staticmethod
    def _parent_alive(parent_pid: int) -> bool:
        if parent_pid <= 0:
            return True
        try:
            os.kill(parent_pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def serve_forever(self) -> None:
        socket_file = Path(self.socket_path).expanduser()
        if len(str(socket_file).encode("utf-8")) >= 104:
            raise ValueError("Unix socket path is too long")
        socket_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # ``mkdir(mode=...)`` does not change an existing directory.  Enforce
        # the private-socket boundary before binding so a pre-created 0755
        # parent cannot expose the endpoint to other users.
        os.chmod(socket_file.parent, 0o700)
        if socket_file.exists():
            if not socket_file.is_socket():
                raise FileExistsError("refusing to replace a non-socket path")
            socket_file.unlink()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener = listener
        try:
            listener.bind(str(socket_file))
            if self._stop.is_set():
                return
            os.chmod(socket_file, 0o600)
            listener.listen(16)
            self._ready.set()
            listener.settimeout(1.0)
            self._last_activity = time.monotonic()
            while not self._stop.is_set():
                if self.parent_pid is not None and not self._parent_alive(self.parent_pid):
                    break
                self._prune_threads()
                if (
                    self.idle_timeout > 0
                    and not self._threads
                    and time.monotonic() - self._last_activity >= self.idle_timeout
                ):
                    break
                try:
                    conn, _ = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if self._stop.is_set():
                        break
                    raise
                self._last_activity = time.monotonic()
                self._spawn_connection(conn)
        finally:
            self._stop.set()
            self._ready.clear()
            try:
                listener.close()
            except OSError:
                pass
            self._listener = None
            with self._threads_lock:
                threads = list(self._threads)
            for thread in threads:
                thread.join(timeout=1.0)
            try:
                if socket_file.is_socket():
                    socket_file.unlink()
            except OSError:
                pass


class _HttpHandler(http.server.BaseHTTPRequestHandler):
    server: "_HttpServer"

    def log_message(self, _format: str, *_args: Any) -> None:
        # Never write request bodies/candidate text to stderr or a log file.
        return

    def _send_json(self, status: int, payload: Mapping[str, Any]) -> None:
        try:
            body = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
        except (TypeError, ValueError):
            body = b'{"ok":false,"error":{"code":"serialization","message":"response failed"}}'
            status = 500
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/health":
            self._send_json(
                404, {"ok": False, "error": {"code": "not_found", "message": "not found"}}
            )
            return
        response = handle_request({"op": "health"}, self.server.scorer)
        self._send_json(200, response)

    def do_POST(self) -> None:
        if self.path not in ("/v1/rerank", "/score", "/v1/chat/completions"):
            self._send_json(
                404, {"ok": False, "error": {"code": "not_found", "message": "not found"}}
            )
            return
        content_length = self.headers.get("Content-Length")
        try:
            length = int(content_length or "-1")
        except ValueError:
            length = -1
        if length < 0 or length > MAX_JSON_LINE_BYTES:
            self._send_json(413, _error_response(ProtocolError("request body is too large")))
            return
        try:
            body = self.rfile.read(length)
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError, MemoryError):
            self._send_json(400, _error_response(ProtocolError("request is not valid JSON")))
            return
        if self.path == "/v1/chat/completions":
            payload = _unwrap_openai_payload(payload)
            if payload is None:
                self._send_json(400, _error_response(ProtocolError("request has no score payload")))
                return
        response = handle_request(payload, self.server.scorer)
        self._send_json(200 if response.get("ok") else 400, response)


def _unwrap_openai_payload(payload: Any) -> Mapping[str, Any] | None:
    """Accept the wrapper emitted by rime-llm-reranker's HTTP client.

    The adapter still returns the compact direct score response; callers can
    use the same validation path regardless of transport.
    """

    if isinstance(payload, Mapping) and "candidates" in payload:
        return payload
    if not isinstance(payload, Mapping):
        return None
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if not isinstance(message, Mapping) or message.get("role") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        try:
            decoded = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, MemoryError):
            continue
        if isinstance(decoded, Mapping) and "candidates" in decoded:
            # The reference rime-llm-reranker wrapper sends complete candidate
            # strings alongside a context prefix.  Preserve an explicit mode,
            # otherwise choose the complete interpretation for this endpoint.
            normalized = dict(decoded)
            normalized.setdefault("candidate_mode", "complete")
            return normalized
    return None


class _HttpServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], scorer: Scorer):
        self.scorer = scorer
        super().__init__(address, _HttpHandler)


def run_service(
    *,
    model: str = DEFAULT_MODEL,
    socket_path: str | None = DEFAULT_SOCKET,
    http_port: int = 0,
    revision: str | None = None,
    expected_sha256: str | None = None,
    warmup: bool = False,
    idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
    parent_pid: int | None = None,
    max_context_tokens: int = 256,
    max_candidate_tokens: int = 128,
    cache_size: int = 256,
    use_gpu: bool = True,
) -> None:
    scorer = MLXScorer(
        model,
        revision=revision,
        expected_fingerprint=expected_sha256,
        max_context_tokens=max_context_tokens,
        max_candidate_tokens=max_candidate_tokens,
        cache_size=cache_size,
        use_gpu=use_gpu,
    )
    stop = threading.Event()
    unix_server: _UnixServer | None = None
    http_server: _HttpServer | None = None
    http_thread: threading.Thread | None = None

    def shutdown(*_args: Any) -> None:
        stop.set()
        if unix_server is not None:
            unix_server.stop()
        if http_server is not None:
            http_server.shutdown()

    old_handlers: dict[int, Any] = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            old_handlers[signum] = signal.signal(signum, shutdown)
        except (ValueError, OSError):
            pass

    if warmup:
        # Compile MLX kernels before publishing the socket so the first Rime
        # request sees steady-state latency rather than cold-start latency.
        scorer.score("", ("暖",), candidate_mode="complete")
        print("qwen35 scorer model warmed", file=sys.stderr)

    if http_port:
        if not 1 <= http_port <= 65535:
            raise ValueError("http_port must be between 1 and 65535")
        http_server = _HttpServer(("127.0.0.1", http_port), scorer)
        http_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        http_thread.start()
        print(f"qwen35 scorer HTTP listening on 127.0.0.1:{http_port}", file=sys.stderr)

    try:
        if socket_path:
            unix_server = _UnixServer(
                scorer,
                socket_path,
                idle_timeout=idle_timeout,
                parent_pid=parent_pid,
            )
            print(f"qwen35 scorer socket listening on {socket_path}", file=sys.stderr)
            unix_server.serve_forever()
        elif http_server is not None:
            while not stop.wait(1.0):
                if parent_pid is not None and not _UnixServer._parent_alive(parent_pid):
                    break
    finally:
        shutdown()
        if http_thread is not None:
            http_thread.join(timeout=2.0)
        for signum, handler in old_handlers.items():
            try:
                signal.signal(signum, handler)
            except (ValueError, OSError):
                pass


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Qwen3.5-0.8B MLX sentence likelihood scorer")
    parser.add_argument(
        "--model",
        default=os.environ.get("MOHU_QWEN35_MODEL", DEFAULT_MODEL),
        help=f"local MLX checkpoint or Hugging Face repo (default: {DEFAULT_MODEL})",
    )
    parser.add_argument("--revision", default=os.environ.get("MOHU_QWEN35_REVISION"))
    parser.add_argument(
        "--expected-sha256",
        default=os.environ.get("MOHU_QWEN35_SHA256"),
        help="fail closed unless the local checkpoint has this SHA-256 fingerprint",
    )
    parser.add_argument(
        "--socket",
        default=os.environ.get("MOHU_QWEN35_SOCKET"),
        help="private Unix socket path (required unless --http-port is set)",
    )
    parser.add_argument(
        "--http-port",
        type=int,
        default=0,
        help="optional localhost HTTP adapter port; 0 disables it",
    )
    parser.add_argument(
        "--idle-timeout",
        type=float,
        default=DEFAULT_IDLE_TIMEOUT,
        help="shut down after this many idle seconds; 0 disables idle shutdown",
    )
    parser.add_argument("--parent-pid", type=int, default=None)
    parser.add_argument(
        "--warmup",
        action="store_true",
        help="load the checkpoint and compile one scoring pass before binding the socket",
    )
    parser.add_argument("--max-context-tokens", type=int, default=256)
    parser.add_argument("--max-candidate-tokens", type=int, default=128)
    parser.add_argument("--cache-size", type=int, default=256)
    parser.add_argument("--no-gpu", action="store_true", help="force the MLX CPU device")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    if not args.socket and not args.http_port:
        _build_arg_parser().error("provide --socket or --http-port")
    try:
        run_service(
            model=args.model,
            socket_path=args.socket,
            http_port=args.http_port,
            revision=args.revision,
            expected_sha256=args.expected_sha256,
            warmup=args.warmup,
            idle_timeout=args.idle_timeout,
            parent_pid=args.parent_pid,
            max_context_tokens=args.max_context_tokens,
            max_candidate_tokens=args.max_candidate_tokens,
            cache_size=args.cache_size,
            use_gpu=not args.no_gpu,
        )
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"qwen35 scorer failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
