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


class TableParsingTest(unittest.TestCase):
    """三种输入的列序兼容（码在前/字在前）与单字码表提取。"""

    def setUp(self) -> None:
        sys.path.insert(0, str(ROOT / "tools"))
        sys.path.insert(0, str(ROOT / "tools/custom"))
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_aux_table_code_first(self) -> None:
        from tools.custom.custom_aux import load_user_aux

        (self.dir / "aux.tsv").write_text("bm\t腌\ndr\tui\t的\n", encoding="utf-8")
        table = load_user_aux(self.dir / "aux.tsv")
        self.assertEqual(table["腌"].normal, "bm")
        self.assertEqual(table["的"].normal, "dr")
        self.assertEqual(table["的"].compat14, "ui")

    def test_shape_table_code_first(self) -> None:
        from tools.custom.custom_aux import load_shape_codes

        (self.dir / "sc.tsv").write_text("ggll\t王\nssss\t木\t4\n", encoding="utf-8")
        source = load_shape_codes(self.dir / "sc.tsv")
        self.assertEqual(source.codes, {"王": ["ggll"], "木": ["ssss"]})
        self.assertEqual(source.root_counts, {"木": 4})

    def test_coded_char_table_extraction(self) -> None:
        from tools.custom.custom_aux import load_coded_chars

        # 夜桜式结构：3 键渐进行排在 4 键行前；假名/符号行混入。
        (self.dir / "ct.tsv").write_text(
            "a\t啊\naa\t阿\naay\t阿\naayk\t阿\naat\t吖\naatn\t吖\n"
            "bk\t报\nbkdp\t暴\nbkdp\t曝\nbkdw\t暴\n"
            "bkexx\t爆\noba\tア\ntk\t£\n«\tno\n",
            encoding="utf-8",
        )
        table = load_coded_chars(self.dir / "ct.tsv")
        self.assertEqual(table.aux["阿"].normal, "yk")
        self.assertEqual(table.aux["吖"].normal, "tn")
        self.assertEqual(table.aux["曝"].normal, "dp")
        # 暴 出现两个辅码：取首现 dp，dw 进别名。
        self.assertEqual(table.aux["暴"].normal, "dp")
        self.assertEqual(table.aliases.get("暴"), ["dw"])
        # 爆 的 5 键行截断取前 2 键。
        self.assertEqual(table.aux["爆"].normal, "ex")
        self.assertEqual(table.stats.graded_rows, 2)
        self.assertEqual(table.stats.graded_mismatch, 0)
        self.assertEqual(table.stats.long_code_rows, 1)
        self.assertEqual(table.stats.skipped_non_han, 3)
        # 字在前的混合表同样可解析。
        (self.dir / "ct2.tsv").write_text("阿\taayk\n吖\taatn\n", encoding="utf-8")
        table2 = load_coded_chars(self.dir / "ct2.tsv")
        self.assertEqual(table2.aux["阿"].normal, "yk")

    def test_coded_char_table_requires_aux_rows(self) -> None:
        from tools.custom.custom_aux import AuxError, load_coded_chars

        (self.dir / "pure.tsv").write_text("a\t啊\nbk\t报\n", encoding="utf-8")
        with self.assertRaises(AuxError) as ctx:
            load_coded_chars(self.dir / "pure.tsv")
        self.assertIn("纯音码表", str(ctx.exception))

    def test_detect_layout_prefers_zrm(self) -> None:
        from tools.custom.build_custom_scheme import detect_layout_for_table
        from tools.custom.custom_aux import load_coded_chars

        # 自然码特征前缀：bk=bao bl=bai bj=ban bh=bang bg=beng。
        (self.dir / "ct.tsv").write_text(
            "bkdp\t金\nbldd\t木\nbjee\t水\nbhpp\t火\nbgkk\t土\n",
            encoding="utf-8",
        )
        table = load_coded_chars(self.dir / "ct.tsv")
        name, ratio = detect_layout_for_table(table)
        self.assertEqual(name, "zrm")
        self.assertGreaterEqual(ratio, 0.8)

    def test_shortcodes_collected_in_source_order(self) -> None:
        from tools.custom.custom_aux import load_coded_chars

        # 1/2/3 键行都进简码物化清单（原表序）；4 键行不进。
        (self.dir / "ct.tsv").write_text(
            "b\t不\nbk\t报\nmoq\t魔\nmoqf\t馍\n",
            encoding="utf-8",
        )
        table = load_coded_chars(self.dir / "ct.tsv")
        self.assertEqual(
            table.shortcodes, [("不", "b"), ("报", "bk"), ("魔", "moq")])
        self.assertEqual(table.stats.short_rows, 2)
        self.assertEqual(table.stats.graded_rows, 1)

    def test_mixed_word_rows_extracted(self) -> None:
        from tools.custom.custom_aux import load_coded_chars

        # 字词混排：词条行（无单字列、有 ≥2 字汉字列）提取为词库。
        (self.dir / "ct.tsv").write_text(
            "b\t不\nmymh\t魔码\nmoqf\t馍\noba\tア\n",
            encoding="utf-8",
        )
        table = load_coded_chars(self.dir / "ct.tsv")
        self.assertEqual(table.words, ["魔码"])
        self.assertEqual(table.stats.in_table_words, 1)
        self.assertEqual(table.stats.skipped_non_han, 1)
        self.assertEqual(table.aux["馍"].normal, "qf")

    def test_load_word_table(self) -> None:
        from tools.custom.custom_aux import AuxError, load_word_table

        (self.dir / "wt.tsv").write_text(
            "mymh\t魔码\n西虹市\txhda\nmoqf\t馍\n# 注释行\n",
            encoding="utf-8",
        )
        source = load_word_table(self.dir / "wt.tsv")
        self.assertEqual(source.words, ["魔码", "西虹市"])
        self.assertEqual(source.skipped, 1)  # 单字行与注释行都不算词

        (self.dir / "empty.tsv").write_text("moqf\t馍\n", encoding="utf-8")
        with self.assertRaises(AuxError):
            load_word_table(self.dir / "empty.tsv")


