"""Contract tests for the local Qwen3.5 scorer service.

These tests intentionally avoid importing MLX model weights.  The optional
integration test at the bottom is enabled only when a local checkpoint is
provided through ``MOHU_QWEN35_MODEL``.
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from tiger_sentence_native.qwen35_scorer import (
    IncompleteModelError,
    ProtocolError,
    Score,
    StubScorer,
    UnixScorerClient,
    UnsupportedModelError,
    _build_arg_parser,
    _HttpServer,
    _UnixServer,
    _validate_expected_fingerprint,
    benchmark_scorer,
    compute_model_fingerprint,
    handle_request,
    summarize_latencies,
    validate_model_directory,
    validate_request,
)


class SupervisorLauncherContractTest(unittest.TestCase):
    def test_launcher_is_a_selection_supervisor(self) -> None:
        launcher = (
            Path(__file__).resolve().parents[1]
            / "tiger_sentence_native"
            / "run_qwen35_scorer.command"
        ).read_text(encoding="utf-8")
        self.assertIn("model-selection", launcher)
        self.assertIn("SCORER_MODEL_SHA", launcher)
        self.assertIn("poll_interval", launcher)
        self.assertIn("stop_child", launcher)
        self.assertNotIn("exec \"$python_bin\"", launcher)

    def test_registry_declares_default_selection(self) -> None:
        metadata = (
            Path(__file__).resolve().parents[1]
            / "tiger_sentence_native"
            / "scorer_models.zsh"
        ).read_text(encoding="utf-8")
        self.assertIn('SCORER_DEFAULT_MODEL="qwen35-0.8b"', metadata)


class RequestValidationTest(unittest.TestCase):
    def test_accepts_score_request_and_normalizes_defaults(self) -> None:
        request = validate_request(
            {
                "op": "score",
                "request_id": "42",
                "candidates": ["甲", "乙"],
            }
        )

        self.assertEqual(request.op, "score")
        self.assertEqual(request.request_id, "42")
        self.assertEqual(request.context, "")
        self.assertEqual(request.candidates, ("甲", "乙"))

    def test_accepts_raw_alias_used_by_lua_client(self) -> None:
        request = validate_request(
            {
                "request_id": "raw-1",
                "raw": "已提交",
                "normalize": "sum_logp",
                "candidates": ["候选"],
            }
        )
        self.assertEqual(request.context, "已提交")

    def test_prefers_context_text_over_raw_code_alias(self) -> None:
        request = validate_request(
            {
                "request_id": "raw-2",
                "context_text": "已提交",
                "raw": "vhrg1",
                "candidates": ["候选"],
            }
        )
        self.assertEqual(request.context, "已提交")

    def test_accepts_complete_candidate_mode(self) -> None:
        request = validate_request(
            {
                "op": "score",
                "context": "已经",
                "candidate_mode": "complete",
                "candidates": ["已经完成"],
            }
        )
        self.assertEqual(request.candidate_mode, "complete")

    def test_accepts_candidate_counts_through_wire_limit(self) -> None:
        for count in range(1, 21):
            with self.subTest(count=count):
                request = validate_request(
                    {"op": "score", "candidates": [f"candidate-{index}" for index in range(count)]}
                )
                self.assertEqual(len(request.candidates), count)

    def test_rejects_empty_or_too_many_candidates_without_echoing_text(self) -> None:
        with self.assertRaisesRegex(ProtocolError, r"1\.\.20"):
            validate_request({"op": "score", "candidates": []})
        with self.assertRaisesRegex(ProtocolError, r"1\.\.20"):
            validate_request({"op": "score", "candidates": ["x"] * 21})

        with self.assertRaises(ProtocolError) as raised:
            validate_request({"op": "score", "candidates": ["secret\ud800"]})
        self.assertNotIn("secret", str(raised.exception))

    def test_rejects_unknown_operation_and_oversized_context(self) -> None:
        with self.assertRaisesRegex(ProtocolError, "op"):
            validate_request({"op": "wat", "candidates": ["甲"]})
        with self.assertRaisesRegex(ProtocolError, "context"):
            validate_request({"op": "score", "context": "x" * 100_001, "candidates": ["甲"]})
        with self.assertRaisesRegex(ProtocolError, "version"):
            validate_request({"version": True, "candidates": ["甲"]})


class ProtocolHandlerTest(unittest.TestCase):
    def test_service_parser_exposes_preload_warmup(self) -> None:
        args = _build_arg_parser().parse_args(["--socket", "/tmp/qwen.sock", "--warmup"])
        self.assertTrue(args.warmup)

    def test_stub_handler_returns_aligned_finite_scores(self) -> None:
        response = handle_request(
            {
                "op": "score",
                "request_id": "r1",
                "context": "你好",
                "candidates": ["世界", "月球"],
            },
            StubScorer(),
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["request_id"], "r1")
        self.assertEqual(len(response["scores"]), 2)
        for score in response["scores"]:
            self.assertIsInstance(score["predicted_tokens"], int)
            self.assertIn("sum_logp", score)
            self.assertTrue(float(score["sum_logp"]) == float(score["sum_logp"]))

    def test_health_does_not_load_model_and_contains_build_metadata(self) -> None:
        response = handle_request({"op": "health"}, StubScorer())

        self.assertTrue(response["ok"])
        self.assertEqual(response["status"], "ok")
        self.assertIn("model", response)
        self.assertIn("build", response)
        self.assertIn("model_sha256", response["model"])
        self.assertEqual(response["model"]["id"], "stub")
        self.assertEqual(response["model"]["sha256"], response["model"]["model_sha256"])

    def test_protocol_error_response_never_includes_candidate_text(self) -> None:
        response = handle_request(
            {"op": "score", "candidates": ["private phrase", "\ud800"]},
            StubScorer(),
        )

        self.assertFalse(response["ok"])
        self.assertNotIn("private phrase", json.dumps(response, ensure_ascii=False))

    def test_health_rejects_non_finite_or_boolean_stub_scores(self) -> None:
        class BadScorer(StubScorer):
            def score(self, context: str, candidates: tuple[str, ...]):
                del context, candidates
                return [{"sum_logp": 0.0, "predicted_tokens": True}]

        response = handle_request(
            {"op": "score", "request_id": "bad", "candidates": ["甲"]},
            BadScorer(),
        )
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "scorer_error")

    def test_health_exception_is_an_error_response(self) -> None:
        class BrokenHealthScorer(StubScorer):
            def health(self):
                raise RuntimeError("private backend detail")

        response = handle_request(
            {"op": "health", "request_id": "health-bad"}, BrokenHealthScorer()
        )
        self.assertFalse(response["ok"])
        self.assertEqual(response["status"], "error")
        self.assertNotIn("private backend detail", json.dumps(response))

    def test_protocol_rejects_zero_predicted_tokens(self) -> None:
        class ZeroScorer(StubScorer):
            def score(
                self, context: str, candidates: tuple[str, ...], *, candidate_mode: str = "suffix"
            ):
                del context, candidates, candidate_mode
                return [Score(0.0, 0)]

        response = handle_request(
            {"op": "score", "request_id": "zero", "candidates": ["甲"]},
            ZeroScorer(),
        )
        self.assertFalse(response["ok"])

    def test_persistent_unix_client_round_trips_multiple_requests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = str(Path(directory) / "scorer.sock")
            server = _UnixServer(StubScorer(), socket_path, idle_timeout=5)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.assertTrue(server.wait_ready(2.0))
            client = UnixScorerClient(socket_path, timeout=1.0)
            try:
                first = client.score("", ("甲",), request_id="one")
                second = client.score("", ("乙", "丙"), request_id="two")
                twenty = client.score(
                    "",
                    tuple(f"候选{index}" for index in range(20)),
                    request_id="twenty",
                )
            finally:
                client.close()
                server.stop()
                thread.join(timeout=2)
            self.assertEqual(len(first), 1)
            self.assertEqual(len(second), 2)
            self.assertEqual(len(twenty), 20)
            self.assertFalse(thread.is_alive())

    def test_unix_server_hardens_an_existing_socket_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            parent.chmod(0o755)
            socket_path = str(parent / "permissions.sock")
            server = _UnixServer(StubScorer(), socket_path, idle_timeout=5)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.assertTrue(server.wait_ready(2.0))
            try:
                self.assertEqual(parent.stat().st_mode & 0o777, 0o700)
                self.assertEqual(Path(socket_path).stat().st_mode & 0o777, 0o600)
            finally:
                server.stop()
                thread.join(timeout=2)

    def test_persistent_unix_client_survives_idle_read_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = str(Path(directory) / "idle.sock")
            server = _UnixServer(StubScorer(), socket_path, idle_timeout=5)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.assertTrue(server.wait_ready(2.0))
            client = UnixScorerClient(socket_path, timeout=2.0)
            try:
                client._connect()
                time.sleep(1.2)
                scores = client.score("", ("甲",), request_id="after-idle")
                self.assertEqual(len(scores), 1)
            finally:
                client.close()
                server.stop()
                thread.join(timeout=2)

    def test_unix_socket_rejects_http_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = str(Path(directory) / "curl.sock")
            server = _UnixServer(StubScorer(), socket_path, idle_timeout=5)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.assertTrue(server.wait_ready(2.0))
            body = json.dumps(
                {"request_id": "curl-1", "raw": "", "candidates": ["甲"]},
                ensure_ascii=False,
            ).encode("utf-8")
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(2.0)
            try:
                client.connect(socket_path)
                client.sendall(
                    b"POST / HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\n"
                    + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode("ascii")
                    + body
                )
                chunks = []
                while chunk := client.recv(4096):
                    chunks.append(chunk)
                raw = b"".join(chunks)
            finally:
                client.close()
                server.stop()
                thread.join(timeout=2)
            self.assertNotIn(b"200", raw[:64])
            self.assertNotIn(b"curl-1", raw)

    def test_unix_server_returns_error_for_json_integer_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = str(Path(directory) / "huge-int.sock")
            server = _UnixServer(StubScorer(), socket_path, idle_timeout=5)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.assertTrue(server.wait_ready(2.0))
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(2.0)
            try:
                client.connect(socket_path)
                huge_integer = b"9" * 5000
                client.sendall(
                    b'{"request_id":' + huge_integer + b',"candidates":["\xe7\x94\xb2"]}\n'
                )
                response = client.recv(4096)
            finally:
                client.close()
                server.stop()
                thread.join(timeout=2)
            self.assertIn(b'"ok":false', response)
            self.assertIn(b"invalid", response)

    def test_unix_server_rejects_deep_json_without_dropping_connection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = str(Path(directory) / "deep-json.sock")
            server = _UnixServer(StubScorer(), socket_path, idle_timeout=5)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.assertTrue(server.wait_ready(2.0))
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(2.0)
            try:
                client.connect(socket_path)
                nested = b"[" * 3000 + b"0" + b"]" * 3000
                client.sendall(nested + b"\n")
                response = client.recv(4096)
            finally:
                client.close()
                server.stop()
                thread.join(timeout=2)
            self.assertIn(b'"ok":false', response)
            self.assertIn(b"invalid", response)

    def test_unix_server_returns_controlled_error_on_response_serialization_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = str(Path(directory) / "bad-response.sock")

            class BadHealthScorer(StubScorer):
                def health(self):
                    return {"ready": True, "model": {"bad": float("nan")}}

            server = _UnixServer(BadHealthScorer(), socket_path, idle_timeout=5)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.assertTrue(server.wait_ready(2.0))
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(2.0)
            try:
                client.connect(socket_path)
                client.sendall(b'{"op":"health","request_id":"bad-response"}\n')
                response = json.loads(client.recv(4096))
            finally:
                client.close()
                server.stop()
                thread.join(timeout=2)
            self.assertFalse(response["ok"])
            self.assertEqual("serialization", response["error"]["code"])

    def test_unix_server_expires_a_slow_partial_frame(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = str(Path(directory) / "slowloris.sock")
            server = _UnixServer(
                StubScorer(),
                socket_path,
                idle_timeout=5,
                partial_frame_timeout=0.05,
                connection_idle_timeout=0.2,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.assertTrue(server.wait_ready(2.0))
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(1.0)
            try:
                client.connect(socket_path)
                client.sendall(b'{"op":"score"')
                started = time.monotonic()
                try:
                    received = client.recv(1)
                except (ConnectionResetError, BrokenPipeError, OSError):
                    received = b""
                self.assertEqual(b"", received)
                self.assertLess(time.monotonic() - started, 0.5)
            finally:
                client.close()
                server.stop()
                thread.join(timeout=2)
            self.assertFalse(thread.is_alive())

    def test_persistent_client_reconnects_after_server_idle_reap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = str(Path(directory) / "reconnect.sock")
            server = _UnixServer(
                StubScorer(),
                socket_path,
                idle_timeout=5,
                connection_idle_timeout=0.05,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.assertTrue(server.wait_ready(2.0))
            client = UnixScorerClient(socket_path, timeout=1.0)
            try:
                client._connect()
                time.sleep(0.15)
                scores = client.score("", ("甲",), request_id="after-reap")
                self.assertEqual(len(scores), 1)
            finally:
                client.close()
                server.stop()
                thread.join(timeout=2)

    def test_unix_client_fails_cleanly_when_socket_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = UnixScorerClient(str(Path(directory) / "missing.sock"), timeout=0.05)
            with self.assertRaises(OSError):
                client.score("", ("甲",))

    def test_unix_client_deadline_is_bounded_against_slow_service(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = str(Path(directory) / "slow.sock")

            class SlowScorer(StubScorer):
                def score(
                    self,
                    context: str,
                    candidates: tuple[str, ...],
                    *,
                    candidate_mode: str = "suffix",
                ):
                    time.sleep(0.2)
                    return super().score(context, candidates, candidate_mode=candidate_mode)

            server = _UnixServer(SlowScorer(), socket_path, idle_timeout=5)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.assertTrue(server.wait_ready(2.0))
            client = UnixScorerClient(socket_path, timeout=0.05)
            started = time.monotonic()
            try:
                with self.assertRaises(OSError):
                    client.score("", ("甲",))
            finally:
                elapsed = time.monotonic() - started
                client.close()
                server.stop()
                thread.join(timeout=2)
            self.assertLess(elapsed, 0.15)

    def test_unix_server_rejects_overlapping_scores_without_queueing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = str(Path(directory) / "busy.sock")
            started = threading.Event()

            class SlowScorer(StubScorer):
                def score(
                    self,
                    context: str,
                    candidates: tuple[str, ...],
                    *,
                    candidate_mode: str = "suffix",
                ):
                    started.set()
                    time.sleep(0.2)
                    return super().score(context, candidates, candidate_mode=candidate_mode)

            server = _UnixServer(SlowScorer(), socket_path, idle_timeout=5)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.assertTrue(server.wait_ready(2.0))
            first = UnixScorerClient(socket_path, timeout=1.0)
            second = UnixScorerClient(socket_path, timeout=1.0)
            first_result: list[object] = []

            def run_first() -> None:
                try:
                    first_result.append(first.score("", ("甲",)))
                except Exception as error:  # pragma: no cover - diagnostic only
                    first_result.append(error)

            worker = threading.Thread(target=run_first, daemon=True)
            worker.start()
            self.assertTrue(started.wait(1.0))
            began = time.monotonic()
            try:
                with self.assertRaises(Exception):
                    second.score("", ("乙",))
            finally:
                elapsed = time.monotonic() - began
                first.close()
                second.close()
                server.stop()
                thread.join(timeout=2)
                worker.join(timeout=2)
            self.assertLess(elapsed, 0.15)
            self.assertEqual(len(first_result), 1)

    def test_busy_response_does_not_echo_an_oversized_request_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = str(Path(directory) / "busy-id.sock")
            started = threading.Event()

            class SlowScorer(StubScorer):
                def score(
                    self,
                    context: str,
                    candidates: tuple[str, ...],
                    *,
                    candidate_mode: str = "suffix",
                ):
                    started.set()
                    time.sleep(0.2)
                    return super().score(context, candidates, candidate_mode=candidate_mode)

            server = _UnixServer(SlowScorer(), socket_path, idle_timeout=5)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.assertTrue(server.wait_ready(2.0))
            first = UnixScorerClient(socket_path, timeout=1.0)
            worker = threading.Thread(
                target=lambda: first.score("", ("甲",), request_id="busy-first"), daemon=True
            )
            worker.start()
            self.assertTrue(started.wait(1.0))
            second = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            second.settimeout(2.0)
            try:
                second.connect(socket_path)
                request = {
                    "op": "score",
                    "request_id": "r" * 200_000,
                    "candidates": ["乙"],
                }
                second.sendall(
                    json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode()
                    + b"\n"
                )
                response = second.recv(4096)
            finally:
                second.close()
                first.close()
                server.stop()
                thread.join(timeout=2)
                worker.join(timeout=2)
            self.assertLess(len(response), 1024)
            decoded = json.loads(response)
            self.assertFalse(decoded["ok"])
            self.assertEqual("", decoded["request_id"])

    def test_unix_client_rejects_a_response_for_another_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = str(Path(directory) / "mismatch.sock")
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(socket_path)
            listener.listen(1)

            def serve_once() -> None:
                connection, _ = listener.accept()
                try:
                    connection.recv(4096)
                    connection.sendall(
                        b'{"ok":true,"request_id":"other","scores":[{"sum_logp":0,"predicted_tokens":1}]}\n'
                    )
                finally:
                    connection.close()
                    listener.close()

            thread = threading.Thread(target=serve_once, daemon=True)
            thread.start()
            client = UnixScorerClient(socket_path, timeout=1.0)
            try:
                with self.assertRaisesRegex(Exception, "request"):
                    client.score("", ("甲",), request_id="expected")
                self.assertIsNone(client._socket)
            finally:
                client.close()
                thread.join(timeout=2)

    def test_missing_local_model_never_attempts_runtime_download(self) -> None:
        from tiger_sentence_native.qwen35_scorer import (
            ModelUnavailableError,
            _resolve_model_reference,
        )

        with self.assertRaises(ModelUnavailableError):
            _resolve_model_reference("mlx-community/Qwen3.5-0.8B-MLX-4bit", None)

    def test_http_adapter_accepts_openai_style_wrapped_payload(self) -> None:
        server = _HttpServer(("127.0.0.1", 0), StubScorer())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            body = json.dumps(
                {
                    "model": "stub",
                    "messages": [
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "request_id": "http-1",
                                    "raw": "abc",
                                    "candidates": ["甲", "乙"],
                                },
                                ensure_ascii=False,
                            ),
                        }
                    ],
                },
                ensure_ascii=False,
            ).encode("utf-8")
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/chat/completions",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=2) as response:
                result = json.loads(response.read().decode("utf-8"))
            self.assertTrue(result["ok"])
            self.assertEqual(result["request_id"], "http-1")
            self.assertEqual(len(result["scores"]), 2)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


class ModelFingerprintTest(unittest.TestCase):
    def test_expected_fingerprint_is_a_strict_load_gate(self) -> None:
        expected = "a" * 64
        self.assertIsNone(_validate_expected_fingerprint(expected, expected))
        with self.assertRaises(UnsupportedModelError):
            _validate_expected_fingerprint("b" * 64, expected)

    @staticmethod
    def _write_model_fixture(
        model_dir: Path,
        *,
        bits: int | None,
        model_type: str = "qwen3_5",
    ) -> None:
        model_dir.mkdir()
        config: dict[str, object] = {"model_type": model_type}
        if model_type == "qwen3_5":
            config["text_config"] = {}
        if bits is not None:
            config["quantization"] = {"bits": bits}
        (model_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
        (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
        (model_dir / "tokenizer_config.json").write_text("{}", encoding="utf-8")
        (model_dir / "model.safetensors").write_bytes(b"weights")

    def test_production_model_rejects_bf16_directory_and_unknown_quantization(self) -> None:
        from tiger_sentence_native.qwen35_scorer import _validate_production_quantization

        with tempfile.TemporaryDirectory() as directory:
            bf16_dir = Path(directory) / "Qwen3.5-0.8B-MLX-bf16"
            self._write_model_fixture(bf16_dir, bits=16)
            config = validate_model_directory(bf16_dir)
            with self.assertRaisesRegex(UnsupportedModelError, "registered"):
                _validate_production_quantization(config, model_reference=str(bf16_dir))

            unknown_dir = Path(directory) / "custom-checkpoint"
            self._write_model_fixture(unknown_dir, bits=None)
            config = validate_model_directory(unknown_dir)
            with self.assertRaisesRegex(UnsupportedModelError, "registered"):
                _validate_production_quantization(config, model_reference=str(unknown_dir))

    def test_registered_alias_without_quantization_metadata_is_rejected(self) -> None:
        from tiger_sentence_native.qwen35_scorer import _validate_production_quantization

        # A checkpoint reached through a non-canonical local name that still
        # pins to a registered model must declare its quantization explicitly.
        with tempfile.TemporaryDirectory() as directory:
            alias_reference = str(Path(directory) / "Qwen3-0.6B-4bit" / "export")
            config = {"model_type": "qwen3"}
            with self.assertRaisesRegex(UnsupportedModelError, "unknown"):
                _validate_production_quantization(config, model_reference=alias_reference)

    def test_production_model_accepts_explicit_4bit_directory(self) -> None:
        from tiger_sentence_native.qwen35_scorer import _validate_production_quantization

        with tempfile.TemporaryDirectory() as directory:
            model_dir = Path(directory) / "Qwen3.5-0.8B-MLX-4bit"
            self._write_model_fixture(model_dir, bits=4)
            config = validate_model_directory(model_dir)
            _validate_production_quantization(config, model_reference=str(model_dir))

    def test_qwen3_checkpoint_is_a_registered_production_model(self) -> None:
        from tiger_sentence_native.qwen35_scorer import (
            _resolve_model_spec,
            _validate_production_quantization,
        )

        with tempfile.TemporaryDirectory() as directory:
            model_dir = Path(directory) / "Qwen3-0.6B-4bit"
            self._write_model_fixture(model_dir, bits=4, model_type="qwen3")
            # Plain text checkpoints carry no VLM text_config.
            config = validate_model_directory(model_dir)
            spec = _resolve_model_spec(str(model_dir))
            self.assertEqual(spec.model_type, "qwen3")
            self.assertFalse(spec.is_vlm)
            _validate_production_quantization(config, model_reference=str(model_dir), spec=spec)

            # A canonical 4-bit directory name without quantization metadata
            # stays loadable, matching the Qwen3.5 production policy.
            canonical = Path(directory) / "Qwen3-0.6B-4bit-export"
            canonical.mkdir()
            (canonical / "config.json").write_text(
                json.dumps({"model_type": "qwen3", "quantization": {"bits": 4}}),
                encoding="utf-8",
            )
            (canonical / "tokenizer.json").write_text("{}", encoding="utf-8")
            (canonical / "tokenizer_config.json").write_text("{}", encoding="utf-8")
            (canonical / "model.safetensors").write_bytes(b"weights")
            config = validate_model_directory(canonical)
            with self.assertRaisesRegex(UnsupportedModelError, "registered"):
                _validate_production_quantization(config, model_reference=str(canonical))

        self.assertEqual(_resolve_model_spec("mlx-community/Qwen3-0.6B-4bit").model_type, "qwen3")
        with self.assertRaisesRegex(UnsupportedModelError, "registered"):
            _resolve_model_spec("mlx-community/Qwen3-0.6B-bf16")

    def test_checkpoint_cannot_masquerade_as_another_registered_model(self) -> None:
        from tiger_sentence_native.qwen35_scorer import _resolve_model_spec

        with tempfile.TemporaryDirectory() as directory:
            model_dir = Path(directory) / "Qwen3.5-0.8B-MLX-4bit"
            self._write_model_fixture(model_dir, bits=4, model_type="qwen3")
            spec = _resolve_model_spec(str(model_dir))
            with self.assertRaisesRegex(UnsupportedModelError, "model_type"):
                validate_model_directory(model_dir, spec)

    def test_fingerprint_is_stable_and_changes_with_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            model_dir = Path(directory)
            (model_dir / "config.json").write_text('{"model_type":"qwen3_5"}\n')
            first = compute_model_fingerprint(model_dir)
            second = compute_model_fingerprint(model_dir)
            self.assertEqual(first, second)
            (model_dir / "config.json").write_text('{"model_type":"qwen3_5","x":1}\n')
            self.assertNotEqual(first, compute_model_fingerprint(model_dir))

    def test_incomplete_vlm_checkpoint_has_actionable_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            model_dir = Path(directory)
            (model_dir / "config.json").write_text('{"model_type":"qwen3_5","text_config":{}}\n')
            from tiger_sentence_native.qwen35_scorer import validate_model_directory

            with self.assertRaises(IncompleteModelError) as raised:
                validate_model_directory(model_dir)
            self.assertIn("safetensors", str(raised.exception))

    def test_indexed_checkpoint_rejects_a_missing_shard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            model_dir = Path(directory)
            (model_dir / "config.json").write_text(
                '{"model_type":"qwen3_5","text_config":{}}\n', encoding="utf-8"
            )
            (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
            (model_dir / "tokenizer_config.json").write_text("{}", encoding="utf-8")
            (model_dir / "model-00001-of-00002.safetensors").write_bytes(b"weights")
            (model_dir / "model.safetensors.index.json").write_text(
                json.dumps(
                    {
                        "metadata": {"total_size": 7},
                        "weight_map": {
                            "a": "model-00001-of-00002.safetensors",
                            "b": "model-00002-of-00002.safetensors",
                        },
                    }
                ),
                encoding="utf-8",
            )
            from tiger_sentence_native.qwen35_scorer import validate_model_directory

            with self.assertRaisesRegex(IncompleteModelError, "shard is missing"):
                validate_model_directory(model_dir)

    def test_local_health_exposes_fingerprint_before_model_load(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            model_dir = Path(directory)
            (model_dir / "config.json").write_text(
                '{"model_type":"qwen3_5","text_config":{}}\n', encoding="utf-8"
            )
            (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
            (model_dir / "tokenizer_config.json").write_text("{}", encoding="utf-8")
            (model_dir / "model.safetensors").write_bytes(b"weights")
            from tiger_sentence_native.qwen35_scorer import MLXScorer

            scorer = MLXScorer(model_dir)
            health = scorer.health()
            self.assertFalse(health["ready"])
            self.assertEqual(health["model"]["model_sha256"], compute_model_fingerprint(model_dir))

    def test_huggingface_style_weight_symlink_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_dir = root / "snapshot"
            blob_dir = root / "blobs"
            model_dir.mkdir()
            blob_dir.mkdir()
            (model_dir / "config.json").write_text(
                '{"model_type":"qwen3_5","text_config":{}}\n', encoding="utf-8"
            )
            (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
            (model_dir / "tokenizer_config.json").write_text("{}", encoding="utf-8")
            blob = blob_dir / "abc123"
            blob.write_bytes(b"weights")
            (model_dir / "model.safetensors").symlink_to(blob)
            (model_dir / "model.safetensors.index.json").write_text(
                json.dumps(
                    {
                        "metadata": {"total_size": len(b"weights")},
                        "weight_map": {"a": "model.safetensors"},
                    }
                ),
                encoding="utf-8",
            )
            from tiger_sentence_native.qwen35_scorer import validate_model_directory

            validate_model_directory(model_dir)
            first = compute_model_fingerprint(model_dir)
            blob.write_bytes(b"changed")
            self.assertNotEqual(first, compute_model_fingerprint(model_dir))

    def test_index_rejects_parent_directory_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_dir = root / "model"
            model_dir.mkdir()
            (model_dir / "config.json").write_text(
                '{"model_type":"qwen3_5","text_config":{}}\n', encoding="utf-8"
            )
            (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
            (model_dir / "tokenizer_config.json").write_text("{}", encoding="utf-8")
            (model_dir / "model.safetensors").write_bytes(b"weights")
            (model_dir / "model.safetensors.index.json").write_text(
                json.dumps(
                    {
                        "metadata": {"total_size": 7},
                        "weight_map": {"a": "../secret.safetensors"},
                    }
                ),
                encoding="utf-8",
            )
            from tiger_sentence_native.qwen35_scorer import validate_model_directory

            with self.assertRaisesRegex(IncompleteModelError, "escapes"):
                validate_model_directory(model_dir)


class BatchingPreparationTest(unittest.TestCase):
    class Tokenizer:
        pad_token_id = 99
        bos_token_id = None
        eos_token_id = 98

        def encode(self, text: str, add_special_tokens: bool = False) -> list[int]:
            del add_special_tokens
            return [ord(char) for char in text]

    def test_right_padding_masks_only_candidate_targets(self) -> None:
        from tiger_sentence_native.qwen35_scorer import MLXScorer

        scorer = MLXScorer("unused", max_context_tokens=10, max_candidate_tokens=10)
        scorer._tokenizer = self.Tokenizer()
        rows, masks, context_length = scorer._prepare_batch("x", ("ab", "c"))

        self.assertEqual(context_length, 1)
        self.assertEqual(rows, [[120, 97, 98], [120, 99, 99]])
        self.assertEqual(masks, [[1.0, 1.0], [1.0, 0.0]])

    def test_complete_candidates_do_not_duplicate_context_prefix(self) -> None:
        from tiger_sentence_native.qwen35_scorer import MLXScorer

        scorer = MLXScorer("unused", max_context_tokens=10, max_candidate_tokens=10)
        scorer._tokenizer = self.Tokenizer()
        rows, masks, context_length = scorer._prepare_batch(
            "xy", ("xyz", "other"), candidate_mode="complete"
        )

        self.assertEqual(context_length, 2)
        self.assertEqual(
            rows,
            [
                [120, 121, 122, 99, 99, 99],
                [99, 111, 116, 104, 101, 114],
            ],
        )
        self.assertEqual(masks[0], [0.0, 1.0, 0.0, 0.0, 0.0])
        self.assertEqual(masks[1], [1.0, 1.0, 1.0, 1.0, 1.0])

    def test_complete_candidates_split_prefix_before_bpe_encoding(self) -> None:
        from tiger_sentence_native.qwen35_scorer import MLXScorer

        class Backend:
            def encode(self, text: str, add_special_tokens: bool = False):
                del add_special_tokens
                values = {
                    "你不": [15],
                    "要在精神": [16, 12, 13],
                    "要再精神": [17, 14, 13],
                    "你不要在精神": [11, 12, 13],
                    "你不要再精神": [11, 14, 13],
                }
                return type("Encoding", (), {"ids": values[text]})()

        class OffsetTokenizer:
            pad_token_id = 99
            bos_token_id = None
            eos_token_id = 98

            def __init__(self) -> None:
                self.backend_tokenizer = Backend()

            def encode(self, text: str, add_special_tokens: bool = False) -> list[int]:
                return self.backend_tokenizer.encode(text, add_special_tokens).ids

        scorer = MLXScorer("unused", max_context_tokens=10, max_candidate_tokens=10)
        scorer._tokenizer = OffsetTokenizer()
        rows, masks, context_length = scorer._prepare_batch(
            "你不", ("你不要在精神", "你不要再精神"), candidate_mode="complete"
        )

        self.assertEqual(context_length, 1)
        self.assertEqual(rows, [[15, 16, 12, 13], [15, 17, 14, 13]])
        # The context and suffix are encoded independently.  Only the three
        # suffix tokens are predicted; the context token is conditioning state.
        self.assertEqual(masks, [[1.0, 1.0, 1.0], [1.0, 1.0, 1.0]])

    def test_short_batches_use_five_rows_and_large_batches_use_twenty(self) -> None:
        from tiger_sentence_native.qwen35_scorer import MLXScorer

        scorer = MLXScorer("unused", max_context_tokens=10, max_candidate_tokens=10)
        scorer._tokenizer = self.Tokenizer()
        short_rows, short_masks, _ = scorer._prepare_batch("x", ("ab", "c"))
        padded_rows, padded_masks = scorer._pad_short_batch(short_rows, short_masks)
        self.assertEqual(len(padded_rows), 5)
        self.assertEqual(len(padded_masks), 5)
        self.assertEqual(padded_rows[:2], short_rows)
        self.assertTrue(all(value == 0.0 for value in padded_masks[2]))

        five_candidates = tuple(f"five{index}" for index in range(5))
        five_rows, five_masks, _ = scorer._prepare_batch("x", five_candidates)
        forwarded_five_rows, forwarded_five_masks = scorer._pad_short_batch(
            five_rows, five_masks
        )
        self.assertEqual(len(forwarded_five_rows), 5)
        self.assertEqual(len(forwarded_five_masks), 5)

        large_candidates = tuple(f"c{index}" for index in range(6))
        large_rows, large_masks, _ = scorer._prepare_batch("x", large_candidates)
        forwarded_rows, forwarded_masks = scorer._pad_short_batch(large_rows, large_masks)
        self.assertEqual(len(forwarded_rows), 20)
        self.assertEqual(len(forwarded_masks), 20)
        self.assertEqual(forwarded_rows[:6], large_rows)
        self.assertTrue(all(value == 0.0 for value in forwarded_masks[6]))

    def test_short_sequences_use_a_stable_eight_token_shape(self) -> None:
        from tiger_sentence_native.qwen35_scorer import MLXScorer

        rows = [[1, 2, 3], [1, 4, 5]]
        masks = [[1.0, 1.0], [1.0, 1.0]]
        padded_rows, padded_masks = MLXScorer._pad_sequence_rows(rows, masks, pad_id=0)
        self.assertEqual([len(row) for row in padded_rows], [8, 8])
        self.assertEqual([len(mask) for mask in padded_masks], [7, 7])
        self.assertEqual(padded_rows[0][:3], rows[0])
        self.assertTrue(all(value == 0.0 for value in padded_masks[0][2:]))

    def test_complete_suffix_is_checked_against_candidate_token_limit(self) -> None:
        from tiger_sentence_native.qwen35_scorer import MLXScorer, ProtocolError

        class BoundaryBackend:
            def encode(self, text: str, add_special_tokens: bool = False):
                del add_special_tokens
                values = {"ctx": [1], "ctxsuffix": [2, 3], "suffix": [4, 5, 6]}
                return type("Encoding", (), {"ids": values[text]})()

        class BoundaryTokenizer:
            pad_token_id = 0
            bos_token_id = None
            eos_token_id = 3

            def __init__(self) -> None:
                self.backend_tokenizer = BoundaryBackend()

            def encode(self, text: str, add_special_tokens: bool = False) -> list[int]:
                return self.backend_tokenizer.encode(text, add_special_tokens).ids

        scorer = MLXScorer("unused", max_context_tokens=10, max_candidate_tokens=2)
        scorer._tokenizer = BoundaryTokenizer()
        with self.assertRaises(ProtocolError):
            scorer._prepare_batch("ctx", ("ctxsuffix",), candidate_mode="complete")



def _mlx_available() -> bool:
    try:
        return importlib.util.find_spec("mlx.core") is not None
    except ModuleNotFoundError:
        return False


@unittest.skipUnless(_mlx_available(), "MLX is optional")
class MLXBatchScoringTest(unittest.TestCase):
    def test_batch_with_no_predictable_position_is_rejected(self) -> None:
        from tiger_sentence_native.qwen35_scorer import MLXScorer, ModelUnavailableError

        scorer = MLXScorer("unused", cache_size=0)
        scorer._model = object()
        with self.assertRaises(ModelUnavailableError):
            scorer._score_batch([[1]], [[0.0]])

    class Tokenizer:
        pad_token_id = 0
        bos_token_id = None
        eos_token_id = 3

        def encode(self, text: str, add_special_tokens: bool = False) -> list[int]:
            del add_special_tokens
            return [{"x": 1, "y": 2, "z": 1}[char] for char in text]

    def test_scores_all_candidates_in_one_forward_pass(self) -> None:
        import mlx.core as mx

        from tiger_sentence_native.qwen35_scorer import MLXScorer

        class ZeroLogitModel:
            calls = 0

            def __call__(self, input_ids):
                self.calls += 1
                return mx.zeros((input_ids.shape[0], input_ids.shape[1], 4), dtype=mx.float32)

            def eval(self):
                return self

        model = ZeroLogitModel()
        scorer = MLXScorer("unused", cache_size=0)
        scorer._model = model
        scorer._tokenizer = self.Tokenizer()
        scorer._warmed = True
        scores = scorer.score("x", ("xy", "xz"), candidate_mode="complete")

        self.assertEqual(model.calls, 1)
        self.assertEqual([score.predicted_tokens for score in scores], [1, 1])
        self.assertTrue(
            all(math.isclose(score.sum_logp, -math.log(4), rel_tol=1e-5) for score in scores)
        )

    def test_batches_above_five_use_the_stable_twenty_row_shape(self) -> None:
        import mlx.core as mx

        from tiger_sentence_native.qwen35_scorer import MLXScorer

        class ShapeModel:
            calls = 0
            batch_rows = None

            def __call__(self, input_ids):
                self.calls += 1
                self.batch_rows = input_ids.shape[0]
                return mx.zeros((input_ids.shape[0], input_ids.shape[1], 4), dtype=mx.float32)

        model = ShapeModel()
        scorer = MLXScorer("unused", cache_size=0)
        scorer._model = model
        scorer._tokenizer = self.Tokenizer()
        scorer._warmed = True
        candidates = ("xy", "xz", "xyy", "xyz", "xzy", "xzz")

        scores = scorer.score("x", candidates, candidate_mode="complete")

        self.assertEqual(model.calls, 1)
        self.assertEqual(model.batch_rows, 20)
        self.assertEqual(len(scores), 6)

    def test_qwen_text_branch_projects_only_candidate_positions(self) -> None:
        import mlx.core as mx

        from tiger_sentence_native.qwen35_scorer import MLXScorer

        class Embedding:
            def __init__(self):
                self.calls = 0
                self.last_shape = None

            def as_linear(self, hidden):
                self.calls += 1
                self.last_shape = hidden.shape
                return mx.zeros((*hidden.shape[:-1], 4), dtype=mx.float32)

        class Inner:
            def __init__(self):
                self.embed_tokens = Embedding()

            def __call__(self, input_ids):
                return mx.zeros((input_ids.shape[0], input_ids.shape[1], 3), dtype=mx.float32)

        class LanguageModel:
            def __init__(self):
                self.model = Inner()
                self.args = type("Args", (), {"tie_word_embeddings": True})()

        class Outer:
            def __init__(self):
                self.language_model = LanguageModel()

            def __call__(self, _input_ids):
                raise AssertionError("full VLM projection should not be used")

        model = Outer()
        scorer = MLXScorer("unused", cache_size=0)
        scorer._model = model
        scorer._tokenizer = self.Tokenizer()
        scorer._warmed = True
        scores = scorer.score("x", ("xy", "xz"), candidate_mode="complete")

        self.assertEqual(len(scores), 2)
        self.assertEqual(model.language_model.model.embed_tokens.calls, 1)
        self.assertEqual(model.language_model.model.embed_tokens.last_shape, (5, 8, 3))

    def test_plain_text_model_uses_tied_embedding_projection(self) -> None:
        import mlx.core as mx

        from tiger_sentence_native.qwen35_scorer import MLXScorer

        class Embedding:
            def __init__(self):
                self.calls = 0
                self.last_shape = None

            def as_linear(self, hidden):
                self.calls += 1
                self.last_shape = hidden.shape
                return mx.zeros((*hidden.shape[:-1], 4), dtype=mx.float32)

        class Transformer:
            def __init__(self):
                self.embed_tokens = Embedding()

            def __call__(self, input_ids):
                return mx.zeros((input_ids.shape[0], input_ids.shape[1], 3), dtype=mx.float32)

        class PlainModel:
            # Qwen3 exposes the transformer directly (no language_model).
            def __init__(self):
                self.model = Transformer()
                self.args = type("Args", (), {"tie_word_embeddings": True})()

            def __call__(self, _input_ids):
                raise AssertionError("full vocab projection should not be used")

        model = PlainModel()
        scorer = MLXScorer("unused", cache_size=0)
        scorer._model = model
        scorer._tokenizer = self.Tokenizer()
        scorer._warmed = True
        scores = scorer.score("x", ("xy", "xz"), candidate_mode="complete")

        self.assertEqual(len(scores), 2)
        self.assertEqual(model.model.embed_tokens.calls, 1)
        self.assertEqual(model.model.embed_tokens.last_shape, (5, 8, 3))


class BenchmarkTest(unittest.TestCase):
    def test_latency_summary_uses_sorted_percentiles(self) -> None:
        summary = summarize_latencies([1.0, 2.0, 3.0, 4.0, 5.0])
        self.assertEqual(summary["count"], 5)
        self.assertEqual(summary["min_ms"], 1.0)
        self.assertEqual(summary["max_ms"], 5.0)
        self.assertEqual(summary["p50_ms"], 3.0)
        self.assertEqual(summary["p95_ms"], 5.0)
        self.assertEqual(summary["p99_ms"], 5.0)

    def test_latency_summary_rejects_empty_or_non_finite_samples(self) -> None:
        with self.assertRaises(ValueError):
            summarize_latencies([])
        with self.assertRaises(ValueError):
            summarize_latencies([1.0, float("nan")])

    def test_benchmark_runs_warmup_and_returns_scores(self) -> None:
        result = benchmark_scorer(
            StubScorer(), context="", candidates=("甲", "乙"), warmup=1, runs=3
        )
        self.assertEqual(result["count"], 3)
        self.assertEqual(len(result["scores"]), 2)
        self.assertIn("health", result)

    def test_benchmark_script_can_be_invoked_by_file_path(self) -> None:
        script = Path(__file__).resolve().parents[1] / "tiger_sentence_native" / "qwen35_bench.py"
        result = subprocess.run(
            [sys.executable, str(script), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_benchmark_cli_accepts_the_full_wire_candidate_budget(self) -> None:
        from tiger_sentence_native import qwen35_bench

        captured: dict[str, object] = {}

        class FakeScorer:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

        def fake_benchmark(_scorer: object, **kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            return {"count": 1, "scores": [], "health": {"ready": True}}

        arguments: list[str] = []
        for index in range(20):
            arguments.extend(("--candidate", f"候选{index}"))
        arguments.extend(("--warmup", "0", "--runs", "1"))
        with mock.patch.object(qwen35_bench, "MLXScorer", FakeScorer), mock.patch.object(
            qwen35_bench, "benchmark_scorer", fake_benchmark
        ), mock.patch.object(qwen35_bench, "print"):
            self.assertEqual(0, qwen35_bench.main(arguments))
        self.assertEqual(20, len(captured["candidates"]))


@unittest.skipUnless(
    os.environ.get("MOHU_QWEN35_MODEL"),
    "set MOHU_QWEN35_MODEL to run the MLX integration test",
)
class OptionalModelIntegrationTest(unittest.TestCase):
    def test_local_model_scores_two_candidates(self) -> None:
        from tiger_sentence_native.qwen35_scorer import MLXScorer

        scorer = MLXScorer(os.environ["MOHU_QWEN35_MODEL"])
        scores = scorer.score("", ("你好", "世界"))
        self.assertEqual(len(scores), 2)
        self.assertTrue(all(score.predicted_tokens >= 0 for score in scores))

    def test_local_model_scores_are_invariant_for_short_batch_padding(self) -> None:
        from tiger_sentence_native.qwen35_scorer import MLXScorer

        first_two = ("那就没什么可犹豫地了", "那就没什么可犹豫的了")
        five = first_two + (
            "那就没什么可犹豫的嘞",
            "那就没什么可犹豫得了",
            "那就没什么可犹豫的乐",
        )
        scorer = MLXScorer(os.environ["MOHU_QWEN35_MODEL"], cache_size=0)
        pair_scores = scorer.score("", first_two, candidate_mode="complete")
        batch_scores = scorer.score("", five, candidate_mode="complete")
        for pair, batch in zip(pair_scores, batch_scores):
            self.assertEqual(pair.predicted_tokens, batch.predicted_tokens)
            self.assertTrue(
                math.isclose(pair.sum_logp, batch.sum_logp, rel_tol=0.0, abs_tol=1e-5),
                (pair, batch),
            )

    def test_local_model_full_budget_is_repeatable(self) -> None:
        from tiger_sentence_native.qwen35_scorer import MLXScorer

        candidates = tuple(f"候选测试句子{index}" for index in range(20))
        scorer = MLXScorer(os.environ["MOHU_QWEN35_MODEL"], cache_size=0)
        first = scorer.score("", candidates, candidate_mode="complete")
        second = scorer.score("", candidates, candidate_mode="complete")
        self.assertEqual(len(first), 20)
        self.assertEqual(len(second), 20)
        for left, right in zip(first, second):
            self.assertEqual(left.predicted_tokens, right.predicted_tokens)
            self.assertTrue(math.isclose(left.sum_logp, right.sum_logp, abs_tol=1e-5))


if __name__ == "__main__":
    unittest.main()
