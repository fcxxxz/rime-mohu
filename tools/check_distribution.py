#!/usr/bin/env python3
"""Validate the minimal published dictionary/schema contract (desktop or mobile)."""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml
from build_split_dist import COMMON_ROOT_PATHS, scheme_root_paths


def header(path: Path) -> dict:
    lines = []
    with path.open(encoding='utf-8') as source:
        for line in source:
            if line.strip() == '...':
                break
            lines.append(line)
        else:
            raise ValueError(f'missing dictionary body marker: {path}')
    value = yaml.safe_load(''.join(lines))
    if not isinstance(value, dict):
        raise ValueError(f'invalid dictionary header: {path}')
    return value


def check_distribution(root: Path, scheme: str) -> None:
    required = scheme_root_paths(scheme)
    for name in required:
        if not (root / name).is_file():
            raise ValueError(f'missing scheme asset: {name}')
    expected_dicts = {p for p in (*COMMON_ROOT_PATHS, *required) if p.endswith('.dict.yaml')}
    actual_dicts = {p.name for p in root.glob('*.dict.yaml')}
    if actual_dicts != expected_dicts:
        raise ValueError(f'dictionary contract mismatch: {actual_dicts ^ expected_dicts}')
    master = header(root / f'mohu_{scheme}.dict.yaml')
    words = header(root / f'mohu_{scheme}.words.dict.yaml')
    if master.get('columns') != ['text', 'code'] or master.get('sort') != 'original':
        raise ValueError('the editable code table must contain exactly text/code in original order')
    if words.get('name') != f'mohu_{scheme}.words' or 'import_tables' in words:
        raise ValueError('the sentence dictionary must be self-contained')
    schema = yaml.safe_load((root / f'mohu_{scheme}.schema.yaml').read_text())
    if schema['translator']['dictionary'] != f'mohu_{scheme}':
        raise ValueError('main code-table wiring is wrong')
    for namespace in ('smart', 'smart_static'):
        if schema[namespace]['dictionary'] != f'mohu_{scheme}.words':
            raise ValueError(f'{namespace} does not use the merged sentence dictionary')
    expected_schemas = {p for p in (*COMMON_ROOT_PATHS, *required) if p.endswith('.schema.yaml')}
    if {p.name for p in root.glob('*.schema.yaml')} != expected_schemas:
        raise ValueError('package contains missing or obsolete schemas')
    if (root / 'tools').exists():
        raise ValueError('maintenance inputs must not be shipped')
    if any(root.glob('*.userdb*')) or (root / 'lua/option_state_data.lua').exists():
        raise ValueError('personal runtime data must never be shipped')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scheme', choices=('zrm', 'flypy'))
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    check_distribution(args.directory, args.scheme)
    print(f'{args.scheme}: 2 scheme dictionaries + 3 auxiliary dictionaries; no split libraries or retired schemas')