class IssueFormTest(unittest.TestCase):
    """「转码表」Issue 表单解析（粘贴路径；附件下载不在单测里触网）。"""

    BODY = """### 码表文件

{table}

### 方案名

{scheme}

### 显示名

我的码

### 双拼布局

{layout}

### 简码无重

{unique}
"""

    def setUp(self) -> None:
        sys.path.insert(0, str(ROOT / "tools"))
        sys.path.insert(0, str(ROOT / "tools/custom"))
        from tools.custom import issue_form

        self.module = issue_form
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_form(self, table, scheme="mycode", layout="自动识别", unique="关闭"):
        body = self.BODY.format(table=table, scheme=scheme, layout=layout, unique=unique)
        body_path = self.dir / "body.txt"
        body_path.write_text(body, encoding="utf-8")
        import subprocess

        return subprocess.run(
            ["uv", "run", "tools/custom/issue_form.py", str(body_path), str(self.dir / "work")],
            cwd=ROOT, capture_output=True, text=True, check=False, timeout=120,
        )

    def test_pasted_table_outputs_env(self) -> None:
        result = self.run_form("aayk\t阿\nbkdp\t暴\n", unique="开启")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SCHEME_NAME=mycode", result.stdout)
        self.assertIn("DISPLAY_NAME=我的码", result.stdout)
        self.assertIn("LAYOUT=", result.stdout)
        self.assertIn("UNIQUE_SHORTCODE=true", result.stdout)
        pasted = self.dir / "work" / "pasted-1.txt"
        self.assertIn("aayk\t阿", pasted.read_text(encoding="utf-8"))

    def test_layout_option_mapping(self) -> None:
        result = self.run_form("aayk\t阿\n", layout="flypy（小鹤）")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("LAYOUT=flypy", result.stdout)

    def test_bad_scheme_name_rejected(self) -> None:
        result = self.run_form("aayk\t阿\n", scheme="MyCode")
        self.assertEqual(result.returncode, 3)
        self.assertIn("方案名", result.stderr)

    def test_empty_table_rejected(self) -> None:
        result = self.run_form("这里没有可识别的码表内容")
        self.assertEqual(result.returncode, 3)
        self.assertIn("码表文件", result.stderr)

    def test_unsafe_attachment_url_ignored(self) -> None:
        # 非 GitHub 域的链接不匹配附件正则，直接被无视（不会发起下载）。
        result = self.run_form("[x.txt](https://evil.example.com/user-attachments/assets/abc)")
        self.assertEqual(result.returncode, 3)
        self.assertIn("没有看到附件", result.stderr)

    def test_safe_url_guard(self) -> None:
        # github.com 附件 URL 走 assert_safe_url：域名白名单外的直接拒绝。
        with self.assertRaises(self.module.FormError) as ctx:
            self.module.assert_safe_url("https://objects.githubusercontent.com.evil.tw/asset")
        self.assertIn("拒绝下载", str(ctx.exception))
        with self.assertRaises(self.module.FormError):
            self.module.assert_safe_url("http://github.com/user-attachments/assets/abc")

    def test_sections_parser(self) -> None:
        sections = self.module.parse_sections("### 甲\n\n1\n\n### 乙\n\n2\n")
        self.assertEqual(sections, {"甲": "1", "乙": "2"})


