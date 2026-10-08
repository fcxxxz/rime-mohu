import io
import subprocess
import sys
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.modern_readings import load_modern_readings, simplified_reading_weight
from tools.tiger_aux import (
    AuxiliaryEntry,
    build_auxiliary_map,
    load_auxiliary_tsv,
    load_root_counts,
    load_tiger_codes,
    select_longest_codes,
    select_primary_code,
    to_auxiliary_entry,
    to_prefix2,
    write_auxiliary_tsv,
)

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))
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

            self.assertEqual(mapping["的"], AuxiliaryEntry("un", "ud", "ui"))
            self.assertEqual(mapping["高"], AuxiliaryEntry("gg"))
            self.assertEqual(mapping["码"], AuxiliaryEntry("mn", "", "mm"))
            self.assertEqual(mapping["甲"], AuxiliaryEntry("ab", "", "ac"))
            self.assertEqual(mapping["𖿲"], AuxiliaryEntry("pe"))
            self.assertEqual(mapping["𖿳"], AuxiliaryEntry("pp", "", "pe"))

    def test_first_four_code_defines_normal_and_compat_positions(self):
        # 取第一个四码：12 位为正常辅码，14 位兼容打法优先于 13 位；
        # 镜像码（如 fubb）不再参与辅码计算。
        primary = select_primary_code(["bfu", "bfub", "fubb"])
        self.assertEqual(primary, "bfub")
        entry = to_auxiliary_entry(primary)
        self.assertEqual(entry, AuxiliaryEntry("bf", "bb", "bu"))
        self.assertEqual(entry.codes(), ["bf", "bb", "bu"])
        self.assertEqual(entry.compat_codes(), ["bb", "bu"])

        # 无四码的字退回首个最长码，正常辅码不变。
        self.assertEqual(select_primary_code(["gg", "mnm"]), "mnm")
        self.assertEqual(
            to_auxiliary_entry("mnm"),
            AuxiliaryEntry("mn", "", "mm"),
        )
        # 与正常辅码相同的兼容位去重。
        self.assertEqual(
            to_auxiliary_entry("pnnw"),
            AuxiliaryEntry("pn", "pw"),
        )

    def test_compat_positions_drop_small_code_letters(self):
        # 辅码兼容位只用大码：≤3 根的全码末位是末根小码（无 14 位），
        # ≤2 根的第 3 位已是末根小码（无 13 位）。
        self.assertEqual(
            to_auxiliary_entry("cgmk", root_count=3),
            AuxiliaryEntry("cg", "", "cm"),
        )
        self.assertEqual(
            to_auxiliary_entry("cgt", root_count=2),
            AuxiliaryEntry("cg"),
        )
        self.assertEqual(
            to_auxiliary_entry("unid", root_count=3),
            AuxiliaryEntry("un", "", "ui"),
        )
        # 四根及以上末位仍是大码，兼容位保留。
        self.assertEqual(
            to_auxiliary_entry("vzbm", root_count=4),
            AuxiliaryEntry("vz", "vm", "vb"),
        )
        # 根数未知（扩展区无拆分数据）保持旧派生。
        self.assertEqual(
            to_auxiliary_entry("cgmk"),
            AuxiliaryEntry("cg", "ck", "cm"),
        )

    def test_load_root_counts_from_chaifen(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "tiger_chaifen.txt"
            path.write_text(
                "的\t〔白勹丶&nbsp;·&nbsp;unid〕\n"
                "灶\t〔火土&nbsp;·&nbsp;cgt〕\n"
                "一\t〔一&nbsp;·&nbsp;fi〕\n",
                encoding="utf-8",
            )

            self.assertEqual(load_root_counts(path), {"的": 3, "灶": 2, "一": 1})

    def test_missing_required_character_is_an_error(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "tiger.dict.yaml"
            path.write_text("---\n...\n甲\tabcd\t1\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "missing Tiger codes: 乙"):
                build_auxiliary_map(path, ["甲", "乙"])

    def test_auxiliary_tsv_round_trip(self):
        output = io.StringIO()
        write_auxiliary_tsv(
            {
                "甲": AuxiliaryEntry("ab", "ad", "ac"),
                "乙": AuxiliaryEntry("xy"),
            },
            output,
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "aux.txt"
            path.write_text(output.getvalue(), encoding="utf-8")

            self.assertEqual(
                load_auxiliary_tsv(path),
                {
                    "甲": AuxiliaryEntry("ab", "ad", "ac"),
                    "乙": AuxiliaryEntry("xy"),
                },
            )


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
        mapping = build_auxiliary_map(
            self.root / "tiger.dict.yaml",
            self.characters,
            root_counts=load_root_counts(
                self.root / "tools/data/tiger_chaifen.txt"
            ),
        )

        self.assertTrue(set(self.characters).issubset(mapping))
        # 的=白勹丶（3 根）无 14 位；码=石马、兒=臼儿（2 根）无 13 位；
        # 高/儿/⺄ 为单根字，本就无兼容位。
        self.assertEqual(mapping["的"], AuxiliaryEntry("un", "", "ui"))
        self.assertEqual(mapping["高"], AuxiliaryEntry("gg"))
        self.assertEqual(mapping["码"], AuxiliaryEntry("mn"))
        self.assertEqual(mapping["儿"], AuxiliaryEntry("pe"))
        self.assertEqual(mapping["兒"], AuxiliaryEntry("pp"))
        self.assertEqual(mapping["𖿲"], AuxiliaryEntry("pe"))
        self.assertEqual(mapping["𖿳"], AuxiliaryEntry("pp"))
        self.assertEqual(mapping["⺄"], AuxiliaryEntry("ae"))

    def test_generated_tsv_matches_repository_map(self):
        generated_path = self.root / "tools/data/tiger_aux.txt"
        self.assertTrue(generated_path.is_file())

        expected = build_auxiliary_map(
            self.root / "tiger.dict.yaml",
            self.characters,
            root_counts=load_root_counts(
                self.root / "tools/data/tiger_chaifen.txt"
            ),
        )
        actual = load_auxiliary_tsv(generated_path)
        self.assertEqual(actual, {char: expected[char] for char in self.characters})

    def test_generation_utils_load_the_canonical_auxiliary_table(self):
        from tools import utils

        self.assertEqual(utils.aux_table["的"].normal, "un")
        self.assertEqual(utils.aux_table["码"].normal, "mn")
        self.assertEqual(utils.aux_table["𖿲"].normal, "pe")
        self.assertEqual(utils.aux_table["𖿳"].normal, "pp")

    def test_character_generator_version_includes_compatibility_targets(self):
        version_inputs = (
            self.root / "tools/data/chars.txt",
            self.root / "tools/data/tiger_aux.txt",
            self.root / "tools/data/tiger_compatibility_chars.txt",
        )
        expected = datetime.fromtimestamp(
            max(path.stat().st_mtime for path in version_inputs)
        ).strftime("%Y%m%d")

        # write_if_changed deliberately ignores version-only changes in the checked-in
        # artifact; assert the generator output rather than filesystem timestamp drift.
        result = subprocess.run(["uv", "run", "tools/gen_chars.py", "--simplified"],
                                cwd=self.root, capture_output=True, text=True, check=True)
        header = result.stdout.split("\n...\n", 1)[0]
        self.assertEqual(__import__("yaml").safe_load(header)["version"], expected)

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
        # 2026-09-10 起辅码兼容位只用大码：的（3 根 unid）无 14 位 ud，
        # 兒（2 根 ppe）无 13 位 pe。
        self.assertEqual(auxiliary["的"], AuxiliaryEntry("un", "", "ui"))
        self.assertEqual(auxiliary["一"], AuxiliaryEntry("fi"))
        self.assertEqual(auxiliary["儿"], AuxiliaryEntry("pe"))
        self.assertEqual(auxiliary["兒"], AuxiliaryEntry("pp"))
        self.assertEqual(auxiliary["凿"], AuxiliaryEntry("cg", "", "cm"))
        self.assertEqual(auxiliary["灶"], AuxiliaryEntry("cg"))

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
            "tools/data/lexicon_sources/zrm/mohu_zrm.chars.dict.yaml",
            "tools/data/lexicon_sources/zrm/mohu_zrm.base.dict.yaml",
            "tools/data/lexicon_sources/zrm/mohu_zrm.tencent.dict.yaml",
            "tools/data/lexicon_sources/zrm/mohu_zrm.moe.dict.yaml",
            "tools/data/lexicon_sources/zrm/mohu_zrm.words.dict.yaml",
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
                    allowed = self.auxiliary[char].codes()
                    if parts[1] not in allowed:
                        errors.append(
                            f"{filename}:{line_number}: {char} uses {parts[1]}, "
                            f"expected {allowed}"
                        )
                if len(errors) >= 20:
                    break
            if len(errors) >= 20:
                break
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
