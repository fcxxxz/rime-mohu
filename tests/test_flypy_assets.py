import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_flypy_assets import (  # noqa: E402
    FIXED_DICTIONARIES,
    convert_fixed_code,
    convert_spelling_code,
)


class FlypyAssetConversionTest(unittest.TestCase):
    def test_flypy_extended_dictionary_labels_flypy_character_table(self) -> None:
        text = (ROOT / "mohu_flypy.extended.dict.yaml").read_text(encoding="utf-8")
        self.assertIn("mohu_flypy.chars      # 小鹤单字表", text)

    def test_native_flypy_fixed_table_is_not_a_converted_asset(self) -> None:
        self.assertNotIn("mohu_zrm_tiger_fixed.dict.yaml", FIXED_DICTIONARIES)
        self.assertNotIn("mohu_zrm_tiger_fixed_legacy.dict.yaml", FIXED_DICTIONARIES)

    def test_converts_double_pinyin_and_preserves_tiger_auxiliary_code(self) -> None:
        self.assertEqual("yz;ab", convert_spelling_code("yb;ab"))
        self.assertEqual("ld;cd", convert_spelling_code("ll;cd"))
        self.assertEqual("xn;ef", convert_spelling_code("xc;ef"))
        self.assertEqual("pp;ft", convert_spelling_code("pp;ft"))

    def test_converts_space_delimited_word_code(self) -> None:
        self.assertEqual("yz;ab ld;cd", convert_spelling_code("yb;ab ll;cd"))

    def test_converts_fixed_code_shapes(self) -> None:
        self.assertEqual("yza", convert_fixed_code("有", "yba"))
        self.assertEqual("yzld", convert_fixed_code("有来", "ybll"))
        self.assertEqual("yzl", convert_fixed_code("有来", "ybl"))
        self.assertEqual("mry", convert_fixed_code("默认", "mry"))
        self.assertEqual("ylld", convert_fixed_code("有来来", "ylll"))
        self.assertEqual("yllx", convert_fixed_code("有来小心", "yllx"))


if __name__ == "__main__":
    unittest.main()
