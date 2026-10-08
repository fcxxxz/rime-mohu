#!/usr/bin/env python3
"""Validate the editable table without changing codes or row order."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def check(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    header, body = text.split("\n...\n", 1)
    meta = yaml.safe_load(header)
    if meta.get("name") != "mohu_zrm" or meta.get("sort") != "original":
        raise ValueError("主表必须使用 name: mohu_zrm 和 sort: original")
    if meta.get("columns") != ["text", "code"]:
        raise ValueError("主表只保留 text、code 两列")
    count = 0
    for number, line in enumerate(body.splitlines(), text[:text.index("\n...\n")].count("\n") + 3):
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 2 or not fields[0] or not fields[1] or any(c.isspace() for c in fields[1]):
            raise ValueError(f"{path}:{number}: 需要 字词<Tab>编码（只允许两列）")
        count += 1
    return count


if __name__ == "__main__":
    print(f"主码表校验通过：{check(ROOT / 'mohu_zrm.dict.yaml')} 条；未改写源表")
