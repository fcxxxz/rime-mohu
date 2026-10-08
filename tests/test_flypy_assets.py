import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_flypy_assets  # noqa: E402
import fly_keys  # noqa: E402
import sync_flykey_quickcodes  # noqa: E402
from build_flypy_assets import (  # noqa: E402
    CODE_DICTIONARIES,
    convert_spelling_code,
    convert_table_code,
)


class FlypyAssetConversionTest(unittest.TestCase):
    def test_flypy_sentence_dictionary_contains_character_source(self):
        text = (ROOT / "mohu_flypy.words.dict.yaml").read_text(encoding="utf-8")
        self.assertIn("# Source: mohu_flypy.chars.dict.yaml", text)
        self.assertNotIn("import_tables:", text.split("\n...\n", 1)[0])

    def test_classics_dictionary_is_a_generated_flypy_asset(self) -> None:
        self.assertEqual(
            "mohu_flypy.classics",
            build_flypy_assets.ZRM_DICTIONARIES["tools/data/lexicon_sources/zrm/mohu_zrm.classics.dict.yaml"],
        )
        zrm = (ROOT / "tools/data/lexicon_sources/zrm/mohu_zrm.classics.dict.yaml").read_text(encoding="utf-8")
        flypy = (ROOT / "tools/data/lexicon_sources/flypy/mohu_flypy.classics.dict.yaml").read_text(encoding="utf-8")
        self.assertEqual(
            flypy,
            build_flypy_assets.convert_dictionary(
                "tools/data/lexicon_sources/zrm/mohu_zrm.classics.dict.yaml", "mohu_flypy.classics"
            ),
        )
        self.assertEqual(
            [line.split("\t", 1)[0] for line in zrm.splitlines() if "\t" in line],
            [line.split("\t", 1)[0] for line in flypy.splitlines() if "\t" in line],
        )

    def test_native_flypy_fixed_table_is_not_a_converted_asset(self) -> None:
        self.assertNotIn("mohu_zrm_tiger_fixed.dict.yaml", CODE_DICTIONARIES)
        self.assertNotIn("mohu_zrm_tiger_fixed_legacy.dict.yaml", CODE_DICTIONARIES)

    def test_converts_double_pinyin_and_preserves_tiger_auxiliary_code(self) -> None:
        self.assertEqual("yz;ab", convert_spelling_code("yb;ab"))
        self.assertEqual("ld;cd", convert_spelling_code("ll;cd"))
        self.assertEqual("xn;ef", convert_spelling_code("xc;ef"))
        self.assertEqual("pp;ft", convert_spelling_code("pp;ft"))

    def test_converts_space_delimited_word_code(self) -> None:
        self.assertEqual("yz;ab ld;cd", convert_spelling_code("yb;ab ll;cd"))

    def test_converts_fixed_code_shapes(self) -> None:
        self.assertEqual("yza", convert_table_code("有", "yba"))
        self.assertEqual("yzld", convert_table_code("有来", "ybll"))
        self.assertEqual("yzl", convert_table_code("有来", "ybl"))
        self.assertEqual("mry", convert_table_code("默认", "mry"))
        self.assertEqual("ylld", convert_table_code("有来来", "ylll"))
        self.assertEqual("yllx", convert_table_code("有来小心", "yllx"))

    def test_builds_flypy_custom_phrases_without_rewriting_source(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)

            (root / "tools/data/lexicon_sources/zrm").mkdir(parents=True, exist_ok=True)

            (root / "tools/data/lexicon_sources/flypy").mkdir(parents=True, exist_ok=True)
            source = root / "mohu_zrm_custom_phrases.txt"
            original = (
                "#@db/db_name\tmohu_zrm_custom_phrases\n"
                "# add entries to mohu_zrm.words.dict.yaml\n"
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
            self.assertIn("mohu_flypy.words.dict.yaml", generated)
            self.assertIn("自定义\tzdy\t0", generated)

    def test_code_table_converts_all_rows_and_keeps_source_unchanged(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)

            (root / "tools/data/lexicon_sources/zrm").mkdir(parents=True, exist_ok=True)

            (root / "tools/data/lexicon_sources/flypy").mkdir(parents=True, exist_ok=True)
            source = root / "mohu_zrm.dict.yaml"
            source.write_text("---\nname: mohu_zrm\nsort: original\n...\n哪里\tnal\n式\tuipu\n师\tuipf\n喂\twzd\n")
            before = source.read_bytes()
            old_root = build_flypy_assets.ROOT
            try:
                build_flypy_assets.ROOT = root
                converted = build_flypy_assets.convert_code_table("mohu_zrm.dict.yaml", "mohu_flypy")
            finally:
                build_flypy_assets.ROOT = old_root
            self.assertEqual(before, source.read_bytes())
            self.assertIn("name: mohu_flypy", converted)
            self.assertIn("式\tuipu", converted)
            self.assertIn("师\tuipf", converted)
            self.assertIn("喂\twwd", converted)
            self.assertLess(converted.index("哪里\tnal"), converted.index("式\tuipu"))
            expected = sync_flykey_quickcodes.build_expected(converted.splitlines(), sync_flykey_quickcodes.find_blocks(converted.splitlines()), fly_keys.FLY_FLYPY)
            for lines in expected.values():
                for line in lines:
                    self.assertIn(line, converted)

    def test_flypy_schemas_wire_fly_flypy_before_generate_code(self) -> None:
        for name in (
            "mohu_flypy.schema.yaml",
            "mohu_flypy_sentence_core.schema.yaml",
        ):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("mohu:/algebra/fly_flypy?", text, name)
            self.assertNotIn("mohu:/algebra/fly_zrm?", text, name)
            # 飞键派生必须先于 generate_code 的 erase/^(.+);(.+)$/ 生效
            self.assertLess(
                text.index("mohu:/algebra/fly_flypy?"),
                text.index("mohu:/algebra/generate_code"),
                name,
            )
        for name in (
            "mohu_zrm.schema.yaml",
            "mohu_zrm_sentence_core.schema.yaml",
        ):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("mohu:/algebra/fly_zrm?", text, name)
            self.assertNotIn("mohu:/algebra/fly_flypy?", text, name)

    def test_user_sentence_top_slot_is_scheme_neutral(self) -> None:
        text = (ROOT / "mohu.yaml").read_text(encoding="utf-8")
        user_slot = text.split("user_sentence_top:", 1)[1].split(
            "user_sentence_bottom:", 1
        )[0]
        self.assertNotIn("mohu_defs:/fly", user_slot)


if __name__ == "__main__":
    unittest.main()
