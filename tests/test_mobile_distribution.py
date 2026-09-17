from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# build_mobile_dist.py 以脚本形态运行时自带 tools/ 于 sys.path；作为模块
# 导入时补上，保持其兄弟 import（build_split_dist）两种形态都可用。
sys.path.insert(0, str(ROOT / "tools"))
from build_mobile_dist import build_mobile, validate_lite_package  # noqa: E402


class MobileDistributionTest(unittest.TestCase):
    def test_lite_packages_ship_no_model_or_native_assets(self) -> None:
        for scheme in ("zrm", "flypy"):
            with self.subTest(scheme=scheme):
                with tempfile.TemporaryDirectory() as tmp:
                    destination = Path(tmp) / "dist"
                    build_mobile(scheme, destination)

                    self.assertFalse(
                        (destination / "mohu" / "model" / "mohu-sentence-ngram-v5.bin").exists()
                    )
                    self.assertFalse((destination / "mohu_semantic").exists())
                    runtime_dir = destination / "mohu" / "runtime"
                    if runtime_dir.is_dir():
                        self.assertFalse(list(runtime_dir.glob("*.dylib")))
                    for desktop_only in (
                        "Rime皮肤编辑器",
                        "Rime同步助手",
                        "解除隔离.command",
                        "squirrel.yaml",
                    ):
                        self.assertFalse((destination / desktop_only).exists())

                    # 模型目录仍有 README 说明自助导入；词表随包。
                    self.assertTrue(
                        (destination / "mohu" / "model" / "README.md").is_file()
                    )
                    self.assertTrue(
                        (
                            destination / "mohu" / "data" / scheme / f"mohu_{scheme}.lexicon.txt"
                        ).is_file()
                    )
                    # 整句 lua 随包（fail-open 语义，供定制版前端升级）。
                    for filename in (
                        "mohu_runtime.lua",
                        "mohu_tiger_sentence.lua",
                    ):
                        self.assertTrue(
                            (destination / "lua" / filename).is_file(),
                            filename,
                        )
                    # 预置方案只启用本包 scheme。
                    custom = (destination / "default.custom.yaml").read_text(encoding="utf-8")
                    self.assertIn(f"schema: mohu_{scheme}", custom)
                    self.assertNotIn("mohu_llm", custom)

    def test_lite_zip_matches_directory_and_excludes_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            destination = root / "dist"
            archive = root / "lite.zip"
            build_mobile("zrm", destination, archive)

            self.assertTrue(archive.is_file())
            with zipfile.ZipFile(archive) as zf:
                names = zf.namelist()
            listed = [
                path.relative_to(destination).as_posix()
                for path in destination.rglob("*")
                if path.is_file()
            ]
            # zip 内容与目录一一对应，且按路径排序写入（可复现产物）。
            self.assertEqual(names, sorted(listed))
            self.assertNotIn("mohu/model/mohu-sentence-ngram-v5.bin", names)

    def test_validate_rejects_smuggled_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "dist"
            model_dir = destination / "mohu" / "model"
            model_dir.mkdir(parents=True)
            (model_dir / "mohu-sentence-ngram-v5.bin").write_bytes(b"model")
            with self.assertRaisesRegex(ValueError, "sentence model"):
                validate_lite_package(destination)

        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "dist"
            semantic_dir = destination / "mohu_semantic"
            semantic_dir.mkdir(parents=True)
            (semantic_dir / "mohu_semantic.onnx").write_bytes(b"onnx")
            with self.assertRaisesRegex(ValueError, "semantic"):
                validate_lite_package(destination)


if __name__ == "__main__":
    unittest.main()
