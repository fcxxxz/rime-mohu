"""tools/custom/build_custom_scheme.py 的集成测试。

用小鹤预设（--name flypytest）跑一次完整构建，对照官方 mohu_flypy
产物做等值断言：主码表除已知 o 区符号分歧外逐词一致、原生词表字词
集合全等、schema 接线正确、共存组包契约成立。错误路径（坏布局、
--strict 缺字）在重活开始前就失败，测试开销小。
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 主表里 zrm 与官方 flypy 自身存在历史分歧的 o 引导符号行
# （官方值不同，派生以自然码主表为准；其中「 ╋」为带空格前缀的行）。
KNOWN_O_REGION_DIVERGENCE = {"╋", " ╋", "—>", "—＞"}


def load_master_rows(path: Path) -> dict[str, set[str]]:
    rows: dict[str, set[str]] = {}
    in_body = False
    fly_block = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if re.match(r"^#\s*开始飞键", line):
            fly_block = True
            continue
        if re.match(r"^#\s*结束飞键", line):
            fly_block = False
            continue
        if fly_block:
            continue
        if line.strip() == "...":
            in_body = True
            continue
        if not in_body or not line.strip() or line.startswith("#") or "\t" not in line:
            continue
        word, code = line.split("\t")[:2]
        rows.setdefault(word, set()).add(code)
    return rows


def load_lexicon_texts(path: Path) -> set[str]:
    result = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        result.add(line.split("\t")[1])
    return result


def load_lexicon_pairs(path: Path) -> set[tuple[str, str]]:
    result = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        result.add((fields[0], fields[1]))
    return result


class BuildCustomSchemeIntegrationTest(unittest.TestCase):
    """一次完整构建（全部断言共用，避免重复两分钟的管线）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.output = Path(tempfile.mkdtemp(prefix="itest-", dir=ROOT / "custom-dist"))
        cls.output = cls.output / "mohu_flypytest"
        result = subprocess.run(
            ["uv", "run", "tools/custom/build_custom_scheme.py",
             "--sp", "flypy", "--name", "flypytest", "--output", str(cls.output)],
            cwd=ROOT, capture_output=True, text=True, check=False, timeout=1200,
        )
        assert result.returncode == 0, result.stderr + result.stdout

    def test_master_dict_matches_official_flypy(self) -> None:
        mine = load_master_rows(self.output / "mohu_flypytest.dict.yaml")
        official = load_master_rows(ROOT / "mohu_flypy.dict.yaml")
        self.assertEqual({w for w in mine if w not in official}, set())
        self.assertEqual({w for w in official if w not in mine}, set())
        diff = {
            w for w in mine.keys() & official.keys()
            if mine[w] != official[w]
        }
        self.assertLessEqual(diff, KNOWN_O_REGION_DIVERGENCE, sorted(diff))

    def test_native_lexicon_text_set_matches_official(self) -> None:
        mine = load_lexicon_texts(
            self.output / "mohu/data/flypytest/mohu_flypytest.lexicon.txt")
        official = load_lexicon_texts(
            ROOT / "tiger_sentence_native/data/flypy/mohu_flypy.lexicon.txt")
        self.assertEqual(mine, official)
        # 码级：自定义构建的 (码,词) 行应是官方行的子集——差异只允许
        # 是官方多出的飞键变体行（自定义方案不带飞键）。
        mine_pairs = load_lexicon_pairs(
            self.output / "mohu/data/flypytest/mohu_flypytest.lexicon.txt")
        official_pairs = load_lexicon_pairs(
            ROOT / "tiger_sentence_native/data/flypy/mohu_flypy.lexicon.txt")
        extra = mine_pairs - official_pairs
        self.assertEqual(extra, set(), sorted(extra)[:5])

    def test_schema_wiring(self) -> None:
        schema = (self.output / "mohu_flypytest.schema.yaml").read_text(encoding="utf-8")
        self.assertIn("schema_id: mohu_flypytest", schema)
        self.assertNotIn("fly_zrm", schema)            # 无飞键装配
        self.assertNotIn("mohu:/algebra/fly_flypy", schema)
        self.assertIn("dictionary: mohu_flypytest.words", schema)
        self.assertIn("scheme: flypytest", schema)     # tiger/scheme 透传
        self.assertIn("candidate_type: mohu_flypytest", schema)
        self.assertIn("mohu/data/flypytest/mohu_flypytest.lexicon.txt", schema)
        self.assertIn("aux_db: lua/zrmdb_flypytest.txt", schema)
        core = (self.output / "mohu_flypytest_sentence_core.schema.yaml").read_text(encoding="utf-8")
        self.assertIn("mohu_flypytest.words", core)
        self.assertNotIn("fly_zrm", core)

    def test_coexistence_packaging(self) -> None:
        self.assertFalse((self.output / "default.yaml").exists())
        custom = (self.output / "default.custom.yaml").read_text(encoding="utf-8")
        self.assertIn("- schema: mohu_flypytest", custom)
        self.assertTrue((self.output / "lua/zrmdb_flypytest.txt").is_file())
        self.assertTrue((self.output / "mohu/four_code_yield_pairs_flypytest.txt").is_file())
        self.assertFalse((self.output / "mohu/four_code_yield_pairs_zrm.txt").exists())
        self.assertFalse((self.output / "mohu/four_code_yield_pairs_flypy.txt").exists())
        self.assertFalse((self.output / "lua/option_state_data.lua").exists())
        self.assertTrue((self.output / "安装说明.md").is_file())
        self.assertTrue((self.output / "构建报告.txt").is_file())
        # 内部方案不出现在选单。
        tiger_schema = (self.output / "tiger.schema.yaml").read_text(encoding="utf-8")
        self.assertNotIn("\n  name:", tiger_schema)

    def test_report_sections(self) -> None:
        report = (self.output / "构建报告.txt").read_text(encoding="utf-8")
        for section in ("辅码覆盖", "辅码离散度", "主码表转换", "原生引擎", "自检", "安装"):
            self.assertIn(section, report)
        self.assertIn("全部通过", report)


