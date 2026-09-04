#!/usr/bin/env python3
"""Build the reviewed classical-text smart dictionary deterministically.

The source manifest and reviewed TSV are deliberately separate from the Rime
output. Network synchronization is out of scope for this offline build step.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
import sys
import unicodedata
import urllib.parse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import opencc

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tools" / "data" / "classics"
MANIFEST = DATA / "sources.yaml"
ENTRIES = DATA / "entries.tsv"
OVERRIDES = DATA / "pinyin_overrides.tsv"
LONG_ENTRIES = DATA / "long_entries.txt"
OUTPUT = ROOT / "mohu_zrm.classics.dict.yaml"
ACTIVE_TABLES = (
    ROOT / "mohu_zrm.base.dict.yaml",
    ROOT / "mohu_zrm.words.dict.yaml",
    ROOT / "mohu_zrm.tencent.dict.yaml",
    ROOT / "mohu_zrm.computer.dict.yaml",
    ROOT / "mohu_zrm.moe.dict.yaml",
)
AUXILIARY = ROOT / "tools" / "data" / "tiger_aux.txt"
READINGS = ROOT / "tools" / "data" / "pinyin_simp.txt"
WIKISOURCE_HOST = "zh.wikisource.org"
WIKISOURCE_API_PATH = "/w/api.php"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
T2S = opencc.OpenCC("t2s")
HAN_RE = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0003347f]+$")
PY_RE = re.compile(r"^[a-z]+$")

HEADER = """# Rime dictionary
# encoding: utf-8
# license: {license}
# Generated from reviewed classical-text entries; see tools/data/classics/SOURCE.md

---
name: mohu_zrm.classics
version: \"{version}\"
sort: by_weight
use_preset_vocabulary: false
columns:
  - text
  - code
  - weight
