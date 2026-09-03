#!/usr/bin/env python3
"""Synchronize the Wanxiang Pinyin general dictionaries into Mohu."""
from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import os
import re
import socket
import sys
import tempfile
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

import zrmify
from tiger_aux import load_auxiliary_tsv

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tools" / "data" / "wanxiang"
MANIFEST = DATA / "manifest.json"
ENTRIES = DATA / "entries.tsv"
OUTPUT = ROOT / "mohu_zrm.wanxiang.dict.yaml"
REPORT = DATA / "sync_report.md"
RAW_ROOT = DATA / "raw"
API_URL = "https://api.github.com/repos/amzxyz/rime-wanxiang/commits/wanxiang"
CONTENTS_URL = "https://api.github.com/repos/amzxyz/rime-wanxiang/contents/{path}?ref={revision}"
BLOB_URL = "https://api.github.com/repos/amzxyz/rime-wanxiang/git/blobs/{sha}"
MAX_RESPONSE_BYTES = 128 * 1024 * 1024
MAX_SOURCE_BYTES = 64 * 1024 * 1024
PY_RE = re.compile(r"^[a-z]+$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class Candidate:
    text: str
    pinyin: str
    source: str
    upstream_weight: int


def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def save_manifest(manifest: dict) -> None:
    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def source_configs(manifest: dict) -> list[tuple[str, dict]]:
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("manifest files must be a non-empty object")
    result = []
    for name, config in files.items():
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9_]+", name):
            raise ValueError(f"invalid source name: {name!r}")
        if not isinstance(config, dict) or not isinstance(config.get("path"), str):
            raise ValueError(f"invalid source configuration: {name!r}")
        if not config["path"].startswith("dicts/") or not config["path"].endswith(".dict.yaml"):
            raise ValueError(f"source path is outside dicts: {config['path']!r}")
        result.append((name, config))
    return result


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, msg, headers, newurl):
        raise ValueError(f"redirect refused: {urlparse(newurl).geturl()}")


def _assert_public_host(host: str) -> None:
    try:
        addresses = {
            info[4][0]
            for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        }
    except OSError as exc:
        raise RuntimeError(f"failed to resolve {host}: {exc}") from exc
    if not addresses:
        raise RuntimeError(f"host has no resolved addresses: {host}")
    for address in addresses:
        parsed = ipaddress.ip_address(address)
        if (
            parsed.is_private
            or parsed.is_loopback
            or parsed.is_link_local
            or parsed.is_multicast
            or parsed.is_unspecified
            or parsed.is_reserved
        ):
            raise ValueError(f"refusing non-public address for {host}: {address}")


def fetch(url: str, *, expected_host: str, allowed_prefix: str | None = None) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != expected_host or parsed.port:
        raise ValueError(f"refusing unexpected URL: {url}")
    if allowed_prefix and not parsed.path.startswith(allowed_prefix):
        raise ValueError(f"refusing unexpected path: {url}")
    _assert_public_host(expected_host)
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "rime-mohu-wanxiang-sync",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers)
    try:
        with build_opener(NoRedirectHandler()).open(request, timeout=600) as response:
            if response.status != 200:
                raise ValueError(f"unexpected HTTP status {response.status}: {url}")
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_RESPONSE_BYTES:
                raise ValueError(f"response is too large: {url}")
            data = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"failed to download {url}: {exc}") from exc
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError(f"response is too large: {url}")
    return data


def normalize_syllable(value: str) -> str:
    # NFC 后 ü 家族（含带声调的 ǖǘǚǜ 预组合字符）必须先替换成 v 再剥声调，
    # 否则分音符会被当作声调组合符号剥掉，lǜ 会错误地变成 lu 而不是 lv。
    value = unicodedata.normalize("NFC", value.strip().lower())
    value = re.sub("[üǖǘǚǜ]", "v", value)
    value = unicodedata.normalize("NFD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r"[1-5]$", "", value)
    if not PY_RE.fullmatch(value):
        raise ValueError(f"invalid pinyin syllable: {value!r}")
    return value


def normalize_pinyin(value: str, text: str) -> str:
    syllables = value.split()
    if len(syllables) != len(text):
        raise ValueError(f"pinyin length mismatch for {text!r}: {value!r}")
    normalized = " ".join(normalize_syllable(item) for item in syllables)
    # This also verifies that every syllable is representable by our scheme.
    zrmify.zrmify(normalized)
    return normalized


def parse_source(path: Path, source: str) -> tuple[list[Candidate], int]:
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        marker = next(i for i, line in enumerate(lines) if line.strip() == "...")
    except StopIteration as exc:
        raise ValueError(f"missing Rime body marker: {path}") from exc
    candidates: list[Candidate] = []
    rejected = 0
    for number, line in enumerate(lines[marker + 1 :], marker + 2):
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) < 3:
            # 上游偶有缺权重列的行（如 jichu:546646），拒收并计数而非中断。
            rejected += 1
            continue
        text, pinyin, raw_weight = fields[:3]
        try:
            weight = int(raw_weight)
            normalized = normalize_pinyin(pinyin, text)
        except (ValueError, AssertionError):
            rejected += 1
            continue
        candidates.append(Candidate(text, normalized, source, weight))
    return candidates, rejected


