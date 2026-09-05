from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.build_split_dist import copy_path

ROOT = Path(__file__).resolve().parents[1]


class FlatDistributionTest(unittest.TestCase):
    def test_user_snapshot_state_is_gitignored(self) -> None:
        for path in (
            "mohu/config/user-ngram.snapshot",
            "mohu/config/user-ngram.snapshot.tmp-123",
        ):
            with self.subTest(path=path):
                result = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT)
                self.assertEqual(0, result.returncode)

    def test_runtime_tree_excludes_user_snapshot_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            config = source / "config"
            config.mkdir(parents=True)
            (config / "README.md").write_text("marker\n", encoding="utf-8")
            (config / "user-ngram.snapshot").write_bytes(b"private history")
            (config / "user-ngram.snapshot.tmp-123").write_bytes(b"temporary history")

            destination = root / "destination"
            copy_path(source, destination)

            self.assertTrue((destination / "config" / "README.md").is_file())
            self.assertFalse((destination / "config" / "user-ngram.snapshot").exists())
            self.assertFalse((destination / "config" / "user-ngram.snapshot.tmp-123").exists())

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
                self.assertIn("mohu-sentence-ngram-v5.bin", model_readme.read_text(encoding="utf-8"))
                config_readme = destination / "mohu" / "config" / "README.md"
                self.assertTrue(config_readme.is_file())
                self.assertFalse((destination / "mohu" / "config" / "user-ngram.snapshot").exists())

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

    def test_model_asset_target_stages_fixed_file_under_model(self) -> None:
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
