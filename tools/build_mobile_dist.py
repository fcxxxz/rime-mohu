#!/usr/bin/env python3
"""Build a mobile (Trime / Hamster) Rime Mohu lite package.

手机精简包与桌面 flat 包同源（build_split_dist.build_distribution 生成同一套
方案/词库/lua），差异只在发行面：

- 精简包**不携带任何模型**（V5 整句模型与语义模型）：用户想要整句能力时
  自行从 Release 下载 `mohu-sentence-ngram-v5.bin` 放入 `mohu/model/`。
  缺模型时整句 lua 按既有 fail-open 逻辑回退普通候选，只记一次错误，
  词组输入、用户词记忆、辅助码、反查、符号命令完全不受影响。
- 不含 native 二进制（dylib/dll/so）：Android 10+ 禁止 dlopen 应用可写
  目录里的 so，iOS 禁止加载下载来的可执行代码。引擎的载体见
  安装说明-手机版.md——Android 使用随包分发 libtigerengine.so 的 Trime
  定制版；iOS 使用静态嵌入引擎的 Hamster 定制版。原版前端上整句自动
  回退。
- 不含语义推理资产（mohu_semantic/）：手机端引擎以 onnxruntime 桩链接，
  语义重排不可用，其余功能不受影响。
- 不含桌面专用内容：Rime皮肤编辑器/、Rime同步助手/、解除隔离.command、
  squirrel.yaml。皮肤编辑器的 lua 启动器在 Android 上会被平台探测跳过。
- 附 default.custom.yaml 只启用对应方案：Trime/Hamster 的「已启用方案」
  同样落在 default.custom.yaml 的 schema_list patch 上，随包预置等价于
  安装后在 App 里勾选一次。
- 附 安装说明-手机版.md，写清两端安装路径与已知差异。

`--zip` 额外产出同名 zip（内容与目录一致、按路径排序写入），作为发行
产物；红线校验 validate_lite_package 保证任何模型/运行时二进制混进包里
时构建直接失败，而不是发出一个装不上的包。
"""

from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path

from build_split_dist import ROOT, SCHEMES, build_distribution

# 整句 lua 是纯 Lua，随包保留以便定制版前端内置引擎时无缝升级；
# 缺二进制时 fail-open，不影响普通输入。
NATIVE_LUA = (
    "mohu_runtime.lua",
    "mohu_sentence.lua",
    "mohu_tiger_sentence.lua",
)

DESKTOP_ONLY_PATHS = (
    "Rime皮肤编辑器",
    "Rime同步助手",
    "解除隔离.command",
    "squirrel.yaml",
)

FIXED_MODEL_NAME = "mohu-sentence-ngram-v5.bin"

MOBILE_INSTALL_DOC = """# 魔虎手机版安装说明（精简包）

本包是魔虎的手机**精简包**（Android Trime／iOS 仓输入法 Hamster），不含
任何模型文件。方案、词库、辅助码与桌面版完全一致；用户词记忆、调频、
反查、符号命令等日常功能开箱即用。

**整句能力（可选）**：想要「神情很迷茫」这类长句排序与桌面一致，需两步：
1. Android 安装随包内置 `libtigerengine.so` 的 **Trime 定制版**（本仓库
   Release 另行提供 APK）；iOS 使用静态嵌入引擎的 **Hamster 定制版**。
2. 从 Release 下载 `mohu-sentence-ngram-v5.bin`，复制到 `mohu/model/` 目录。
不导入模型时一切正常，只是长句排序退化为普通词典造句。

## Android（同文输入法 Trime）

1. 安装 [Trime](https://github.com/osfans/trime/releases)（arm64 设备下载
   arm64-v8a 包），在系统设置里启用并切换到 Trime。
2. 首次启动向导中把数据目录设为手机存储的 `rime` 文件夹（默认
   `/sdcard/rime`），并在弹窗里允许访问。
3. 把本包内全部文件复制到 `/sdcard/rime/`（保留 lua/、opencc/、mohu/、
   etc/、recipes/ 目录结构）。
4. 打开 Trime 设置，点右上角刷新图标「部署」，等待编译完成（词库较大，
   首次部署可能需要几分钟）。
5. 部署完成后在任意输入框即可使用；`default.custom.yaml` 已预置只启用本包
   对应的方案，也可以在「方案」里自行切换。

## iOS（仓输入法 Hamster）

1. 通过 TestFlight 或自行编译安装
   [Hamster](https://github.com/imfuxiao/Hamster)，在系统设置 → 键盘里启用。
2. 把本包内全部文件复制进 Hamster 的 Rime 目录（Files 应用 → Hamster →
   Rime；保留目录结构）。
3. 打开 Hamster App 点「重新部署」，或首次唤起键盘时等待自动部署。

## 已知差异

- `/skin` 皮肤编辑器为桌面功能，手机上不可用。
- 建议使用 26 键全键盘布局；反查引导键 `` ` `` 可通过键盘长按符号输入，
  或使用 `ohm` 引导。
- 桌面快捷键（Ctrl 组合、Tab 词移动等）在软键盘上无效，翻页请用屏幕
  候选栏或 `-/=` 键。
"""


