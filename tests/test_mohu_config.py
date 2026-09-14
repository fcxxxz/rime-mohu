import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


COMPLETION_NAMESPACES = {
    "mohu_zrm.schema.yaml": ("smart", "smart_static"),
    "mohu_flypy.schema.yaml": ("smart", "smart_static"),
    "mohu_zrm_core.schema.yaml": ("smart", "smart_static"),
    "mohu_flypy_core.schema.yaml": ("smart", "smart_static"),
    "mohu_zrm_sentence_core.schema.yaml": ("translator", "translator_static"),
    "mohu_flypy_sentence_core.schema.yaml": ("translator", "translator_static"),
}


PUBLIC_CORE_SCHEMAS = (
    "mohu_zrm.schema.yaml",
    "mohu_flypy.schema.yaml",
    "mohu_zrm_core.schema.yaml",
    "mohu_flypy_core.schema.yaml",
)


class MohuConfigTest(unittest.TestCase):
    def test_dynamic_translators_preserve_completion(self) -> None:
        for path, namespaces in COMPLETION_NAMESPACES.items():
            with self.subTest(path=path):
                schema = yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))
                for namespace in namespaces:
                    with self.subTest(namespace=namespace):
                        self.assertIs(
                            True,
                            schema[namespace]["enable_completion"],
                        )
                        self.assertIs(
                            True,
                            schema[namespace]["enable_word_completion"],
                        )

    def test_public_and_core_completion_invariants(self) -> None:
        for path in PUBLIC_CORE_SCHEMAS:
            with self.subTest(path=path):
                schema = yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))
                for namespace in ("fixed", "fixed_legacy", "custom_phrase"):
                    with self.subTest(namespace=namespace):
                        self.assertIs(False, schema[namespace]["enable_completion"])
                for namespace in ("reverse_tiger", "reverse_tiger_backtick"):
                    with self.subTest(namespace=namespace):
                        self.assertIs(True, schema[namespace]["enable_completion"])

    def test_extended_dictionaries_keep_wanxiang_import(self) -> None:
        for scheme in ("zrm", "flypy"):
            with self.subTest(scheme=scheme):
                dictionary = yaml.safe_load(
                    (ROOT / f"mohu_{scheme}.extended.dict.yaml").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertIn(f"mohu_{scheme}.wanxiang", dictionary["import_tables"])

    def test_default_registers_only_public_schemes(self) -> None:
        default = yaml.safe_load((ROOT / "default.yaml").read_text(encoding="utf-8"))
        self.assertEqual(
            [{"schema": "mohu_zrm"}, {"schema": "mohu_flypy"}],
            default["schema_list"],
        )

    def test_public_schemas_use_native_model_directory_and_no_qwen_menu(self) -> None:
        for scheme in ("zrm", "flypy"):
            with self.subTest(scheme=scheme):
                path = ROOT / f"mohu_{scheme}.schema.yaml"
                schema = yaml.safe_load(path.read_text(encoding="utf-8"))
                self.assertEqual(f"mohu_{scheme}", schema["schema"]["schema_id"])
                self.assertEqual("mohu/model", schema["tiger"]["model"])
                self.assertEqual(f"mohu/data/{scheme}/mohu_{scheme}.lexicon.txt", schema["tiger"]["lexicon"])
                self.assertEqual(f"mohu_{scheme}", schema["tiger"]["candidate_type"])
                self.assertNotIn("mohu_model_rerank", str(schema))
                self.assertNotIn("mohu_tiger_model_menu", str(schema))
                self.assertNotIn("Qwen", path.read_text(encoding="utf-8"))

    def test_runtime_module_and_flat_builder_exist(self) -> None:
        self.assertTrue((ROOT / "tiger_sentence_native/mohu_runtime.lua").is_file())
        self.assertTrue((ROOT / "tools/build_flat_dist.py").is_file())

    def test_no_removed_qwen_or_installer_runtime_files_are_packaged(self) -> None:
        for scheme in ("zrm", "flypy"):
            output = ROOT / f"dist-{scheme}"
            if not output.exists():
                continue
            names = [str(path.relative_to(output)) for path in output.rglob("*")]
            retired_name = f"mohu_llm_{scheme}.schema.yaml"
            runtime_names = [name for name in names if name != retired_name]
            self.assertFalse(
                any(
                    re.search(r"qwen|install_mohu|package\.json|mohu_llm", name, re.I)
                    for name in runtime_names
                )
            )
            retired = (output / retired_name).read_text(encoding="utf-8")
            self.assertIn('version: "retired"', retired)
            self.assertNotIn("  name:", retired)


if __name__ == "__main__":
    unittest.main()
