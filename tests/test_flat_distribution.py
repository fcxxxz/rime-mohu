from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FlatDistributionTest(unittest.TestCase):
    def build(self, scheme: str, destination: Path) -> None:
        result = subprocess.run(
            ["uv", "run", "tools/build_flat_dist.py", scheme, str(destination)],
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
                model_readme = destination / "mohu" / "model" / "README.md"
                self.assertTrue(model_readme.is_file())
                self.assertIn("mohu-sentence-ngram-vN.bin", model_readme.read_text(encoding="utf-8"))

                for path in destination.rglob("*"):
                    self.assertNotIn("mohu_llm", str(path.relative_to(destination)))

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