def active_words() -> set[str]:
    names = ("chars", "base", "words", "tencent", "computer", "moe", "classics")
    result: set[str] = set()
    for name in names:
        path = ROOT / f"mohu_zrm.{name}.dict.yaml"
        if not path.exists():
            continue
        in_body = False
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() == "...":
                in_body = True
                continue
            if in_body and line and not line.startswith("#") and "\t" in line:
                result.add(line.split("\t", 1)[0])
    return result


def load_auxiliary() -> dict[str, list[str]]:
    return load_auxiliary_tsv(ROOT / "tools" / "data" / "tiger_aux.txt")


def select_candidates() -> tuple[list[Candidate], dict[str, int]]:
    manifest = load_manifest()
    grouped: dict[str, list[Candidate]] = defaultdict(list)
    rejected = 0
    for source, config in source_configs(manifest):
        path = DATA / config["raw_path"]
        candidates, count = parse_source(path, source)
        rejected += count
        for candidate in candidates:
            grouped[candidate.text].append(candidate)

    existing = active_words()
    selected: list[Candidate] = []
    duplicate_existing = 0
    conflicts = 0
    for text, values in grouped.items():
        if text in existing:
            duplicate_existing += 1
            continue
        pinyins = {value.pinyin for value in values}
        conflicts += len(pinyins) > 1
        selected.append(
            min(values, key=lambda value: (-value.upstream_weight, value.pinyin, value.source))
        )
    selected.sort(key=lambda value: (value.text, value.pinyin, value.source))
    # 没有主辅码的字无法生成符合魔虎词库约定的编码，拒收并计数。
    auxiliary = load_auxiliary()
    usable = [
        candidate for candidate in selected if all(char in auxiliary for char in candidate.text)
    ]
    return usable, {
        "source_rows_rejected": rejected,
        "source_words": sum(len(values) for values in grouped.values()),
        "duplicate_existing": duplicate_existing,
        "pronunciation_conflicts": conflicts,
        "missing_auxiliary": len(selected) - len(usable),
    }


def render_entries(entries: list[Candidate]) -> str:
    lines = ["text\tpinyin\tsource\tupstream_weight"]
    lines.extend(
        f"{entry.text}\t{entry.pinyin}\t{entry.source}\t{entry.upstream_weight}"
        for entry in entries
    )
    return "\n".join(lines) + "\n"


def render_dictionary(entries: list[Candidate], version: str, auxiliary: dict[str, list[str]]) -> str:
    lines = [
        "# Rime dictionary",
        "# encoding: utf-8",
        "# license: CC-BY-4.0 (upstream); generated adaptation",
        "# Source: tools/data/wanxiang/SOURCE.md",
        "",
        "---",
        "name: mohu_zrm.wanxiang",
        f'version: "{version}"',
        "sort: by_weight",
        "use_preset_vocabulary: false",
        "columns:",
        "  - text",
        "  - code",
        "  - weight",
        "...",
        "",
    ]
    for entry in entries:
        syllables = entry.pinyin.split()
        codes = [
            f"{zrmify.zrmify(syllable)};{auxiliary[char][0]}"
            for syllable, char in zip(syllables, entry.text)
        ]
        lines.append(f"{entry.text}\t{' '.join(codes)}\t20")
    return "\n".join(lines) + "\n"


def build() -> dict[str, int]:
    entries, stats = select_candidates()
    # 与已提交的生成词典对比增量：CI 的全新克隆里没有本地中间文件，
    # 只有 mohu_zrm.wanxiang.dict.yaml 始终存在，才能算出真实的每日增删。
    previous: dict[str, str] = {}
    if OUTPUT.exists():
        in_body = False
        for line in OUTPUT.read_text(encoding="utf-8").splitlines():
            if line.strip() == "...":
                in_body = True
                continue
            if in_body and line and not line.startswith("#") and "\t" in line:
                fields = line.split("\t")
                previous[fields[0]] = fields[1]
    current = {entry.text: zrmify.zrmify(entry.pinyin) for entry in entries}
    stats.update(
        added=len(set(current) - set(previous)),
        removed=len(set(previous) - set(current)),
        pronunciation_changed=sum(
            word in previous and previous[word] != code for word, code in current.items()
        ),
        selected=len(entries),
    )
    ENTRIES.write_text(render_entries(entries), encoding="utf-8")
    revision = load_manifest()["revision"]
    OUTPUT.write_text(render_dictionary(entries, revision[:12], load_auxiliary()), encoding="utf-8")
    REPORT.write_text(render_report(stats, revision), encoding="utf-8")
    return stats


def render_report(stats: dict[str, int], revision: str) -> str:
    return "\n".join(
        [
            "# 万象同步报告",
            "",
            f"- upstream revision: `{revision}`",
            f"- selected entries: {stats['selected']}",
            f"- added: {stats['added']}",
            f"- removed: {stats['removed']}",
            f"- pronunciation changed: {stats['pronunciation_changed']}",
            f"- duplicate existing words: {stats['duplicate_existing']}",
            f"- pronunciation conflicts: {stats['pronunciation_conflicts']}",
            f"- rejected source rows: {stats['source_rows_rejected']}",
            f"- dropped for missing auxiliary: {stats.get('missing_auxiliary', 0)}",
            "",
            "The generated dictionary uses a fixed local weight of 20; upstream weights are retained only in entries.tsv.",
            "",
        ]
    )


