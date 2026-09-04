import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "tiger_sentence_native"


def quarantine(path: Path) -> None:
    subprocess.run(
        ["xattr", "-w", "com.apple.quarantine", "0081;00000000;Chrome;00000000-0000-0000-0000-000000000000", str(path)],
        check=True,
        capture_output=True,
    )


def quarantined(path: Path) -> bool:
    result = subprocess.run(
        ["xattr", "-p", "com.apple.quarantine", str(path)],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def copy_package_lua(package: Path) -> None:
    shutil.copytree(ROOT / "lua", package / "lua")
    for name in (
        "mohu_llm_runtime.lua",
        "mohu_sentence.lua",
        "mohu_tiger_sentence.lua",
        "mohu_personal_lexicon.lua",
        "mohu_tiger_reranker.lua",
        "mohu_tiger_reranker_profile.lua",
        "mohu_tiger_model_catalog.lua",
        "mohu_tiger_model_menu.lua",
    ):
        source_root = ROOT / "lua" if name == "mohu_personal_lexicon.lua" else NATIVE
        shutil.copy2(source_root / name, package / "lua" / name)


def copy_package_manifests(package: Path) -> None:
    models = package / "models"
    models.mkdir(parents=True, exist_ok=True)
    for name in ("qwen35-0.8b.manifest", "qwen3-0.6b.manifest"):
        shutil.copy2(NATIVE / "models" / name, models / name)


def copy_package_base(package: Path, scheme: str) -> None:
    base = package / "base"
    base.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "default.yaml", base / "default.yaml")
    shutil.copy2(ROOT / f"mohu_{scheme}.schema.yaml", base / f"mohu_{scheme}.schema.yaml")
    (base / "mohu.yaml").write_text("packaged mohu config\n", encoding="utf-8")
    (base / "squirrel.yaml").write_text("packaged squirrel config\n", encoding="utf-8")
    (base / f"mohu_{scheme}_custom_phrases.txt").write_text(
        "packaged custom phrases\n", encoding="utf-8"
    )
    (base / f"mohu_{scheme}.extended.dict.yaml").write_text(
        "packaged extended dictionary\n", encoding="utf-8"
    )


def create_installer_package(package: Path, scheme: str) -> None:
    for relative in (
        f"install_mohu_llm_{scheme}.command",
        "install_mohu_llm_scheme.command",
        f"mohu_llm_{scheme}.package.json",
        f"mohu_llm_{scheme}.schema.yaml",
    ):
        source = ROOT / relative if relative.endswith(".schema.yaml") else NATIVE / relative
        shutil.copy2(source, package / relative)
    shutil.copy2(package / f"mohu_llm_{scheme}.package.json", package / "package.json")
    copy_package_base(package, scheme)
    copy_package_lua(package)
    (package / "lua" / "mohu_processor.lua").write_text(
        "packaged processor\n", encoding="utf-8"
    )
    (package / "lua" / f"four_code_yield_pairs_{scheme}.txt").write_text(
        "packaged yield pairs\n", encoding="utf-8"
    )
    copy_package_manifests(package)
    runtime = package / "runtime"
    runtime.mkdir()
    for source in (
        NATIVE / "libtigerengine.dylib",
        NATIVE / "qwen35_scorer.py",
        NATIVE / "run_qwen35_scorer.command",
        NATIVE / "install_qwen35_launch_agent.command",
        NATIVE / "scorer_models.zsh",
        NATIVE / "switch_qwen_model.command",
        NATIVE / "mohu_tiger_reranker_profile.lua",
        NATIVE / "mohu_tiger_reranker_profile_qwen3_06b.lua",
    ):
        shutil.copy2(source, runtime / source.name)
    for filename in (
        "run_qwen35_scorer.command",
        "install_qwen35_launch_agent.command",
        "switch_qwen_model.command",
    ):
        os.chmod(runtime / filename, 0o755)
    shutil.copytree(NATIVE / "data" / scheme, package / "data" / scheme)
    (package / "data" / "sentence-ngram-mobile.bin").write_bytes(b"test-ngram")
    os.chmod(package / f"install_mohu_llm_{scheme}.command", 0o755)
    os.chmod(package / "install_mohu_llm_scheme.command", 0o755)


