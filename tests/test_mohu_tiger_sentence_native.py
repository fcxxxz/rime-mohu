import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = ROOT / "mohu_zrm.schema.yaml"
NATIVE_SCHEMA = ROOT / "tiger_sentence_native" / "mohu_tiger_sentence.schema.yaml"
NATIVE_TRANSLATOR = "lua_translator@*mohu_tiger_sentence*translator"


def load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        return yaml.safe_load(stream)


class MohuTigerSentenceSchemaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.default = load_yaml(DEFAULT_SCHEMA)
        cls.native = load_yaml(NATIVE_SCHEMA)

    def test_keeps_native_identity(self) -> None:
        self.assertEqual("mohu_tiger_sentence", self.native["schema"]["schema_id"])
        self.assertEqual("魔虎大模型", self.native["schema"]["name"])

    def test_uses_default_schema_dependencies(self) -> None:
        self.assertEqual(
            self.default["schema"]["dependencies"],
            self.native["schema"]["dependencies"],
        )

    def test_exposes_neural_switch_without_early_commit(self) -> None:
        default_names = [item["name"] for item in self.default["switches"]]
        native_names = [item["name"] for item in self.native["switches"]]
        self.assertNotIn("mohu_tiger_sentence_early_commit", default_names)
        self.assertNotIn("mohu_tiger_sentence_early_commit", native_names)
        self.assertIn("mohu_tiger_sentence_neural_rerank", native_names)
        native_switch = next(
            item
            for item in self.native["switches"]
            if item["name"] == "mohu_tiger_sentence_neural_rerank"
        )
        self.assertEqual(["模型重排关", "模型重排开"], native_switch["states"])

    def test_neural_rerank_uses_profile_paths_and_bounded_deadline(self) -> None:
        tiger = self.native["tiger"]
        self.assertEqual("", tiger["rerank_service"])
        self.assertEqual("", tiger["rerank_socket"])
        self.assertEqual("", tiger["rerank_model"])
        self.assertEqual("", tiger["rerank_http_endpoint"])
        self.assertEqual(45, tiger["rerank_timeout_ms"])
        self.assertEqual(140, tiger["rerank_full_timeout_ms"])
        self.assertNotIn("alpha", tiger)

    def test_removes_native_processor_from_runtime_pipeline(self) -> None:
        self.assertNotIn(
            "lua_processor@*mohu_tiger_sentence*processor",
            self.native["engine"]["processors"],
        )

    def test_uses_default_segmentors_and_filters(self) -> None:
        self.assertEqual(
            self.default["engine"]["segmentors"],
            self.native["engine"]["segmentors"],
        )
        self.assertEqual(
            self.default["engine"]["filters"],
            self.native["engine"]["filters"],
        )

    def test_inserts_native_translator_before_default_mohu_translator(self) -> None:
        expected = list(self.default["engine"]["translators"])
        insert_at = expected.index("lua_translator@*mohu_express_translator@with_reorder")
        expected.insert(insert_at, NATIVE_TRANSLATOR)
        self.assertEqual(expected, self.native["engine"]["translators"])

    def test_copies_default_component_configuration(self) -> None:
        sections = (
            "navigator",
            "speller",
            "smart",
            "smart_static",
            "fixed",
            "fixed_legacy",
            "translator_legacy",
            "custom_phrase",
            "chaifen",
            "pinyinhint",
            "emoji",
            "reverse_format",
            "reverse_tiger",
            "reverse_tiger_backtick",
            "punctuator",
            "key_binder",
            "recognizer",
            "recognizer_emoji",
            "mohu",
        )
        for section in sections:
            with self.subTest(section=section):
                self.assertEqual(self.default[section], self.native[section])

    def test_native_schema_does_not_enable_octagram(self) -> None:
        self.assertEqual("mohu:/octagram/enable_for_sentence", self.default["__include"])
        self.assertNotIn("__include", self.native)

    def test_native_candidates_fit_between_fixed_and_smart_quality(self) -> None:
        self.assertGreater(
            self.native["fixed"]["initial_quality"],
            self.native["tiger"]["initial_quality"],
        )
        self.assertGreater(
            self.native["tiger"]["initial_quality"],
            self.native["smart"]["initial_quality"],
        )


if __name__ == "__main__":
    unittest.main()