class ReorderBySourceOrderTest(unittest.TestCase):
    """--char-table 的同码组按原码表字序做位置重分配。"""

    def setUp(self) -> None:
        sys.path.insert(0, str(ROOT / "tools"))
        sys.path.insert(0, str(ROOT / "tools/custom"))
        from tools.custom.build_custom_scheme import _reorder_rows_by_source_order

        self.reorder = _reorder_rows_by_source_order

    def test_dispersed_same_code_reordered(self) -> None:
        # 同 code 行物理上不相邻（继承虎码序分散）：源码表序魔在馍前，
        # 内容置换后魔占据组内最早的行位置。
        lines = ["# 注释\n", "摸\tmo\n", "馍\tmoq\n", "膜\tmob\n", "魔\tmoq\n"]
        out = self.reorder(lines, {"魔": 3, "馍": 9, "摸": 0})
        self.assertEqual(
            out,
            ["# 注释\n", "摸\tmo\n", "魔\tmoq\n", "膜\tmob\n", "馍\tmoq\n"],
        )

    def test_duplicate_rows_of_same_char_stay_grouped(self) -> None:
        # 同字的重复行（兼容变体坍缩）order 相同、稳定保持相邻。
        lines = ["瘼\tmoqf\n", "馍\tmoqf\n", "馍\tmoqf\n"]
        out = self.reorder(lines, {"魔": 1, "馍": 2})
        self.assertEqual(out, ["馍\tmoqf\n", "馍\tmoqf\n", "瘼\tmoqf\n"])

    def test_unknown_chars_stay_at_tail_in_order(self) -> None:
        lines = ["馍\tmoqf\n", "瘼\tmoqf\n", "魔\tmoqf\n"]
        out = self.reorder(lines, {"魔": 3, "馍": 9})
        self.assertEqual(out, ["魔\tmoqf\n", "馍\tmoqf\n", "瘼\tmoqf\n"])

    def test_word_rows_untouched(self) -> None:
        # 词行不参与分组（单字行才进组），保持原位置。
        lines = ["词语\tmoq\n", "馍\tmoq\n", "膜\tmob\n", "魔\tmoq\n"]
        out = self.reorder(lines, {"魔": 1, "馍": 2})
        self.assertEqual(out, ["词语\tmoq\n", "魔\tmoq\n", "膜\tmob\n", "馍\tmoq\n"])


