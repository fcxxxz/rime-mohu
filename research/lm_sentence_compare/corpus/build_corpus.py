#!/usr/bin/env python3
"""Build the fixed 20,000-sentence corpus used by the LM comparison.

The source files are downloaded directly rather than through the Hugging Face
datasets-server API. This makes the run reproducible on machines where the
viewer endpoint is unavailable and lets the manifest pin every byte consumed.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.request
import zipfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Iterable, Mapping, Sequence

HERE = Path(__file__).resolve().parent.parent
DEFAULT_OUT = HERE

TNEWS_URL = "https://storage.googleapis.com/cluebenchmark/tasks/tnews_public.zip"
LCCC_URL = (
    "https://huggingface.co/datasets/silver/lccc/resolve/"
    "5bd582fa28cd7143f2f9c852e08e23089d677c44/lccc_base_test.jsonl.gz?download=true"
)
TNEWS_SHA256 = "77c476e70cfe0b014a81b84c6e1db2142a8a2f52f4ae0a8216aa75e673933462"
LCCC_SHA256 = "cf8757587bdb8f360cc94fc38baadf9e185bad65a26155527a8430c048676016"
TNEWS_COMMIT = "CLUE:tnews_public-2021-07-25"
LCCC_COMMIT = "silver/lccc@5bd582fa28cd7143f2f9c852e08e23089d677c44"

TARGET_NEWS = 10_000
TARGET_DAILY = 10_000
MIN_LEN = 6
MAX_LEN = 24

TNEWS_LABELS = {
    "100": "news_story",
    "101": "news_culture",
    "102": "news_entertainment",
    "103": "news_sports",
    "104": "news_finance",
    "106": "news_house",
    "107": "news_car",
    "108": "news_edu",
    "109": "news_tech",
    "110": "news_military",
    "112": "news_travel",
    "113": "news_world",
    "114": "news_stock",
    "115": "news_agriculture",
    "116": "news_game",
}

_CJK = "\u3400-\u4dbf\u4e00-\u9fff"
_CJK_RE = re.compile(rf"^[{_CJK}]+$")
_LEADING_NON_CJK = re.compile(rf"^[^{_CJK}]+")
_TRAILING_NON_CJK = re.compile(rf"[^{_CJK}]+$")
_SPACE = re.compile(r"\s+")
try:
    import opencc  # type: ignore

    _T2S = opencc.OpenCC("t2s")
except ImportError:  # pragma: no cover - project dependency is normally present
    _T2S = None


def clean_text(text: str) -> str | None:
    """Normalize one utterance and keep only bounded, pure-CJK text."""

    if not isinstance(text, str):
        return None
    text = _SPACE.sub("", text.strip())
    if _T2S is not None:
        text = _T2S.convert(text)
    text = _LEADING_NON_CJK.sub("", text)
    text = _TRAILING_NON_CJK.sub("", text)
    if not MIN_LEN <= len(text) <= MAX_LEN or _CJK_RE.fullmatch(text) is None:
        return None
    return text


def _stable_key(seed: int, value: str) -> tuple[bytes, str]:
    digest = hashlib.sha256(f"{seed}:{value}".encode("utf-8")).digest()
    return digest, value


def read_tnews(path: str | Path) -> list[dict[str, str]]:
    """Read the labelled TNews training split from the public ZIP."""

    result: list[dict[str, str]] = []
    seen: set[str] = set()
    with zipfile.ZipFile(path) as archive:
        member = "train.json"
        if member not in archive.namelist():
            raise ValueError(f"TNews archive does not contain {member}")
        with archive.open(member) as stream:
            for line_number, raw in enumerate(stream, start=1):
                if not raw.strip():
                    continue
                row = json.loads(raw.decode("utf-8"))
                text = clean_text(row.get("sentence", ""))
                label = TNEWS_LABELS.get(str(row.get("label", "")))
                if text is None or label is None or text in seen:
                    continue
                seen.add(text)
                result.append(
                    {
                        "id": f"tnews:{line_number}",
                        "source": "news",
                        "label": label,
                        "text": text,
                    }
                )
    return result


def read_lccc(path: str | Path) -> list[dict[str, str]]:
    """Flatten the LCCC test dialogues into unique daily utterances."""

    result: list[dict[str, str]] = []
    seen: set[str] = set()
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            dialogue = json.loads(line)
            if not isinstance(dialogue, list):
                continue
            for utterance_index, utterance in enumerate(dialogue):
                text = clean_text(utterance)
                if text is None or text in seen:
                    continue
                seen.add(text)
                result.append(
                    {
                        "id": f"lccc:{line_number}:{utterance_index}",
                        "source": "daily",
                        "label": "daily",
                        "text": text,
                    }
                )
    return result


def _allocate_counts(groups: Mapping[str, Sequence[object]], quota: int) -> dict[str, int]:
    total = sum(len(values) for values in groups.values())
    if total < quota:
        raise ValueError(f"source has fewer than required quota ({total} < {quota})")
    names = sorted(groups)
    if not names:
        raise ValueError("cannot sample an empty source")
    raw = {name: quota * len(groups[name]) / total for name in names}
    counts = {name: int(raw[name]) for name in names}
    remainder = quota - sum(counts.values())
    for name in sorted(names, key=lambda item: (-(raw[item] - counts[item]), item))[:remainder]:
        counts[name] += 1
    return counts


def _stratified_sample(rows: Sequence[dict[str, str]], quota: int, seed: int) -> list[dict[str, str]]:
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[row["label"]].append(row)
    counts = _allocate_counts(groups, quota)
    selected: list[dict[str, str]] = []
    for index, label in enumerate(sorted(groups)):
        bucket = sorted(groups[label], key=lambda row: _stable_key(seed + index, row["id"]))
        selected.extend(bucket[: counts[label]])
    selected.sort(key=lambda row: _stable_key(seed ^ 0x5EED, row["id"]))
    return selected


def sample_corpus(news: Sequence[dict[str, str]], daily: Sequence[dict[str, str]], *,
                  news_count: int, daily_count: int, seed: int) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Return exact, deterministic news and daily quotas."""

    if news_count <= 0 or daily_count <= 0:
        raise ValueError("news and daily quotas must be positive")
    if len(news) < news_count:
        raise ValueError(f"news quota unavailable: {len(news)} < {news_count}")
    if len(daily) < daily_count:
        raise ValueError(f"daily quota unavailable: {len(daily)} < {daily_count}")
    selected_news = _stratified_sample(news, news_count, seed)
    news_texts = {row["text"] for row in selected_news}
    daily_pool = [row for row in daily if row["text"] not in news_texts]
    if len(daily_pool) < daily_count:
        raise ValueError(
            "daily quota unavailable after cross-source dedup: "
            f"{len(daily_pool)} < {daily_count}"
        )
    ordered_daily = sorted(daily_pool, key=lambda row: _stable_key(seed + 1, row["id"]))
    selected_daily = ordered_daily[:daily_count]
    return selected_news, selected_daily


