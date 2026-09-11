import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ("mohu_zrm", "mohu_flypy")


class NeuralToggleSchemaTest(unittest.TestCase):
    def test_independent_menu_switch_next_to_contextual_order_without_reset(self):
        for schema_id in SCHEMAS:
            with self.subTest(schema=schema_id):
                schema = yaml.safe_load(
                    (ROOT / f"{schema_id}.schema.yaml").read_text(encoding="utf-8")
                )
                switches = schema["switches"]
                names = [switch.get("name") for switch in switches]
                self.assertEqual(names.count("neural_rerank"), 1)
                position = names.index("neural_rerank")
                self.assertEqual(position, names.index("contextual_order") + 1)
                self.assertEqual(
                    switches[position],
                    {"name": "neural_rerank", "states": ["魔虎语义关", "魔虎语义开"]},
                )
                self.assertEqual(
                    switches[position - 1],
                    {"name": "contextual_order", "states": ["单次候选调频", "跨候选调频"]},
                )
                self.assertIn("lua_processor@*option_sync", schema["engine"]["processors"])

    def test_switcher_saves_neural_and_v5_options_independently(self):
        config = yaml.safe_load((ROOT / "default.yaml").read_text(encoding="utf-8"))
        saved = config["switcher"]["save_options"]
        self.assertEqual(saved.count("neural_rerank"), 1)
        self.assertEqual(saved.count("contextual_order"), 1)


if __name__ == "__main__":
    unittest.main()