class UniqueShortcodeTest(unittest.TestCase):
    """--unique-shortcode 的简码无重整理（纯函数级）。"""

    def setUp(self) -> None:
        sys.path.insert(0, str(ROOT / "tools"))
        sys.path.insert(0, str(ROOT / "tools/custom"))
        from tools.custom.build_custom_scheme import BuildStats, _dedupe_shortcode_rows

        self.dedupe = _dedupe_shortcode_rows
        self.stats_cls = BuildStats

    def dedup(self, rows, unique, remapped=None):
        stats = self.stats_cls()
        out = self.dedupe(list(rows), remapped or set(), stats, unique)
        return [r for r in out if r != "..."], stats

    def test_one_and_two_key_pushed_when_unique(self) -> None:
        rows = ["不\tb\n", "吧\tb\n", "吧\tbadm\n",
                "把\tba\n", "拔\tba\n", "拔\tbau\n"]
        out, stats = self.dedup(rows, unique=True)
        self.assertNotIn("吧\tb\n", out)      # 一简撞位：吧 推后
        self.assertIn("不\tb\n", out)
        self.assertNotIn("拔\tba\n", out)     # 二简撞位：拔 推后
        self.assertIn("把\tba\n", out)
        self.assertIn("吧\tbadm\n", out)      # 更长码行不受影响
        self.assertEqual(stats.unique_pushed, {1: 1, 2: 1})

    def test_native_three_key_collision_pushed_when_unique(self) -> None:
        # 默认模式下双方皆原生行的同码三简是保留特例；unique 模式也推。
        rows = ["宾\tbjn\n", "彬\tbjn\n", "彬\tbjnr\n"]
        out, stats = self.dedup(rows, unique=True)
        self.assertIn("宾\tbjn\n", out)
        self.assertNotIn("彬\tbjn\n", out)
        self.assertEqual(stats.unique_pushed, {3: 1})

    def test_no_longer_code_kept_as_fallback(self) -> None:
        # 撞位字没有更长码行时保留兜底，否则字会从码表消失。
        rows = ["不\tb\n", "吧\tb\n"]
        out, stats = self.dedup(rows, unique=True)
        self.assertIn("吧\tb\n", out)
        self.assertEqual(stats.unique_pushed, {})

    def test_default_keeps_native_three_key_special_case(self) -> None:
        rows = ["宾\tbjn\n", "彬\tbjn\n"]
        out, stats = self.dedup(rows, unique=False)
        self.assertIn("宾\tbjn\n", out)
        self.assertIn("彬\tbjn\n", out)       # 原生同码特例不动
        self.assertEqual(stats.master_pushed_back, [])

    def test_default_pushes_remapped_three_key(self) -> None:
        rows = ["宾\tbjn\n", "彬\tbjn\n"]
        out, stats = self.dedup(rows, unique=False, remapped={("彬", "bjn")})
        self.assertIn("宾\tbjn\n", out)
        self.assertNotIn("彬\tbjn\n", out)
        self.assertTrue(stats.master_pushed_back)


class CharTableCliTest(unittest.TestCase):
    """--char-table 的错误路径（重活开始前即失败）。"""

    def test_pure_phonetic_table_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pure = Path(tmp) / "pure.txt"
            pure.write_text("a\t啊\nbk\t报\n", encoding="utf-8")
            result = subprocess.run(
                ["uv", "run", "tools/custom/build_custom_scheme.py",
                 "--char-table", str(pure), "--name", "puretest"],
                cwd=ROOT, capture_output=True, text=True, check=False, timeout=120,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("纯音码表", result.stderr)

    def test_unknown_layout_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            weird = Path(tmp) / "weird.txt"
            # ab 不是任何内置布局的合法音节；辅码行存在但识别不出双拼。
            weird.write_text("abdp\t金\nabdd\t木\n", encoding="utf-8")
            result = subprocess.run(
                ["uv", "run", "tools/custom/build_custom_scheme.py",
                 "--char-table", str(weird), "--name", "weirdtest"],
                cwd=ROOT, capture_output=True, text=True, check=False, timeout=120,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("命中率", result.stderr)

    def test_char_table_exclusive_with_aux(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            aux = Path(tmp) / "aux.tsv"
            aux.write_text("的\tdr\n", encoding="utf-8")
            pure = Path(tmp) / "t.txt"
            pure.write_text("aayk\t阿\n", encoding="utf-8")
            result = subprocess.run(
                ["uv", "run", "tools/custom/build_custom_scheme.py",
                 "--char-table", str(pure), "--aux", str(aux), "--name", "excl"],
                cwd=ROOT, capture_output=True, text=True, check=False, timeout=120,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("只能二选一", result.stderr)


if __name__ == "__main__":
    unittest.main()
