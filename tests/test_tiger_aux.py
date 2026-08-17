import csv
import inspect
import io
import subprocess
import sys
import unittest
from collections import Counter, defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.modern_readings import load_modern_readings, simplified_reading_weight
from tools.tiger_aux import (
    build_auxiliary_map,
    load_auxiliary_tsv,
    load_tiger_codes,
    select_longest_codes,
    to_prefix2,
    write_auxiliary_tsv,
)

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))
import rebuild_fixed_tiger  # noqa: E402
from tiger_compatibility import (  # noqa: E402
    build_compatibility_auxiliary_map,
    derive_compatibility_auxiliaries,
)


class ModernReadingTest(unittest.TestCase):
    def test_loads_exact_single_character_modern_readings(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "pinyin_simp.txt"
            path.write_text(
                "重\tchong\t10\n"
                "重\tzhong\t20\n"
                "重庆\tchong qing\t30\n",
                encoding="utf-8",
            )

            modern = load_modern_readings(path)

        self.assertIn(("重", "chong"), modern)
        self.assertIn(("重", "zhong"), modern)
        self.assertNotIn(("重", "tong"), modern)
        self.assertNotIn(("重庆", "chong qing"), modern)

    def test_forces_only_unsupported_simplified_readings_to_zero_weight(self):
        modern = {("吃", "chi"), ("了", "liao")}

        self.assertEqual(simplified_reading_weight("吃", "chi", 100, modern), 100)
        self.assertEqual(simplified_reading_weight("吃", "ji", 805, modern), 0)
        self.assertEqual(simplified_reading_weight("了", "liao", 0, modern), 0)


class TigerAuxUnitTest(unittest.TestCase):
    def test_loads_single_character_codes_in_source_order(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "tiger.dict.yaml"
            path.write_text(
                "---\nname: tiger\n...\n的\tu\t9\n的\tuni\t9\n词语\tabcd\t8\n的\tunid\t1\n",
                encoding="utf-8",
            )

            self.assertEqual(load_tiger_codes(path), {"的": ["u", "uni", "unid"]})

    def test_rejects_malformed_tiger_codes(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "tiger.dict.yaml"
            path.write_text("---\n...\n甲\ta1\t1\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "invalid Tiger code"):
                load_tiger_codes(path)

    def test_selects_all_tied_longest_codes(self):
        self.assertEqual(select_longest_codes(["a", "abc", "ade", "ab"]), ["abc", "ade"])

    def test_prefix2_does_not_use_or_pad_short_codes(self):
        self.assertEqual(to_prefix2("unid"), "un")
        self.assertEqual(to_prefix2("gg"), "gg")
        self.assertEqual(to_prefix2("a"), "a")

    def test_builds_prefix2_map_and_stably_deduplicates(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "tiger.dict.yaml"
            path.write_text(
                "---\n...\n的\tu\t9\n的\tuni\t9\n的\tunid\t1\n"
                "高\tg\t9\n高\tgg\t1\n码\tmn\t9\n码\tmnm\t1\n"
                "甲\tabc\t1\n甲\tabd\t1\n甲\tade\t1\n"
                "儿\tpe\t1\n兒\tppe\t1\n",
                encoding="utf-8",
            )

            mapping = build_auxiliary_map(path, ["的", "高", "码", "甲", "𖿲", "𖿳"])

            self.assertEqual(mapping["的"], ["un"])
            self.assertEqual(mapping["高"], ["gg"])
            self.assertEqual(mapping["码"], ["mn"])
            self.assertEqual(mapping["甲"], ["ab", "ad"])
            self.assertEqual(mapping["𖿲"], ["pe"])
            self.assertEqual(mapping["𖿳"], ["pp"])

    def test_missing_required_character_is_an_error(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "tiger.dict.yaml"
            path.write_text("---\n...\n甲\tabcd\t1\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "missing Tiger codes: 乙"):
                build_auxiliary_map(path, ["甲", "乙"])

    def test_auxiliary_tsv_round_trip(self):
        output = io.StringIO()
        write_auxiliary_tsv({"甲": ["ab", "ad"], "乙": ["xy"]}, output)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "aux.txt"
            path.write_text(output.getvalue(), encoding="utf-8")

            self.assertEqual(load_auxiliary_tsv(path), {"甲": ["ab", "ad"], "乙": ["xy"]})


class TigerCompatibilityUnitTest(unittest.TestCase):
    def test_derives_third_and_fourth_tiger_positions(self):
        self.assertEqual(
            derive_compatibility_auxiliaries(["lwxn"]),
            ["lx", "ln"],
        )
        self.assertEqual(
            derive_compatibility_auxiliaries(["lwni"]),
            ["ln", "li"],
        )

    def test_deduplicates_equal_positions_without_stopping_early(self):
        self.assertEqual(derive_compatibility_auxiliaries(["lwcc"]), ["lc"])
        self.assertEqual(
            derive_compatibility_auxiliaries(["abcd", "abed"]),
            ["ac", "ad", "ae"],
        )

    def test_ignores_tiger_codes_shorter_than_three(self):
        self.assertEqual(derive_compatibility_auxiliaries(["a", "ab"]), [])

    def test_builds_map_from_all_tied_longest_codes(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "tiger.dict.yaml"
            path.write_text(
                "---\n...\n莺\tlwxn\t9\n莺\tlwxo\t8\n萤\tlwcc\t7\n",
                encoding="utf-8",
            )

            mapping = build_compatibility_auxiliary_map(path)

        self.assertEqual(mapping["莺"], ["lx", "ln", "lo"])
        self.assertEqual(mapping["萤"], ["lc"])


class TigerAuxRepositoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.characters = []
        seen = set()
        for relative_path in ("tools/data/chars.txt", "tools/data/chars.dict.yaml"):
            for raw_line in (cls.root / relative_path).read_text(encoding="utf-8").splitlines():
                if not raw_line or raw_line.startswith("#"):
                    continue
                char = raw_line.split("\t", 1)[0]
                if len(char) == 1 and char not in seen:
                    seen.add(char)
                    cls.characters.append(char)

    def test_repository_map_covers_every_character(self):
        mapping = build_auxiliary_map(self.root / "tiger.dict.yaml", self.characters)

        self.assertTrue(set(self.characters).issubset(mapping))
        self.assertEqual(mapping["的"], ["un"])
        self.assertEqual(mapping["高"], ["gg"])
        self.assertEqual(mapping["码"], ["mn"])
        self.assertEqual(mapping["儿"], ["pe"])
        self.assertEqual(mapping["兒"], ["pp"])
        self.assertEqual(mapping["𖿲"], ["pe"])
        self.assertEqual(mapping["𖿳"], ["pp"])
        self.assertEqual(mapping["⺄"], ["ae"])

    def test_generated_tsv_matches_repository_map(self):
        generated_path = self.root / "tools/data/tiger_aux.txt"
        self.assertTrue(generated_path.is_file())

        expected = build_auxiliary_map(self.root / "tiger.dict.yaml", self.characters)
        actual = load_auxiliary_tsv(generated_path)
        self.assertEqual(actual, {char: expected[char] for char in self.characters})

    def test_generation_utils_load_the_canonical_auxiliary_table(self):
        from tools import utils

        self.assertEqual(utils.aux_table["的"], ["un"])
        self.assertEqual(utils.aux_table["码"], ["mn"])
        self.assertEqual(utils.aux_table["𖿲"], ["pe"])
        self.assertEqual(utils.aux_table["𖿳"], ["pp"])

    def test_generators_run_as_direct_scripts(self):
        for script in ("tools/gen_chars.py", "tools/gen_zrmdb.py"):
            with self.subTest(script=script):
                result = subprocess.run(
                    ["uv", "run", script],
                    cwd=self.root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("的\t", result.stdout)


class FixedDictionaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]

    @staticmethod
    def dictionary_rows(path):
        rows = []
        in_body = False
        for line in path.read_text(encoding="utf-8").splitlines():
            if line == "...":
                in_body = True
                continue
            if not in_body or not line or line.startswith("#"):
                continue
            rows.append(line.split("\t"))
        return rows

    def test_production_pinyin_weights_include_simplified_frequency_column(self):
        loader = getattr(rebuild_fixed_tiger, "load_production_pinyin_table", None)
        self.assertIsNotNone(loader)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "chars.txt"
            path.write_text(
                "甲\tjia\t100\t1\n乙\tjia\t0\t900\n丙\tjia\t20\n",
                encoding="utf-8",
            )

            table = loader(path)

        self.assertEqual(table["甲"], [("jia", 100.0)])
        self.assertEqual(table["乙"], [("jia", 900.0)])
        self.assertEqual(table["丙"], [("jia", 20.0)])

    def test_loads_compatibility_order_from_race_profile(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            charset = root / "chars.txt"
            profile = root / "race.tsv"
            charset.write_text("# group\n甲\n乙\n丙\n", encoding="utf-8")
            profile.write_text(
                "rank\tchar\tfrequency_weight\n"
                "1\t乙\t3\n"
                "2\t甲\t2\n"
                "3\t丙\t1\n",
                encoding="utf-8",
            )

            order = rebuild_fixed_tiger.load_compatibility_order(
                charset, profile
            )

        self.assertEqual(order, ["乙", "甲", "丙"])

    def test_rejects_incomplete_compatibility_order(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            charset = root / "chars.txt"
            profile = root / "race.tsv"
            charset.write_text("甲\n乙\n丙\n", encoding="utf-8")
            profile.write_text(
                "rank\tchar\tfrequency_weight\n"
                "1\t乙\t3\n"
                "2\t甲\t2\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError, "profile does not match charset"
            ):
                rebuild_fixed_tiger.load_compatibility_order(charset, profile)

    def test_compatibility_allocation_maximizes_unresolved_coverage(self):
        primary = [
            rebuild_fixed_tiger.SourceEntry("甲", "aaxx", 30),
            rebuild_fixed_tiger.SourceEntry("乙", "bbxx", 20),
        ]
        compatibility = [
            rebuild_fixed_tiger.SourceEntry("甲", "ccca", 30),
            rebuild_fixed_tiger.SourceEntry("甲", "cccb", 30),
            rebuild_fixed_tiger.SourceEntry("乙", "ccca", 20),
        ]

        rows = rebuild_fixed_tiger.allocate_compatibility_codes(
            [], primary, compatibility, ["甲", "乙"]
        )

        self.assertEqual(
            {(row.text, row.code) for row in rows},
            {("乙", "ccca"), ("甲", "cccb")},
        )

    def test_compatibility_allocation_keeps_both_free_codes_for_one_character(self):
        compatibility = [
            rebuild_fixed_tiger.SourceEntry("莹", "yyln", 100),
            rebuild_fixed_tiger.SourceEntry("莹", "yyli", 100),
        ]

        rows = rebuild_fixed_tiger.allocate_compatibility_codes(
            [],
            [rebuild_fixed_tiger.SourceEntry("莹", "yylw", 100)],
            compatibility,
            ["莹"],
        )

        self.assertEqual(
            {(row.text, row.code) for row in rows},
            {("莹", "yyln"), ("莹", "yyli")},
        )

    def test_visible_fixed_owner_blocks_compatibility_promotion(self):
        base = [rebuild_fixed_tiger.TableEntry("乙", "ccca", 20, 0)]

        rows = rebuild_fixed_tiger.allocate_compatibility_codes(
            base,
            [rebuild_fixed_tiger.SourceEntry("甲", "aaxx", 30)],
            [rebuild_fixed_tiger.SourceEntry("甲", "ccca", 30)],
            ["甲", "乙"],
        )

        pairs = {(row.text, row.code) for row in rows}
        self.assertIn(("乙", "ccca"), pairs)
        self.assertNotIn(("甲", "ccca"), pairs)

    def test_out_of_scope_fixed_owner_does_not_block_promotion(self):
        base = [rebuild_fixed_tiger.TableEntry("乙", "ccca", 20, 0)]

        rows = rebuild_fixed_tiger.allocate_compatibility_codes(
            base,
            [rebuild_fixed_tiger.SourceEntry("甲", "aaxx", 30)],
            [rebuild_fixed_tiger.SourceEntry("甲", "ccca", 30)],
            ["甲"],
        )

        pairs = {(row.text, row.code) for row in rows}
        self.assertIn(("乙", "ccca"), pairs)
        self.assertIn(("甲", "ccca"), pairs)

    def test_higher_rank_wins_one_shared_compatibility_code(self):
        primary = [
            rebuild_fixed_tiger.SourceEntry("甲", "aaxx", 30),
            rebuild_fixed_tiger.SourceEntry("乙", "bbxx", 20),
        ]
        compatibility = [
            rebuild_fixed_tiger.SourceEntry("甲", "ccca", 30),
            rebuild_fixed_tiger.SourceEntry("乙", "ccca", 20),
        ]

        rows = rebuild_fixed_tiger.allocate_compatibility_codes(
            [], primary, compatibility, ["甲", "乙"]
        )

        self.assertEqual(
            {(row.text, row.code) for row in rows},
            {("甲", "ccca")},
        )

    def test_full_allocation_accepts_natural_compatibility_inputs(self):
        parameters = inspect.signature(
            rebuild_fixed_tiger.build_full_character_allocation
        ).parameters

        self.assertIn("compatibility_auxiliary_codes", parameters)
        self.assertIn("compatibility_order", parameters)

    def test_natural_compatibility_examples_are_generated_and_promoted(self):
        smart_rows = self.dictionary_rows(
            self.root / "mohu_zrm.chars.dict.yaml"
        )
        smart_pairs = {(fields[0], fields[1]) for fields in smart_rows}
        self.assertTrue(
            {
                ("莺", "yy;lx"),
                ("莺", "yy;ln"),
                ("萤", "yy;lc"),
                ("莹", "yy;ln"),
                ("莹", "yy;li"),
            }.issubset(smart_pairs)
        )

        legacy_rows = self.dictionary_rows(
            self.root / "mohu_zrm_tiger_fixed_legacy.dict.yaml"
        )
        legacy_pairs = {(fields[0], fields[1]) for fields in legacy_rows}
        self.assertTrue(
            {
                ("蕉", "jcl"),
                ("藠", "jclu"),
                ("莺", "yylx"),
                ("萤", "yylc"),
                ("莹", "yyln"),
                ("萦", "yyli"),
            }.issubset(legacy_pairs)
        )
        self.assertNotIn(("莺", "yyln"), legacy_pairs)
        self.assertNotIn(("莹", "yyli"), legacy_pairs)

    def test_flypy_character_build_excludes_natural_compatibility_auxiliaries(self):
        from tools import build_flypy_assets

        converted = build_flypy_assets.convert_dictionary(
            "mohu_zrm.chars.dict.yaml",
            "mohu_flypy.chars",
        )
        rows = set(converted.splitlines())

        self.assertIn("莺\tyk;lw\t25291", rows)
        self.assertIn("萤\tyk;lw\t13116", rows)
        self.assertIn("莹\tyk;lw\t106488", rows)
        self.assertNotIn("莺\tyk;lx\t25291", rows)
        self.assertNotIn("莺\tyk;ln\t25291", rows)
        self.assertNotIn("萤\tyk;lc\t13116", rows)
        self.assertNotIn("莹\tyk;ln\t106488", rows)
        self.assertNotIn("莹\tyk;li\t106488", rows)

    def test_simplified_character_dictionary_contains_both_compatibility_positions(self):
        result = subprocess.run(
            ["uv", "run", "tools/gen_chars.py", "--simplified"],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        rows = set(result.stdout.splitlines())
        self.assertIn("莺\tyy;lx\t25291", rows)
        self.assertIn("莺\tyy;ln\t25291", rows)
        self.assertIn("萤\tyy;lc\t13116", rows)
        self.assertIn("莹\tyy;ln\t106488", rows)
        self.assertIn("莹\tyy;li\t106488", rows)

    def test_legacy_collisions_cascade_to_the_next_unique_prefix(self):
        entries = [
            rebuild_fixed_tiger.SourceEntry("甲", "abcd", 30),
            rebuild_fixed_tiger.SourceEntry("乙", "abef", 20),
            rebuild_fixed_tiger.SourceEntry("丙", "abgh", 10),
        ]
        legacy = [
            rebuild_fixed_tiger.SourceEntry("甲", "a", 0, "original"),
            rebuild_fixed_tiger.SourceEntry("乙", "a", 0, "original"),
            rebuild_fixed_tiger.SourceEntry("丙", "ab", 0, "original"),
        ]

        rows = rebuild_fixed_tiger.allocate_reading_ordered_codes(
            entries, ["甲", "乙", "丙"], legacy
        )

        assignments = {(row.text, row.code) for row in rows}
        self.assertTrue(
            {("甲", "a"), ("乙", "ab"), ("丙", "abg")}.issubset(assignments)
        )
        owners = defaultdict(set)
        for row in rows:
            owners[row.code].add(row.text)
        self.assertTrue(all(len(chars) == 1 for chars in owners.values()))

    def test_multi_allocation_cascades_through_three_and_four_keys(self):
        entries = [
            rebuild_fixed_tiger.SourceEntry("甲", "abcd", 40),
            rebuild_fixed_tiger.SourceEntry("乙", "abcd", 30),
            rebuild_fixed_tiger.SourceEntry("丙", "abcd", 20),
        ]
        legacy = [
            rebuild_fixed_tiger.SourceEntry("甲", "ab", 0, "original"),
        ]

        rows = rebuild_fixed_tiger.allocate_legacy_codes(
            entries, ["甲", "乙", "丙"], legacy
        )

        self.assertEqual(
            [(row.text, row.code) for row in rows],
            [("甲", "ab"), ("乙", "abc"), ("丙", "abcd")],
        )

    def test_multi_allocation_preserves_one_and_two_key_collisions(self):
        entries = [
            rebuild_fixed_tiger.SourceEntry("甲", "abcd", 30),
            rebuild_fixed_tiger.SourceEntry("乙", "abef", 20),
            rebuild_fixed_tiger.SourceEntry("丙", "abgh", 10),
        ]
        legacy = [
            rebuild_fixed_tiger.SourceEntry("甲", "a", 0, "original"),
            rebuild_fixed_tiger.SourceEntry("乙", "a", 0, "original"),
        ]

        rows = rebuild_fixed_tiger.allocate_legacy_codes(
            entries, ["甲", "乙", "丙"], legacy
        )

        pairs = [(row.text, row.code) for row in rows]
        self.assertEqual(pairs[:2], [("甲", "a"), ("乙", "a")])
        self.assertIn(("丙", "abg"), pairs)

    def test_multi_allocation_preserves_short_fallbacks_and_seeds_cascade(self):
        entries = [
            rebuild_fixed_tiger.SourceEntry("甲", "abcd", 40),
            rebuild_fixed_tiger.SourceEntry("乙", "abcd", 30),
            rebuild_fixed_tiger.SourceEntry("丙", "abcd", 20),
        ]
        legacy = [
            rebuild_fixed_tiger.SourceEntry("甲", "ab", 0, "original"),
        ]
        fallback = [
            rebuild_fixed_tiger.TableEntry("甲", "a", 40, 0),
            rebuild_fixed_tiger.TableEntry("乙", "ab", 30, 1),
        ]

        rows = rebuild_fixed_tiger.allocate_legacy_codes(
            entries,
            ["甲", "乙", "丙"],
            legacy,
            fallback_rows=fallback,
        )

        self.assertEqual(
            [(row.text, row.code) for row in rows],
            [("甲", "ab"), ("乙", "ab"), ("丙", "abc")],
        )

    def test_multi_allocation_only_suppresses_matching_code_paths(self):
        entries = [
            rebuild_fixed_tiger.SourceEntry("甲", "xyzz", 50),
            rebuild_fixed_tiger.SourceEntry("甲", "abcd", 40),
            rebuild_fixed_tiger.SourceEntry("乙", "abce", 30),
        ]
        legacy = [
            rebuild_fixed_tiger.SourceEntry("甲", "xy", 0, "original"),
        ]

        rows = rebuild_fixed_tiger.allocate_legacy_codes(
            entries, ["甲", "乙"], legacy
        )

        self.assertIn(("甲", "abc"), {(row.text, row.code) for row in rows})

    def test_multi_allocation_orders_by_reading_weight_then_tiger_order(self):
        entries = [
            rebuild_fixed_tiger.SourceEntry("甲", "abcd", 10),
            rebuild_fixed_tiger.SourceEntry("乙", "abce", 30),
            rebuild_fixed_tiger.SourceEntry("丙", "abcf", 30),
        ]

        rows = rebuild_fixed_tiger.allocate_legacy_codes(
            entries, ["甲", "乙", "丙"], []
        )

        owners = {row.code: row.text for row in rows}
        self.assertEqual(owners.get("abc"), "乙")

    def test_multi_allocation_uses_tiger_order_before_source_order_for_ties(self):
        entries = [
            rebuild_fixed_tiger.SourceEntry("乙", "abce", 30),
            rebuild_fixed_tiger.SourceEntry("甲", "abcd", 30),
        ]

        rows = rebuild_fixed_tiger.allocate_legacy_codes(
            entries, ["甲", "乙"], []
        )

        owners = {row.code: row.text for row in rows}
        self.assertEqual(owners.get("abc"), "甲")

    def test_multi_allocation_uses_current_reading_weight(self):
        entries = [
            rebuild_fixed_tiger.SourceEntry("甲", "xyzz", 1000),
            rebuild_fixed_tiger.SourceEntry("甲", "abcd", 10),
            rebuild_fixed_tiger.SourceEntry("乙", "abce", 20),
        ]

        rows = rebuild_fixed_tiger.allocate_legacy_codes(
            entries, ["甲", "乙"], []
        )

        owners = {row.code: row.text for row in rows}
        self.assertEqual(owners.get("abc"), "乙")

    def test_multi_allocation_omits_exhausted_prefixes(self):
        entries = [rebuild_fixed_tiger.SourceEntry("甲", "abcd", 10)]
        legacy = [
            rebuild_fixed_tiger.SourceEntry("甲", "ab", 0, "original"),
        ]

        rows = rebuild_fixed_tiger.allocate_legacy_codes(
            entries, ["甲"], legacy
        )

        self.assertNotIn("abc", {row.code for row in rows})
        self.assertNotIn("abcd", {row.code for row in rows})

    def test_exhausted_short_prefix_uses_no_fixed_row(self):
        entries = [
            rebuild_fixed_tiger.SourceEntry("甲", "abcd", 40),
            rebuild_fixed_tiger.SourceEntry("乙", "abef", 30),
            rebuild_fixed_tiger.SourceEntry("丙", "abik", 20),
            rebuild_fixed_tiger.SourceEntry("丁", "abij", 10),
        ]
        legacy = [
            rebuild_fixed_tiger.SourceEntry("甲", "a", 0, "original"),
            rebuild_fixed_tiger.SourceEntry("乙", "ab", 0, "original"),
            rebuild_fixed_tiger.SourceEntry("丙", "abi", 0, "original"),
            rebuild_fixed_tiger.SourceEntry("丁", "a", 0, "original"),
        ]

        rows = rebuild_fixed_tiger.allocate_reading_ordered_codes(
            entries, ["甲", "乙", "丙", "丁"], legacy
        )

        self.assertFalse(any(row.text == "丁" for row in rows))

    def test_rejects_duplicate_short_code_owners(self):
        validator = getattr(rebuild_fixed_tiger, "validate_unique_short_codes", None)
        self.assertIsNotNone(validator)
        rows = [
            rebuild_fixed_tiger.TableEntry("甲", "a", 20, 0),
            rebuild_fixed_tiger.TableEntry("乙", "a", 10, 1),
        ]

        with self.assertRaisesRegex(ValueError, "duplicate short code a: 甲 乙"):
            validator(rows)

    def test_loads_and_validates_fixed_char_code_overrides(self):
        loader = getattr(rebuild_fixed_tiger, "load_fixed_char_code_overrides", None)
        self.assertIsNotNone(loader)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "overrides.tsv"
            path.write_text(
                "# character\tnatural_code\tflypy_code\n"
                "甲\tabc\txyz\n"
                "乙\tdef\tuvw\n",
                encoding="utf-8",
            )

            self.assertEqual({"甲": "abc", "乙": "def"}, loader(path, "zrm"))
            self.assertEqual({"甲": "xyz", "乙": "uvw"}, loader(path, "flypy"))

            invalid_cases = (
                ("甲\tab\txyz\n", "exactly three lowercase"),
                ("甲\tabc\txy1\n", "exactly three lowercase"),
                ("甲\tabc\txyz\n甲\tdef\tuvw\n", "duplicate character"),
                ("甲\tabc\txyz\n乙\tabc\tuvw\n", "duplicate zrm code"),
                ("甲\tabc\txyz\n乙\tdef\txyz\n", "duplicate flypy code"),
            )
            for content, message in invalid_cases:
                with self.subTest(content=content):
                    path.write_text(content, encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, message):
                        loader(path, "zrm")

    def test_manual_three_key_override_reserves_its_two_key_prefix(self):
        self.assertIn(
            "fixed_codes",
            inspect.signature(
                rebuild_fixed_tiger.allocate_reading_ordered_codes
            ).parameters,
        )
        self.assertIn(
            "fixed_codes",
            inspect.signature(rebuild_fixed_tiger.allocate_legacy_codes).parameters,
        )
        entries = [
            rebuild_fixed_tiger.SourceEntry("甲", "abcd", 30),
            rebuild_fixed_tiger.SourceEntry("乙", "abef", 20),
            rebuild_fixed_tiger.SourceEntry("丙", "acdf", 10),
        ]
        overrides = {"甲": "abc"}

        unique_rows = rebuild_fixed_tiger.allocate_reading_ordered_codes(
            entries,
            ["甲", "乙", "丙"],
            fixed_codes=overrides,
        )
        unique_pairs = {(row.text, row.code) for row in unique_rows}
        self.assertIn(("甲", "abc"), unique_pairs)
        self.assertFalse(any(row.code == "ab" for row in unique_rows))

        legacy_rows = rebuild_fixed_tiger.allocate_legacy_codes(
            entries,
            ["甲", "乙", "丙"],
            [
                rebuild_fixed_tiger.SourceEntry("甲", "ab", 0, "original"),
                rebuild_fixed_tiger.SourceEntry("乙", "ab", 0, "original"),
            ],
            fallback_rows=unique_rows,
            fixed_codes=overrides,
        )
        legacy_pairs = {(row.text, row.code) for row in legacy_rows}
        self.assertIn(("甲", "abc"), legacy_pairs)
        self.assertFalse(any(row.code == "ab" for row in legacy_rows))

    def test_curated_character_overrides_and_two_key_words_are_ordered(self):
        overrides = {
            "zrm": {
                "𤭢": "czz",
                "欻": "iwc",
                "挼": "rwu",
                "扽": "dfu",
                "𰻝": "bde",
            },
            "flypy": {
                "𤭢": "cwz",
                "欻": "ixc",
                "挼": "rxu",
                "扽": "dfu",
                "𰻝": "ble",
            },
        }
        expected_sequences = {
            "zrm": {
                "tz": ["忒", "投资"],
                "hd": ["黄", "很多", "回答"],
                "gs": ["共", "公司"],
                "zh": ["脏", "最后"],
                "tg": ["疼", "通过"],
                "dj": ["但", "大家"],
                "ku": ["哭", "开始"],
                "gz": ["给", "工作"],
                "lu": ["路", "老师"],
            },
            "flypy": {
                "tz": ["头", "投资"],
                "hd": ["还", "很多", "回答"],
                "gs": ["共", "公司"],
                "zh": ["脏", "最后"],
                "tg": ["疼", "通过"],
                "dj": ["但", "大家"],
                "ku": ["哭", "开始"],
                "gz": ["够", "工作"],
                "lu": ["路", "老师"],
            },
        }
        released_first_words = {
            "zrm": {"cz": "存在", "iw": "成为", "rw": "认为", "df": "地方", "bd": "不但"},
            "flypy": {"cw": "错误", "ix": "出现", "rx": "如下", "df": "地方", "bl": "本来"},
        }

        for scheme in ("zrm", "flypy"):
            blocked_codes = {code[:2] for code in overrides[scheme].values()}
            for suffix in ("", "_legacy"):
                with self.subTest(scheme=scheme, suffix=suffix):
                    tiger_rows = self.dictionary_rows(
                        self.root / f"mohu_{scheme}_tiger_fixed{suffix}.dict.yaml"
                    )
                    tiger_pairs = {(fields[0], fields[1]) for fields in tiger_rows}
                    self.assertTrue(
                        set(overrides[scheme].items()).issubset(tiger_pairs)
                    )
                    self.assertFalse(
                        any(fields[1] in blocked_codes for fields in tiger_rows)
                    )

                    parent_rows = self.dictionary_rows(
                        self.root / f"mohu_{scheme}_fixed{suffix}.dict.yaml"
                    )
                    for code, expected in expected_sequences[scheme].items():
                        matching = [fields[0] for fields in parent_rows if fields[1] == code]
                        self.assertEqual(expected, matching[: len(expected)])
                    for code, expected in released_first_words[scheme].items():
                        matching = [fields[0] for fields in parent_rows if fields[1] == code]
                        self.assertTrue(matching)
                        self.assertEqual(expected, matching[0])

                    self.assertFalse(
                        any(fields[0] == "欧洲" and len(fields[1]) == 2 for fields in parent_rows)
                    )
                    self.assertNotIn(("同时", "tsu"), {(fields[0], fields[1]) for fields in parent_rows})

    def test_allocates_prefixes_by_tiger_order_before_reading_weight(self):
        builder = getattr(rebuild_fixed_tiger, "build_full_character_allocation", None)
        self.assertIsNotNone(builder)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            tiger_path = root / "tiger.dict.yaml"
            chars_path = root / "chars.txt"
            auxiliary_path = root / "tiger_aux.txt"
            tiger_path.write_text(
                "---\n...\n甲\tabcd\t9\n甲\tabce\t8\n丙\tzzzz\t7\n乙\tabcf\t6\n",
                encoding="utf-8",
            )
            chars_path.write_text("乙\tjia\t100\n甲\tjia\t1\n", encoding="utf-8")
            auxiliary_path.write_text("甲\tab\n乙\tab\n", encoding="utf-8")

            order, rows, _, _, _ = builder(
                tiger_path,
                chars_path,
                auxiliary_path,
                expected_count=2,
            )

        self.assertEqual(order, ["甲", "乙"])
        codes = {
            char: min(
                (row.code for row in rows if row.text == char and row.source == "shape"),
                key=len,
            )
            for char in ("甲", "乙")
        }
        self.assertEqual(codes["甲"], "j")
        self.assertEqual(codes["乙"], "jw")

    def test_builds_full_codes_for_each_double_pinyin_natively(self):
        builder = rebuild_fixed_tiger.build_full_character_allocation
        self.assertIn("double_pinyin", inspect.signature(builder).parameters)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            tiger_path = root / "tiger.dict.yaml"
            chars_path = root / "chars.txt"
            auxiliary_path = root / "tiger_aux.txt"
            legacy_path = root / "legacy.txt"
            tiger_path.write_text("---\n...\n有\tabcd\t9\n", encoding="utf-8")
            chars_path.write_text("有\tyou\t100\n", encoding="utf-8")
            auxiliary_path.write_text("有\tab\n", encoding="utf-8")
            legacy_path.write_text("1\t有\tyb\n", encoding="utf-8")

            _, zrm_short_rows, _, zrm_full_rows, _ = builder(
                tiger_path,
                chars_path,
                auxiliary_path,
                expected_count=1,
                legacy_path=legacy_path,
                double_pinyin="zrm",
            )
            _, flypy_short_rows, _, flypy_full_rows, _ = builder(
                tiger_path,
                chars_path,
                auxiliary_path,
                expected_count=1,
                legacy_path=legacy_path,
                double_pinyin="flypy",
            )

        self.assertIn(("有", "yb"), {(row.text, row.code) for row in zrm_short_rows})
        self.assertIn(("有", "yz"), {(row.text, row.code) for row in flypy_short_rows})
        self.assertEqual({row.code for row in zrm_full_rows}, {"ybab"})
        self.assertEqual({row.code for row in flypy_full_rows}, {"yzab"})

    def test_tiger_order_breaks_equal_reading_weight_ties(self):
        builder = getattr(rebuild_fixed_tiger, "build_full_character_allocation", None)
        self.assertIsNotNone(builder)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            tiger_path = root / "tiger.dict.yaml"
            chars_path = root / "chars.txt"
            auxiliary_path = root / "tiger_aux.txt"
            tiger_path.write_text(
                "---\n...\n甲\tabcd\t9\n乙\tabcf\t8\n",
                encoding="utf-8",
            )
            chars_path.write_text("乙\tjia\t100\n甲\tjia\t100\n", encoding="utf-8")
            auxiliary_path.write_text("甲\tab\n乙\tab\n", encoding="utf-8")

            _, rows, multi_rows, _, _ = builder(
                tiger_path,
                chars_path,
                auxiliary_path,
                expected_count=2,
            )

        codes = {
            char: min(
                (row.code for row in rows if row.text == char and row.source == "shape"),
                key=len,
            )
            for char in ("甲", "乙")
        }
        self.assertEqual(codes["甲"], "j")
        self.assertEqual(codes["乙"], "jw")
        multi_pairs = {(row.text, row.code) for row in multi_rows}
        self.assertTrue({("甲", "j"), ("乙", "jw")}.issubset(multi_pairs))

    def test_simplified_shortcuts_only_use_modern_readings(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            tiger_path = root / "tiger.dict.yaml"
            chars_path = root / "chars.txt"
            auxiliary_path = root / "tiger_aux.txt"
            legacy_path = root / "legacy.txt"
            tiger_path.write_text(
                "---\n...\n吃\tdeai\t9\n重\tldn\t8\n",
                encoding="utf-8",
            )
            chars_path.write_text(
                "吃\tchi\t100\t100\n"
                "吃\tji\t10\t0\n"
                "吃\tqi\t0\t0\n"
                "重\tchong\t50\t50\n"
                "重\tzhong\t200\t200\n"
                "重\ttong\t1\t0\n",
                encoding="utf-8",
            )
            auxiliary_path.write_text("吃\tde\n重\tld\n", encoding="utf-8")
            legacy_path.write_text("1\t吃\tj\n", encoding="utf-8")

            allocation = rebuild_fixed_tiger.build_full_character_allocation(
                tiger_path,
                chars_path,
                auxiliary_path,
                expected_count=2,
                legacy_path=legacy_path,
                shortcut_readings={
                    ("吃", "chi"),
                    ("重", "chong"),
                    ("重", "zhong"),
                },
            )
            self.assertEqual(len(allocation), 5)
            _, short_rows, multi_rows, full_rows, _ = allocation

        short_pairs = {(row.text, row.code) for row in short_rows}
        multi_pairs = {(row.text, row.code) for row in multi_rows}
        full_pairs = {(row.text, row.code) for row in full_rows}
        self.assertIn(("吃", "i"), short_pairs)
        self.assertFalse(
            any(char == "吃" and code.startswith(("j", "q")) for char, code in short_pairs)
        )
        self.assertTrue(any(char == "重" and code.startswith("i") for char, code in short_pairs))
        self.assertTrue(any(char == "重" and code.startswith("v") for char, code in short_pairs))
        self.assertFalse(any(char == "重" and code.startswith("t") for char, code in short_pairs))
        self.assertIn(("吃", "j"), multi_pairs)
        self.assertFalse(any(char == "吃" and code.startswith("q") for char, code in multi_pairs))
        self.assertFalse(any(char == "重" and code.startswith("t") for char, code in multi_pairs))
        self.assertTrue({("吃", "jide"), ("吃", "qide"), ("重", "tsld")}.issubset(full_pairs))

    def test_fixed_rows_only_contain_allocated_short_codes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            tiger_path = root / "tiger.dict.yaml"
            chars_path = root / "chars.txt"
            auxiliary_path = root / "tiger_aux.txt"
            tiger_path.write_text("---\n...\n甲\tabcd\t9\n", encoding="utf-8")
            chars_path.write_text("甲\tjia\t100\t200\n", encoding="utf-8")
            auxiliary_path.write_text("甲\tab\n", encoding="utf-8")

            _, rows, _, _, _ = rebuild_fixed_tiger.build_full_character_allocation(
                tiger_path,
                chars_path,
                auxiliary_path,
                expected_count=1,
            )

        codes = {row.code for row in rows if row.text == "甲"}
        self.assertEqual(codes, {"j"})

    def test_short_tables_report_and_rank_file_follow_tiger_order(self):
        order_loader = getattr(rebuild_fixed_tiger, "build_tiger_character_order", None)
        self.assertIsNotNone(order_loader)
        pinyin_table = rebuild_fixed_tiger.load_production_pinyin_table(
            self.root / "tools/data/chars.txt"
        )
        expected_order = order_loader(
            self.root / "tiger.dict.yaml",
            pinyin_table,
            expected_count=83951,
        )

        table_rows = self.dictionary_rows(self.root / "mohu_zrm_tiger_fixed.dict.yaml")
        self.assertTrue(table_rows)
        self.assertTrue(all(1 <= len(fields[1]) <= 3 for fields in table_rows))

        report_path = (
            self.root
            / "research/tiger_aux/output/tiger_zrm_prefix2_all_code_lengths.tsv"
        )
        with report_path.open(encoding="utf-8", newline="") as source:
            report_rows = list(csv.DictReader(source, delimiter="\t"))
        self.assertEqual(len(report_rows), 83951)
        self.assertEqual([row["char"] for row in report_rows], expected_order)
        self.assertEqual(
            [int(row["tiger_rank"]) for row in report_rows],
            list(range(1, 83952)),
        )
        self.assertTrue(
            all(
                int(row["code_length"]) == len(row["short_code"])
                and 1 <= int(row["code_length"]) <= 4
                for row in report_rows
            )
        )
        self.assertTrue({"的", "词", "詞", "儿", "兒"}.issubset(
            {row["char"] for row in report_rows}
        ))

        rank_rows = []
        for line in (self.root / "lua/tiger_rank.txt").read_text(encoding="utf-8").splitlines():
            char, raw_rank = line.split("\t")
            rank_rows.append((char, int(raw_rank)))
        self.assertEqual([char for char, _ in rank_rows], expected_order)
        self.assertEqual([rank for _, rank in rank_rows], list(range(1, 83952)))

    def test_flypy_short_table_has_its_own_code_length_report(self):
        report_path = (
            self.root
            / "research/tiger_aux/output/tiger_flypy_prefix2_all_code_lengths.tsv"
        )
        self.assertTrue(report_path.is_file())
        with report_path.open(encoding="utf-8", newline="") as source:
            report_rows = list(csv.DictReader(source, delimiter="\t"))

        self.assertEqual(len(report_rows), 83951)
        self.assertTrue(
            all(
                int(row["code_length"]) == len(row["short_code"])
                and 1 <= int(row["code_length"]) <= 4
                for row in report_rows
            )
        )

    def test_legacy_shortcuts_are_reserved_before_tiger_allocation(self):
        rows = self.dictionary_rows(self.root / "mohu_zrm_tiger_fixed.dict.yaml")
        pairs = {(fields[0], fields[1]) for fields in rows}
        expected = {("和", "h"), ("哦", "o"), ("吗", "ma")}
        self.assertTrue(expected.issubset(pairs))
        self.assertNotIn(("或", "h"), pairs)
        self.assertNotIn(("区", "o"), pairs)

    def test_fixed_tables_do_not_duplicate_smart_full_code_paths(self):
        forbidden = {
            ("词", "cisa"),
            ("詞", "cisa"),
            ("照", "vkop"),
            ("昭", "vkop"),
            ("徽", "hvwv"),
            ("微", "wzwv"),
            ("𖿲", "erpe"),
            ("𖿳", "erpp"),
        }
        pairs = {
            (fields[0], fields[1])
            for fields in self.dictionary_rows(self.root / "mohu_zrm_tiger_fixed.dict.yaml")
        }
        self.assertTrue(forbidden.isdisjoint(pairs))

    def test_generated_short_codes_are_unique_in_both_schemes(self):
        expected_one_key = {"b": "不", "d": "的", "x": "小"}
        displaced = {("吧", "b"), ("大", "d"), ("像", "x")}
        for filename in (
            "mohu_zrm_tiger_fixed.dict.yaml",
            "mohu_flypy_tiger_fixed.dict.yaml",
        ):
            with self.subTest(filename=filename):
                rows = self.dictionary_rows(self.root / filename)
                owners = defaultdict(set)
                for fields in rows:
                    self.assertTrue(1 <= len(fields[1]) <= 3)
                    owners[fields[1]].add(fields[0])
                duplicates = {
                    code: sorted(chars)
                    for code, chars in owners.items()
                    if len(chars) > 1
                }
                self.assertEqual({}, duplicates)
                self.assertTrue(displaced.isdisjoint(
                    {(fields[0], fields[1]) for fields in rows}
                ))
                for code, char in expected_one_key.items():
                    self.assertEqual(owners[code], {char})

    def test_legacy_fixed_tables_preserve_moran_multi_short_code(self):
        expected_gai = {"zrm": "glv", "flypy": "gdv"}
        expected_ning = {"zrm": "ny", "flypy": "nk"}
        for scheme in ("zrm", "flypy"):
            filename = f"mohu_{scheme}_tiger_fixed_legacy.dict.yaml"
            with self.subTest(filename=filename):
                rows = self.dictionary_rows(self.root / filename)
                pairs = {
                    (fields[0], fields[1])
                    for fields in rows
                }
                self.assertIn(("不", "b"), pairs)
                self.assertIn(("吧", "b"), pairs)
                self.assertIn(("把", "ba"), pairs)
                self.assertIn(("拔", "ba"), pairs)
                self.assertIn(("仍", "rg"), pairs)
                self.assertIn(("宁", expected_ning[scheme]), pairs)
                self.assertIn(("改", expected_gai[scheme]), pairs)
                self.assertNotIn(("改", "glw"), pairs)

                owners = defaultdict(set)
                for char, code in pairs:
                    owners[code].add(char)
                duplicate_lengths = {
                    len(code)
                    for code, chars in owners.items()
                    if len(chars) > 1
                }
                self.assertTrue(duplicate_lengths.issubset({1, 2}))
                self.assertTrue(all(1 <= len(code) <= 4 for _, code in pairs))
                self.assertEqual(
                    Counter(len(fields[1]) for fields in rows),
                    {1: 42, 2: 435, 3: 4459, 4: 3222},
                )

    def test_multi_fixed_table_recursively_advances_ci_candidates(self):
        rows = self.dictionary_rows(
            self.root / "mohu_zrm_tiger_fixed_legacy.dict.yaml"
        )
        by_code = defaultdict(list)
        for fields in rows:
            by_code[fields[1]].append(fields[0])

        self.assertIn("此", by_code["ci"])
        self.assertEqual(by_code["cis"], ["词"])
        self.assertNotIn("cisa", by_code)

    def test_multi_fixed_rows_do_not_repeat_matching_shorter_prefixes(self):
        for scheme in ("zrm", "flypy"):
            with self.subTest(scheme=scheme):
                rows = self.dictionary_rows(
                    self.root / f"mohu_{scheme}_tiger_fixed_legacy.dict.yaml"
                )
                codes_by_char = defaultdict(set)
                for fields in rows:
                    codes_by_char[fields[0]].add(fields[1])

                repeated = []
                for char, codes in codes_by_char.items():
                    for code in codes:
                        if len(code) < 3:
                            continue
                        shorter = [
                            other
                            for other in codes
                            if len(other) < len(code) and code.startswith(other)
                        ]
                        if shorter:
                            repeated.append((char, code, sorted(shorter)))
                self.assertEqual([], repeated[:20])

    def test_legacy_parent_tables_embed_legacy_tiger_rows(self):
        for scheme in ("zrm", "flypy"):
            with self.subTest(scheme=scheme):
                filename = self.root / f"mohu_{scheme}_fixed_legacy.dict.yaml"
                text = filename.read_text(encoding="utf-8")
                self.assertIn(
                    f"name: mohu_{scheme}_fixed_legacy\n",
                    text,
                )
                self.assertNotIn(f"  - mohu_{scheme}_tiger_fixed_legacy\n", text)
                self.assertIn("#----------生成单字----------#\n", text)
                self.assertIn("安安定定\t", text)

    def test_parent_tables_keep_native_characters_before_same_code_words(self):
        cases = (
            ("zrm", "glv", "橄榄枝"),
            ("flypy", "gdv", "够得着"),
        )
        for scheme, code, word in cases:
            for suffix in ("", "_legacy"):
                filename = self.root / f"mohu_{scheme}_fixed{suffix}.dict.yaml"
                with self.subTest(filename=filename.name, code=code):
                    rows = self.dictionary_rows(filename)
                    matching = [fields[0] for fields in rows if fields[1] == code]
                    self.assertIn("改", matching)
                    self.assertIn(word, matching)
                    self.assertLess(matching.index("改"), matching.index(word))
                    char_row = next(fields for fields in rows if fields[:2] == ["改", code])
                    self.assertGreaterEqual(len(char_row), 4)
                    self.assertEqual("", char_row[2])

    def test_generated_short_codes_prefix_current_full_codes(self):
        for scheme in ("zrm", "flypy"):
            full_codes = defaultdict(set)
            for fields in self.dictionary_rows(
                self.root / f"mohu_{scheme}.chars.dict.yaml"
            ):
                if len(fields) >= 2:
                    full_codes[fields[0]].add(fields[1].replace(";", ""))

            for suffix in ("", "_legacy"):
                with self.subTest(scheme=scheme, suffix=suffix):
                    invalid = []
                    for fields in self.dictionary_rows(
                        self.root / f"mohu_{scheme}_tiger_fixed{suffix}.dict.yaml"
                    ):
                        char, short_code = fields[:2]
                        if not any(
                            full_code.startswith(short_code)
                            for full_code in full_codes[char]
                        ):
                            invalid.append((char, short_code))
                    self.assertEqual([], invalid[:20])

    def test_parent_fixed_tables_embed_generated_characters(self):
        filename = "mohu_zrm_fixed.dict.yaml"
        text = (self.root / filename).read_text(encoding="utf-8")
        self.assertNotIn("  - mohu_zrm_tiger_fixed\n", text)
        self.assertIn("#----------生成单字----------#\n", text)
        self.assertIn("安安定定\taadd", text)
        pairs = {
            (fields[0], fields[1])
            for fields in self.dictionary_rows(self.root / filename)
        }
        self.assertIn(("改", "glv"), pairs)

    def test_removed_character_rows_are_archived_with_source_lines(self):
        cases = (
            ("tools/data/mohu_fixed_simp_legacy_chars.txt", "啊\ta\taaka"),
            ("tools/data/mohu_fixed_simp_legacy_chars.txt", "阿\taa"),
        )
        for filename, old_row in cases:
            with self.subTest(filename=filename):
                lines = (self.root / filename).read_text(encoding="utf-8").splitlines()
                self.assertTrue(any(line.split("\t", 1)[0].isdigit() for line in lines))
                self.assertTrue(any(line.endswith(old_row) for line in lines))

    def test_smart_dictionary_contains_full_codes_and_compatibility_aliases(self):
        rows = self.dictionary_rows(self.root / "mohu_zrm.chars.dict.yaml")
        pairs = {(fields[0], fields[1].replace(";", "")) for fields in rows}

        self.assertIn(("兒", "erpp"), pairs)
        self.assertIn(("𖿲", "erpe"), pairs)
        self.assertIn(("𖿳", "erpp"), pairs)

    def test_japanese_no_uses_no_as_its_input_reading(self):
        expected = ("の", "no;ae")
        for filename in (
            "mohu_zrm.chars.dict.yaml",
            "mohu_flypy.chars.dict.yaml",
        ):
            with self.subTest(filename=filename):
                rows = self.dictionary_rows(self.root / filename)
                codes = {fields[1] for fields in rows if fields[0] == expected[0]}
                self.assertIn(expected[1], codes)
                noa_candidates = [
                    fields[0]
                    for fields in rows
                    if fields[1].replace(";", "").startswith("noa")
                    and fields[2] == "0"
                ]
                self.assertEqual(expected[0], noa_candidates[0])

        symbol_rows = self.dictionary_rows(self.root / "mohu_fixed.symbols.dict.yaml")
        symbol_codes = {fields[1] for fields in symbol_rows if fields[0] == "の"}
        self.assertIn("noa", symbol_codes)

    def test_simplified_table_excludes_compatibility_shortcuts(self):
        pairs = {
            (fields[0], fields[1])
            for fields in self.dictionary_rows(
                self.root / "mohu_zrm_tiger_fixed.dict.yaml"
            )
        }

        self.assertIn(("吃", "ii"), pairs)
        self.assertNotIn(("吃", "jid"), pairs)
        self.assertNotIn(("吃", "qid"), pairs)
        self.assertTrue(any(char == "重" and code.startswith("is") for char, code in pairs))
        self.assertTrue(any(char == "重" and code.startswith("vs") for char, code in pairs))
        self.assertFalse(any(char == "重" and code.startswith("ts") for char, code in pairs))

    def test_simplified_reading_audit_covers_full_tiger_set(self):
        audit_path = (
            self.root
            / "research/tiger_aux/output/simplified_reading_compatibility.tsv"
        )
        with audit_path.open(encoding="utf-8", newline="") as source:
            rows = list(csv.DictReader(source, delimiter="\t"))

        self.assertEqual(
            list(rows[0]),
            [
                "tiger_rank",
                "char",
                "pinyin",
                "trad_weight",
                "simp_weight",
                "classification",
                "shortcut_codes",
                "full_codes",
            ],
        )
        self.assertEqual(len({row["char"] for row in rows}), 83951)
        self.assertEqual(
            sum(row["classification"] == "compatibility" for row in rows),
            94847,
        )

        readings_by_char = defaultdict(list)
        classification = {}
        for row in rows:
            readings_by_char[row["char"]].append(row["classification"])
            classification[(row["char"], row["pinyin"])] = row["classification"]
            if row["classification"] == "compatibility":
                self.assertEqual(row["shortcut_codes"], "")
                self.assertTrue(row["full_codes"])

        categories = Counter()
        for values in readings_by_char.values():
            modern_count = values.count("modern")
            if modern_count == len(values):
                categories["all-modern"] += 1
            elif modern_count:
                categories["mixed"] += 1
            else:
                categories["all-compat"] += 1
        self.assertEqual(
            categories,
            {"all-modern": 8119, "all-compat": 75832},
        )
        self.assertEqual(classification[("吃", "chi")], "modern")
        self.assertNotIn(("吃", "ji"), classification)
        self.assertEqual(classification[("重", "chong")], "modern")
        self.assertEqual(classification[("重", "zhong")], "modern")
        self.assertNotIn(("重", "tong"), classification)

    def test_generation_rules_depend_on_modern_reading_sources(self):
        makefile = (self.root / "Makefile").read_text(encoding="utf-8")
        dict_rule = next(
            line for line in makefile.splitlines() if line.startswith("dict:")
        )
        self.assertIn("chars", dict_rule)
        chars_rule = next(
            line for line in makefile.splitlines()
            if line.startswith("mohu_zrm.chars.dict.yaml:")
        )
        self.assertIn("tools/data/pinyin_simp.txt", chars_rule)
        self.assertIn("tools/modern_readings.py", chars_rule)

        generator = (self.root / "tools/rebuild_fixed_tiger.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("tools/data/pinyin_simp.txt", generator)
        self.assertIn("tools/modern_readings.py", makefile)


class TigerDecompositionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]

    @staticmethod
    def load_mapping(path):
        mapping = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#"):
                continue
            char, value = line.split("\t", 1)
            mapping[char] = value
        return mapping

    def test_runtime_decomposition_uses_official_longest_tiger_codes(self):
        decomposition = self.load_mapping(self.root / "opencc/mohu_chaifen.txt")
        expected = {"的": "unid", "一": "fi", "儿": "pe", "兒": "ppe"}
        for char, full_code in expected.items():
            with self.subTest(char=char):
                self.assertIn(full_code, decomposition[char])

        auxiliary = load_auxiliary_tsv(self.root / "tools/data/tiger_aux.txt")
        self.assertEqual(auxiliary["的"], ["un"])
        self.assertEqual(auxiliary["一"], ["fi"])
        self.assertEqual(auxiliary["儿"], ["pe"])
        self.assertEqual(auxiliary["兒"], ["pp"])

    def test_tiger_equivalent_aliases_have_decomposition_hints(self):
        decomposition = self.load_mapping(self.root / "opencc/mohu_chaifen.txt")
        self.assertIn("pe", decomposition["𖿲"])
        self.assertIn("ppe", decomposition["𖿳"])

    def test_runtime_generator_does_not_read_legacy_mohu_decompositions(self):
        generator = (self.root / "tools/gen_chaifen_filter.py").read_text(encoding="utf-8")
        makefile = (self.root / "Makefile").read_text(encoding="utf-8")
        self.assertNotIn("mohu_chai.txt", generator)
        chaifen_rule = next(
            line for line in makefile.splitlines() if line.startswith("opencc/mohu_chaifen.txt:")
        )
        self.assertNotIn("mohu_chai.txt", chaifen_rule)
        self.assertIn("tiger_chaifen.txt", chaifen_rule)


class DictionaryAuxiliaryInvariantTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.auxiliary = load_auxiliary_tsv(cls.root / "tools/data/tiger_aux.txt")

    def test_every_explicit_auxiliary_segment_uses_tiger_prefix2(self):
        dictionaries = (
            "mohu_zrm.chars.dict.yaml",
            "mohu_zrm.base.dict.yaml",
            "mohu_zrm.tencent.dict.yaml",
            "mohu_zrm.computer.dict.yaml",
            "mohu_zrm.moe.dict.yaml",
            "mohu_zrm.words.dict.yaml",
        )
        errors = []
        for filename in dictionaries:
            path = self.root / filename
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if not line or line.startswith("#"):
                    continue
                fields = line.split("\t")
                if len(fields) < 2 or ";" not in fields[1]:
                    continue
                characters = [char for char in fields[0] if char in self.auxiliary]
                segments = [segment for segment in fields[1].split() if ";" in segment]
                if len(characters) != len(segments):
                    errors.append(f"{filename}:{line_number}: segment count")
                    continue
                for char, segment in zip(characters, segments):
                    parts = segment.split(";")
                    if len(parts) != 2 or not parts[0] or not parts[1]:
                        errors.append(f"{filename}:{line_number}: malformed {segment!r}")
                        continue
                    if parts[1] not in self.auxiliary.get(char, ()):
                        errors.append(
                            f"{filename}:{line_number}: {char} uses {parts[1]}, "
                            f"expected {self.auxiliary.get(char)}"
                        )
                if len(errors) >= 20:
                    break
            if len(errors) >= 20:
                break
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
