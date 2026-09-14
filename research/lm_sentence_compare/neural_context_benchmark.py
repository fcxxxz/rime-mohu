"""Offline context-cap diagnostics using persistent, serialized neural score probes.

The corpus is JSONL with text/id/source/label fields; the native lexicon is TSV
code/text/rank/freq_rank/reading_freq. No training-disjointness is established.
Full two-letter-per-character lexical groups are preferred. Synthetic single-char
substitutions fill a shortfall only, and are labeled and reported separately.

--output names a new or empty directory. cases.jsonl freezes every candidate pool;
rows.jsonl streams every cold, warmup, and measured response. case_results.jsonl
contains repeat medians and competition ranks; comparisons.jsonl contains paired
baseline/optimized checks. manifest.json records hashes, limits, and methodology;
summary.json separates corpus quality, fixtures, context bins, and timing phases.

Each engine's first request is labeled cold before explicit full-sweep warmups.
This is process-first, not disk-cold: artifact hashing can warm the file cache.
Cap, case, and engine order rotate; candidate order stays fixed across all caps.
Only one request is outstanding at a time, including during process startup.
Probe elapsed_ms is scorer time, not frontend latency or model-loading time.
Exit 1 indicates a measured score, top-set, or rank equivalence failure; exit 2
indicates invalid input or a probe failure. Partial raw rows survive failures.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import select
import statistics
import subprocess
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

CAPS = (2, 4, 8, 16, 160)
ENGINES = ("baseline", "optimized")
CORPUS_KINDS = ("lexical_tail", "synthetic_char_substitution")
CONTEXT_BINS = ("0", "1-2", "3-4", "5-8", "9-16", "17-32", "33-160", "161+")
SYNTHETIC_POOL_LIMIT = 20
HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0002fa1f]+\Z")


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_key(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def context_bin(length):
    for upper, label in ((0, "0"), (2, "1-2"), (4, "3-4"), (8, "5-8"),
                         (16, "9-16"), (32, "17-32"), (160, "33-160")):
        if length <= upper:
            return label
    return "161+"


def read_lexicon(path):
    groups = defaultdict(set)
    word_codes = defaultdict(set)
    char_groups = defaultdict(set)
    char_codes = defaultdict(set)
    with Path(path).open(encoding="utf-8-sig") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip() or line.startswith("#"):
                continue
            fields = line.rstrip("\r\n").split("\t")
            if len(fields) < 2:
                raise ValueError(f"{path}:{number}: expected code/text TSV")
            code, text = fields[:2]
            if not re.fullmatch("[a-z]+", code) or not HAN.fullmatch(text):
                continue
            if len(text) == 1 and len(code) == 2:
                char_groups[code].add(text)
                char_codes[text].add(code)
            if len(text) in (2, 3) and len(code) == 2 * len(text):
                groups[code].add(text)
                word_codes[text].add(code)
    return groups, word_codes, char_groups, char_codes


def make_case(context, gold, code, candidates, kind, source):
    identifier = "tail-" + stable_key(f"{context}\0{gold}\0{code}\0{kind}")[:24]
    ordered = sorted(set(candidates), key=lambda text: (stable_key(identifier + "\0" + text), text))
    return {
        "id": identifier, "kind": kind, "source": source, "context": context,
        "context_length": len(context), "context_bin": context_bin(len(context)),
        "gold": gold, "gold_length": len(gold), "code": code,
        "candidates": ordered, "candidate_count": len(ordered),
        "quality_eligible": True,
    }


def stratified_cases(cases, limit):
    buckets = defaultdict(list)
    for case in sorted(cases, key=lambda item: item["id"]):
        buckets[case["context_bin"]].append(case)
    selected = []
    position = 0
    while len(selected) < limit:
        added = False
        for label in CONTEXT_BINS:
            if position < len(buckets[label]):
                selected.append(buckets[label][position])
                added = True
                if len(selected) == limit:
                    break
        if not added:
            break
        position += 1
    return selected


def build_dataset(lexicon, corpus, limit=200):
    if limit < 1:
        raise ValueError("limit must be positive")
    groups, word_codes, char_groups, char_codes = read_lexicon(lexicon)
    lexical = []
    fallback = []
    seen = set()
    records = 0
    tail_positions = 0
    with Path(corpus).open(encoding="utf-8-sig") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError as error:
                raise ValueError(f"{corpus}:{number}: invalid JSON") from error
            if not isinstance(record, dict) or not isinstance(record.get("text"), str):
                raise ValueError(f"{corpus}:{number}: expected an object with string text")
            records += 1
            text = " ".join(record["text"].split())
            while text and unicodedata.category(text[-1])[0] in "PZ":
                text = text[:-1]
            source = {"line": number, "corpus_id": record.get("id"),
                      "source": record.get("source"), "label": record.get("label")}
            for length in (2, 3):
                if len(text) <= length or not HAN.fullmatch(text[-length:]):
                    continue
                context, gold = text[:-length], text[-length:]
                if (context, gold) in seen:
                    continue
                seen.add((context, gold))
                tail_positions += 1
                codes = sorted(code for code in word_codes[gold] if len(groups[code]) >= 2)
                if codes:
                    code = codes[0]
                    case = make_case(context, gold, code, groups[code], "lexical_tail", source)
                    case["lexical_group_size"] = len(groups[code])
                    lexical.append(case)
                else:
                    fallback.append((context, gold, source))
    selected = stratified_cases(lexical, limit)
    synthetic = []
    if len(selected) < limit:
        for context, gold, source in fallback:
            codes = sorted(word_codes[gold])
            if not codes and all(char_codes[char] for char in gold):
                codes = ["".join(sorted(char_codes[char])[0] for char in gold)]
            for code in codes:
                readings = [code[index:index + 2] for index in range(0, len(code), 2)]
                if not all(char in char_groups[reading] for char, reading in zip(gold, readings)):
                    continue
                alternatives = {
                    gold[:index] + replacement + gold[index + 1:]
                    for index, reading in enumerate(readings)
                    for replacement in char_groups[reading]
                } - {gold}
                if not alternatives:
                    continue
                ordered = sorted(alternatives, key=lambda text: (stable_key(code + gold + text), text))
                case = make_case(context, gold, code,
                                 [gold] + ordered[:SYNTHETIC_POOL_LIMIT - 1],
                                 "synthetic_char_substitution", source)
                case["synthetic_pool_available"] = len(alternatives) + 1
                synthetic.append(case)
                break
        selected.extend(stratified_cases(synthetic, limit - len(selected)))
    return {
        "cases": selected,
        "metadata": {
            "corpus_sha256": sha256_file(corpus), "lexicon_sha256": sha256_file(lexicon),
            "corpus_records": records, "unique_tail_positions": tail_positions,
            "lexical_available": len(lexical), "synthetic_available_if_needed": len(synthetic),
            "requested_limit": limit, "selected": len(selected), "shortfall": limit - len(selected),
            "selected_by_kind": dict(Counter(case["kind"] for case in selected)),
            "selected_context_bins": dict(Counter(case["context_bin"] for case in selected)),
            "max_selected_context_length": max((case["context_length"] for case in selected), default=0),
            "max_candidate_pool": max((len(case["candidates"]) for case in selected), default=0),
            "selection": "stable SHA-256 ordering, round-robin context bins, lexical before synthetic",
            "normalization": "collapse whitespace; strip trailing Unicode punctuation/separators",
            "lexical_pool_limit": None, "synthetic_pool_limit": SYNTHETIC_POOL_LIMIT,
            "tail_lengths": [2, 3], "scan_limit": None,
        },
    }


def fixed_fixtures():
    actual20 = [
        "家给了", "嫁给了", "家给乐", "家给勒", "夹给了",
        "加给了", "价给了", "甲给了", "家给叻", "架给了",
        "佳给了", "假给了", "嫁给乐", "驾给了", "迦给了",
        "家给垃", "稼给了", "嘉给了", "伽给了", "荚给了",
    ]
    definitions = [
        ("fixture-actual20", "外婆", actual20, "嫁给了"),
        ("fixture-mixed", "甲乙丙丁" * 42 + "外🙂婆",
         ["家给了", "嫁给了", "家", "嫁给", "家给了", "🙂", "家🙂给", "嫁给了一个人", ""], None),
    ]
    return [
        {"id": identifier, "kind": "fixture", "context": context,
         "context_length": len(context), "context_bin": context_bin(len(context)),
         "candidates": candidates, "candidate_count": len(candidates),
         "gold": gold, "quality_eligible": False,
         "source": "tests/tigerengine_neural_rerank_test.cc actual20; synthetic mixed/OOV stress"}
        for identifier, context, candidates, gold in definitions
    ]


def annotate_vocabulary(cases, vocab):
    for case in cases:
        case["candidate_oov"] = {
            text: sorted(set(text) - vocab.keys())
            for text in case["candidates"] if set(text) - vocab.keys()
        }
        case["context_oov"] = sorted(set(case["context"]) - vocab.keys())
        case["quality_eligible"] = case["kind"] in CORPUS_KINDS and not case["candidate_oov"]


def rank_scores(candidates, scores, gold=None):
    if len(candidates) != len(scores) or not scores:
        raise ValueError("ranking requires one score per candidate")
    frequencies = Counter(scores)
    score_ranks = {}
    position = 1
    for score in sorted(frequencies, reverse=True):
        score_ranks[score] = position
        position += frequencies[score]
    ranks = [score_ranks[score] for score in scores]
    top_indices = [index for index, rank in enumerate(ranks) if rank == 1]
    tied = len(top_indices) != 1
    top1 = None if tied else candidates[top_indices[0]]
    gold_indices = [index for index, text in enumerate(candidates) if text == gold]
    return {
        "ranks": ranks, "top_indices": top_indices,
        "top_texts": sorted({candidates[index] for index in top_indices}),
        "top1": top1, "top_tied": tied,
        "gold_rank": min((ranks[index] for index in gold_indices), default=None),
        "gold_top1": None if tied or not gold_indices else top1 == gold,
    }


def measurement_schedule(cases, repeats=3, warmups=1):
    if not cases or repeats < 1 or warmups < 0:
        raise ValueError("need cases, positive repeats, and nonnegative warmups")
    first = next((case for case in cases if case["id"] == "fixture-actual20"), cases[0])
    case_positions = {case["id"]: index for index, case in enumerate(cases)}
    yield {"case_id": first["id"], "phase": "cold", "repeat": 0, "cap": 160,
           "engines": ENGINES}
    for phase, rounds in (("warmup", warmups), ("measured", repeats)):
        for repeat in range(rounds):
            offset = repeat % len(cases)
            rotated_cases = cases[offset:] + cases[:offset]
            for case in rotated_cases:
                case_index = case_positions[case["id"]]
                cap_offset = (repeat + case_index) % len(CAPS)
                rotated_caps = CAPS[cap_offset:] + CAPS[:cap_offset]
                for cap in rotated_caps:
                    engines = ENGINES if (repeat + case_index + CAPS.index(cap)) % 2 == 0 else ENGINES[::-1]
                    yield {"case_id": case["id"], "phase": phase, "repeat": repeat,
                           "cap": cap, "engines": engines}


def validate_response(response, candidate_count):
    if not isinstance(response, dict):
        raise ValueError("probe response must be a JSON object")
    scores = response.get("scores")
    if not isinstance(scores, list) or len(scores) != candidate_count:
        raise ValueError("probe returned a different candidate count")
    for value in scores + [response.get("elapsed_ms")]:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("probe scores and elapsed_ms must be finite numbers")
    if response["elapsed_ms"] < 0:
        raise ValueError("probe elapsed_ms must be nonnegative")
    for name in ("transformer_rows", "output_rows"):
        value = response.get(name)
        if type(value) is not int or value < 0:
            raise ValueError(f"probe {name} must be a nonnegative integer")
    return response


class Probe:
    def __init__(self, executable, model, vocab, stderr_path, timeout=120.0):
        self.timeout = timeout
        self.pending = bytearray()
        self.requests = 0
        self.stderr = Path(stderr_path).open("wb")
        try:
            self.process = subprocess.Popen(
                [str(executable), str(model), str(vocab)], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=self.stderr,
            )
        except BaseException:
            self.stderr.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            self.process.stdin.close()
        except BrokenPipeError:
            pass
        if exc_type is not None and self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        finally:
            self.process.stdout.close()
            self.stderr.close()

    def score(self, cap, context, candidates):
        if type(cap) is not int or not 0 <= cap <= 160 or not candidates:
            raise ValueError("request requires cap 0..160 and at least one candidate")
        fields = [str(cap), context, *candidates]
        if any(any(delimiter in field for delimiter in "\t\r\n") for field in fields):
            raise ValueError("TSV fields must not contain tabs or newlines")
        started = time.perf_counter()
        self.process.stdin.write(("\t".join(fields) + "\n").encode("utf-8"))
        self.process.stdin.flush()
        deadline = started + self.timeout
        while b"\n" not in self.pending:
            remaining = deadline - time.perf_counter()
            if remaining <= 0 or not select.select([self.process.stdout], [], [], remaining)[0]:
                raise TimeoutError(f"probe response timed out after {self.timeout}s")
            block = os.read(self.process.stdout.fileno(), 65536)
            if not block:
                raise RuntimeError(f"probe closed stdout (exit {self.process.poll()}); inspect stderr log")
            self.pending.extend(block)
            if len(self.pending) > 16 * 1024 * 1024:
                raise ValueError("probe response exceeds 16 MiB")
        line, _, remainder = self.pending.partition(b"\n")
        self.pending = bytearray(remainder)
        elapsed = (time.perf_counter() - started) * 1000.0
        response = validate_response(json.loads(line), len(candidates))
        self.requests += 1
        return {**response, "wall_ms": elapsed, "request_index": self.requests,
                "first_process_request": self.requests == 1}


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def distribution(values):
    return {"count": len(values), "p50": percentile(values, 0.5),
            "p95": percentile(values, 0.95),
            "min": min(values, default=None), "max": max(values, default=None)}


def quality_stats(case_rows):
    corpus = [row for row in case_rows if row["kind"] in CORPUS_KINDS]
    supported = [row for row in corpus if row["quality_eligible"]]
    eligible = [row for row in supported if row["gold_top1"] is not None]
    correct = sum(row["gold_top1"] for row in eligible)
    return {
        "corpus_cases": len(corpus), "oov_pools_excluded": len(corpus) - len(supported),
        "eligible": len(eligible), "ties_excluded": len(supported) - len(eligible),
        "gold_top1": correct, "gold_top1_rate": correct / len(eligible) if eligible else None,
        "mean_gold_rank_including_ties": (
            statistics.mean(row["gold_rank"] for row in supported) if supported else None),
    }


def latency_stats(case_rows):
    medians = [row["elapsed_median_ms"] for row in case_rows]
    return {"cases": len(case_rows), "case_median_p50_ms": percentile(medians, 0.5),
            "case_median_p95_ms": percentile(medians, 0.95),
            "transformer_rows": distribution([row["transformer_rows_median"] for row in case_rows]),
            "output_rows": distribution([row["output_rows_median"] for row in case_rows])}


def compare_caps(current, reference):
    pairs = [(row, reference[row["case_id"]]) for row in current
             if row["kind"] in CORPUS_KINDS and row["quality_eligible"]]
    eligible = [(row, full) for row, full in pairs
                if row["gold_top1"] is not None and full["gold_top1"] is not None]
    return {
        "pairs": len(pairs), "eligible": len(eligible), "ties_excluded": len(pairs) - len(eligible),
        "changed_top1": sum(row["top1"] != full["top1"] for row, full in eligible),
        "changed_top_set_including_ties": sum(row["top_texts"] != full["top_texts"] for row, full in pairs),
        "regressed": sum(full["gold_top1"] and not row["gold_top1"] for row, full in eligible),
        "improved": sum(row["gold_top1"] and not full["gold_top1"] for row, full in eligible),
    }


def equivalence_stats(comparisons):
    return {
        "pairs": len(comparisons),
        "score_failures": sum(not row["scores_within_tolerance"] for row in comparisons),
        "max_abs_score_error": max((row["max_abs_score_error"] for row in comparisons), default=None),
        "top_set_mismatches": sum(not row["top_set_equal"] for row in comparisons),
        "rank_mismatches": sum(not row["ranks_equal"] for row in comparisons),
        "ties_present": sum(row["ties_present"] for row in comparisons),
        "paired_baseline_minus_optimized_ms": distribution(
            [row["baseline_minus_optimized_ms"] for row in comparisons]),
    }


def aggregate_results(cases, rows, tolerance=1e-4):
    by_id = {case["id"]: case for case in cases}
    groups = defaultdict(list)
    pairs = defaultdict(dict)
    for row in rows:
        if row["phase"] != "measured":
            continue
        groups[row["case_id"], row["cap"], row["engine"]].append(row)
        pair = pairs[row["case_id"], row["cap"], row["repeat"]]
        if row["engine"] in pair:
            raise ValueError("duplicate measured pair/engine")
        pair[row["engine"]] = row
    case_rows = []
    for (case_id, cap, engine), repeats in sorted(groups.items()):
        case = by_id[case_id]
        columns = list(zip(*(row["scores"] for row in repeats)))
        scores = [statistics.median(column) for column in columns]
        ranked = rank_scores(case["candidates"], scores, case.get("gold"))
        case_rows.append({
            "case_id": case_id, "cap": cap, "engine": engine, "kind": case["kind"],
            "context_bin": case["context_bin"], "context_length": case["context_length"],
            "requested_context_length": min(cap, case["context_length"]),
            "candidate_count": len(case["candidates"]), "quality_eligible": case["quality_eligible"],
            "repeats": len(repeats), "scores": scores, **ranked,
            "elapsed_median_ms": statistics.median(row["elapsed_ms"] for row in repeats),
            "wall_median_ms": statistics.median(row["wall_ms"] for row in repeats),
            "transformer_rows_median": statistics.median(row["transformer_rows"] for row in repeats),
            "output_rows_median": statistics.median(row["output_rows"] for row in repeats),
            "max_repeat_score_span": max((max(column) - min(column) for column in columns), default=0),
            "repeat_top_sets": sorted({tuple(rank_scores(case["candidates"], row["scores"])["top_texts"])
                                       for row in repeats}),
        })
    indexed = {(row["case_id"], row["cap"], row["engine"]): row for row in case_rows}
    for row in case_rows:
        full = indexed[row["case_id"], 160, row["engine"]]
        row["gold_rank_delta_vs_cap160"] = (
            row["gold_rank"] - full["gold_rank"] if row["gold_rank"] is not None else None)
        row["top_set_changed_vs_cap160"] = row["top_texts"] != full["top_texts"]
    comparisons = []
    for (case_id, cap, repeat), pair in sorted(pairs.items()):
        if set(pair) != set(ENGINES):
            raise ValueError("incomplete baseline/optimized measured pair")
        baseline, optimized = pair["baseline"], pair["optimized"]
        case = by_id[case_id]
        baseline_rank = rank_scores(case["candidates"], baseline["scores"], case.get("gold"))
        optimized_rank = rank_scores(case["candidates"], optimized["scores"], case.get("gold"))
        maximum = max(abs(first - second) for first, second in zip(baseline["scores"], optimized["scores"]))
        comparisons.append({
            "case_id": case_id, "kind": case["kind"], "cap": cap, "repeat": repeat,
            "max_abs_score_error": maximum, "scores_within_tolerance": maximum <= tolerance,
            "top_set_equal": baseline_rank["top_texts"] == optimized_rank["top_texts"],
            "ranks_equal": baseline_rank["ranks"] == optimized_rank["ranks"],
            "baseline_gold_rank": baseline_rank["gold_rank"],
            "optimized_gold_rank": optimized_rank["gold_rank"],
            "ties_present": baseline_rank["top_tied"] or optimized_rank["top_tied"],
            "baseline_minus_optimized_ms": baseline["elapsed_ms"] - optimized["elapsed_ms"],
        })
    summary = {"score_abs_tolerance": tolerance, "by_cap": {}, "phases": {}}
    for cap in CAPS:
        cap_comparisons = [row for row in comparisons if row["cap"] == cap]
        report = {"equivalence": equivalence_stats(cap_comparisons), "vs_cap160": {}}
        for engine in ENGINES:
            selected = [row for row in case_rows if row["cap"] == cap and row["engine"] == engine]
            corpus = [row for row in selected if row["kind"] in CORPUS_KINDS]
            full = {row["case_id"]: row for row in case_rows if row["cap"] == 160 and row["engine"] == engine}
            report[engine] = {
                "quality": quality_stats(corpus), "latency": latency_stats(corpus),
                "by_kind": {
                    kind: {"quality": quality_stats([row for row in corpus if row["kind"] == kind]),
                           "latency": latency_stats([row for row in corpus if row["kind"] == kind])}
                    for kind in CORPUS_KINDS
                },
                "context_bins": {
                    label: {"quality": quality_stats([row for row in corpus if row["context_bin"] == label]),
                            "latency": latency_stats([row for row in corpus if row["context_bin"] == label]),
                            "vs_cap160": compare_caps([row for row in corpus if row["context_bin"] == label], full)}
                    for label in CONTEXT_BINS
                },
                "fixtures": {row["case_id"]: row for row in selected if row["kind"] == "fixture"},
            }
            report["vs_cap160"][engine] = compare_caps(corpus, full)
        report["equivalence_by_kind"] = {
            kind: equivalence_stats([row for row in cap_comparisons if row["kind"] == kind])
            for kind in (*CORPUS_KINDS, "fixture")
        }
        summary["by_cap"][str(cap)] = report
    for phase in ("cold", "warmup", "measured"):
        summary["phases"][phase] = {
            engine: {"elapsed_ms": distribution([row["elapsed_ms"] for row in rows
                                                 if row["phase"] == phase and row["engine"] == engine]),
                     "wall_ms": distribution([row["wall_ms"] for row in rows
                                              if row["phase"] == phase and row["engine"] == engine])}
            for engine in ENGINES
        }
    summary["equivalence_passed"] = all(
        row["scores_within_tolerance"] and row["top_set_equal"] and row["ranks_equal"]
        for row in comparisons
    )
    return {"summary": summary, "case_rows": case_rows, "comparisons": comparisons}


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                          encoding="utf-8")


def write_jsonl(path, rows):
    with Path(path).open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def run_benchmark(args):
    output = args.output.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("output must be a new or empty directory")
    paths = {name: getattr(args, name).resolve()
             for name in ("probe", "baseline_probe", "model", "vocab", "lexicon", "corpus")}
    for name, path in paths.items():
        if not path.is_file():
            raise ValueError(f"{name} is not a file: {path}")
    dataset = build_dataset(paths["lexicon"], paths["corpus"], args.limit)
    vocab = json.loads(paths["vocab"].read_text(encoding="utf-8"))
    if not isinstance(vocab, dict) or any(type(value) is not int or value < 0 for value in vocab.values()):
        raise ValueError("vocab must be a JSON token-to-nonnegative-integer mapping")
    cases = dataset["cases"] + fixed_fixtures()
    annotate_vocabulary(cases, vocab)
    artifacts = {name: {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256_file(path)}
                 for name, path in paths.items()}
    manifest = {
        "format_version": 1, "status": "running", "started_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "offline labeled diagnostic; NOT training-disjoint production acceptance",
        "artifacts": artifacts, "harness_sha256": sha256_file(__file__),
        "dataset": dataset["metadata"], "caps": list(CAPS), "repeats": args.repeats,
        "warmup_sweeps": args.warmups, "score_abs_tolerance": args.score_tolerance,
        "probe_timeout_seconds": args.timeout, "argv": args.invocation,
        "platform": platform.platform(), "machine": platform.machine(), "python": sys.version,
        "logical_cpus": os.cpu_count(),
        "thread_environment": {name: os.environ.get(name) for name in
                               ("VECLIB_MAXIMUM_THREADS", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS")},
        "methodology": {
            "cold": "one first request per fresh process at actual20/cap160, before all warmups",
            "warmup": "explicit sweeps over every case and cap; all retained but excluded from measured summaries",
            "rotation": "rotate cases by repeat, caps by repeat/case index, alternate paired engine order",
            "serialization": "lazy sequential process startup; one outstanding request globally",
            "quality": "rank elementwise median raw scores across repeats; exact top ties excluded from accuracy",
            "oov": "exclude whole candidate pools with any OOV candidate from quality, never modify pools",
            "latency": "probe elapsed_ms; per-case repeat medians then case-weighted p50/p95; paired deltas also saved",
            "baseline_counts": "zero means instrumentation unavailable, NOT zero model work",
            "equivalence": "all measured same-cap pairs, especially cap160; score tolerance AND exact ranks/top sets",
        },
        "limitations": [
            "Corpus labels are diagnostic occurrence labels, not human judgments of every alternative.",
            "No training overlap audit, production traffic sampling, confidence intervals, or frontend timing.",
            "Selection balances available context bins, not natural usage frequency; lexical readings may be polyphonic.",
            "Synthetic substitutes can be nonwords; synthetic and lexical metrics must not be conflated.",
            "Cap160 is the last 160 Unicode codepoints, not unbounded context; probes may further clip for model capacity.",
            "Context lengths are pre-OOV codepoints, not retained model tokens; corpus may contain few long contexts.",
            "Cold is process-first, not machine/disk-cold; hashing inputs warms filesystem caches.",
            "Two models remain resident though scoring is serialized; OS load and thermal state are uncontrolled.",
            "Measured latency covers this selected fixed pool distribution; actual20/mixed fixtures are reported separately.",
        ],
    }
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "manifest.json", manifest)
    write_jsonl(output / "cases.jsonl", cases)
    rows = []
    try:
        with ExitStack() as stack:
            stream = stack.enter_context((output / "rows.jsonl").open("w", encoding="utf-8"))
            probes = {}
            by_id = {case["id"]: case for case in cases}
            for pair_id, item in enumerate(measurement_schedule(cases, args.repeats, args.warmups)):
                case = by_id[item["case_id"]]
                for engine in item["engines"]:
                    if engine not in probes:
                        path = paths["probe" if engine == "optimized" else "baseline_probe"]
                        probes[engine] = stack.enter_context(Probe(
                            path, paths["model"], paths["vocab"], output / f"{engine}.stderr.log", args.timeout))
                    response = probes[engine].score(item["cap"], case["context"], case["candidates"])
                    row = {"sequence": len(rows), "pair_id": pair_id,
                           "case_id": case["id"], "kind": case["kind"], "engine": engine,
                           "phase": item["phase"], "repeat": item["repeat"], "cap": item["cap"],
                           "context_length": case["context_length"],
                           "requested_context_length": min(item["cap"], case["context_length"]),
                           "candidate_count": len(case["candidates"]), **response}
                    stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                    stream.flush()
                    rows.append(row)
        results = aggregate_results(cases, rows, args.score_tolerance)
        write_jsonl(output / "case_results.jsonl", results["case_rows"])
        write_jsonl(output / "comparisons.jsonl", results["comparisons"])
        write_json(output / "summary.json", results["summary"])
        manifest["status"] = "complete"
        manifest["equivalence_passed"] = results["summary"]["equivalence_passed"]
        manifest["rows_written"] = len(rows)
    except BaseException as error:
        manifest["status"] = "failed"
        manifest["error"] = f"{type(error).__name__}: {error}"
        manifest["rows_written"] = len(rows)
        raise
    finally:
        manifest["finished_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(output / "manifest.json", manifest)
    print(f"Saved {len(cases)} cases / {len(rows)} requests to {output}")
    return 0 if manifest["equivalence_passed"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("probe", "baseline-probe", "model", "vocab", "lexicon", "corpus", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=200, help="corpus case limit, excluding two fixtures (default: 200)")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--warmups", type=int, default=1, help="full unmeasured sweeps before measured repeats (default: 1)")
    parser.add_argument("--score-tolerance", type=float, default=1e-4)
    parser.add_argument("--timeout", type=float, default=120.0, help="seconds per probe response")
    args = parser.parse_args(argv)
    args.invocation = list(sys.argv[1:] if argv is None else argv)
    if args.limit < 1 or args.repeats < 1 or args.warmups < 0:
        parser.error("limit/repeats must be positive and warmups nonnegative")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("timeout must be positive and finite")
    if not math.isfinite(args.score_tolerance) or args.score_tolerance < 0:
        parser.error("score tolerance must be nonnegative and finite")
    try:
        return run_benchmark(args)
    except (ValueError, OSError, RuntimeError) as error:
        print(f"benchmark failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
