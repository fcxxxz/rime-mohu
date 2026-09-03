import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_flypy_assets  # noqa: E402
from build_flypy_assets import (  # noqa: E402
    FIXED_DICTIONARIES,
    convert_fixed_code,
    convert_spelling_code,
)


class FlypyAssetConversionTest(unittest.TestCase):
    def test_flypy_extended_dictionary_labels_flypy_character_table(self) -> None:
        text = (ROOT / "mohu_flypy.extended.dict.yaml").read_text(encoding="utf-8")
        self.assertIn("mohu_flypy.chars      # 小鹤单字表", text)

    def test_classics_dictionary_is_a_generated_flypy_asset(self) -> None:
        self.assertEqual(
            "mohu_flypy.classics",
            build_flypy_assets.ZRM_DICTIONARIES["mohu_zrm.classics.dict.yaml"],
        )
        zrm = (ROOT / "mohu_zrm.classics.dict.yaml").read_text(encoding="utf-8")
        flypy = (ROOT / "mohu_flypy.classics.dict.yaml").read_text(encoding="utf-8")
        self.assertEqual(
            flypy,
            build_flypy_assets.convert_dictionary(
                "mohu_zrm.classics.dict.yaml", "mohu_flypy.classics"
            ),
        )
        self.assertEqual(
            [line.split("\t", 1)[0] for line in zrm.splitlines() if "\t" in line],
            [line.split("\t", 1)[0] for line in flypy.splitlines() if "\t" in line],
        )

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

    def test_builds_flypy_custom_phrases_without_rewriting_source(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "mohu_zrm_custom_phrases.txt"
            original = (
                "#@db/db_name\tmohu_zrm_custom_phrases\n"
                "# add entries to mohu_zrm.extended.dict.yaml\n"
                "自定义\tzdy\t0\n"
            )
            source.write_text(original, encoding="utf-8")
            with mock.patch.object(build_flypy_assets, "ROOT", root):
                build_flypy_assets.build_flypy_custom_phrases()

            self.assertEqual(original, source.read_text(encoding="utf-8"))
            generated = (root / "mohu_flypy_custom_phrases.txt").read_text(
                encoding="utf-8"
            )
            self.assertIn("mohu_flypy_custom_phrases", generated)
            self.assertIn("mohu_flypy.extended.dict.yaml", generated)
            self.assertIn("自定义\tzdy\t0", generated)

    def test_fixed_dictionary_keeps_priority_words_before_generated_characters(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "mohu_zrm_fixed.dict.yaml").write_text(
                "---\n"
                "name: mohu_zrm_fixed\n"
                'version: "1"\n'
                "sort: original\n"
                "...\n"
                "\n"
                "#----------置顶词----------#\n"
                "哪里\tnal\n"
                "\n"
                "#----------生成单字----------#\n"
                "𦰡\tnal\t\t0\n"
                "\n"
                "#----------词库----------#\n"
                "哪里\tnali\n",
                encoding="utf-8",
            )
            (root / "mohu_flypy_tiger_fixed.dict.yaml").write_text(
                "# Generated\n"
                "---\n"
                "name: mohu_flypy_tiger_fixed\n"
                'version: "1"\n'
                "sort: by_weight\n"
                "columns:\n"
                "  - text\n"
                "  - code\n"
                "  - weight\n"
                "...\n"
                "\n"
                "𦰡\tnal\t0\n",
                encoding="utf-8",
            )
            with mock.patch.object(build_flypy_assets, "ROOT", root):
                converted = build_flypy_assets.convert_fixed_dictionary(
                    "mohu_zrm_fixed.dict.yaml", "mohu_flypy_fixed"
                )

        priority = converted.index("#----------置顶词----------#")
        generated = converted.index("#----------生成单字----------#")
        words = converted.index("#----------词库----------#")
        self.assertLess(priority, generated)
        self.assertLess(generated, words)
        self.assertIn("哪里\tnal\n", converted[priority:generated])
        self.assertIn("𦰡\tnal\t\t0", converted[generated:words])


if __name__ == "__main__":
    unittest.main()
