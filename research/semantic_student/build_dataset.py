"""Build semantic reranker training menus from segmented corpora via native replay.

Input are the word-segmented ``fenci/`` corpora (one sentence per line, words
separated by spaces). Per case: a 2..6 char CJK word with at least two CJK
context chars before it -> pure double-pinyin code (tools.zrmify + pypinyin)
-> production Tiger V5 decode (beam 200, all ranks, top-K) -> one validated
``qwen-semantic-rerank/v1`` record whose candidates, scores, ranks and
segmented codes all come from that exact decode.

Splits are source-file disjoint: dev and test sources never contribute
training sentences. This is a *native-replay* dataset (native decode
candidates only, no fixed/smart table or user-dictionary candidates); the
limitations are recorded in the manifest and release claims still require
full-menu isolated Rime replay.

Usage:
  uv run python research/semantic_student/build_dataset.py \
      [--train-sources "books_,forum,weibo,wiki,zhihu,thuc,oscar,lccc,llm,wx"] \
      [--dev-sources rmrb] [--test-sources tnews] \
      [--sentences-per-split 800000,50000,50000] [--top 20] [--workers 8]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from multiprocessing import Pool
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pypinyin import lazy_pinyin
from pypinyin import __version__ as pypinyin_version

from tools.zrmify import zrmify
from tools.qwen_semantic_rerank import FORMAT_VERSION, validate_menu_record

DATASET_VERSION = "native-replay/v2"
CONTEXT_CHARS = 48
WORD_MIN_CHARS = 2
WORD_MAX_CHARS = 6
MIN_SENTENCE_WORDS = 3
MAX_SENTENCE_CHARS = 80
CJK_RANGES = ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF))
FENCI_DIR = REPO / "research/lm_sentence_compare/corpus/fenci"
ALLOWED_OUTPUT_ROOTS = ((HERE / "datasets").resolve(), Path("/tmp").resolve())


def guard_path(path: Path, *, label: str) -> Path:
    """Reject traversal and confine dataset outputs to audited roots."""

    if ".." in path.parts:
        raise SystemExit(f"{label} must not contain '..': {path}")
    resolved = path.resolve()
    if ".." in resolved.parts:
        raise SystemExit(f"resolved {label} must not contain '..': {resolved}")
    if not any(resolved == root or resolved.is_relative_to(root) for root in ALLOWED_OUTPUT_ROOTS):
        raise SystemExit(f"{label} must stay inside {ALLOWED_OUTPUT_ROOTS}: {resolved}")
    return resolved


def _is_cjk(text: str) -> bool:
    return all(any(low <= ord(char) <= high for low, high in CJK_RANGES) for char in text)


def encode_word(word: str) -> str | None:
    syllables = lazy_pinyin(word)
    if len(syllables) != len(word):
        return None
    code = "".join(zrmify(p) for p in syllables)
    if not code or not code.isascii() or not code.isalpha():
        return None
    return code


def deterministic_sample(line: str, modulus: int, residue: int) -> bool:
    digest = hashlib.blake2b(line.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % modulus == residue


def iter_sentence_cases(words: list[str], max_per_sentence: int):
    """Yield (word, context, code) for eligible mid-sentence CJK words."""

    emitted = 0
    context_parts: list[str] = []
    context_len = 0
    for word in words:
        if emitted >= max_per_sentence:
            return
        eligible = _is_cjk(word) and WORD_MIN_CHARS <= len(word) <= WORD_MAX_CHARS
        if eligible and context_len >= 2:
            code = encode_word(word)
            if code is not None:
                yield word, "".join(context_parts), code
                emitted += 1
        context_parts.append(word)
        context_len += len(word)


def candidate_payload(menu_index: int, cand, raw: str) -> dict:
    return {
        "menu_index": menu_index,
        "text": cand.text,
        "type": "mohu_zrm",
        "start": 0,
        "end": len(raw),
        "consumes_current_input": True,
        "protected": False,
        "preedit": cand.segmented,
        "native_rank": menu_index + 1,
        "native_score": cand.score,
        "native_score_kind": "static_or_personalized_v5",
        "source_flags": ["full_input", "native"],
        "dictionary_evidence": {
            "known_phrase_spans": [],
            "personal_phrase_spans": [],
            "fixed_phrase_spans": [],
        },
        "code_evidence": {
            # Verified syllable segmentation from this exact native decode
            # (zrm double-pinyin syllables, not pinyin readings).
            "syllables": cand.segmented.split(),
            "auxiliary_constraints": [],
            "reading_constraints": [],
        },
    }


def build_record(*, case_id: str, source: str, raw: str, committed_context: str,
                 target_text: str, candidates_payload: list[dict], preedit: str) -> dict:
    texts = [item["text"] for item in candidates_payload]
    try:
        oracle_index = texts.index(target_text)
    except ValueError:
        oracle_index = -1
    record = {
        "format_version": FORMAT_VERSION,
        "case_id": case_id,
        "source": source,
        "schema": "mohu_zrm",
        "raw_input": raw,
        "preedit": preedit,
        "committed_context": committed_context[-CONTEXT_CHARS:],
        "target_text": target_text,
        "target_visible": oracle_index >= 0,
        "oracle_rank": oracle_index + 1 if oracle_index >= 0 else None,
        "engine_capabilities": None,
        "candidates": candidates_payload,
    }
    return validate_menu_record(record)


def collect_sentences(prefixes_text: str, limit: int, sample_modulus: int) -> list[tuple[str, list[str]]]:
    prefixes = tuple(prefix for prefix in prefixes_text.split(",") if prefix)
    if not prefixes:
        raise SystemExit("no source prefixes given")
    files = sorted(
        path
        for path in FENCI_DIR.glob("*.txt")
        if any(path.name.startswith(prefix) for prefix in prefixes)
    )
    if not files:
        raise SystemExit(f"no fenci source matches prefixes {prefixes!r}")
    collected: list[tuple[str, list[str]]] = []
    for path in files:
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if len(collected) >= limit:
                    break
                text = line.rstrip("\n")
                if not deterministic_sample(text, sample_modulus, 0):
                    continue
                words = text.split()
                if len(words) < MIN_SENTENCE_WORDS or len(text) > MAX_SENTENCE_CHARS:
                    continue
                collected.append((path.name, words))
        if len(collected) >= limit:
            break
    return collected


def _worker(args) -> dict:
    shard_index, role, sentences, top, max_per_sentence, shard_dir, word_edge_weight = args
    sys.path.insert(0, str(HERE))
    sys.path.insert(0, str(REPO))
    from native_menu import NativeMenuDecoder

    stats = Counter()
    oracle_ranks = Counter()
    shard_path = guard_path(shard_dir / f"{role}-{shard_index:04d}.jsonl", label="shard path")
    with NativeMenuDecoder(word_edge_weight=word_edge_weight) as decoder, \
            shard_path.open("w", encoding="utf-8") as out:
        for source_name, words in sentences:
            for word_index, (word, context, code) in enumerate(
                iter_sentence_cases(words, max_per_sentence)
            ):
                stats["decode_attempts"] += 1
                menu = decoder.decode(code)
                if menu.truncated:
                    # all_ranks mode caps the emitted list; benign for top-K export
                    stats["decode_list_truncated"] += 1
                if menu.early_truncated:
                    stats["decode_early_truncated"] += 1
                    continue
                payload = [
                    candidate_payload(index, cand, code)
                    for index, cand in enumerate(menu.candidates[:top])
                ]
                if not payload:
                    stats["empty_menu"] += 1
                    continue
                try:
                    record = build_record(
                        case_id=f"{source_name}:{deterministic_id(source_name, words, word_index)}",
                        source=source_name.removesuffix(".txt"),
                        raw=code,
                        committed_context=context,
                        target_text=word,
                        candidates_payload=payload,
                        preedit=payload[0]["preedit"],
                    )
                except Exception as exc:  # invalid records are counted, never exported
                    stats["invalid_record"] += 1
                    stats[f"invalid:{type(exc).__name__}"] += 1
                    continue
                stats["cases"] += 1
                if record["target_visible"]:
                    stats["target_visible"] += 1
                    oracle_ranks[record["oracle_rank"]] += 1
                    out.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                else:
                    stats["target_missing"] += 1
    return {"stats": dict(stats), "oracle_ranks": {str(k): v for k, v in sorted(oracle_ranks.items())}}


def deterministic_id(source_name: str, words: list[str], word_index: int) -> str:
    digest = hashlib.blake2b(
        (source_name + "\x00" + "".join(words) + "\x00" + str(word_index)).encode("utf-8"),
        digest_size=8,
    ).hexdigest()
    return f"{digest}-{word_index}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=str, default="native-v2")
    parser.add_argument("--train-sources", type=str,
                        default="books_,forum,weibo,wiki,zhihu,thuc,oscar,lccc,llm,wx")
    parser.add_argument("--dev-sources", type=str, default="rmrb")
    parser.add_argument("--test-sources", type=str, default="tnews")
    parser.add_argument("--train-sentences", type=int, default=800_000)
    parser.add_argument("--dev-sentences", type=int, default=50_000)
    parser.add_argument("--test-sentences", type=int, default=50_000)
    parser.add_argument("--train-sample-modulus", type=int, default=64,
                        help="keep 1/N of source lines for training diversity")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--max-per-sentence", type=int, default=2)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--word-edge-weight", type=float, default=0.0,
                        help="capture menus under the word-edge prior engine "
                             "(C3: use the deployed weight to match production)")
    args = parser.parse_args()

    out = guard_path(HERE / "datasets" / args.out, label="output directory")
    out.mkdir(parents=True, exist_ok=True)

    plans = {
        "train": (args.train_sources, args.train_sentences, 1),
        "dev": (args.dev_sources, args.dev_sentences, 1),
        "test": (args.test_sources, args.test_sentences, 1),
    }
    jobs: list[tuple] = []
    collected_summary: dict[str, dict] = {}
    for role, (prefix, limit, modulus) in plans.items():
        sentences = collect_sentences(prefix, limit, args.train_sample_modulus if role == "train" else 2)
        collected_summary[role] = {"prefix": prefix, "sentences": len(sentences)}
        if not sentences:
            raise SystemExit(f"no sentences collected for role {role}")
        shards = max(1, args.workers if role == "train" else max(1, args.workers // 2))
        chunk = (len(sentences) + shards - 1) // shards
        for index in range(shards):
            part = sentences[index * chunk:(index + 1) * chunk]
            if part:
                jobs.append((index, role, part, args.top, args.max_per_sentence, out,
                             args.word_edge_weight))

    with Pool(processes=args.workers) as pool:
        results = pool.map(_worker, jobs)

    totals: Counter = Counter()
    oracle_total: Counter = Counter()
    for result in results:
        totals.update(Counter(result["stats"]))
        oracle_total.update(Counter({int(k): v for k, v in result["oracle_ranks"].items()}))

    from native_menu import NativeMenuDecoder as _Decoder
    engine_manifest = _Decoder(word_edge_weight=args.word_edge_weight).manifest

    manifest = {
        "dataset_version": DATASET_VERSION,
        "format_version": FORMAT_VERSION,
        "fenci_dir": str(FENCI_DIR),
        "collected": collected_summary,
        "train_sample_modulus": args.train_sample_modulus,
        "top": args.top,
        "max_per_sentence": args.max_per_sentence,
        "context_chars": CONTEXT_CHARS,
        "engine": engine_manifest,
        "pypinyin_version": pypinyin_version,
        "stats": dict(totals),
        "oracle_rank_distribution": {str(k): v for k, v in sorted(oracle_total.items())},
        "limitations": [
            "native decode candidates only; no fixed/smart table or user-dictionary candidates",
            "dictionary phrase evidence not filled (empty arrays by design in v1)",
            "syllables are native segmented zrm double-pinyin codes, not pinyin readings",
            "word codes use pypinyin default readings for the target word",
            "train/dev/test are source-file disjoint but not document-disjoint within a source",
        ],
    }
    manifest_path = guard_path(out / "manifest.json", label="manifest path")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in manifest["stats"].items() if not k.startswith("invalid")}, indent=2))
    print("oracle ranks (top):", dict(sorted(oracle_total.items())[:12]))


if __name__ == "__main__":
    main()
