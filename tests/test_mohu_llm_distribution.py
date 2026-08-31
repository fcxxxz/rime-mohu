import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MohuLlmDistributionTest(unittest.TestCase):
    def run_make(self, target: str, destination: Path, ngram: Path) -> None:
        env = os.environ.copy()
        env["TIGER_NGRAM"] = str(ngram)
        env["MOHU_LLM_ZRM_DESTDIR"] = str(destination)
        env["MOHU_LLM_FLYPY_DESTDIR"] = str(destination)
        result = subprocess.run(
            ["make", target], cwd=ROOT, env=env, text=True,
            capture_output=True, check=False,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_scheme_packages_have_explicit_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ngram = root / "sentence-ngram-mobile.bin"
            ngram.write_bytes(b"test-ngram")
            for scheme, target, other in (
                ("zrm", "mohu-llm-zrm-dist", "flypy"),
                ("flypy", "mohu-llm-flypy-dist", "zrm"),
            ):
                destination = root / scheme
                self.run_make(target, destination, ngram)
                self.assertTrue((destination / f"mohu_llm_{scheme}.schema.yaml").is_file())
                self.assertTrue((destination / "package.json").is_file())
                self.assertTrue((destination / f"install_mohu_llm_{scheme}.command").is_file())
                self.assertTrue((destination / "install_mohu_llm_scheme.command").is_file())
                self.assertTrue((destination / "lua" / "mohu_llm_runtime.lua").is_file())
                self.assertTrue((destination / "lua" / "mohu_sentence.lua").is_file())
                self.assertTrue((destination / "lua" / "mohu_personal_lexicon.lua").is_file())
                self.assertTrue((destination / "lua" / "mohu_processor.lua").is_file())
                self.assertTrue((destination / "lua" / "mohu_express_translator.lua").is_file())
                self.assertTrue((destination / "runtime").is_dir())
                self.assertTrue((destination / "data" / scheme / f"mohu_llm_{scheme}.lexicon.txt").is_file())
                self.assertTrue((destination / "data" / "sentence-ngram-mobile.bin").is_file())
                self.assertFalse((destination / f"mohu_llm_{other}.schema.yaml").exists())
                self.assertFalse((destination / "data" / other).exists())
                self.assertFalse(any(destination.rglob("*.safetensors")))
                self.assertFalse(any(destination.rglob("*.gguf")))
                payload = (destination / "package.json").read_text(encoding="utf-8")
                self.assertIn(f'"schema_id": "mohu_llm_{scheme}"', payload)
                self.assertNotIn(f'"schema_id": "mohu_llm_{other}"', payload)

    def test_windows_engine_is_staged_when_provided(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ngram = root / "sentence-ngram-mobile.bin"
            ngram.write_bytes(b"test-ngram")
            engine_dir = root / "engine-win64"
            engine_dir.mkdir()
            (engine_dir / "libtigerengine.dll").write_bytes(b"test-dll")
            (engine_dir / "lua54.dll").write_bytes(b"test-lua")
            destination = root / "zrm"
            env = os.environ.copy()
            env["TIGER_NGRAM"] = str(ngram)
            env["MOHU_LLM_ZRM_DESTDIR"] = str(destination)
            env["TIGER_ENGINE_DLL"] = str(engine_dir / "libtigerengine.dll")
            result = subprocess.run(
                ["make", "mohu-llm-zrm-dist"], cwd=ROOT, env=env, text=True,
                capture_output=True, check=False,
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertTrue((destination / "runtime" / "libtigerengine.dll").is_file())
            self.assertTrue((destination / "runtime" / "lua54.dll").is_file())
            self.assertTrue((destination / "install_mohu_llm_windows.ps1").is_file())

    def test_reusing_custom_destination_does_not_leak_other_scheme(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ngram = root / "sentence-ngram-mobile.bin"
            ngram.write_bytes(b"test-ngram")
            destination = root / "shared"
            env = os.environ.copy()
            env["TIGER_NGRAM"] = str(ngram)
            env["MOHU_LLM_ZRM_DESTDIR"] = str(destination)
            env["MOHU_LLM_FLYPY_DESTDIR"] = str(destination)
            for target in ("mohu-llm-zrm-dist", "mohu-llm-flypy-dist"):
                result = subprocess.run(
                    ["make", target], cwd=ROOT, env=env, text=True,
                    capture_output=True, check=False,
                )
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertTrue((destination / "mohu_llm_flypy.schema.yaml").is_file())
            self.assertFalse((destination / "mohu_llm_zrm.schema.yaml").exists())
            self.assertTrue((destination / "data/flypy/mohu_llm_flypy.lexicon.txt").is_file())
            self.assertFalse((destination / "data/zrm").exists())

    def test_standard_split_default_excludes_llm_schemas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "zrm"
            result = subprocess.run(
                ["uv", "run", "tools/build_split_dist.py", "zrm", str(destination)],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            default = (destination / "default.yaml").read_text(encoding="utf-8")
            self.assertNotIn("mohu_llm_zrm", default)
            self.assertNotIn("mohu_llm_flypy", default)
            self.assertFalse((destination / "mohu_llm_zrm.schema.yaml").exists())
            self.assertFalse((destination / "mohu_llm_flypy.schema.yaml").exists())

    def test_standard_dist_excludes_llm_schema_files_and_default_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "dist"
            result = subprocess.run(
                ["make", "dist", f"DESTDIR={destination}"],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertFalse((destination / "mohu_llm_zrm.schema.yaml").exists())
            self.assertFalse((destination / "mohu_llm_flypy.schema.yaml").exists())
            default = (destination / "default.yaml").read_text(encoding="utf-8")
            self.assertNotIn("mohu_llm_zrm", default)
            self.assertNotIn("mohu_llm_flypy", default)

    def test_standard_dist_cleans_reused_llm_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "dist"
            destination.mkdir()
            (destination / "mohu_llm_zrm.schema.yaml").write_text("stale\n", encoding="utf-8")
            (destination / "mohu_llm").mkdir()
            (destination / "mohu_llm" / "stale.txt").write_text("stale\n", encoding="utf-8")
            result = subprocess.run(
                ["make", "dist", f"DESTDIR={destination}"],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertFalse((destination / "mohu_llm_zrm.schema.yaml").exists())
            self.assertFalse((destination / "mohu_llm").exists())

    def test_zip_entries_are_relative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "package.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("mohu_llm_zrm.schema.yaml", "schema: {}\n")
            with zipfile.ZipFile(archive) as source:
                for info in source.infolist():
                    self.assertFalse(info.filename.startswith("/"))
                    self.assertNotIn("..", Path(info.filename).parts)


if __name__ == "__main__":
    unittest.main()
