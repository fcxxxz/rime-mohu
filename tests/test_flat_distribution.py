from __future__ import annotations

import filecmp
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FlatDistributionTest(unittest.TestCase):
    def build(
        self, scheme: str, destination: Path, windows_runtime: Path | None = None
    ) -> None:
        command = ["uv", "run", "tools/build_flat_dist.py", scheme, str(destination)]
        if windows_runtime is not None:
            command.extend(["--windows-runtime", str(windows_runtime)])
        result = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_flat_packages_have_one_public_schema_and_no_model_or_installer(self) -> None:
        for scheme in ("zrm", "flypy"):
            with self.subTest(scheme=scheme), tempfile.TemporaryDirectory() as tmp:
                destination = Path(tmp) / scheme
                self.build(scheme, destination)

                default = (destination / "default.yaml").read_text(encoding="utf-8")
                schema_ids = re.findall(r"^\s*- schema: (\S+)\s*$", default, re.MULTILINE)
                self.assertEqual([f"mohu_{scheme}"], schema_ids)
                self.assertTrue((destination / f"mohu_{scheme}.schema.yaml").is_file())
                self.assertFalse((destination / "base").exists())
                self.assertFalse((destination / "mohu_llm").exists())
                self.assertFalse((destination / "package.json").exists())
                self.assertFalse(list(destination.glob("install_*.command")))
                self.assertFalse(list(destination.rglob("mohu-sentence-ngram-v*.bin")))
                self.assertTrue(
                    (destination / "mohu" / "data" / scheme / f"mohu_{scheme}.lexicon.txt").is_file()
                )
                self.assertTrue((destination / "Rime同步助手" / "安装.command").is_file())
                install_doc = destination / "安装说明.md"
                self.assertTrue(install_doc.is_file())
                install_text = install_doc.read_text(encoding="utf-8")
                self.assertIn("xattr -dr com.apple.quarantine ~/Library/Rime/mohu", install_text)
                self.assertIn("mohu/model/mohu-sentence-ngram-v5.bin", install_text)
                self.assertIn("仅适用于 macOS", install_text)
                self.assertIn("Windows 用户跳过本节", install_text)
                clear_quarantine = destination / "解除隔离.command"
                self.assertTrue(clear_quarantine.is_file())
                self.assertTrue(clear_quarantine.stat().st_mode & 0o111)
                self.assertIn("com.apple.quarantine", clear_quarantine.read_text(encoding="utf-8"))
                model_readme = destination / "mohu" / "model" / "README.md"
                self.assertTrue(model_readme.is_file())
                self.assertIn("mohu-sentence-ngram-vN.bin", model_readme.read_text(encoding="utf-8"))

                for path in destination.rglob("*"):
                    relative = str(path.relative_to(destination))
                    if relative == f"mohu_llm_{scheme}.schema.yaml":
                        retired = path.read_text(encoding="utf-8")
                        self.assertIn('version: "retired"', retired)
                        self.assertNotIn("  name:", retired)
                        continue
                    self.assertNotIn("mohu_llm", relative)

    def test_flat_packages_copy_every_file_from_windows_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "windows-runtime"
            runtime.mkdir()
            expected = {
                "libtigerengine.dll": b"entry",
                "lua54.dll": b"lua",
                "future-library.dll": b"future",
                "runtime-manifest.json": json.dumps(
                    {
                        "entry": "libtigerengine.dll",
                        "files": [
                            {"name": "libtigerengine.dll"},
                            {"name": "lua54.dll"},
                            {"name": "future-library.dll"},
                        ],
                        "preload": ["lua54.dll", "future-library.dll"],
                    }
                ).encode(),
                "runtime-preload.txt": b"lua54.dll\nfuture-library.dll\n",
            }
            for name, content in expected.items():
                (runtime / name).write_bytes(content)

            for scheme in ("zrm", "flypy"):
                with self.subTest(scheme=scheme):
                    destination = root / scheme
                    self.build(scheme, destination, runtime)
                    packaged_runtime = destination / "mohu" / "runtime"
                    self.assertEqual(
                        expected,
                        {
                            path.name: path.read_bytes()
                            for path in packaged_runtime.iterdir()
                            if path.name in expected
                        },
                    )

    def test_flat_packages_ship_macos_engine_and_its_dependency(self) -> None:
        # libtigerengine.dylib 以 @loader_path 解析 libonnxruntime.1.dylib。只拷引擎
        # 会让 native 通道 dlopen 失败并静默 fail-open（模型装了也不生效），
        # 以 dist-* 作为 source_dir 的 mira 运行同样会复现。
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "zrm"
            self.build("zrm", destination)
            runtime = destination / "mohu" / "runtime"
            engine = runtime / "libtigerengine.dylib"
            dependency = runtime / "libonnxruntime.1.dylib"
            self.assertTrue(engine.is_file())
            self.assertTrue(dependency.is_file())
            self.assertTrue(
                filecmp.cmp(
                    ROOT / "tiger_sentence_native" / "libonnxruntime.1.dylib",
                    dependency,
                    shallow=False,
                )
            )

    def test_flat_packages_ship_semantic_model_assets(self) -> None:
        # 魔虎语义进程内 C2 推理必需模型与词表；漏拷时「魔虎语义开」
        # 首次命中即加载失败并把开关退回「关」，表现为功能装了不生效。
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "zrm"
            self.build("zrm", destination)
            semantic = destination / "mohu_semantic"
            model = semantic / "mohu_semantic.onnx"
            vocab = semantic / "vocab.tsv"
            self.assertTrue(model.is_file())
            self.assertTrue(vocab.is_file())
            self.assertTrue(
                filecmp.cmp(ROOT / "mohu_semantic" / "mohu_semantic.onnx",
                            model, shallow=False)
            )
            self.assertTrue(
                filecmp.cmp(ROOT / "mohu_semantic" / "vocab.tsv",
                            vocab, shallow=False)
            )

    def test_windows_runtime_requires_engine_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "windows-runtime"
            runtime.mkdir()
            (runtime / "unrelated.dll").write_bytes(b"not an entry")
            result = subprocess.run(
                ["uv", "run", "tools/build_flat_dist.py", "zrm", str(root / "zrm"),
                 "--windows-runtime", str(runtime)],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("libtigerengine.dll", result.stderr)

    def test_windows_runtime_requires_complete_closure_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "windows-runtime"
            runtime.mkdir()
            (runtime / "libtigerengine.dll").write_bytes(b"entry")
            result = subprocess.run(
                ["uv", "run", "tools/build_flat_dist.py", "zrm", str(root / "zrm"),
                 "--windows-runtime", str(runtime)],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("runtime-manifest.json", result.stderr)

    def test_windows_runtime_rejects_malformed_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "windows-runtime"
            runtime.mkdir()
            (runtime / "libtigerengine.dll").write_bytes(b"entry")
            (runtime / "runtime-manifest.json").write_text("[]\n", encoding="utf-8")
            (runtime / "runtime-preload.txt").write_text("", encoding="utf-8")
            result = subprocess.run(
                ["uv", "run", "tools/build_flat_dist.py", "zrm", str(root / "zrm"),
                 "--windows-runtime", str(runtime)],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("runtime-manifest.json", result.stderr)

    def test_windows_runtime_rejects_casefold_duplicate_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "windows-runtime"
            runtime.mkdir()
            for name in ("libtigerengine.dll", "alpha.dll", "ALPHA.DLL"):
                (runtime / name).write_bytes(b"runtime")
            if len(list(runtime.glob("*.[dD][lL][lL]"))) != 3:
                self.skipTest("case-insensitive filesystem cannot create a DLL name collision")
            (runtime / "runtime-manifest.json").write_text(
                json.dumps({"entry": "libtigerengine.dll", "files": [
                    {"name": "libtigerengine.dll"}, {"name": "alpha.dll"}],
                    "preload": ["alpha.dll"]}), encoding="utf-8")
            (runtime / "runtime-preload.txt").write_text("alpha.dll\n", encoding="utf-8")
            result = subprocess.run(
                ["uv", "run", "tools/build_flat_dist.py", "zrm", str(root / "zrm"),
                 "--windows-runtime", str(runtime)],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("runtime-manifest.json", result.stderr)

    def test_model_asset_target_stages_versioned_file_under_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "mohu-sentence-ngram-v5.bin"
            source.write_bytes(b"model")
            destination = Path(tmp) / "model-dist"
            result = subprocess.run(
                ["make", "model-dist", f"TIGER_NGRAM={source}"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            staged = ROOT / "model-dist" / "mohu" / "model" / "mohu-sentence-ngram-v5.bin"
            self.assertEqual(b"model", staged.read_bytes())
            shutil.rmtree(ROOT / "model-dist")


if __name__ == "__main__":
    unittest.main()
