#!/usr/bin/env python3
"""准备 librime 评测部署目录并生成各模式输入文件。

- staging/data/：仓库方案文件副本（yaml/lua/opencc）+ 三个语法模型
  （zh-hans-t-essay-bgc / zh-hans-t-essay-bgw / wanxiang-lts-zh-hans）。
- 变体方案：mohu_zrm_sentence_bgc（八股文 bgc）与 mohu_zrm_sentence_wx
  （万象，使用万象官方推荐 collocation/penalty 参数）；原
  mohu_zrm_sentence 即八股文 bgw（mohu 整句默认）。
- default.yaml 的 schema_list 裁剪为本评测所需。
- 由 cases.jsonl 生成 inputs/<mode>.tsv：id<TAB>raw（按 raw 去重，
  id 用首个句子代表；解码结果按 raw 回连全部句子）。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
STAGING = HERE / "staging"
DATA = STAGING / "data"
INPUTS = STAGING / "inputs"
MODES = ("pure", "sparse", "word1", "char1")
STAGING_MARKER = ".mohu-lm-staging-v1"
STAGING_MARKER_CONTENT = "mohu-lm-sentence-benchmark-v1\n"

WANXIANG_GRAM = HERE / "wanxiang-lts-zh-hans.gram"
WANXIANG_URL = "https://github.com/amzxyz/RIME-LMDG/releases/download/LTS/wanxiang-lts-zh-hans.gram"
WANXIANG_SHA256 = "4554bbe1ba683c416e64ab15d65c944743bdad5251285032681f12d24ee87102"

SCHEMA_LIST = """schema_list:
  - schema: mohu_zrm_sentence
  - schema: mohu_zrm_sentence_bgc
  - schema: mohu_zrm_sentence_wx
  - schema: mohu_zrm_sentence_ng
  - schema: mohu_zrm_fixed
  - schema: mohu_zrm_fixed_legacy
  - schema: mohu_charset
  - schema: tiger
"""

WANXIANG_GRAMMAR = """# 语法模型（万象官方推荐参数）
grammar:
  language: wanxiang-lts-zh-hans
  collocation_max_length: 6
  collocation_min_length: 3
  collocation_penalty: -14
  non_collocation_penalty: -6
  weak_collocation_penalty: -100
  rear_penalty: -20
"""

BGC_GRAMMAR = """# 语法模型（八股文 bgc，整句口径 collocation）
grammar:
  language: zh-hans-t-essay-bgc
  collocation_max_length: 4
  collocation_min_length: 3
