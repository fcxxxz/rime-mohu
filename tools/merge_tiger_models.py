#!/usr/bin/env python3
"""Merge Tiger sentence models into a single MHCTN01 container.

The native engine (tiger_sentence_native/tigerengine.cc) can load one
single-file MHCTN01 container that carries both the character-level decoding
model (TCSKNM01/TCSKNM02) and the optional word-level scoring model (MHKNM01)
with a single mmap.  This tool packs the two existing model files into that
container; each section is a byte-for-byte copy of its source model and the
engine loads the sections as views, so no inner offsets are rewritten.

Container layout (little-endian, 64-byte header, sections start at offset 64,
char section first, then word):

    offset  0  magic 8 bytes "MHCTN01\\0"
    offset  8  u32 version = 1
    offset 12  u32 header_size = 64
    offset 16  u64 file_size (total container bytes)
    offset 24  u32 flags (bit0 = char section, bit1 = word section)
    offset 28  u32 reserved = 0
    offset 32  u64 char_off / offset 40 u64 char_len (absolute)
    offset 48  u64 word_off / offset 56 u64 word_len (0 when absent)

License note: the word-level model is trained on corpora that include LCCC
(CC-BY-NC-SA) among others.  Merged containers inherit those terms — personal
and research distribution only; commercial redistribution requires retraining
the word model on a permissively licensed corpus.
"""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from pathlib import Path

MAGIC = b"MHCTN01\0"
VERSION = 1
HEADER_SIZE = 64
HEADER = struct.Struct("<8sIIQIIQQQQ")  # magic, ver, hsize, fsize, flags, rsv, char x2, word x2
FLAG_CHAR = 1
FLAG_WORD = 2
CHUNK = 8 * 1024 * 1024  # stream in 8 MB blocks
CHAR_MAGICS = (b"TCSKNM01", b"TCSKNM02")
WORD_MAGIC = b"MHKNM01"


def read_magic(path: Path) -> bytes:
    with path.open("rb") as f:
        return f.read(8)


def copy_section(src: Path, out, digest) -> int:
    """Stream src into out in chunks, feeding digest; return bytes written."""
    written = 0
    with src.open("rb") as f:
        while True:
            block = f.read(CHUNK)
            if not block:
                break
            out.write(block)
            digest.update(block)
            written += len(block)
    return written


def human(size: int) -> str:
    return f"{size / (1 << 20):.1f} MiB" if size >= (1 << 20) else f"{size} B"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--char", type=Path, required=True,
                        help="character-level model (TCSKNM01/TCSKNM02)")
    parser.add_argument("--word", type=Path,
                        help="word-level scorer (MHKNM01); omit for a char-only container")
    parser.add_argument("--out", type=Path, required=True,
                        help="output MHCTN01 container path")
    args = parser.parse_args()

    magic = read_magic(args.char)
    if magic not in CHAR_MAGICS:
        print(f"error: char model {args.char}: magic {magic!r} is not TCSKNM01/TCSKNM02",
              file=sys.stderr)
        return 1
    if args.word is not None:
        magic = read_magic(args.word)
        if not magic.startswith(WORD_MAGIC):
            print(f"error: word model {args.word}: magic {magic!r} is not MHKNM01",
                  file=sys.stderr)
            return 1

    char_len = args.char.stat().st_size
    word_len = args.word.stat().st_size if args.word is not None else 0
    char_off = HEADER_SIZE
    word_off = char_off + char_len if word_len else 0
    file_size = HEADER_SIZE + char_len + word_len
    flags = FLAG_CHAR | (FLAG_WORD if word_len else 0)

    char_digest = hashlib.sha256()
    word_digest = hashlib.sha256()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("wb") as out:
        out.write(b"\0" * HEADER_SIZE)  # placeholder, backfilled below
        copy_section(args.char, out, char_digest)
        if word_len:
            copy_section(args.word, out, word_digest)
        out.seek(0)
        out.write(HEADER.pack(MAGIC, VERSION, HEADER_SIZE, file_size, flags, 0,
                              char_off, char_len, word_off, word_len))

    actual = args.out.stat().st_size
    if actual != file_size:
        print(f"error: wrote {actual} bytes but header claims {file_size}", file=sys.stderr)
        return 1
    print(f"container: {args.out} ({human(actual)}, {actual} bytes)")
    print(f"  header : {HEADER_SIZE} bytes, flags=0x{flags:x}, version={VERSION}")
    print(f"  char   : off={char_off} len={char_len} ({human(char_len)}) "
          f"sha256={char_digest.hexdigest()}")
    if word_len:
        print(f"  word   : off={word_off} len={word_len} ({human(word_len)}) "
              f"sha256={word_digest.hexdigest()}")
    else:
        print("  word   : none (char-only container)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
