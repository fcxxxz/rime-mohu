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

        self.assertIn(("wk", "为", "1", "3"), zrm)
        self.assertIn(("wkxo", "维修", "1", "20"), zrm)
        self.assertIn(("ww", "为", "1", "3"), fly)
        self.assertIn(("wwxo", "维修", "1", "20"), fly)
        self.assertIn(("ba", "爸", "1", "12"), fly)
        self.assertIn(("ab", "阿布", "2", "20001"), fly)

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
        self.assertEqual(zrm, sorted(zrm, key=lambda r: (r[0], int(r[2]), r[1], int(r[3]))))
        self.assertEqual(fly, sorted(fly, key=lambda r: (r[0], int(r[2]), r[1], int(r[3]))))

    def test_rejects_absolute_output_paths(self):
        with self.assertRaises(ValueError):
            self.tool.validate_output_path(Path("/tmp/llm.lexicon.txt"), ROOT)

    def test_uses_character_readings_to_avoid_reconverting_fly_rows(self):
        rows = [
            ("wz", "为", "1", "3"),
            ("wk", "为", "1", "3"),  # already a fly-key variant
            ("wzxq", "维修", "1", "20"),
        ]
        readings = {"为": {"wz"}, "维": {"wz"}, "修": {"xq"}}
        fly = self.tool.build_rows(rows, scheme="flypy", character_syllables=readings)
        self.assertIn(("ww", "为", "1", "3"), fly)
        self.assertIn(("wk", "为", "1", "3"), fly)
        self.assertIn(("wwxq", "维修", "1", "20"), fly)
        self.assertNotIn(("wzxq", "维修", "1", "20"), fly)

    def test_checked_in_artifacts_have_equal_text_coverage_and_fly_closure(self):
        paths = {
            scheme: ROOT / "tiger_sentence_native" / "data" / scheme
            / f"mohu_{scheme}.lexicon.txt"
            for scheme in ("zrm", "flypy")
        }
        loaded = {scheme: self.tool.load_rows(path) for scheme, path in paths.items()}
        self.assertEqual({r[1] for r in loaded["zrm"]}, {r[1] for r in loaded["flypy"]})
        source = self.tool.load_rows(ROOT / "tiger_sentence_native" / "mohu_tiger.lexicon.txt")
        readings = self.tool.load_character_syllables(ROOT / "mohu_zrm.chars.dict.yaml")
        self.assertEqual(loaded["zrm"], self.tool.build_rows(source, "zrm", readings))
        self.assertEqual(loaded["flypy"], self.tool.build_rows(source, "flypy", readings))
        for rows in loaded.values():
            row_set = set(rows)
            for code, text, rank, freq in rows:
                if len(code) < 2 * len(text) or not code[: 2 * len(text)].isalpha():
                    continue
                for variant in self.tool._fly_closure(code, text):
                    self.assertIn((variant, text, rank, freq), row_set)

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
