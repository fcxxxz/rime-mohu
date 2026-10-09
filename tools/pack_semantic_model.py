#!/usr/bin/env python3
"""Package public semantic model files as an installable ZIP or bounded tar parts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
import zipfile
from pathlib import Path

# Stay below GitHub's per-asset limit, including deployments using decimal GB.
MAX_ASSET_BYTES = 1900 * 1024 * 1024
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
GENERATED_METADATA = {"安装说明.txt", "mohu-model-manifest.json"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def model_files(directory: Path) -> list[Path]:
    files = sorted(
        path
        for path in directory.iterdir()
        if path.is_file()
        and not path.name.startswith(".")
        and path.name not in GENERATED_METADATA
        and (
            path.suffix in {".json", ".txt", ".jinja", ".safetensors", ".model", ".md"}
            or path.name in {"LICENSE", "NOTICE"}
        )
    )
    names = {path.name for path in files}
    if not {"config.json", "tokenizer.json"} <= names:
        raise ValueError("model is missing config.json or tokenizer.json")
    weights = [path for path in files if path.suffix == ".safetensors"]
    if not weights or any(path.stat().st_size == 0 for path in weights):
        raise ValueError("model is missing nonempty safetensors weights")
    json.loads((directory / "config.json").read_text())
    for index in files:
        if index.name.endswith(".safetensors.index.json"):
            for shard in json.loads(index.read_text()).get("weight_map", {}).values():
                if shard not in names:
                    raise ValueError(f"model index references missing shard: {shard}")
    return files


class SplitWriter:
    def __init__(self, directory: Path, prefix: str, limit: int):
        self.directory, self.prefix, self.limit = directory, prefix, limit
        self.parts: list[Path] = []
        self.stream = None
        self.used = 0

    def write(self, data: bytes) -> int:
        total = len(data)
        data = memoryview(data)
        while data:
            if self.stream is None or self.used == self.limit:
                if self.stream is not None:
                    self.stream.close()
                number = len(self.parts)
                if number >= 26 * 26:
                    raise ValueError("too many archive parts")
                suffix = chr(97 + number // 26) + chr(97 + number % 26)
                path = self.directory / (self.prefix + suffix)
                self.parts.append(path)
                self.stream = path.open("wb")
                self.used = 0
            count = min(len(data), self.limit - self.used)
            self.stream.write(data[:count])
            self.used += count
            data = data[count:]
        return total

    def close(self) -> None:
        if self.stream is not None:
            self.stream.close()


def pack_model(
    directory: Path, output: Path, tag: str, repo: str, max_asset_bytes: int = MAX_ASSET_BYTES
) -> list[Path]:
    pieces = repo.split("/")
    if (
        len(pieces) != 2
        or not all(NAME.fullmatch(piece) for piece in pieces)
        or not NAME.fullmatch(tag)
    ):
        raise ValueError("unsafe model repository or tier name")
    if max_asset_bytes <= 0:
        raise ValueError("asset limit must be positive")
    folder = pieces[1]
    files = model_files(directory)
    output.mkdir(parents=True, exist_ok=True)
    basename = f"mohu-semantic-qwen2.5-{tag}"
    archive = output / (basename + ".zip")
    instructions = (
        f"魔虎语义模型：{repo}\n\n"
        f"解压后把 {folder} 文件夹放到 Rime 用户目录的 mohu/model/ 下。\n"
        f"macOS 路径：~/Library/Rime/mohu/model/{folder}/config.json\n"
        "不要替换整个 mohu 目录，保留原来的 V5 模型和其他文件。\n\n"
        f"已安装本机语义服务时，方案 custom.yaml 的 patch 中配置：\n"
        f'  tiger/semantic_http_url: "http://127.0.0.1:8765?model={tag}"\n'
        "重新部署 Rime，再打开“魔虎语义开”。\n"
        "此包只提供模型权重；本机语义服务和依赖需要已经安装。\n"
        "MLX 量化模型用于 Apple Silicon；不能当作 Windows/ONNX 模型使用。\n"
        "完整说明：docs/semantic-rerank.md\n"
    )
    manifest = json.dumps(
        {
            "repository": repo,
            "tier": tag,
            "folder": folder,
            "files": {path.name: sha256(path) for path in files},
        },
        ensure_ascii=False,
        indent=2,
    )
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        for path in files:
            package.write(path, folder + "/" + path.name)
        package.writestr(folder + "/安装说明.txt", instructions)
        package.writestr(folder + "/mohu-model-manifest.json", manifest)
    if archive.stat().st_size <= max_asset_bytes:
        assets = [archive]
    else:
        archive.unlink()  # This is the oversized intermediate, never an input.
        writer = SplitWriter(output, basename + ".tar.part-", max_asset_bytes)
        try:
            with tarfile.open(fileobj=writer, mode="w|", dereference=True) as package:
                for path in files:
                    # Retain the existing flat tar layout for large legacy tiers.
                    package.add(path, arcname=path.name, recursive=False)
        finally:
            writer.close()
        assets = writer.parts
    checksums = output / (basename + ".sha256")
    checksums.write_text("".join(f"{sha256(path)}  {path.name}\n" for path in assets))
    return assets + [checksums]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--repo", required=True)
    args = parser.parse_args()
    for asset in pack_model(args.model_dir, args.output_dir, args.tag, args.repo):
        print(f"{asset}: {asset.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