class MohuLlmInstallerTest(unittest.TestCase):
    CASES = (("zrm", "魔虎大模型·自然码"), ("flypy", "魔虎大模型·小鹤"))

    def test_each_package_has_manifest_and_executable_installer(self) -> None:
        for scheme, display_name in self.CASES:
            with self.subTest(scheme=scheme):
                installer = NATIVE / f"install_mohu_llm_{scheme}.command"
                manifest = NATIVE / f"mohu_llm_{scheme}.package.json"
                self.assertTrue(installer.is_file())
                self.assertTrue(os.access(installer, os.X_OK))
                self.assertTrue(manifest.is_file())
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                self.assertEqual("mohu_llm", payload["package_type"])
                self.assertEqual(scheme, payload["scheme"])
                self.assertEqual(f"mohu_llm_{scheme}", payload["schema_id"])
                self.assertEqual(display_name, payload["display_name"])
                self.assertEqual(f"mohu_llm_{scheme}.schema.yaml", payload["schema"])
                self.assertEqual("base", payload["base_dir"])
                self.assertEqual(f"data/{scheme}", payload["data_dir"])
                self.assertEqual("runtime", payload["runtime_dir"])
                self.assertEqual(
                    ["models/qwen35-0.8b.manifest", "models/qwen3-0.6b.manifest"],
                    payload["model_manifests"],
                )
                self.assertTrue(payload["lua_files"])
                self.assertTrue(payload["runtime_files"])
                self.assertTrue(payload["data_files"])
                self.assertTrue(payload["executable_files"])
                self.assertIn("mohu_llm_runtime.lua", payload["lua_files"])
                self.assertIn("mohu_sentence.lua", payload["lua_files"])
                self.assertIn("mohu_tiger_sentence.lua", payload["lua_files"])
                self.assertIn("mohu_personal_lexicon.lua", payload["lua_files"])
                self.assertIn("mohu_tiger_reranker.lua", payload["lua_files"])
                self.assertIn("mohu_tiger_model_catalog.lua", payload["lua_files"])
                self.assertIn("mohu_tiger_model_menu.lua", payload["lua_files"])
                self.assertIn("option_sync.lua", payload["lua_files"])
                self.assertIn("option_state.lua", payload["lua_files"])

    def test_installer_copies_only_selected_scheme_and_registers_idempotently(self) -> None:
        for scheme, _display_name in self.CASES:
            with self.subTest(scheme=scheme), tempfile.TemporaryDirectory() as package_tmp, tempfile.TemporaryDirectory() as rime_tmp:
                package = Path(package_tmp)
                for relative in (
                    f"install_mohu_llm_{scheme}.command",
                    "install_mohu_llm_scheme.command",
                    f"mohu_llm_{scheme}.package.json",
                    f"mohu_llm_{scheme}.schema.yaml",
                ):
                    source = ROOT / relative if relative.endswith(".schema.yaml") else NATIVE / relative
                    shutil.copy2(source, package / relative)
                shutil.copy2(package / f"mohu_llm_{scheme}.package.json", package / "package.json")
                copy_package_base(package, scheme)
                copy_package_lua(package)
                copy_package_manifests(package)
                runtime = package / "runtime"
                runtime.mkdir()
                for source in (
                    NATIVE / "libtigerengine.dylib",
                    NATIVE / "qwen35_scorer.py",
                    NATIVE / "run_qwen35_scorer.command",
                    NATIVE / "install_qwen35_launch_agent.command",
                    NATIVE / "scorer_models.zsh",
                    NATIVE / "switch_qwen_model.command",
                    NATIVE / "mohu_tiger_reranker_profile.lua",
                    NATIVE / "mohu_tiger_reranker_profile_qwen3_06b.lua",
                ):
                    shutil.copy2(source, runtime / source.name)
                for filename in ("run_qwen35_scorer.command", "install_qwen35_launch_agent.command", "switch_qwen_model.command"):
                    os.chmod(runtime / filename, 0o755)
                shutil.copytree(NATIVE / "data" / scheme, package / "data" / scheme)
                (package / "data" / "sentence-ngram-mobile.bin").write_bytes(b"test-ngram")
                os.chmod(package / f"install_mohu_llm_{scheme}.command", 0o755)

                custom = Path(rime_tmp) / "default.custom.yaml"
                custom.write_text("# user settings\npatch:\n  menu: {page_size: 9}\n", encoding="utf-8")
                env = {**os.environ, "MOHU_RIME_DIR": rime_tmp, "MOHU_SQUIRREL_BIN": "/does/not/exist", "MOHU_SKIP_SCORER_INSTALL": "1"}
                command = [str(package / f"install_mohu_llm_{scheme}.command")]
                first = subprocess.run(command, env=env, text=True, capture_output=True, check=False)
                self.assertEqual(0, first.returncode, first.stdout + first.stderr)
                second = subprocess.run(command, env=env, text=True, capture_output=True, check=False)
                self.assertEqual(0, second.returncode, second.stdout + second.stderr)

                self.assertTrue((Path(rime_tmp) / f"mohu_llm_{scheme}.schema.yaml").is_file())
                self.assertTrue((Path(rime_tmp) / f"mohu_{scheme}.schema.yaml").is_file())
                self.assertTrue((Path(rime_tmp) / "default.yaml").is_file())
                self.assertTrue((Path(rime_tmp) / "mohu_llm" / "runtime").is_dir())
                self.assertTrue((Path(rime_tmp) / "mohu_llm" / "data" / scheme).is_dir())
                self.assertFalse((Path(rime_tmp) / "tiger").exists())
                merged = custom.read_text(encoding="utf-8")
                self.assertIn(f"schema: mohu_llm_{scheme}", merged)
                self.assertEqual(1, merged.count(f"schema: mohu_llm_{scheme}"))
                self.assertIn("menu: {page_size: 9}", merged)
                self.assertTrue((Path(rime_tmp) / "mohu_llm" / "config" / "model-selection").is_file())

    def test_installer_preserves_user_maintained_scheme_files_on_upgrade(self) -> None:
        for scheme, _display_name in self.CASES:
            with self.subTest(scheme=scheme), tempfile.TemporaryDirectory() as package_tmp, tempfile.TemporaryDirectory() as rime_tmp:
                package = Path(package_tmp)
                create_installer_package(package, scheme)
                rime = Path(rime_tmp)
                expected = {
                    "default.yaml": "user default\n",
                    "mohu.yaml": "user mohu config\n",
                    f"mohu_{scheme}_custom_phrases.txt": "user custom phrases\n",
                    f"mohu_{scheme}.extended.dict.yaml": "user extended dictionary\n",
                    "lua/mohu_processor.lua": "user processor\n",
                    f"lua/four_code_yield_pairs_{scheme}.txt": "user yield pairs\n",
                }
                for relative, content in expected.items():
                    path = rime / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content, encoding="utf-8")
                installed_schema = rime / f"mohu_{scheme}.schema.yaml"
                installed_schema.write_text("stale schema\n", encoding="utf-8")
                installed_squirrel = rime / "squirrel.yaml"
                installed_squirrel.write_text("stale squirrel config\n", encoding="utf-8")

                env = {
                    **os.environ,
                    "MOHU_RIME_DIR": rime_tmp,
                    "MOHU_SQUIRREL_BIN": "/does/not/exist",
                    "MOHU_SKIP_SCORER_INSTALL": "1",
                }
                result = subprocess.run(
                    [str(package / f"install_mohu_llm_{scheme}.command")],
                    env=env,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                for relative, content in expected.items():
                    self.assertEqual(content, (rime / relative).read_text(encoding="utf-8"))
                self.assertNotEqual("stale schema\n", installed_schema.read_text(encoding="utf-8"))
                self.assertEqual(
                    "packaged squirrel config\n",
                    installed_squirrel.read_text(encoding="utf-8"),
                )

    def test_installer_strips_download_quarantine_from_installed_files(self) -> None:
        # Browser-downloaded zips carry com.apple.quarantine; a quarantined
        # ad-hoc-signed dylib is blocked by Gatekeeper inside Squirrel and the
        # native sentence engine silently falls back to dictionary candidates.
        if sys.platform != "darwin" or shutil.which("xattr") is None:
            self.skipTest("requires macOS xattr")
        for scheme, _display_name in self.CASES:
            with self.subTest(scheme=scheme), tempfile.TemporaryDirectory() as package_tmp, tempfile.TemporaryDirectory() as rime_tmp:
                package = Path(package_tmp)
                create_installer_package(package, scheme)
                for relative in (
                    "runtime/libtigerengine.dylib",
                    f"install_mohu_llm_{scheme}.command",
                    f"lua/mohu_llm_runtime.lua",
                ):
                    quarantine(package / relative)
                env = {
                    **os.environ,
                    "MOHU_RIME_DIR": rime_tmp,
                    "MOHU_SQUIRREL_BIN": "/does/not/exist",
                    "MOHU_SKIP_SCORER_INSTALL": "1",
                }
                result = subprocess.run(
                    [str(package / f"install_mohu_llm_{scheme}.command")],
                    env=env,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                rime = Path(rime_tmp)
                for relative in (
                    "mohu_llm/runtime/libtigerengine.dylib",
                    f"mohu_llm_{scheme}.schema.yaml",
                    "lua/mohu_llm_runtime.lua",
                    "default.yaml",
                ):
                    self.assertFalse(quarantined(rime / relative), relative)

    def test_installer_fails_closed_when_required_lua_or_lexicon_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as package_tmp, tempfile.TemporaryDirectory() as rime_tmp:
            package = Path(package_tmp)
            for relative in (
                "install_mohu_llm_zrm.command",
                "install_mohu_llm_scheme.command",
                "mohu_llm_zrm.package.json",
                "mohu_llm_zrm.schema.yaml",
            ):
                source = ROOT / relative if relative.endswith(".schema.yaml") else NATIVE / relative
                shutil.copy2(source, package / relative)
            shutil.copy2(package / "mohu_llm_zrm.package.json", package / "package.json")
            copy_package_base(package, "zrm")
            copy_package_lua(package)
            copy_package_manifests(package)
            runtime = package / "runtime"
            runtime.mkdir()
            for source in (
                NATIVE / "libtigerengine.dylib",
                NATIVE / "qwen35_scorer.py",
                NATIVE / "run_qwen35_scorer.command",
                NATIVE / "install_qwen35_launch_agent.command",
                NATIVE / "scorer_models.zsh",
                NATIVE / "switch_qwen_model.command",
                NATIVE / "mohu_tiger_reranker_profile.lua",
                NATIVE / "mohu_tiger_reranker_profile_qwen3_06b.lua",
            ):
                shutil.copy2(source, runtime / source.name)
            for filename in ("run_qwen35_scorer.command", "install_qwen35_launch_agent.command", "switch_qwen_model.command"):
                os.chmod(runtime / filename, 0o755)
            shutil.copytree(NATIVE / "data" / "zrm", package / "data" / "zrm")
            (package / "lua" / "mohu_tiger_model_menu.lua").unlink()
            (package / "data" / "sentence-ngram-mobile.bin").write_bytes(b"test-ngram")
            (package / "data" / "zrm" / "mohu_llm_zrm.lexicon.txt").write_text("", encoding="utf-8")
            os.chmod(package / "install_mohu_llm_zrm.command", 0o755)
            os.chmod(package / "install_mohu_llm_scheme.command", 0o755)
            env = {**os.environ, "MOHU_RIME_DIR": rime_tmp, "MOHU_SQUIRREL_BIN": "/does/not/exist", "MOHU_SKIP_SCORER_INSTALL": "1"}
            result = subprocess.run([str(package / "install_mohu_llm_zrm.command")], env=env, text=True, capture_output=True, check=False)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("missing Lua file", result.stdout + result.stderr)
            shutil.copy2(NATIVE / "mohu_tiger_model_menu.lua", package / "lua" / "mohu_tiger_model_menu.lua")
            result = subprocess.run([str(package / "install_mohu_llm_zrm.command")], env=env, text=True, capture_output=True, check=False)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("missing or empty lexicon", result.stdout + result.stderr)

    def test_two_scheme_installers_share_one_patch_and_preserve_top_level_keys(self) -> None:
        # The full fixture is assembled once, then both installers run against
        # one Rime directory to catch duplicate top-level patch blocks.
        with tempfile.TemporaryDirectory() as package_tmp, tempfile.TemporaryDirectory() as rime_tmp:
            packages = Path(package_tmp)
            for scheme in ("zrm", "flypy"):
                package = packages / scheme
                package.mkdir()
                for relative in (
                    f"install_mohu_llm_{scheme}.command",
                    "install_mohu_llm_scheme.command",
                    f"mohu_llm_{scheme}.package.json",
                    f"mohu_llm_{scheme}.schema.yaml",
                ):
                    source = ROOT / relative if relative.endswith(".schema.yaml") else NATIVE / relative
                    shutil.copy2(source, package / relative)
                shutil.copy2(package / f"mohu_llm_{scheme}.package.json", package / "package.json")
                copy_package_base(package, scheme)
                copy_package_lua(package)
                copy_package_manifests(package)
                runtime = package / "runtime"
                runtime.mkdir()
                for source in (NATIVE / "libtigerengine.dylib", NATIVE / "qwen35_scorer.py", NATIVE / "run_qwen35_scorer.command", NATIVE / "install_qwen35_launch_agent.command", NATIVE / "scorer_models.zsh", NATIVE / "switch_qwen_model.command", NATIVE / "mohu_tiger_reranker_profile.lua", NATIVE / "mohu_tiger_reranker_profile_qwen3_06b.lua"):
                    shutil.copy2(source, runtime / source.name)
                for filename in ("run_qwen35_scorer.command", "install_qwen35_launch_agent.command", "switch_qwen_model.command"):
                    os.chmod(runtime / filename, 0o755)
                shutil.copytree(NATIVE / "data" / scheme, package / "data" / scheme)
                (package / "data" / "sentence-ngram-mobile.bin").write_bytes(b"test-ngram")
                os.chmod(package / f"install_mohu_llm_{scheme}.command", 0o755)
                os.chmod(package / "install_mohu_llm_scheme.command", 0o755)
            custom = Path(rime_tmp) / "default.custom.yaml"
            custom.write_text("patch: {menu: {page_size: 9}} # keep\n", encoding="utf-8")
            env = {**os.environ, "MOHU_RIME_DIR": rime_tmp, "MOHU_SQUIRREL_BIN": "/does/not/exist", "MOHU_SKIP_SCORER_INSTALL": "1"}
            for scheme in ("zrm", "flypy"):
                result = subprocess.run([str(packages / scheme / f"install_mohu_llm_{scheme}.command")], env=env, text=True, capture_output=True, check=False)
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            parsed = yaml.safe_load(custom.read_text(encoding="utf-8"))
            registered = parsed["patch"]["schema_list/+"]
            self.assertEqual(
                [{"schema": "mohu_llm_zrm"}, {"schema": "mohu_llm_flypy"}],
                registered,
            )
            self.assertEqual(1, custom.read_text(encoding="utf-8").count("patch:"))

    def test_two_scheme_installers_work_in_either_order(self) -> None:
        for first, second in (("zrm", "flypy"), ("flypy", "zrm")):
            with self.subTest(order=(first, second)), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                ngram = root / "sentence-ngram-mobile.bin"
                ngram.write_bytes(b"test-ngram")
                packages = {}
                for scheme in ("zrm", "flypy"):
                    destination = root / scheme
                    env = {**os.environ, "TIGER_NGRAM": str(ngram), f"MOHU_LLM_{scheme.upper()}_DESTDIR": str(destination)}
                    result = subprocess.run(["make", f"mohu-llm-{scheme}-dist"], cwd=ROOT, env=env, text=True, capture_output=True, check=False)
                    self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                    packages[scheme] = destination
                rime = root / "rime"
                custom = rime / "default.custom.yaml"
                rime.mkdir()
                custom.write_text("patch:\n  menu: {page_size: 9}\nother: keep\n", encoding="utf-8")
                env = {**os.environ, "MOHU_RIME_DIR": str(rime), "MOHU_SQUIRREL_BIN": str(root / "missing"), "MOHU_SKIP_SCORER_INSTALL": "1"}
                for scheme in (first, second):
                    result = subprocess.run([str(packages[scheme] / f"install_mohu_llm_{scheme}.command")], env=env, text=True, capture_output=True, check=False)
                    self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                parsed = yaml.safe_load(custom.read_text(encoding="utf-8"))
                ids = [item["schema"] for item in parsed["patch"]["schema_list/+"]]
                self.assertEqual({"mohu_llm_zrm", "mohu_llm_flypy"}, set(ids))
                self.assertEqual(2, len(ids))
                self.assertEqual("keep", parsed["other"])

    def test_installer_merges_blank_inline_block_and_commented_custom_files(self) -> None:
        cases = (
            "",
            "# only comments\n",
            "patch:\n  menu: {page_size: 9}\n",
            "patch:\n  menu: {page_size: 9}",
            "patch: {menu: {page_size: 9}} # keep this\n",
            "patch: {} # empty map\n",
            "patch: {schema_list/+: []} # empty\n",
            "patch: {schema_list/+: [ ]} # spaced empty\n",
            "patch:\n  schema_list/+: [] # block flow empty\n",
            "patch:\n  schema_list/+: [ ] # block flow spaced empty\n",
            "patch:\n  schema_list/+: [{schema: existing}] # block flow item\n",
            "patch:\n  schema_list/+:\n    - schema: mohu_llm_zrm # existing\n",
        )
        installer = NATIVE / "install_mohu_llm_zrm.command"
        for original in cases:
            with self.subTest(original=original), tempfile.TemporaryDirectory() as package_tmp, tempfile.TemporaryDirectory() as rime_tmp:
                package = Path(package_tmp)
                for relative in (
                    "install_mohu_llm_zrm.command",
                    "install_mohu_llm_scheme.command",
                    "mohu_llm_zrm.package.json",
                    "mohu_llm_zrm.schema.yaml",
                ):
                    source = ROOT / relative if relative.endswith(".schema.yaml") else NATIVE / relative
                    shutil.copy2(source, package / relative)
                shutil.copy2(package / "mohu_llm_zrm.package.json", package / "package.json")
                copy_package_base(package, "zrm")
                copy_package_lua(package)
                copy_package_manifests(package)
                runtime = package / "runtime"
                runtime.mkdir()
                for source in (
                    NATIVE / "libtigerengine.dylib",
                    NATIVE / "qwen35_scorer.py",
                    NATIVE / "run_qwen35_scorer.command",
                    NATIVE / "install_qwen35_launch_agent.command",
                    NATIVE / "scorer_models.zsh",
                    NATIVE / "switch_qwen_model.command",
                    NATIVE / "mohu_tiger_reranker_profile.lua",
                    NATIVE / "mohu_tiger_reranker_profile_qwen3_06b.lua",
                ):
                    shutil.copy2(source, runtime / source.name)
                shutil.copytree(NATIVE / "data" / "zrm", package / "data" / "zrm")
                (package / "data" / "sentence-ngram-mobile.bin").write_bytes(b"test-ngram")
                os.chmod(package / "install_mohu_llm_zrm.command", 0o755)
                os.chmod(package / "install_mohu_llm_scheme.command", 0o755)

                custom = Path(rime_tmp) / "default.custom.yaml"
                if original:
                    custom.write_text(original, encoding="utf-8")
                env = {**os.environ, "MOHU_RIME_DIR": rime_tmp, "MOHU_SQUIRREL_BIN": "/does/not/exist", "MOHU_SKIP_SCORER_INSTALL": "1"}
                result = subprocess.run([str(package / installer.name)], env=env, text=True, capture_output=True, check=False)
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                merged = custom.read_text(encoding="utf-8")
                self.assertEqual(1, merged.count("schema: mohu_llm_zrm"))
                parsed = yaml.safe_load(merged)
                self.assertIn(
                    {"schema": "mohu_llm_zrm"},
                    parsed["patch"]["schema_list/+"],
                )
                if "page_size: 9" in original:
                    self.assertIn("page_size: 9", merged)


if __name__ == "__main__":
    unittest.main()
