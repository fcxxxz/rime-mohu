"""Isolated local scorer service for the semantic student (contract section 7).

Serves bounded, synchronous menu scoring over a private Unix socket (mode
0600 inside a 0700 per-user directory). Every request and response is
validated against ``qwen-semantic-rerank/v1`` via ``tools/qwen_semantic_rerank``;
responses contain exactly one score per supplied eligible index. The service
performs no network access, no model download, no text generation, and no
persistence of candidate or context content. Diagnostics are counts, timing
buckets, profile IDs, hashes, and error classes only.

This is an isolated development service; it is not wired into any Rime
schema, and the socket directory is caller-provided (no live user dir).

Usage:
  python scorer_service.py --onnx <dir> --vocab-from <checkpoint.pt> \
      --socket-dir <0700 dir> [--profile student-small-v1] [--deadline-ms 250]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import stat
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import numpy as np
import torch
from onnxruntime import InferenceSession

from tools.qwen_semantic_rerank import (
    FORMAT_VERSION,
    make_scoring_request,
    validate_menu_record,
    validate_scoring_response,
)

MAX_FRAME_BYTES = 1 << 20
MAX_CONTEXT = 96
MAX_CANDIDATE_CHARS = 16
MAX_CANDIDATES = 20
PAD, UNK = 0, 1


class ScorerError(Exception):
    def __init__(self, class_name: str) -> None:
        super().__init__(class_name)
        self.class_name = class_name


class SemanticScorer:
    """Loads one ONNX profile and scores validated protocol requests."""

    def __init__(self, onnx_path: Path, checkpoint_path: Path, profile: str):
        self.profile = profile
        checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
        self.vocab = checkpoint["vocab"]
        self.config = checkpoint["config"]
        self.session = InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
        self.onnx_sha256 = hashlib.sha256(onnx_path.read_bytes()).hexdigest()
        self.vocab_size = len(self.vocab)

    def score(self, request: dict) -> dict:
        menu = request.get("menu")
        if not isinstance(menu, dict):
            raise ScorerError("request_menu_missing")
        validated = validate_menu_record(menu)
        eligible = request.get("eligible_menu_indices")
        if not isinstance(eligible, list) or not eligible:
            raise ScorerError("request_eligible_missing")
        expected = [item["menu_index"] for item in validated["candidates"]
                    if item["consumes_current_input"] and not item["protected"]]
        if eligible != expected:
            raise ScorerError("request_eligible_mismatch")

        context = validated["committed_context"][:MAX_CONTEXT]
        context_ids = [self.vocab.get(char, UNK) for char in context]
        row = {
            "context_ids": np.zeros((1, MAX_CONTEXT), dtype=np.int64),
            "candidate_ids": np.zeros((1, MAX_CANDIDATES, MAX_CANDIDATE_CHARS), dtype=np.int64),
            "candidate_mask": np.zeros((1, MAX_CANDIDATES), dtype=bool),
            "native_rank": np.ones((1, MAX_CANDIDATES), dtype=np.int64),
            "native_score": np.zeros((1, MAX_CANDIDATES), dtype=np.float32),
            "syllable_count": np.ones((1, MAX_CANDIDATES), dtype=np.float32),
            "char_len": np.ones((1, MAX_CANDIDATES), dtype=np.float32),
            "has_score": np.ones((1, MAX_CANDIDATES), dtype=np.float32),
        }
        if context_ids:
            row["context_ids"][0, : len(context_ids)] = context_ids
        mean = self.config["score_mean"]
        std = self.config["score_std"]
        for column, candidate in enumerate(validated["candidates"][:MAX_CANDIDATES]):
            ids = [self.vocab.get(char, UNK) for char in candidate["text"][:MAX_CANDIDATE_CHARS]]
            if ids:
                row["candidate_ids"][0, column, : len(ids)] = ids
            row["candidate_mask"][0, column] = True
            row["native_rank"][0, column] = candidate["native_rank"]
            raw = candidate["native_score"]
            row["native_score"][0, column] = (raw - mean) / std if raw is not None else 0.0
            row["syllable_count"][0, column] = len(candidate["code_evidence"]["syllables"])
            row["char_len"][0, column] = len(candidate["text"])
        scores = self.session.run(["scores"], {key: value for key, value in row.items()})[0][0]

        response = {
            "format_version": FORMAT_VERSION,
            "protocol_checksum": request["protocol_checksum"],
            "model_profile": self.profile,
            "scores": [
                {"menu_index": index, "score": float(scores[index])}
                for index in eligible
                if index < len(scores)
            ],
        }
        return validate_scoring_response(response, request)

    def info(self) -> dict:
        return {
            "profile": self.profile,
            "onnx_sha256": self.onnx_sha256,
            "vocab_size": self.vocab_size,
            "format_version": FORMAT_VERSION,
        }


GATE_PROTOCOL = "mohu-semantic-gate/v1"


def handle_gate_line(scorer: SemanticScorer, raw: bytes) -> bytes | None:
    """Experimental tab-line client protocol for the Lua gate filter.

    ``SCORE2\\tcontext\\tK\\teng1..engK\\tcand1..candK\\n`` -> ``OK\\ts...\\n``

    SCORE2 carries the per-candidate native decode scores (from producer
    provenance) that the student was trained on; without them the model
    collapses (measured 0.9462 -> 0.0867 with a constant), so SCORE2 is the
    required form. Legacy ``SCORE\\tcontext\\tcands`` (constant-score) stays
    accepted only for compatibility probing and is not a quality path.
    """

    try:
        line = raw.decode("utf-8").rstrip("\r\n")
    except UnicodeDecodeError:
        return None
    fields = line.split("\t")

    if fields[0] == "SCORE2":
        if len(fields) < 5:
            return None
        try:
            count = int(fields[2])
        except ValueError:
            return None
        if count <= 0 or count > MAX_CANDIDATES or len(fields) != 3 + 2 * count:
            return None
        try:
            engine_scores = [float(value) for value in fields[3:3 + count]]
        except ValueError:
            return None
        if any(value != value for value in engine_scores):
            return None
        context = fields[1][:MAX_CONTEXT]
        texts = fields[3 + count:3 + 2 * count]
        raw_scores = engine_scores
    elif fields[0] == "SCORE" and len(fields) >= 3:
        context = fields[1][:MAX_CONTEXT]
        texts = fields[2:]
        if len(texts) > MAX_CANDIDATES:
            return None
        raw_scores = [0.0] * len(texts)
    else:
        return None

    context_ids = [scorer.vocab.get(char, UNK) for char in context]
    row = {
        "context_ids": np.zeros((1, MAX_CONTEXT), dtype=np.int64),
        "candidate_ids": np.zeros((1, MAX_CANDIDATES, MAX_CANDIDATE_CHARS), dtype=np.int64),
        "candidate_mask": np.zeros((1, MAX_CANDIDATES), dtype=bool),
        "native_rank": np.ones((1, MAX_CANDIDATES), dtype=np.int64),
        "native_score": np.zeros((1, MAX_CANDIDATES), dtype=np.float32),
        "syllable_count": np.ones((1, MAX_CANDIDATES), dtype=np.float32),
        "char_len": np.ones((1, MAX_CANDIDATES), dtype=np.float32),
        "has_score": np.ones((1, MAX_CANDIDATES), dtype=np.float32),
    }
    if context_ids:
        row["context_ids"][0, : len(context_ids)] = context_ids
    mean = scorer.config["score_mean"]
    std = scorer.config["score_std"]
    for column, text in enumerate(texts):
        ids = [scorer.vocab.get(char, UNK) for char in text[:MAX_CANDIDATE_CHARS]]
        if not ids:
            return None
        row["candidate_ids"][0, column, : len(ids)] = ids
        row["candidate_mask"][0, column] = True
        row["native_rank"][0, column] = column + 1
        row["native_score"][0, column] = (raw_scores[column] - mean) / std
    scores = scorer.session.run(["scores"], {key: value for key, value in row.items()})[0][0]
    parts = ["OK"] + [f"{scores[index]:.6f}" for index in range(len(texts))]
    return ("\t".join(parts) + "\n").encode("utf-8")


def read_frame(connection: socket.socket) -> bytes:
    header = connection.recv(4)
    if len(header) != 4:
        raise ScorerError("frame_header_truncated")
    length = int.from_bytes(header, "big")
    if length <= 0 or length > MAX_FRAME_BYTES:
        raise ScorerError("frame_length_invalid")
    payload = b""
    while len(payload) < length:
        chunk = connection.recv(length - len(payload))
        if not chunk:
            raise ScorerError("frame_body_truncated")
        payload += chunk
    return payload


def write_frame(connection: socket.socket, payload: bytes) -> None:
    if len(payload) > MAX_FRAME_BYTES:
        raise ScorerError("frame_response_too_large")
    connection.sendall(len(payload).to_bytes(4, "big") + payload)


def ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != 0o700:
        path.chmod(0o700)
    return path


def handle(connection: socket.socket, scorer: SemanticScorer, deadline_ms: int, stats: dict) -> None:
    try:
        with connection:
            raw = read_frame(connection)
            if raw.startswith(b"SCORE"):
                # Experimental gate protocol: single line request/response.
                reply = handle_gate_line(scorer, raw)
                if reply is None:
                    stats["error_gate_protocol"] = stats.get("error_gate_protocol", 0) + 1
                    write_frame(connection, b"ERR\n")
                else:
                    stats["gate_scored"] = stats.get("gate_scored", 0) + 1
                    write_frame(connection, reply)
                return
            started = time.perf_counter()
            try:
                request = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                stats["error_frame_json"] = stats.get("error_frame_json", 0) + 1
                write_frame(connection, json.dumps({"error": "frame_json_invalid"}).encode())
                return
            try:
                response = scorer.score(request)
            except ScorerError as exc:
                stats[f"error_{exc.class_name}"] = stats.get(f"error_{exc.class_name}", 0) + 1
                write_frame(connection, json.dumps({"error": exc.class_name}).encode())
                return
            except Exception:
                stats["error_internal"] = stats.get("error_internal", 0) + 1
                write_frame(connection, json.dumps({"error": "internal"}).encode())
                return
            elapsed_ms = (time.perf_counter() - started) * 1000
            bucket = "lt25" if elapsed_ms < 25 else ("lt100" if elapsed_ms < 100 else "ge100")
            write_frame(connection, json.dumps(response, ensure_ascii=False,
                                               separators=(",", ":")).encode())
            stats[f"latency_{bucket}"] = stats.get(f"latency_{bucket}", 0) + 1
            stats["scored"] = stats.get("scored", 0) + 1
    except (ConnectionResetError, BrokenPipeError, OSError):
        stats["error_connection"] = stats.get("error_connection", 0) + 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--onnx", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--socket-dir", type=Path, required=True)
    parser.add_argument("--profile", type=str, default="student-small-v1")
    parser.add_argument("--deadline-ms", type=int, default=250)
    parser.add_argument("--socket-name", type=str, default="semantic.sock")
    args = parser.parse_args()

    socket_dir = ensure_private_dir(args.socket_dir)
    socket_path = socket_dir / args.socket_name
    if socket_path.exists():
        socket_path.unlink()

    scorer = SemanticScorer(args.onnx, args.checkpoint, args.profile)
    stats: dict = {"scored": 0}
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    os.chmod(socket_path, 0o600)
    server.listen(8)
    print(json.dumps({"listening": str(socket_path), **scorer.info()}), flush=True)
    try:
        while True:
            connection, _ = server.accept()
            connection.settimeout(args.deadline_ms / 1000.0)
            worker = threading.Thread(target=handle, args=(connection, scorer, args.deadline_ms, stats),
                                      daemon=True)
            worker.start()
    finally:
        server.close()
        socket_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
