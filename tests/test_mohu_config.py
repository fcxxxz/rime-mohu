import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class MohuConfigTest(unittest.TestCase):
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

    def test_no_removed_qwen_or_installer_files_are_packaged(self) -> None:
        for scheme in ("zrm", "flypy"):
            output = ROOT / f"dist-{scheme}"
            if not output.exists():
                continue
            names = [str(path.relative_to(output)) for path in output.rglob("*")]
            self.assertFalse(any(re.search(r"qwen|install_mohu|package\.json|mohu_llm", name, re.I) for name in names))


if __name__ == "__main__":
    unittest.main()
