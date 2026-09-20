import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEXICON = ROOT / "tiger_sentence_native" / "data" / "zrm" / "mohu_zrm.lexicon.txt"
CHARS_DICT = ROOT / "mohu_zrm.chars.dict.yaml"
TOOL = ROOT / "tools" / "fix_tiger_lexicon_fly.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("fix_tiger_lexicon_fly", TOOL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TigerLexiconFlyCoverageTest(unittest.TestCase):
    """生成后的 native 词表必须包含配置要求的完整飞键闭包。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tool = load_tool()
        cls.rows, _, _ = cls.tool.parse_lexicon(LEXICON)
        cls.syllables = cls.tool.load_syllables(CHARS_DICT)

    def test_no_missing_fly_rows(self) -> None:
        new_rows, _stats = self.tool.compute_missing(self.rows, self.syllables)
        self.assertEqual(
            [], new_rows,
            f"码表缺少 {len(new_rows)} 行飞键条目，"
            f"运行 `uv run tools/fix_tiger_lexicon_fly.py` 补齐，"
            f"首条缺失: {new_rows[0] if new_rows else None}")

    def test_fly_codes_mirror_normal_codes(self) -> None:
        from tools import build_mohu_lexicons

        row_set = {
            (r[0], r[1], r[2], r[3], r[4] if len(r) > 4 else "")
            for r in self.rows
        }
        for row in self.rows:
            code, text, rank, freq = row[:4]
            reading = row[4] if len(row) > 4 else ""
            for variant in build_mohu_lexicons._fly_closure(
                code, text, self.tool.FLY
            ):
                expected = (variant, text, rank, freq, reading or "")
                self.assertIn(
                    expected,
                    row_set,
                    f"飞键 {variant} 缺少「{text}」（源行 {code} rank={rank}）",
                )

if __name__ == "__main__":
    unittest.main()
