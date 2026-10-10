#!/usr/bin/env python3
"""Merge maintenance corpus sources into one self-contained runtime dictionary.

Rows, import order, missing codes/weights and duplicates are retained. Librime,
not this builder, handles unencoded phrases and dictionary frequency semantics.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
from pathlib import Path

import yaml

try:
    from .long_word_completion import flypy_rows
    from .long_word_completion import master_long_word_rows as derive_long_words
except ImportError:
    from long_word_completion import flypy_rows
    from long_word_completion import master_long_word_rows as derive_long_words

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SUFFIXES = ('chars', 'base', 'words', 'tencent', 'moe', 'classics', 'wanxiang')


def source_paths(scheme: str, root: Path = ROOT) -> list[Path]:
    if scheme not in ('zrm', 'flypy'):
        raise ValueError(f'unsupported scheme: {scheme}')
    return [root / 'tools/data/lexicon_sources' / scheme / f'mohu_{scheme}.{suffix}.dict.yaml'
            for suffix in SOURCE_SUFFIXES]


def merge_dictionary(name: str, sources: list[Path], destination: Path,
                     supplemental_rows: list[tuple[str, str, str]] = ()) -> int:
    """Atomically stream source rows using a common text/code/weight layout."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f'.{destination.name}.', dir=destination.parent)
    temporary_path = Path(temporary)
    temporary_path.chmod(0o644)
    count = 0
    digest = hashlib.sha256()
    header = (f'# 【运行时整句词库｜自动生成，请勿直接编辑】\n'
              f'# 用途：smart/native 整句输入使用的合并词库；维护入口是 tools/data/lexicon_sources/{name.split(".")[0].replace("mohu_", "")}/。\n'
              f'# 构建：make dict；本文件会被重新生成，直接修改不会保留。\n'
              f'# Generated runtime sentence dictionary; corpus inputs: tools/data/lexicon_sources/.\n'
              f'# Source licenses and attribution are retained in the source blocks below.\n'
              f'---\nname: {name}\nversion: "000000000000"\nsort: by_weight\n'
              f'use_preset_vocabulary: false\ncolumns: [text, code, weight]\n...\n')
    # The header contains UTF-8 comments. `seek()` uses byte offsets, while
    # str.index() returns a character offset; convert explicitly so adding
    # Chinese guidance cannot corrupt the generated header.
    version_char_offset = header.index('000000000000')
    version_offset = len(header[:version_char_offset].encode('utf-8'))
    try:
        with os.fdopen(fd, 'w+b') as output:
            output.write(header.encode('utf-8'))
            for source in sources:
                with source.open(encoding='utf-8') as input_file:
                    source_header = []
                    for line in input_file:
                        if line.strip() == '...':
                            break
                        source_header.append(line)
                    else:
                        raise ValueError(f'missing dictionary body marker: {source}')
                    metadata = yaml.safe_load(''.join(source_header))
                    if not isinstance(metadata, dict):
                        raise ValueError(f'invalid dictionary header: {source}')
                    columns = metadata.get('columns', ['text', 'code', 'weight'])
                    if not isinstance(columns, list) or len(columns) != len(set(columns)) or 'text' not in columns:
                        raise ValueError(f'invalid source columns: {source}')
                    positions = {column: columns.index(column) for column in ('text', 'code', 'weight') if column in columns}
                    prefix = ('\n# Source: ' + source.name + '\n' +
                              ''.join(line for line in source_header if line.lstrip().startswith('#')))
                    output.write(prefix.encode('utf-8'))
                    digest.update(prefix.encode('utf-8'))
                    for number, line in enumerate(input_file, len(source_header) + 2):
                        if not line.strip() or line.lstrip().startswith('#'):
                            continue
                        fields = line.rstrip('\r\n').split('\t')
                        row = [fields[positions[key]] if key in positions and positions[key] < len(fields) else ''
                               for key in ('text', 'code', 'weight')]
                        if not row[0]:
                            raise ValueError(f'empty word at {source}:{number}')
                        # Omitted trailing fields retain librime's defaults and do
                        # not leave trailing tabs in the generated dictionary.
                        while len(row) > 1 and row[-1] == '':
                            row.pop()
                        rendered = ('\t'.join(row) + '\n').encode('utf-8')
                        output.write(rendered)
                        digest.update(rendered)
                        count += 1
            if supplemental_rows:
                prefix = b'\n# Source: master-table long words (derived full spellings)\n'
                output.write(prefix)
                digest.update(prefix)
                for row in supplemental_rows:
                    rendered = ('\t'.join(row) + '\n').encode('utf-8')
                    output.write(rendered)
                    digest.update(rendered)
                    count += 1
            output.seek(version_offset)
            output.write(digest.hexdigest()[:12].encode('ascii'))
        # Do not change mtime on an identical build; deployment uses checksums.
        if destination.exists() and same_bytes(temporary_path, destination):
            temporary_path.unlink()
            destination.chmod(0o644)
        else:
            os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)
    return count


def same_bytes(left: Path, right: Path) -> bool:
    if left.stat().st_size != right.stat().st_size:
        return False
    with left.open('rb') as a, right.open('rb') as b:
        while chunk := a.read(1024 * 1024):
            if chunk != b.read(len(chunk)):
                return False
    return True


def master_long_word_rows(root: Path = ROOT):
    return derive_long_words(root, source_paths('zrm', root))


def build(root: Path = ROOT) -> None:
    supplemental, report = master_long_word_rows(root)
    for scheme in ('zrm', 'flypy'):
        destination = root / f'mohu_{scheme}.words.dict.yaml'
        rows = supplemental if scheme == 'zrm' else flypy_rows(supplemental)
        count = merge_dictionary(f'mohu_{scheme}.words', source_paths(scheme, root), destination,
                                 supplemental_rows=rows)
        print(f'{destination.name}: {count} source rows, no imported runtime tables')
    print(f"Master long words: {report['master_long_words']}, existing: "
          f"{report['already_in_corpus']}, derived: {report['derived']}, "
          f"unresolved: {len(report['unresolved'])}")


if __name__ == '__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    build()
