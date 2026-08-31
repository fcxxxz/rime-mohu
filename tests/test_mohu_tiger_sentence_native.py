import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
NATIVE_TRANSLATOR = "lua_translator@*mohu_sentence*translator"
MODEL_MENU_TRANSLATOR = "lua_translator@*mohu_tiger_model_menu*translator"


def load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        return yaml.safe_load(stream)


class MohuLlmSchemaTest(unittest.TestCase):
    CASES = (("zrm", "自然码", "mohu_zrm", "mohu_zrm_fixed"),
             ("flypy", "小鹤", "mohu_flypy", "mohu_flypy_fixed"))

    def test_each_schema_has_explicit_identity_and_lexicon(self) -> None:
        for scheme, label, dictionary, fixed in self.CASES:
            with self.subTest(scheme=scheme):
                schema = load_yaml(ROOT / f"mohu_llm_{scheme}.schema.yaml")
                self.assertEqual(f"mohu_llm_{scheme}", schema["schema"]["schema_id"])
                self.assertEqual(f"魔虎大模型·{label}", schema["schema"]["name"])
                self.assertIn(dictionary, schema["schema"]["dependencies"])
                self.assertIn(fixed, schema["schema"]["dependencies"])
                self.assertEqual(scheme, schema["tiger"]["scheme"])
                self.assertEqual(f"mohu_llm/data/{scheme}/mohu_llm_{scheme}.lexicon.txt", schema["tiger"]["lexicon"])
                self.assertEqual(f"mohu_llm_{scheme}", schema["tiger"]["candidate_type"])

    def test_native_runtime_fallback_uses_selected_scheme(self) -> None:
        text = (ROOT / "tiger_sentence_native" / "mohu_tiger_sentence.lua").read_text(encoding="utf-8")
        self.assertIn("env and env._tiger_scheme", text)

    def test_both_schemas_have_complete_native_pipeline(self) -> None:
        for scheme, _label, _dictionary, _fixed in self.CASES:
            with self.subTest(scheme=scheme):
                schema = load_yaml(ROOT / f"mohu_llm_{scheme}.schema.yaml")
                switches = {item["name"]: item["states"] for item in schema["switches"]}
                self.assertEqual(["模型重排关", "模型重排开"], switches["mohu_llm_model_rerank"])
                self.assertNotIn("mohu_tiger_sentence_early_commit", switches)
                self.assertIn(MODEL_MENU_TRANSLATOR, schema["engine"]["translators"])
                self.assertIn(NATIVE_TRANSLATOR, schema["engine"]["translators"])
                self.assertIn("lua_filter@*mohu_reorder_filter", schema["engine"]["filters"])
                self.assertIn("lua_translator@*mohu_symbol_hint", schema["engine"]["translators"])
                self.assertIn("reverse_lookup_translator@reverse_tiger", schema["engine"]["translators"])
                self.assertNotIn("lua_processor@*mohu_tiger_sentence*processor", schema["engine"]["processors"])
                self.assertNotIn("__include", schema)
                self.assertNotIn("octagram", str(schema).lower())

    def test_native_quality_is_between_fixed_and_smart(self) -> None:
        for scheme, _label, _dictionary, _fixed in self.CASES:
            schema = load_yaml(ROOT / f"mohu_llm_{scheme}.schema.yaml")
            self.assertGreater(schema["fixed"]["initial_quality"], schema["tiger"]["initial_quality"])
            self.assertGreater(schema["tiger"]["initial_quality"], schema["smart"]["initial_quality"])

    def test_legacy_native_schema_is_removed(self) -> None:
        self.assertFalse((ROOT / "tiger_sentence_native" / "mohu_tiger_sentence.schema.yaml").exists())


if __name__ == "__main__":
    unittest.main()
