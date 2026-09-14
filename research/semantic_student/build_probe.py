"""Build a frozen news/daily probe set from the fixed 20k corpus.

Turns the frozen ``sentences.jsonl`` (news/daily, the corpus used by the
cross-candidate benchmarks) into native-replay menu records with jieba word
cases, so semantic students get a second, cross-benchmark-comparable held-out
number. Never used for training.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pypinyin import lazy_pinyin

import jieba

from tools.zrmify import zrmify
from tools.qwen_semantic_rerank import FORMAT_VERSION, validate_menu_record
from build_dataset import (
    CONTEXT_CHARS, WORD_MIN_CHARS, WORD_MAX_CHARS, CJK_RANGES,
    _is_cjk, encode_word, candidate_payload, guard_path,
)
from native_menu import NativeMenuDecoder

PROBE_VERSION = "native-replay-probe/v1"


def iter_cases(text: str, max_per_sentence: int):
    emitted = 0
    for word, start, _end in jieba.tokenize(text):
        if emitted >= max_per_sentence:
            return
        if not _is_cjk(word) or not (WORD_MIN_CHARS <= len(word) <= WORD_MAX_CHARS):
            continue
        context = text[:start]
        if len(context) < 2 or not _is_cjk(context[-2:]):
            continue
        code = encode_word(word)
        if code is None:
            continue
        yield word, context, code
        emitted += 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sentences", type=Path,
                        default=REPO / "research/lm_sentence_compare/corpus/sentences.jsonl")
    parser.add_argument("--out", type=str, default="native-v2")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--max-per-sentence", type=int, default=4)
    args = parser.parse_args()

    out = guard_path(HERE / "datasets" / args.out, label="probe output")
    sentences = [json.loads(line) for line in args.sentences.read_text(encoding="utf-8").splitlines() if line.strip()]
    probe_path = out / "probe.jsonl"
    stats = Counter()
    oracle_ranks = Counter()
    with NativeMenuDecoder() as decoder, probe_path.open("w", encoding="utf-8") as sink:
        for sentence in sentences:
            for word_index, (word, context, code) in enumerate(
                iter_cases(sentence["text"], args.max_per_sentence)
            ):
                stats["attempts"] += 1
                menu = decoder.decode(code)
                payload = [candidate_payload(i, cand, code) for i, cand in enumerate(menu.candidates[: args.top])]
                if not payload:
                    continue
                texts = [item["text"] for item in payload]
                try:
                    oracle_index = texts.index(word)
                except ValueError:
                    stats["target_missing"] += 1
                    continue
                record = validate_menu_record({
                    "format_version": FORMAT_VERSION,
                    "case_id": f"probe:{sentence['id']}#{word_index}",
                    "source": f"probe-{sentence['source']}",
                    "schema": "mohu_zrm",
                    "raw_input": code,
                    "preedit": payload[0]["preedit"],
                    "committed_context": context[-CONTEXT_CHARS:],
                    "target_text": word,
                    "target_visible": True,
                    "oracle_rank": oracle_index + 1,
                    "engine_capabilities": None,
                    "candidates": payload,
                })
                stats["records"] += 1
                oracle_ranks[record["oracle_rank"]] += 1
                sink.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(json.dumps(dict(stats), indent=1))
    print("oracle ranks:", dict(sorted(oracle_ranks.items())[:10]))


if __name__ == "__main__":
    main()
