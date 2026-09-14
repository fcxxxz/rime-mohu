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

    def test_option_persistence_is_single_sourced(self):
        config = yaml.safe_load((ROOT / "default.yaml").read_text(encoding="utf-8"))
        self.assertNotIn(
            "save_options",
            config.get("switcher", {}),
            "开关持久化应由 option_sync + lua/option_state_data.lua 单一来源承担，"
            "save_options/user.yaml 双写会在重启恢复时打架",
        )
        option_sync = (ROOT / "lua" / "option_sync.lua").read_text(encoding="utf-8")
        for name in ("neural_rerank", "contextual_order"):
            self.assertIn(f'"{name}"', option_sync)


if __name__ == "__main__":
    unittest.main()