def write_mobile_default_custom(scheme: str, destination: Path) -> None:
    path = destination / "default.custom.yaml"
    path.write_text(
        "# 手机精简包预置：只启用本包对应方案（Trime/Hamster 的方案勾选"
        "同样写回这个 patch，可自行增删）。\n"
        "patch:\n"
        "  schema_list:\n"
        f"    - schema: mohu_{scheme}\n",
        encoding="utf-8",
    )


def strip_desktop_paths(destination: Path) -> None:
    for name in DESKTOP_ONLY_PATHS:
        path = destination / name
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)


def validate_lite_package(destination: Path) -> None:
    """精简包红线：模型、语义推理与 native 二进制一律不得混入。"""
    model = destination / "mohu" / "model" / FIXED_MODEL_NAME
    if model.is_file():
        raise ValueError(
            f"lite package must not ship the sentence model: {model} "
            "(users import mohu-sentence-ngram-v5.bin themselves)"
        )
    semantic_hits = list(destination.glob("mohu_semantic/*.onnx"))
    if semantic_hits:
        raise ValueError(
            f"lite package must not ship semantic inference assets: {semantic_hits[0]}"
        )
    runtime_hits = [
        path
        for path in (destination / "mohu" / "runtime").glob("*")
        if path.suffix in {".dylib", ".dll", ".so"}
    ] if (destination / "mohu" / "runtime").is_dir() else []
    if runtime_hits:
        raise ValueError(
            f"lite package must not ship native binaries: {runtime_hits[0]}"
        )
    for name in DESKTOP_ONLY_PATHS:
        if (destination / name).exists():
            raise ValueError(f"lite package must not ship desktop-only path: {name}")


def write_lite_zip(destination: Path, archive: Path) -> None:
    """按包内相对路径排序写入 zip，保证产物可复现、diff 友好。"""
    members = [
        path
        for path in destination.rglob("*")
        if path.is_file()
    ]
    members.sort(key=lambda path: path.relative_to(destination).as_posix())
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in members:
            zf.write(path, path.relative_to(destination).as_posix())


def build_mobile(scheme: str, destination: Path, archive: Path | None = None) -> None:
    if scheme not in SCHEMES:
        raise ValueError(f"unsupported scheme: {scheme}")

    build_distribution(scheme, destination)

    lua_dir = destination / "lua"
    for filename in NATIVE_LUA:
        shutil.copy2(ROOT / "tiger_sentence_native" / filename, lua_dir / filename)

    # 词表是纯数据：从 /sdcard（或 iOS App 容器）读取没有限制，随包分发
    # 供定制版前端里的 native 引擎使用。
    lexicon = ROOT / "tiger_sentence_native" / "data" / scheme / f"mohu_{scheme}.lexicon.txt"
    data_dir = destination / "mohu" / "data" / scheme
    data_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(lexicon, data_dir / lexicon.name)

    model_dir = destination / "mohu" / "model"
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "README.md").write_text(
        "# Mohu sentence model (mobile lite)\n\n"
        "This lite package ships without any model. For desktop-parity sentence\n"
        "ordering, download `mohu-sentence-ngram-v5.bin` from the GitHub Release\n"
        "and place it in this directory. Requires the custom Trime build (Android)\n"
        "or the custom Hamster build (iOS) that embeds the tiger engine.\n",
        encoding="utf-8",
    )

    strip_desktop_paths(destination)
    write_mobile_default_custom(scheme, destination)
    (destination / "安装说明-手机版.md").write_text(
        MOBILE_INSTALL_DOC, encoding="utf-8"
    )

    validate_lite_package(destination)
    if archive is not None:
        write_lite_zip(destination, archive)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a mobile Rime Mohu lite package")
    parser.add_argument("scheme", choices=sorted(SCHEMES))
    parser.add_argument("destination", type=Path)
    parser.add_argument(
        "--zip",
        type=Path,
        default=None,
        help="also write the package as a zip archive at this path",
    )
    args = parser.parse_args()
    build_mobile(args.scheme, args.destination, args.zip)


if __name__ == "__main__":
    main()