class RemapLexiconRowTest(unittest.TestCase):
    """源 lexicon 单字行的虎码辅码尾重映射（换辅码体系的关键步骤）。"""

    BASE = {"的": None, "命": None}
    NEW = {"的": None, "命": None}

    def setUp(self) -> None:
        sys.path.insert(0, str(ROOT / "tools"))
        sys.path.insert(0, str(ROOT / "tools/custom"))
        from tiger_aux import AuxiliaryEntry

        from tools.custom.build_custom_scheme import remap_lexicon_row

        self.remap = remap_lexicon_row
        base = {
            "的": AuxiliaryEntry("un", "", "ui"),
            "命": AuxiliaryEntry("jd", "", "jz"),
        }
        new = {
            "的": AuxiliaryEntry("sd"),
            "命": AuxiliaryEntry("wj"),
        }
        self.base, self.new = base, new

    def test_full_and_first_letter(self) -> None:
        f = lambda code: self.remap(code, "命", self.base, self.new)
        self.assertEqual(f("my"), "my")          # 裸双拼不动
        self.assertEqual(f("myj"), "myw")        # 三码：首辅 j→w
        self.assertEqual(f("myjd"), "mywj")      # 全码 jd→wj
        self.assertEqual(f("myjdo"), "mywjo")    # 全码+o 标记
        self.assertEqual(f("myjd/"), "mywj/")    # 全码+/ 标记
        self.assertEqual(f("myj/"), "myw/")      # 三码+/ 形

    def test_compat_and_fallback(self) -> None:
        f = lambda code: self.remap(code, "的", self.base, self.new)
        # 13 位兼容 ui：新表无兼容位，落到正常位。
        self.assertEqual(f("deui"), "desd")
        # 与虎码尾不匹配的无理尾保留原样。
        self.assertEqual(f("dexy"), "dexy")
        self.assertEqual(f("deq"), "deq")

    def test_unchanged_shapes(self) -> None:
        # 一简行、多字词行、非单字不动。
        self.assertEqual(self.remap("d", "的", self.base, self.new), "d")
        self.assertEqual(self.remap("dewq", "的的", self.base, self.new), "dewq")
        # 未知形态（4 位尾等）保留。
        self.assertEqual(self.remap("deunxy", "的", self.base, self.new), "deunxy")


class BuildCustomSchemeErrorPathTest(unittest.TestCase):
    def test_reserved_name_rejected(self) -> None:
        result = subprocess.run(
            ["uv", "run", "tools/custom/build_custom_scheme.py",
             "--sp", "flypy", "--name", "zrm"],
            cwd=ROOT, capture_output=True, text=True, check=False, timeout=120,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("冲突", result.stderr)

    def test_bad_layout_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.yaml"
            bad.write_text(
                "name: bad\ninitials: {zh: v, ch: i, sh: u}\nfinals: {ang: h}\n",
                encoding="utf-8")
            result = subprocess.run(
                ["uv", "run", "tools/custom/build_custom_scheme.py", "--sp", str(bad)],
                cwd=ROOT, capture_output=True, text=True, check=False, timeout=120,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("构建失败", result.stderr)
            self.assertTrue(
                "韵母" in result.stderr or "零声母" in result.stderr,
                result.stderr,
            )

    def test_strict_aux_coverage_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            aux = Path(tmp) / "aux.tsv"
            aux.write_text("的\tdr\n", encoding="utf-8")
            result = subprocess.run(
                ["uv", "run", "tools/custom/build_custom_scheme.py",
                 "--aux", str(aux), "--name", "stricttest", "--strict"],
                cwd=ROOT, capture_output=True, text=True, check=False, timeout=120,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("--strict", result.stderr)

    def test_aux_and_shape_code_mutually_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            aux = Path(tmp) / "aux.tsv"
            aux.write_text("的\tdr\n", encoding="utf-8")
            shape = Path(tmp) / "shape.tsv"
            shape.write_text("虎\tymfw\n", encoding="utf-8")
            result = subprocess.run(
                ["uv", "run", "tools/custom/build_custom_scheme.py",
                 "--aux", str(aux), "--shape-code", str(shape)],
                cwd=ROOT, capture_output=True, text=True, check=False, timeout=120,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("只能二选一", result.stderr)


if __name__ == "__main__":
    unittest.main()