def verify_snapshots(manifest: dict) -> None:
    revision = manifest.get("revision")
    if not isinstance(revision, str) or not COMMIT_RE.fullmatch(revision):
        raise ValueError("manifest revision must be a 40-character commit SHA")
    for source, config in source_configs(manifest):
        path = DATA / config["raw_path"]
        if not path.is_file():
            raise ValueError(f"missing raw snapshot: {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        expected = config.get("sha256")
        if not isinstance(expected, str) or not SHA_RE.fullmatch(expected) or digest != expected:
            raise ValueError(f"snapshot hash mismatch: {source}")


def check() -> dict[str, int]:
    manifest = load_manifest()
    verify_snapshots(manifest)
    stats = build()
    entries, _ = select_candidates()
    expected = render_dictionary(entries, manifest["revision"][:12], load_auxiliary())
    if OUTPUT.read_text(encoding="utf-8") != expected:
        raise ValueError("generated dictionary is not deterministic")
    return stats


def fetch_json(url: str, *, allowed_prefix: str) -> dict:
    payload = fetch(url, expected_host="api.github.com", allowed_prefix=allowed_prefix)
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid GitHub JSON response: {url}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"unexpected GitHub response shape: {url}")
    return value


def download_source(config: dict, revision: str) -> tuple[bytes, str]:
    metadata = fetch_json(
        CONTENTS_URL.format(path=config["path"], revision=revision),
        allowed_prefix="/repos/amzxyz/rime-wanxiang/contents/dicts/",
    )
    blob_sha = metadata.get("sha")
    size = metadata.get("size")
    if not isinstance(blob_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", blob_sha):
        raise ValueError(f"invalid upstream blob SHA for {config['path']}")
    if not isinstance(size, int) or size < 0 or size > MAX_SOURCE_BYTES:
        raise ValueError(f"upstream source is too large or has invalid size: {config['path']}")
    blob: dict | None = None
    for attempt in range(3):
        try:
            blob = fetch_json(
                BLOB_URL.format(sha=blob_sha),
                allowed_prefix="/repos/amzxyz/rime-wanxiang/git/blobs/",
            )
            break
        except (RuntimeError, ValueError) as exc:
            print(
                f"retrying blob download for {config['path']} (attempt {attempt + 1}): {exc}",
                file=sys.stderr,
            )
            time.sleep(5 * (attempt + 1))
    if blob is None:
        raise RuntimeError(f"failed to download blob for {config['path']} after retries")
    if blob.get("encoding") != "base64" or not isinstance(blob.get("content"), str):
        raise ValueError(f"GitHub did not return base64 blob content: {config['path']}")
    try:
        # GitHub 的 blob API 会在 base64 内容中每 60 字符插入换行。
        content = "".join(blob["content"].split())
        data = base64.b64decode(content, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid base64 blob content: {config['path']}") from exc
    if len(data) != size:
        raise ValueError(f"upstream blob size mismatch: {config['path']}")
    digest = hashlib.sha256(data).hexdigest()
    expected = config.get("sha256")
    if isinstance(expected, str) and expected and digest != expected:
        raise ValueError(f"manifest hash mismatch: {config['path']}")
    return data, digest


def download_revision(manifest: dict, revision: str) -> None:
    downloads: list[tuple[dict, bytes, str]] = []
    for source, config in source_configs(manifest):
        data, digest = download_source(config, revision)
        downloads.append((config, data, digest))
    for config, data, digest in downloads:
        config["sha256"] = digest
        path = DATA / config["raw_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            handle.write(data)
            temporary = Path(handle.name)
        temporary.replace(path)


def sync() -> None:
    manifest = load_manifest()
    revision = manifest["revision"]
    if not isinstance(revision, str) or not COMMIT_RE.fullmatch(revision):
        raise ValueError("manifest revision must be a 40-character commit SHA")
    download_revision(manifest, revision)
    save_manifest(manifest)
    build()


def update() -> bool:
    manifest = load_manifest()
    payload = json.loads(fetch(API_URL, expected_host="api.github.com", allowed_prefix="/repos/amzxyz/rime-wanxiang/commits/"))
    revision = payload.get("sha")
    if not isinstance(revision, str) or not COMMIT_RE.fullmatch(revision):
        raise ValueError("GitHub API returned an invalid revision")
    if revision == manifest.get("revision"):
        return False
    manifest["revision"] = revision
    download_revision(manifest, revision)
    save_manifest(manifest)
    build()
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("sync", "build", "check", "update"))
    args = parser.parse_args(argv)
    try:
        if args.command == "sync":
            sync()
            build()
        elif args.command == "build":
            build()
        elif args.command == "check":
            check()
        else:
            changed = update()
            print("updated" if changed else "upstream unchanged")
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"wanxiang sync failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
