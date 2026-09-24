import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "build_mohu_lexicons.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("build_mohu_lexicons", TOOL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class MohuLlmLexiconBuilderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = load_tool()

    def test_builds_scheme_specific_codes_and_fly_closure(self):
        source = """# code\ttext\trank\tfreq_rank
ba\t爸\t1\t12
wz\t为\t1\t3
wzxo\t维修\t1\t20
ab\t阿布\t2\t20001
"""
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "source.txt"
            src.write_text(source, encoding="utf-8")
            rows = self.tool.load_rows(src)
            zrm = self.tool.build_rows(rows, scheme="zrm")
            fly = self.tool.build_rows(rows, scheme="flypy")

        self.assertIn(("wk", "为", "1", "3", "", "wz"), zrm)  # zrm 飞键变体指回 wz
        self.assertIn(("wkxo", "维修", "1", "20", "", ""), zrm)
        self.assertIn(("ww", "为", "1", "3", "", ""), fly)
        self.assertIn(("wwxo", "维修", "1", "20", "", ""), fly)
        self.assertIn(("ba", "爸", "1", "12", "", ""), fly)
        self.assertIn(("ab", "阿布", "2", "0", "", ""), fly)  # 未知权重词行第4列为0

    def test_text_target_set_matches_and_output_is_stably_sorted(self):
        source = """ba\t爸\t1\t12
wz\t为\t2\t3
"""
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "source.txt"
            src.write_text(source, encoding="utf-8")
            rows = self.tool.load_rows(src)
            zrm = self.tool.build_rows(rows, scheme="zrm")
            fly = self.tool.build_rows(rows, scheme="flypy")

        self.assertEqual({r[1] for r in zrm}, {r[1] for r in fly})
        self.assertEqual(zrm, sorted(
            zrm, key=lambda r: (r[0], int(r[2]), r[1], int(r[3]), r[4])))
        self.assertEqual(fly, sorted(
            fly, key=lambda r: (r[0], int(r[2]), r[1], int(r[3]), r[4])))

    def test_reading_frequency_column_from_character_dictionary(self):
        with tempfile.TemporaryDirectory() as tmp:
            chars = Path(tmp) / "chars.dict.yaml"
            chars.write_text(
                "...\n"
                "万\tmo;fp\t1\n"
                "万\twj;fp\t1201402\n"
                "摸\tmo;ul\t342354\n"
                "为\twz;dz\t88\n",
                encoding="utf-8",
            )
            source = Path(tmp) / "source.txt"
            source.write_text(
                "mo\t万\t4\t285\n"
                "mof\t万\t1\t285\n"
                "wj\t万\t2\t285\n"
                "mo\t摸\t1\t886\n"
                "wk\t为\t1\t3\n"       # 源码表自带的飞键行
                "mowo\t摸万\t1\t9\n",   # 多字行不挂读音频率
                encoding="utf-8",
            )
            frequencies = self.tool.load_reading_frequencies(chars)
            rows = self.tool.load_rows(source, frequencies)
        by_code_text = {(r[0], r[1]): r for r in rows}
        self.assertEqual(by_code_text[("mo", "万")][4], "1")
        self.assertEqual(by_code_text[("mof", "万")][4], "1")
        self.assertEqual(by_code_text[("wj", "万")][4], "1201402")
        self.assertEqual(by_code_text[("mo", "摸")][4], "342354")
        self.assertEqual(by_code_text[("wk", "为")][4], "88")  # 飞键行回溯 wz
        self.assertEqual(by_code_text[("mowo", "摸万")][4], "")

    def test_reading_frequency_dedupes_aux_variants_per_syllable(self):
        with tempfile.TemporaryDirectory() as tmp:
            chars = Path(tmp) / "chars.dict.yaml"
            chars.write_text(
                "...\n"
                "摸\tmo;ul\t342354\n"
                "摸\tmo;aa\t342354\n",
                encoding="utf-8",
            )
            frequencies = self.tool.load_reading_frequencies(chars)
        self.assertEqual(frequencies, {("摸", "mo"): 342354})

    def test_rejects_absolute_output_paths(self):
        with self.assertRaises(ValueError):
            self.tool.validate_output_path(Path("/tmp/llm.lexicon.txt"), ROOT)

    def test_uses_character_readings_to_avoid_reconverting_fly_rows(self):
        rows = [
            ("wz", "为", "1", "3", "", ""),
            ("wk", "为", "1", "3", "", ""),  # already a fly-key variant
            ("wzxq", "维修", "1", "20", "", ""),
        ]
        readings = {"为": {"wz"}, "维": {"wz"}, "修": {"xq"}}
        fly = self.tool.build_rows(rows, scheme="flypy", character_syllables=readings)
        self.assertIn(("ww", "为", "1", "3", "", ""), fly)
        # 小鹤不设 wei 飞键：zrm 飞键行还原为 wz 基础码后转换，不再保留 wk 形态
        self.assertNotIn(("wk", "为", "1", "3", "", ""), fly)
        self.assertIn(("wwxq", "维修", "1", "20", "", ""), fly)
        self.assertIn(("wwxo", "维修", "1", "20", "", ""), fly)  # 小鹤闭包仅 xq→xo
        self.assertNotIn(("wzxq", "维修", "1", "20", "", ""), fly)

    def test_variant_rows_carry_canonical_reading_head(self):
        """飞键换头变体行携带第 6 列规范头；非换头行（含辅码变体）为空。"""
        rows = [("ju", "句", "8", "1062", "254300", "")]
        zrm = self.tool.build_rows(rows, scheme="zrm")
        fly = self.tool.build_rows(rows, scheme="flypy")
        by_code = {r[0]: r for r in zrm}
        self.assertEqual(by_code[("ju")][5], "")      # 源码本身，无规范头
        self.assertEqual(by_code[("jv")][5], "ju")    # jv→ju 换头变体
        by_code_fly = {r[0]: r for r in fly}
        self.assertEqual(by_code_fly[("ju")][5], "")
        self.assertEqual(by_code_fly[("jv")][5], "ju")
        # 词行（多字）不携带规范头：读音先验对多字行恒中性
        word_rows = [("vgju", "整句", "2", "20001", "", "")]
        zrm_word = self.tool.build_rows(word_rows, scheme="zrm")
        for code, text, rank, freq, reading, canonical in zrm_word:
            if text == "整句":
                self.assertEqual(canonical, "")

    def test_three_char_full_code_injection_and_abbreviation_weight(self):
        source = [("bgr", "八个人", "1", "20001", "", "")]
        words = {"八个人": (354, "bagerf"), "把个人": (173, "bagerf")}
        rows = self.tool.build_rows(source, scheme="zrm", word_weights=words)
        self.assertIn(("bgr", "八个人", "1", "354", "", ""), rows)
        self.assertIn(("bagerf", "八个人", "99", "354", "", ""), rows)
        self.assertIn(("bagerf", "把个人", "99", "173", "", ""), rows)

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base.dict.yaml"
            base.write_text(
                "...\n八个人\tba;hb ge;jg rf;jr\t354\n"
                "把个人\tba;um ge;jg rf;jr\t173\n"
                "八个人\tba;zz ge;zz rf;zz\t1\n",
                encoding="utf-8",
            )
            self.assertEqual(self.tool.load_base_words(base, 3), words)

    def test_three_char_full_code_fly_key_closure(self):
        words = {"为修句": (42, "wzxqju")}
        rows = self.tool.build_rows([], scheme="zrm", word_weights=words)
        self.assertIn(("wzxqju", "为修句", "99", "42", "", ""), rows)
        self.assertIn(("wkxo jv".replace(" ", ""), "为修句", "99", "42", "", ""), rows)

    def test_checked_in_artifacts_have_equal_text_coverage_and_fly_closure(self):
        paths = {
            scheme: ROOT / "tiger_sentence_native" / "data" / scheme
            / f"mohu_{scheme}.lexicon.txt"
            for scheme in ("zrm", "flypy")
        }
        loaded = {scheme: self.tool.load_rows(path) for scheme, path in paths.items()}
        self.assertEqual({r[1] for r in loaded["zrm"]}, {r[1] for r in loaded["flypy"]})
        chars_dict = ROOT / "mohu_zrm.chars.dict.yaml"
        frequencies = self.tool.load_reading_frequencies(chars_dict)
        source = self.tool.load_rows(
            ROOT / "tiger_sentence_native/mohu_tiger.lexicon.txt", frequencies)
        readings = self.tool.load_character_syllables(chars_dict)
        # 词表注入与词重回填是构建的一部分（2026-09-20 方案 B 二字、
        # 2026-09-23 三字全码）：与 main() 相同地传入两方案的 base
        # 二/三字词表，产物才能往返一致。
        zrm_words = self.tool.load_base_words(ROOT / "mohu_zrm.base.dict.yaml", 2)
        zrm_words.update(self.tool.load_base_words(ROOT / "mohu_zrm.base.dict.yaml", 3))
        flypy_words = self.tool.load_base_words(ROOT / "mohu_flypy.base.dict.yaml", 2)
        flypy_words.update(self.tool.load_base_words(ROOT / "mohu_flypy.base.dict.yaml", 3))
        self.assertEqual(loaded["zrm"],
                         self.tool.build_rows(source, "zrm", readings, zrm_words))
        self.assertEqual(loaded["flypy"],
                         self.tool.build_rows(source, "flypy", readings, flypy_words))
        for scheme, rows in loaded.items():
            fly = self.tool.FLY_ZRM if scheme == "zrm" else self.tool.FLY_FLYPY
            # 闭包不变量按 (码, 词) 存在性检查：注入行（rank 99）的闭包
            # 槽可能被不同档位的源行占据（如 xomo 休谟 rank 1），等值
            # 比较会误报——变体存在即满足不变量。
            prefix_set = {(row[0], row[1]) for row in rows}
            for code, text, rank, freq, reading, _canonical in rows:
                if len(code) < 2 * len(text) or not code[: 2 * len(text)].isalpha():
                    continue
                for variant in self.tool._fly_closure(code, text, fly):
                    self.assertIn((variant, text), prefix_set)

    def test_flypy_fly_set_matches_flypy_syllables(self):
        """小鹤飞键：xq→xo(xiu)、qx→qo(qia)；qie(qp)/wei(ww) 不设飞键。"""
        paths = {
            scheme: ROOT / "tiger_sentence_native" / "data" / scheme
            / f"mohu_{scheme}.lexicon.txt"
            for scheme in ("zrm", "flypy")
        }
        loaded = {scheme: self.tool.load_rows(path) for scheme, path in paths.items()}
        flypy_codes = {r[0] for r in loaded["flypy"]}
        by_text = {}
        for code, text, *_ in loaded["flypy"]:
            by_text.setdefault(text, set()).add(code)
        # xq→xo 闭包存在且含整词链式组合
        self.assertIn("xo", by_text["修"])
        self.assertIn("hdxo", by_text["害羞"])
        self.assertIn("hdxq", by_text["害羞"])
        self.assertIn("wwxo", by_text["维修"])
        # qx→qo（qia 的飞键）：qo 只挂 qia 字，qie 用 qp 无飞键
        self.assertIn("qo", by_text["恰"])
        self.assertIn("qo", by_text["掐"])
        self.assertIn("qohc", by_text["恰好"])
        self.assertIn("qp", by_text["且"])
        self.assertNotIn("qo", by_text["且"])
        self.assertNotIn("aiqo", by_text["哀切"])
        self.assertIn("aiqp", by_text["哀切"])
        # zrm 飞键死行还原为小鹤基础码，不透传 wk/wz 形态
        self.assertNotIn("wk", by_text["为"])
        self.assertIn("ww", by_text["为"])
        self.assertNotIn("anwk", by_text["安慰"])
        self.assertIn("anww", by_text["安慰"])
        self.assertIn("aw", by_text["安慰"])  # 真首字母简码保留
        # 飞键对码位不放简词（2026-09-20 决定）：qx/qo 双侧的 取消 都移除
        self.assertNotIn("qx", by_text["取消"])
        self.assertNotIn("qo", by_text["取消"])
        self.assertIn("qx", by_text["恰"])    # qia 本码仍在
        # wc 卧槽等 w,c 首字母简码不受飞键清理影响
        self.assertIn("wc", by_text["卧槽"])
        # 自然码侧三条飞键闭包原样保留
        zrm_by_text = {}
        for code, text, *_ in loaded["zrm"]:
            zrm_by_text.setdefault(text, set()).add(code)
        self.assertIn("wk", zrm_by_text["为"])
        self.assertIn("qo", zrm_by_text["且"])
        self.assertIn("xo", zrm_by_text["修"])
        self.assertIn("aiqo", zrm_by_text["哀切"])

    def test_filters_non_pinyin_auxiliary_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            chars = Path(tmp) / "chars.dict.yaml"
            chars.write_text("...\n甲\tpp;aux\n乙\tba;aux\n丙\t!!;aux\n", encoding="utf-8")
            readings = self.tool.load_character_syllables(chars)
        self.assertNotIn("甲", readings)
        self.assertEqual({"ba"}, readings["乙"])
        self.assertNotIn("丙", readings)

    def test_accepts_reversible_natural_code_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            chars = Path(tmp) / "chars.dict.yaml"
            chars.write_text(
                "...\n甲\tdz;aux\n乙\tuz;aux\n丙\trw;aux\n丁\tpp;aux\n",
                encoding="utf-8",
            )
            readings = self.tool.load_character_syllables(chars)
        self.assertEqual({"dz"}, readings["甲"])
        self.assertEqual({"uz"}, readings["乙"])
        self.assertEqual({"rw"}, readings["丙"])
        self.assertNotIn("丁", readings)

    def test_makefile_exposes_lexicon_generation_and_runs_its_test(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("mohu_lexicons:", makefile)
        self.assertIn("tools/build_mohu_lexicons.py", makefile)
        self.assertIn("tests.test_mohu_lexicons", makefile)


if __name__ == "__main__":
    unittest.main()
