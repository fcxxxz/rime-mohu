# -*- coding: utf-8 -*-
"""Local ONNX student scorer service for the gated rerank filter.

Loads the distilled TinyCharLM ONNX export (shared-context-KV graph:
inputs full_ids (B,T) int64 + ctx_len int64 -> scores (B,)) and serves
candidate scores over a line-oriented local Unix socket.

Wire protocol (one request per line, UTF-8, tab-separated fields):
    PING
        -> PONG
    SCORE\t<context>\t<cand1>\t<cand2>\t...
        -> OK\t<score1>\t<score2>\t...
        -> ERR\t<reason>

Fields must not contain tab or newline.  Scoring semantics are identical
to the deployed engine's char scorer: score = sum logP(candidate chars |
BOS + last 160 context chars), candidates capped at 31 chars, batch of up
to 20 candidates per request — one forward pass per request.

Example:
    uv run --with onnxruntime python tiger_sentence_native/student_scorer.py \
      --model-dir /path/to/student_v3_onnx --socket /tmp/mohu-student.sock
"""
import argparse
import json
import os
import socket
import sys
import threading

import numpy as np
import onnxruntime as ort

BOS, PAD, UNK = 2, 0, 1
MAX_CANDIDATES = 20
MAX_CONTEXT_CHARS = 160
MAX_CAND_CHARS = 31
MAX_LINE_BYTES = 65536


class Scorer:
    def __init__(self, model_dir):
        vocab_path = os.path.join(model_dir, "vocab.json")
        onnx_path = os.path.join(model_dir, "student_shared_kv.onnx")
        with open(vocab_path, encoding="utf-8") as f:
            self.vocab = json.load(f)
        self.session = ort.InferenceSession(
            onnx_path, providers=["CPUExecutionProvider"])

    def encode(self, text):
        vocab = self.vocab
        return [vocab.get(ch, UNK) for ch in text]

    def score(self, context, candidates):
        if not 1 <= len(candidates) <= MAX_CANDIDATES:
            raise ValueError("candidates must contain 1..%d items"
                             % MAX_CANDIDATES)
        ctx_ids = [BOS] + self.encode(context)[:MAX_CONTEXT_CHARS]
        rows, split = [], len(ctx_ids) - 1
        for cand in candidates:
            cand_ids = self.encode(cand)[:MAX_CAND_CHARS]
            if not cand_ids:
                raise ValueError("empty candidate")
            rows.append(ctx_ids + cand_ids)
        width = max(len(r) for r in rows)
        ids = np.full((len(rows), width), PAD, dtype=np.int64)
        for j, row in enumerate(rows):
            ids[j, :len(row)] = row
        out = self.session.run(None, {
            "full_ids": ids,
            "ctx_len": np.array(split, dtype=np.int64)})[0]
        return [float(x) for x in out]


def handle_line(scorer, line):
    if line == "PING":
        return "PONG"
    parts = line.split("\t")
    if len(parts) < 3 or parts[0] != "SCORE":
        return "ERR\tbad request"
    context, candidates = parts[1], parts[2:]
    try:
        scores = scorer.score(context, candidates)
    except Exception as exc:  # noqa: BLE001 - single wire error channel
        return "ERR\t%s" % type(exc).__name__
    return "OK\t" + "\t".join("%.4f" % s for s in scores)


def serve_client(conn, scorer):
    conn.settimeout(300)
    buf = b""
    try:
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
            if len(buf) > MAX_LINE_BYTES * 2:
                break
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if len(line) > MAX_LINE_BYTES:
                    response = "ERR\tline too long"
                else:
                    try:
                        text = line.decode("utf-8")
                    except UnicodeDecodeError:
                        response = "ERR\tbad utf-8"
                    else:
                        if "\r" in text:
                            text = text.rstrip("\r")
                        print("REQ %s" % text[:48].replace("\t", "|"),
                              file=sys.stderr, flush=True)
                        response = handle_line(scorer, text)
                conn.sendall(response.encode("utf-8") + b"\n")
    except OSError:
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True,
                    help="directory with student_shared_kv.onnx + vocab.json")
    ap.add_argument("--socket", required=True,
                    help="unix socket path to listen on")
    ap.add_argument("--idle-timeout", type=float, default=0,
                    help="exit after N idle seconds (0 = never)")
    args = ap.parse_args()

    scorer = Scorer(args.model_dir)
    sock_path = args.socket
    if os.path.exists(sock_path):
        os.unlink(sock_path)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(sock_path)
    os.chmod(sock_path, 0o600)
    server.listen(4)
    print("listening on %s" % sock_path, file=sys.stderr, flush=True)

    def accept_loop():
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            serve_client(conn, scorer)

    thread = threading.Thread(target=accept_loop, daemon=True)
    thread.start()
    thread.join(timeout=args.idle_timeout or None)


if __name__ == "__main__":
    main()