def write_corpus(path: str | Path, rows: Iterable[Mapping[str, str]]) -> int:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    counters: Counter[str] = Counter()
    count = 0
    seen: set[str] = set()
    with destination.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            text = str(row["text"])
            if text in seen:
                continue
            seen.add(text)
            source = str(row["source"])
            index = counters[source]
            counters[source] += 1
            output = {
                "id": f"{source}-{index:05d}",
                "source": source,
                "label": str(row["label"]),
                "text": text,
            }
            stream.write(json.dumps(output, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_file(url: str, destination: str | Path, expected_sha256: str) -> str:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and sha256_file(destination) == expected_sha256:
        return expected_sha256
    with tempfile.NamedTemporaryFile(
        prefix=destination.name + ".",
        dir=destination.parent,
        delete=False,
    ) as temporary_file:
        temporary = Path(temporary_file.name)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "rime-mohu-lm-benchmark/1.0"})
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
        digest = sha256_file(temporary)
        if digest != expected_sha256:
            raise ValueError(f"SHA-256 mismatch for {url}: expected {expected_sha256}, got {digest}")
        temporary.replace(destination)
        return digest
    finally:
        temporary.unlink(missing_ok=True)


def _stage_source(path: Path, destination: Path, expected: str) -> tuple[str, bool]:
    source_digest = sha256_file(path)
    if source_digest != expected:
        raise ValueError(
            f"SHA-256 mismatch for {path}: expected {expected}, got {source_digest}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    if path.resolve() == destination.resolve():
        return source_digest, True
    with tempfile.NamedTemporaryFile(
        prefix=destination.name + ".",
        dir=destination.parent,
        delete=False,
    ) as temporary_file:
        temporary = Path(temporary_file.name)
    try:
        shutil.copy2(path, temporary)
        staged_digest = sha256_file(temporary)
        if staged_digest != expected:
            raise ValueError(
                f"SHA-256 mismatch for {path}: expected {expected}, got {staged_digest}"
            )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return source_digest, True


def build_corpus(*, out: str | Path = DEFAULT_OUT, seed: int = 20260829,
                 news_count: int = TARGET_NEWS, daily_count: int = TARGET_DAILY,
                 tnews_zip: str | Path | None = None,
                 lccc_gz: str | Path | None = None) -> dict[str, object]:
    out = Path(out)
    source_dir = out / "sources"
    if tnews_zip is None:
        tnews_path = source_dir / "tnews_public.zip"
        tnews_digest = download_file(TNEWS_URL, tnews_path, TNEWS_SHA256)
        tnews_pinned = True
    else:
        tnews_digest, tnews_pinned = _stage_source(Path(tnews_zip), source_dir / "tnews_public.zip", TNEWS_SHA256)
        tnews_path = source_dir / "tnews_public.zip"
    if lccc_gz is None:
        lccc_path = source_dir / "lccc_base_test.jsonl.gz"
        lccc_digest = download_file(LCCC_URL, lccc_path, LCCC_SHA256)
        lccc_pinned = True
    else:
        lccc_digest, lccc_pinned = _stage_source(Path(lccc_gz), source_dir / "lccc_base_test.jsonl.gz", LCCC_SHA256)
        lccc_path = source_dir / "lccc_base_test.jsonl.gz"

    news = read_tnews(tnews_path)
    daily = read_lccc(lccc_path)
    selected_news, selected_daily = sample_corpus(
        news, daily, news_count=news_count, daily_count=daily_count, seed=seed
    )
    corpus_dir = out / "corpus"
    news_path = corpus_dir / "news.jsonl"
    daily_path = corpus_dir / "daily.jsonl"
    all_path = corpus_dir / "sentences.jsonl"
    news_written = write_corpus(news_path, selected_news)
    daily_written = write_corpus(daily_path, selected_daily)
    all_written = write_corpus(all_path, (*selected_news, *selected_daily))
    expected_total = len(selected_news) + len(selected_daily)
    if news_written != len(selected_news) or daily_written != len(selected_daily) or all_written != expected_total:
        raise ValueError(
            "corpus writer dropped rows: "
            f"news={news_written}/{len(selected_news)}, "
            f"daily={daily_written}/{len(selected_daily)}, "
            f"total={all_written}/{expected_total}"
        )

    manifest: dict[str, object] = {
        "version": 2,
        "generated_date": str(date.today()),
        "seed": seed,
        "filter": {"min_chars": MIN_LEN, "max_chars": MAX_LEN, "pure_cjk": True},
        "counts": {
            "available": {"news": len(news), "daily": len(daily)},
            "selected": {"news": len(selected_news), "daily": len(selected_daily), "total": len(selected_news) + len(selected_daily)},
            "labels": dict(Counter(row["label"] for row in selected_news)),
        },
        "sources": {
            "news": {"url": TNEWS_URL, "revision": TNEWS_COMMIT, "sha256": tnews_digest, "pinned": tnews_pinned, "license": "CLUE public dataset; see source terms"},
            "daily": {"url": LCCC_URL, "revision": LCCC_COMMIT, "sha256": lccc_digest, "pinned": lccc_pinned, "license": "MIT metadata on HF mirror; original corpus terms apply"},
        },
        "files": {
            "news": {"path": str(news_path.relative_to(out)), "sha256": sha256_file(news_path)},
            "daily": {"path": str(daily_path.relative_to(out)), "sha256": sha256_file(daily_path)},
            "sentences": {"path": str(all_path.relative_to(out)), "sha256": sha256_file(all_path)},
        },
    }
    manifest_path = out / "corpus-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the fixed TNews/LCCC benchmark corpus")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--seed", type=int, default=20260829)
    parser.add_argument("--news-count", type=int, default=TARGET_NEWS)
    parser.add_argument("--daily-count", type=int, default=TARGET_DAILY)
    parser.add_argument("--tnews-zip", type=Path)
    parser.add_argument("--lccc-gz", type=Path)
    args = parser.parse_args()
    manifest = build_corpus(
        out=args.out,
        seed=args.seed,
        news_count=args.news_count,
        daily_count=args.daily_count,
        tnews_zip=args.tnews_zip,
        lccc_gz=args.lccc_gz,
    )
    print(json.dumps(manifest["counts"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
