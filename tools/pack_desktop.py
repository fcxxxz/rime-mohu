#!/usr/bin/env python3
"""Zip a built desktop scheme directory with files directly at archive root."""
from __future__ import annotations

import argparse
from pathlib import Path
import zipfile


def pack(scheme: str, directory: Path, archive: Path) -> None:
    for filename in ('default.yaml', f'mohu_{scheme}.schema.yaml',
                     f'mohu_{scheme}.dict.yaml', f'mohu_{scheme}.words.dict.yaml'):
        if not (directory / filename).is_file():
            raise ValueError(f'package is missing {filename}: {directory}')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output:
        for path in sorted(directory.rglob('*')):
            if path.is_file():
                output.write(path, path.relative_to(directory).as_posix())
    print(f'{scheme}: {archive} ({archive.stat().st_size:,} bytes)')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scheme', choices=('zrm', 'flypy'))
    parser.add_argument('directory', type=Path)
    parser.add_argument('archive', type=Path)
    args = parser.parse_args()
    pack(args.scheme, args.directory, args.archive)


if __name__ == '__main__':
    main()
