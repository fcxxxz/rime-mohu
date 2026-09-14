"""Fail-open client for the isolated semantic scorer service.

Sends one validated protocol scoring request with a hard synchronous
deadline. Any transport error, timeout, malformed response, profile
mismatch, or validation failure returns ``None`` and the caller must keep
the exact native menu order (fail-open). Contains no retry loop on purpose.

Usage:
  python scorer_client.py --socket <path> --case <record.json> [--deadline-ms 100]
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.qwen_semantic_rerank import (
    FORMAT_VERSION,
    apply_semantic_response,
    make_scoring_request,
    validate_scoring_response,
)

MAX_FRAME_BYTES = 1 << 20


def score_menu(record: dict, socket_path: Path, *, profile: str, deadline_ms: int) -> list[int] | None:
    """Return the final index order, or None to preserve native order."""

    try:
        request = make_scoring_request(record, model_profile=profile)
    except Exception:
        return None
    payload = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode()
    if len(payload) > MAX_FRAME_BYTES:
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(deadline_ms / 1000.0)
            client.connect(str(socket_path))
            client.sendall(len(payload).to_bytes(4, "big") + payload)
            header = client.recv(4)
            if len(header) != 4:
                return None
            length = int.from_bytes(header, "big")
            if length <= 0 or length > MAX_FRAME_BYTES:
                return None
            body = b""
            while len(body) < length:
                chunk = client.recv(length - len(body))
                if not chunk:
                    return None
                body += chunk
        response = json.loads(body)
        if "error" in response:
            return None
        if response.get("format_version") != FORMAT_VERSION:
            return None
        validated = validate_scoring_response(response, request)
        return apply_semantic_response(record, validated, model_profile=profile)
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--profile", type=str, default="student-small-v1")
    parser.add_argument("--deadline-ms", type=int, default=100)
    args = parser.parse_args()

    record = json.loads(args.case.read_text(encoding="utf-8"))
    order = score_menu(record, args.socket, profile=args.profile, deadline_ms=args.deadline_ms)
    if order is None:
        print("FAIL_OPEN: native order preserved")
        return
    print(json.dumps({"final_menu_indices": order}, ensure_ascii=False))


if __name__ == "__main__":
    main()