...
"""


@dataclass(frozen=True)
class Entry:
    entry_id: str
    source_id: str
    work: str
    locator: str
    layer: str
    text: str
    pinyin: str
    weight: int
    review: str


def load_manifest() -> dict[str, dict[str, object]]:
    # sources.yaml is JSON-compatible YAML so this build has no PyYAML dependency.
    try:
        raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON-compatible manifest: {MANIFEST}") from exc
    sources = raw.get("sources") if isinstance(raw, dict) else None
    if not isinstance(sources, list):
        raise ValueError("sources manifest must contain a sources list")
    result: dict[str, dict[str, object]] = {}
    required = (
        "id", "title", "url", "revision", "license", "raw_path", "sha256", "status"
    )
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("each source manifest row must be an object")
        missing = [key for key in required if key not in source]
        if missing:
            raise ValueError(f"source manifest row missing: {', '.join(missing)}")
        source_id = source["id"]
        if not isinstance(source_id, str) or not source_id or source_id in result:
            raise ValueError(f"invalid or duplicate source id: {source_id!r}")
        result[source_id] = source
    return result


def source_is_verified(source: dict[str, object]) -> bool:
    raw_path = source.get("raw_path")
    sha256 = source.get("sha256")
    if not (
        source.get("status") == "verified"
        and isinstance(source.get("revision"), str)
        and bool(source["revision"])
        and source.get("license") == "CC-BY-SA 4.0"
        and source.get("redistribution") == "approved"
        and isinstance(source.get("base_text"), str)
        and bool(source["base_text"])
        and source.get("proofread") in {"proofread", "validated"}
        and isinstance(raw_path, str)
        and bool(raw_path)
        and isinstance(sha256, str)
        and bool(re.fullmatch(r"[0-9a-fA-F]{64}", sha256))
    ):
        return False
    snapshot = (DATA / raw_path).resolve()
    raw_root = (DATA / "raw").resolve()
    if snapshot.parent != raw_root and raw_root not in snapshot.parents:
        return False
    if not snapshot.is_file():
        return False
    return hashlib.sha256(snapshot.read_bytes()).hexdigest() == sha256.lower()


def load_auxiliary() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for number, raw in enumerate(AUXILIARY.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not raw or raw.startswith("#"):
            continue
        parts = raw.split("\t")
        if len(parts) < 2 or len(parts[0]) != 1:
            raise ValueError(f"invalid auxiliary row at {AUXILIARY}:{number}")
        # 首列为正常辅码；13/14 位兼容码仅供查阅，构词仍用正常辅码。
        result[parts[0]] = parts[1].split()
    return result


def load_readings() -> dict[str, set[str]]:
    readings: dict[str, set[str]] = defaultdict(set)
    for number, raw in enumerate(READINGS.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not raw or raw.startswith("#"):
            continue
        fields = raw.split("\t")
        if len(fields) < 2 or not fields[0] or not fields[1]:
            raise ValueError(f"invalid reading row at {READINGS}:{number}")
        if len(fields[0]) != 1:
            continue
        if not PY_RE.fullmatch(fields[1]):
            raise ValueError(f"invalid character reading at {READINGS}:{number}")
        readings[fields[0]].add(fields[1])
    return dict(readings)


def strip_tone(value: str) -> str:
    value = unicodedata.normalize("NFD", value.lower())
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.replace("ü", "v").replace("ê", "e")
    return re.sub(r"[1-5]$", "", value)


def load_overrides() -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    if not OVERRIDES.exists():
        return result
    for number, raw in enumerate(OVERRIDES.read_text(encoding="utf-8").splitlines(), 1):
        if not raw or raw.startswith("#"):
            continue
        fields = raw.split("\t")
        if len(fields) != 3 or not fields[0] or not fields[1] or not fields[2]:
            raise ValueError(f"invalid pronunciation override at {OVERRIDES}:{number}")
        if fields[0] in result:
            raise ValueError(f"duplicate pronunciation override at {OVERRIDES}:{number}")
        result[fields[0]] = (fields[1], fields[2])
    return result


def load_entries(sources: dict[str, dict[str, object]]) -> list[Entry]:
    entries: list[Entry] = []
    entry_ids: set[str] = set()
    long_entries = {
        line.strip()
        for line in LONG_ENTRIES.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    if not ENTRIES.exists():
        return entries
    for number, raw in enumerate(ENTRIES.read_text(encoding="utf-8").splitlines(), 1):
        if not raw or raw.startswith("#"):
            continue
        fields = raw.split("\t")
        if len(fields) != 9:
            raise ValueError(f"expected 9 columns at {ENTRIES}:{number}")
        entry = Entry(
            fields[0], fields[1], fields[2], fields[3], fields[4], fields[5], fields[6],
            int(fields[7]), fields[8],
        )
        if entry.source_id not in sources:
            raise ValueError(f"unknown source {entry.source_id!r} at {ENTRIES}:{number}")
        if not entry.entry_id or entry.entry_id in entry_ids:
            raise ValueError(f"empty or duplicate entry id at {ENTRIES}:{number}")
        entry_ids.add(entry.entry_id)
        if not entry.work or not entry.locator or not entry.layer or not entry.pinyin:
            raise ValueError(f"missing provenance or pronunciation at {ENTRIES}:{number}")
        source = sources[entry.source_id]
        if not source_is_verified(source):
            raise ValueError(f"source {entry.source_id!r} is not verified")
        if entry.review != "approved":
            raise ValueError(f"entry {entry.entry_id!r} is not approved")
        if entry.layer not in {"title", "name", "allusion", "phrase", "line"}:
            raise ValueError(f"unsupported layer at {ENTRIES}:{number}: {entry.layer!r}")
        if entry.layer in {"title", "name", "allusion"}:
            valid_length = 2 <= len(entry.text) <= 6
        elif len(entry.text) >= 9:
            valid_length = len(entry.text) <= 10 and entry.text in long_entries
        else:
            valid_length = 4 <= len(entry.text) <= 8
        if not valid_length or not HAN_RE.fullmatch(entry.text):
            raise ValueError(f"invalid Han entry length or characters: {entry.entry_id!r}")
        if T2S.convert(entry.text) != entry.text:
            raise ValueError(f"entry is not simplified: {entry.entry_id!r}")
        if any(char in entry.text for char in "，。！？；：、‘’“”《》（）【】,!.?;:"):
            raise ValueError(f"punctuation in entry {entry.entry_id!r}")
        if entry.weight != 1:
            raise ValueError(f"classics entries must use weight 1 at {ENTRIES}:{number}")
        entries.append(entry)
    return entries


def parse_columns(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    in_header = True
    columns: list[str] = []
    in_columns = False
    for line in lines:
        if line.strip() == "...":
            break
        if line.startswith("columns:"):
            in_columns = True
            continue
        if in_columns:
            match = re.match(r"^\s+-\s+(\w+)\s*$", line)
            if match:
                columns.append(match.group(1))
            elif line and not line.startswith(" "):
                in_columns = False
    if not columns:
        columns = ["text", "code", "weight"]
    if "text" not in columns:
        raise ValueError(f"dictionary has no text column: {path}")
    return columns


def existing_texts() -> set[str]:
    result: set[str] = set()
    for path in ACTIVE_TABLES:
        columns = parse_columns(path)
        text_index = columns.index("text")
        in_body = False
        for raw in path.read_text(encoding="utf-8").splitlines():
            if raw.strip() == "...":
                in_body = True
                continue
            if not in_body or not raw or raw.startswith("#") or "\t" not in raw:
                continue
            fields = raw.split("\t")
            if text_index < len(fields) and fields[text_index]:
                result.add(fields[text_index])
    return result


def build_rows(
    entries: list[Entry],
    auxiliary: dict[str, list[str]],
    readings: dict[str, set[str]],
    overrides: dict[str, tuple[str, str]],
) -> tuple[list[str], Counter[str]]:
    stats: Counter[str] = Counter()
    seen: dict[str, tuple[tuple[str, ...], int, tuple[str, ...]]] = {}
    for entry in entries:
        pinyin = overrides.get(entry.entry_id, (entry.pinyin, ""))[0]
        syllables = tuple(strip_tone(value) for value in pinyin.split())
        if len(syllables) != len(entry.text):
            raise ValueError(f"pinyin count mismatch: {entry.entry_id}")
        tokens: list[str] = []
        for char, syllable in zip(entry.text, syllables):
            if not PY_RE.fullmatch(syllable):
                raise ValueError(f"invalid pinyin {syllable!r}: {entry.entry_id}")
            known = readings.get(char, set())
            if syllable not in known:
                raise ValueError(
                    f"unverified reading {char}/{syllable}: {entry.entry_id}"
                )
            if len(known) > 1 and entry.entry_id not in overrides:
                raise ValueError(
                    f"polyphonic entry requires an evidenced override: {entry.entry_id}"
                )
            try:
                double = __import__("zrmify").zrmify1(syllable)
            except Exception as exc:
                raise ValueError(f"invalid pinyin {syllable!r}: {entry.entry_id}") from exc
            auxes = auxiliary.get(char)
            if not auxes:
                raise ValueError(f"missing Tiger auxiliary for {char}: {entry.entry_id}")
            tokens.append(f"{double};{auxes[0]}")
        encoded = (syllables, entry.weight, tuple(tokens))
        previous = seen.get(entry.text)
        if previous is not None:
            if previous != encoded:
                raise ValueError(f"conflicting duplicate text: {entry.text}")
            stats["duplicate_classics"] += 1
            continue
        seen[entry.text] = encoded
        stats["emitted"] += 1

    output = [
        f"{text}\t{' '.join(tokens)}\t{weight}\n"
        for text, (_pinyin, weight, tokens) in sorted(seen.items())
    ]
    return output, stats


def render(entries: list[Entry], sources: dict[str, dict[str, object]]) -> tuple[str, Counter[str]]:
    auxiliary = load_auxiliary()
    readings = load_readings()
    overrides = load_overrides()
    entry_ids = {entry.entry_id for entry in entries}
    unused_overrides = sorted(set(overrides) - entry_ids)
    if unused_overrides:
        raise ValueError(f"pronunciation overrides reference unknown entries: {unused_overrides}")
    rows, stats = build_rows(entries, auxiliary, readings, overrides)
    existing = existing_texts()
    filtered = []
    for row in rows:
        text = row.split("\t", 1)[0]
        if text in existing:
            stats["duplicate_existing"] += 1
        else:
            filtered.append(row)
    licenses = {str(s["license"]) for s in sources.values() if source_is_verified(s)}
    license_name = "CC-BY-SA 4.0" if licenses and licenses == {"CC-BY-SA 4.0"} else "pending-source-verification"
    version = hashlib.sha256("".join(filtered).encode("utf-8")).hexdigest()[:12]
    return HEADER.format(license=license_name, version=version) + "".join(filtered), stats


def write_manifest(sources: dict[str, dict[str, object]]) -> None:
    MANIFEST.write_text(
        json.dumps({"sources": list(sources.values())}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def public_addresses(host: str) -> list[str]:
    addresses = sorted(
        {
            item[4][0]
            for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        }
    )
    if not addresses:
        raise ValueError(f"no address resolved for {host}")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError(f"refusing non-public address for {host}: {address}")
    return addresses


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str) -> None:
        super().__init__(host, timeout=30, context=ssl.create_default_context())
        self._address = address

    def connect(self) -> None:
        raw_socket = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)


def fetch_wikisource_revision(revision: str) -> dict[str, object]:
    if not re.fullmatch(r"[1-9][0-9]*", revision):
        raise ValueError("revision must be a positive integer")
    query = urllib.parse.urlencode(
        {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "prop": "revisions",
            "revids": revision,
            "rvprop": "ids|timestamp|content",
            "rvslots": "main",
        }
    )
    last_error: OSError | None = None
    for address in public_addresses(WIKISOURCE_HOST):
        connection = PinnedHTTPSConnection(WIKISOURCE_HOST, address)
        try:
            connection.request(
                "GET",
                f"{WIKISOURCE_API_PATH}?{query}",
                headers={
                    "Host": WIKISOURCE_HOST,
                    "User-Agent": "rime-mohu-classics/1.0 (source synchronization)",
                    "Accept": "application/json",
                },
            )
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError(f"Wikisource API returned HTTP {response.status}")
            length = response.getheader("Content-Length")
            if length is not None and int(length) > MAX_RESPONSE_BYTES:
                raise ValueError("Wikisource response is too large")
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                raise ValueError("Wikisource response is too large")
            value = json.loads(payload)
            if not isinstance(value, dict):
                raise ValueError("Wikisource response is not an object")
            return value
        except OSError as exc:
            last_error = exc
        finally:
            connection.close()
    raise OSError(f"failed to connect to {WIKISOURCE_HOST}") from last_error


def sync_source(
    source_id: str,
    revision: str,
    sources: dict[str, dict[str, object]],
) -> None:
    source = sources.get(source_id)
    if source is None:
        raise ValueError(f"unknown source: {source_id}")
    page_title = source.get("page_title")
    if not isinstance(page_title, str) or not page_title:
        raise ValueError(f"source {source_id!r} has no MediaWiki page title")
    payload = fetch_wikisource_revision(revision)
    pages = payload.get("query", {}).get("pages", [])
    if not isinstance(pages, list) or len(pages) != 1 or pages[0].get("missing"):
        raise ValueError(f"revision {revision} was not returned for {source_id}")
    page = pages[0]
    revisions = page.get("revisions", [])
    if not isinstance(revisions, list) or len(revisions) != 1:
        raise ValueError(f"unexpected revision response for {source_id}")
    revision_data = revisions[0]
    if str(revision_data.get("revid")) != revision:
        raise ValueError(f"unexpected revision response for {source_id}")
    if page.get("title") != page_title:
        raise ValueError(
            f"revision {revision} belongs to {page.get('title')!r}, not {page_title!r}"
        )
    content = revision_data.get("slots", {}).get("main", {}).get("content")
    if not isinstance(content, str) or not content:
        raise ValueError(f"revision {revision} has no wikitext content")
    raw_relative = Path("raw") / source_id / f"{revision}.wikitext"
    raw_path = DATA / raw_relative
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(content, encoding="utf-8")
    source.update(
        {
            "revision": revision,
            "retrieved": datetime.now(timezone.utc).isoformat(),
            "upstream_timestamp": revision_data.get("timestamp"),
            "raw_path": raw_relative.as_posix(),
            "sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
            "status": "fetched-unreviewed",
        }
    )
    write_manifest(sources)
    print(
        json.dumps(
            {"source": source_id, "revision": revision, "raw": str(raw_path)},
            ensure_ascii=False,
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("build", "check", "audit"):
        subparsers.add_parser(command)
    sync = subparsers.add_parser("sync")
    sync.add_argument("--source", required=True)
    sync.add_argument("--revision", required=True)
    args = parser.parse_args(argv)
    sources = load_manifest()
    if args.command == "sync":
        sync_source(args.source, args.revision, sources)
        return 0
    entries = load_entries(sources)
    content, stats = render(entries, sources)
    if args.command == "audit":
        print(json.dumps({"entries": len(entries), "stats": stats}, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "check":
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != content:
            print(f"out of date: {OUTPUT}", file=sys.stderr)
            return 1
        return 0
    OUTPUT.write_text(content, encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "entries": len(entries), "stats": stats}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
