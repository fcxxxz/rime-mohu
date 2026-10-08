#!/usr/bin/env python3

import argparse
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMES = {"zrm", "flypy"}
SCHEMA_LINE = re.compile(r"^(\s*)- schema: (\S+)\s*$")
SCHEMA_NAME_LINE = re.compile(r"^  name:\s")

COMMON_ROOT_PATHS = (
    "README.md",
    "安装说明.md",
    "LICENSE",
    "etc",
    "mohu.yaml",
    "mohu_defs.yaml",
    "mohu_charset.dict.yaml",
    "mohu_charset.schema.yaml",
    "mohu_pinyin.dict.yaml",
    "mohu_pinyin.schema.yaml",
    "key_bindings.yaml",
    "punctuation.yaml",
    "symbols.yaml",
    "recipe.yaml",
    "recipes",
    "squirrel.yaml",
    "tiger.dict.yaml",
    "tiger.schema.yaml",
    "Rime同步助手",
)


def copy_path(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(
            source,
            destination,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(
                "__pycache__",
                "user-ngram.snapshot",
                "user-ngram.snapshot.tmp-*",
            ),
        )
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def recreate_destination(destination: Path) -> None:
    resolved = destination.resolve()
    root = ROOT.resolve()
    if resolved == root or resolved == Path(resolved.anchor) or resolved in root.parents:
        raise ValueError(f"unsafe destination: {destination}")

    if destination.is_symlink() or destination.is_file():
        destination.unlink()
    elif destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)


def copy_runtime_directories(scheme: str, destination: Path) -> None:
    copy_path(ROOT / "lua", destination / "lua")
    # option_state_data.lua 是用户机器上的运行时开关状态，绝不随包分发：
    # 覆盖安装会把用户开关重置成仓库里的测试残值。
    (destination / "lua" / "option_state_data.lua").unlink(missing_ok=True)

    other_scheme = "flypy" if scheme == "zrm" else "zrm"
    mohu_destination = destination / "mohu"
    copy_path(ROOT / "mohu", mohu_destination)
    (mohu_destination / f"four_code_yield_pairs_{other_scheme}.txt").unlink()

    opencc_destination = destination / "opencc"
    opencc_destination.mkdir()
    for pattern in ("*.ocd2", "*.json", "mohu_TSPhrases.txt"):
        for source in sorted((ROOT / "opencc").glob(pattern)):
            copy_path(source, opencc_destination / source.name)


def write_filtered_default(scheme: str, destination: Path) -> None:
    public_schema = f"mohu_{scheme}"
    output_lines = []
    for line in (ROOT / "default.yaml").read_text(encoding="utf-8").splitlines(
        keepends=True
    ):
        match = SCHEMA_LINE.match(line.rstrip("\r\n"))
        if match:
            schema_id = match.group(2)
            # Each flat package exposes exactly one public native scheme.
            if schema_id != public_schema:
                continue
        output_lines.append(line)
    destination.write_text("".join(output_lines), encoding="utf-8")


def remove_schema_name(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    path.write_text(
        "".join(line for line in lines if not SCHEMA_NAME_LINE.match(line)),
        encoding="utf-8",
    )


def hide_internal_schemas(scheme: str, destination: Path) -> None:
    public_schema = f"mohu_{scheme}"
    for path in sorted(destination.glob("*.schema.yaml")):
        if path.name != f"{public_schema}.schema.yaml":
            remove_schema_name(path)



def scheme_root_paths(scheme: str) -> tuple[str, ...]:
    if scheme not in SCHEMES:
        raise ValueError(f"unsupported scheme: {scheme}")
    return (f"mohu_{scheme}.schema.yaml", f"mohu_{scheme}_sentence_core.schema.yaml",
            f"mohu_{scheme}.dict.yaml", f"mohu_{scheme}.words.dict.yaml",
            f"mohu_{scheme}_custom_phrases.txt")


def build_distribution(scheme: str, destination: Path) -> None:
    if scheme not in SCHEMES:
        raise ValueError(f"unsupported scheme: {scheme}")

    recreate_destination(destination)
    for relative in COMMON_ROOT_PATHS:
        copy_path(ROOT / relative, destination / relative)
    for relative in scheme_root_paths(scheme):
        copy_path(ROOT / relative, destination / relative)
    copy_runtime_directories(scheme, destination)
    write_filtered_default(scheme, destination / "default.yaml")
    hide_internal_schemas(scheme, destination)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a scheme-specific Mohu package")
    parser.add_argument("scheme", choices=sorted(SCHEMES))
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    build_distribution(args.scheme, args.destination)


if __name__ == "__main__":
    main()