"""

NOGRAM_GRAMMAR = """# 无语言模型基线（关闭 octagram）
grammar: {}
"""


def verify_file_hash(path: Path, expected: str, *, label: str = "file") -> str:
    if not path.is_file():
        raise FileNotFoundError(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != expected:
        raise ValueError(f"{label} SHA-256 mismatch: expected {expected}, got {actual}")
    return actual


def _reset_staging_data(staging: Path) -> Path:
    lexical_root = staging.expanduser().absolute()
    allowed_system_aliases = {
        Path("/tmp"),
        Path("/private/tmp"),
        Path("/var"),
        Path("/var/tmp"),
    }
    current = lexical_root
    while True:
        if current.is_symlink() and current not in allowed_system_aliases:
            raise ValueError(f"refusing symlinked staging root: {current}")
        if current == current.parent:
            break
        current = current.parent
    root = lexical_root.resolve()
    live_rime = (Path.home() / "Library" / "Rime").resolve()
    forbidden = {
        Path("/").resolve(),
        Path.home().resolve(),
        Path(tempfile.gettempdir()).resolve(),
        Path("/tmp").resolve(),
        Path("/private/tmp").resolve(),
        Path("/var/tmp").resolve(),
        REPO.resolve(),
        HERE.resolve(),
    }
    if root in forbidden or root == live_rime or live_rime in root.parents:
        raise ValueError(f"refusing unsafe staging root: {root}")
    if root.exists() and not root.is_dir():
        raise ValueError(f"staging root is not a directory: {root}")
    root.mkdir(parents=True, exist_ok=True)

    marker = root / STAGING_MARKER
    data = root / "data"
    if marker.is_symlink() or (marker.exists() and not marker.is_file()):
        raise ValueError(f"invalid staging ownership marker: {marker}")
    if marker.is_file() and marker.read_text(encoding="utf-8") != STAGING_MARKER_CONTENT:
        raise ValueError(f"invalid staging ownership marker: {marker}")
    if data.is_symlink():
        raise ValueError(f"refusing symlinked staging data: {data}")
    if data.exists():
        if not data.is_dir() or not marker.is_file():
            raise ValueError(f"refusing to remove unmarked staging data: {data}")
        shutil.rmtree(data)

    marker.write_text(STAGING_MARKER_CONTENT, encoding="utf-8")
    data.mkdir()
    return data


def _owned_inputs_dir(inputs: Path) -> Path:
    path = inputs.expanduser()
    lexical_parent = path.parent.absolute()
    system_aliases = {Path("/var"), Path("/tmp")}
    untrusted_ancestor = next(
        (
            parent
            for parent in (lexical_parent, *lexical_parent.parents)
            if parent not in system_aliases and parent.is_symlink()
        ),
        None,
    )
    if path.is_symlink() or untrusted_ancestor is not None:
        raise ValueError(f"refusing symlinked staging inputs: {path}")
    root = lexical_parent
    marker = root / STAGING_MARKER
    if marker.is_symlink() or not marker.is_file() or marker.read_text(encoding="utf-8") != STAGING_MARKER_CONTENT:
        raise ValueError(f"refusing unmarked staging inputs: {path}")
    if path.exists() and not path.is_dir():
        raise ValueError(f"staging inputs is not a directory: {path}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def prepare_data(*, staging: Path = STAGING, wanxiang_gram: Path = WANXIANG_GRAM) -> None:
    global DATA, INPUTS
    verify_file_hash(wanxiang_gram, WANXIANG_SHA256, label="Wanxiang grammar")
    # Keep the lexical path intact until _reset_staging_data can reject a
    # symlinked root; it returns the canonical owned data directory.
    DATA = _reset_staging_data(staging.expanduser())
    INPUTS = DATA.parent / "inputs"

    for item in REPO.iterdir():
        name = item.name
        if item.is_file() and (
                name.endswith(".yaml") or name.endswith("_custom_phrases.txt")):
            shutil.copy2(item, DATA / name)
        elif item.is_dir() and name in ("lua", "opencc"):
            shutil.copytree(item, DATA / name,
                            ignore=shutil.ignore_patterns("*.md", "Makefile"))
    # 语法模型：复制快照，避免外部源文件的后续修改改变 staging。
    for gram in ("zh-hans-t-essay-bgc.gram", "zh-hans-t-essay-bgw.gram"):
        shutil.copy2(REPO / gram, DATA / gram)
    wx_dst = DATA / WANXIANG_GRAM.name
    shutil.copy2(wanxiang_gram, wx_dst)
    verify_file_hash(wx_dst, WANXIANG_SHA256, label="staged Wanxiang grammar")

    # 裁剪 default.yaml
    default = (REPO / "default.yaml").read_text(encoding="utf-8")
    patched = re.sub(r"schema_list:.*?(?=switcher:)", SCHEMA_LIST,
                     default, count=1, flags=re.S)
    (DATA / "default.yaml").write_text(patched, encoding="utf-8")

    # 变体方案：改 schema_id + 替换尾部 octagram include 为显式 grammar 节
    base = (REPO / "mohu_zrm_sentence.schema.yaml").read_text(encoding="utf-8")
    octagram_include_line = "__include: mohu:/octagram/enable_for_sentence"
    if octagram_include_line not in base:
        raise SystemExit("mohu_zrm_sentence.schema.yaml: octagram include not found")
    for variant, grammar_block in (("bgc", BGC_GRAMMAR),
                                   ("wx", WANXIANG_GRAMMAR),
                                   ("ng", NOGRAM_GRAMMAR)):
        text = base.replace("schema_id: mohu_zrm_sentence\n",
                            f"schema_id: mohu_zrm_sentence_{variant}\n", 1)
        name_line = "name: 整句·魔虎·自然码\n"
        text = text.replace(name_line,
                            f"name: 整句评测·{variant}\n", 1)
        text = text.replace(octagram_include_line,
                            grammar_block.rstrip("\n"), 1)
        if "grammar" not in text:
            raise SystemExit(f"variant {variant}: grammar patch failed")
        (DATA / f"mohu_zrm_sentence_{variant}.schema.yaml").write_text(
            text, encoding="utf-8")


def build_inputs(*, cases_file: Path = HERE / "cases.jsonl", inputs: Path = INPUTS) -> None:
    inputs = _owned_inputs_dir(inputs)
    if not cases_file.exists():
        raise SystemExit(f"missing {cases_file}; run encode_sentences.py first")
    seen: dict[str, dict[str, str]] = {m: {} for m in MODES}  # mode -> raw -> id
    n = 0
    with cases_file.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            n += 1
            for mode in MODES:
                raw = row["modes"][mode]
                seen[mode].setdefault(raw, row["id"])
    for mode in MODES:
        path = inputs / f"{mode}.tsv"
        if path.is_symlink():
            raise ValueError(f"refusing symlinked staging input file: {path}")
        with path.open("w", encoding="utf-8") as out:
            for raw, case_id in sorted(seen[mode].items()):
                out.write(f"{case_id}\t{raw}\n")
        print(f"{mode}: {len(seen[mode])} unique raws (from {n} cases)",
              file=sys.stderr)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Prepare isolated Rime staging")
    parser.add_argument("--cases", type=Path, default=HERE / "cases.jsonl")
    parser.add_argument("--staging", type=Path, default=STAGING)
    parser.add_argument("--wanxiang-gram", type=Path, default=WANXIANG_GRAM)
    args = parser.parse_args()
    prepare_data(staging=args.staging, wanxiang_gram=args.wanxiang_gram)
    build_inputs(cases_file=args.cases, inputs=INPUTS)
    print(f"staging ready: {args.staging / 'data'}", file=sys.stderr)


if __name__ == "__main__":
    main()
