"""tools/custom/build_custom_scheme.py 的集成测试。

用小鹤预设（--name flypytest）跑一次完整构建，对照官方 mohu_flypy
产物做等值断言：主码表除已知 o 区符号分歧外逐词一致、原生词表字词
集合全等、schema 接线正确、共存组包契约成立。错误路径（坏布局、
--strict 缺字）在重活开始前就失败，测试开销小。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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

    def test_source_order_is_per_code_and_reading(self) -> None:
        from tools.custom.build_custom_scheme import _reorder_rows_by_source_order
        from tools.custom.custom_aux import load_coded_chars

        path = self.dir / "polyphones.tsv"
        path.write_text(
            "aaxy\t甲\naazz\t乙\nbbxy\t乙\nbbzz\t甲\n"
            "bbq\t甲\nbbq\t乙\n", encoding="utf-8")
        table = load_coded_chars(path)
        lines = ["甲\tbbxy\n", "乙\tbbxy\n", "乙\taaxy\n", "甲\taaxy\n",
                 "乙\tbbq\n", "甲\tbbq\n"]
        self.assertEqual(_reorder_rows_by_source_order(lines, table.order), [
            "乙\tbbxy\n", "甲\tbbxy\n", "甲\taaxy\n", "乙\taaxy\n",
            "甲\tbbq\n", "乙\tbbq\n",
        ])

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

    def run_form(self, table, scheme="mycode", layout="自动识别"):
        body = self.BODY.format(table=table, scheme=scheme, layout=layout)
        body_path = self.dir / "body.txt"
        body_path.write_text(body, encoding="utf-8")
        import subprocess

        return subprocess.run(
            ["uv", "run", "tools/custom/issue_form.py", str(body_path), str(self.dir / "work")],
            cwd=ROOT, capture_output=True, text=True, check=False, timeout=120,
        )

    def test_pasted_table_outputs_env(self) -> None:
        result = self.run_form("aayk\t阿\nbkdp\t暴\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SCHEME_NAME=mycode", result.stdout)
        self.assertIn("DISPLAY_NAME=我的码", result.stdout)
        self.assertIn("LAYOUT=", result.stdout)
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

    def test_files_channel_attachment_url_recognized(self) -> None:
        # Issue 拖拽文件的新通道：files/<id>/<文件名>；必须能被识别且过安全校验。
        url = "https://github.com/user-attachments/files/33667628/1.0_._.txt"
        body = f"[表.txt]({url})"
        self.assertEqual(self.module.ATTACHMENT_URL.findall(body), [url])
        self.module.assert_safe_url(url)
        # assets 旧通道不受影响。
        old = "https://github.com/user-attachments/assets/abc-DEF123"
        self.assertEqual(self.module.ATTACHMENT_URL.findall(f"[a]({old})"), [old])


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
        out = self.reorder(lines, {("魔", "mo"): 3, ("馍", "mo"): 9, ("摸", "mo"): 0})
        self.assertEqual(
            out,
            ["# 注释\n", "摸\tmo\n", "魔\tmoq\n", "膜\tmob\n", "馍\tmoq\n"],
        )

    def test_duplicate_rows_of_same_char_stay_grouped(self) -> None:
        # 同字的重复行（兼容变体坍缩）order 相同、稳定保持相邻。
        lines = ["瘼\tmoqf\n", "馍\tmoqf\n", "馍\tmoqf\n"]
        out = self.reorder(lines, {("魔", "mo"): 1, ("馍", "mo"): 2})
        self.assertEqual(out, ["馍\tmoqf\n", "馍\tmoqf\n", "瘼\tmoqf\n"])

    def test_unknown_chars_stay_at_tail_in_order(self) -> None:
        lines = ["馍\tmoqf\n", "瘼\tmoqf\n", "魔\tmoqf\n"]
        out = self.reorder(lines, {("魔", "mo"): 3, ("馍", "mo"): 9})
        self.assertEqual(out, ["魔\tmoqf\n", "馍\tmoqf\n", "瘼\tmoqf\n"])

    def test_word_rows_untouched(self) -> None:
        # 词行不参与分组（单字行才进组），保持原位置。
        lines = ["词语\tmoq\n", "馍\tmoq\n", "膜\tmob\n", "魔\tmoq\n"]
        out = self.reorder(lines, {("魔", "mo"): 1, ("馍", "mo"): 2})
        self.assertEqual(out, ["词语\tmoq\n", "魔\tmoq\n", "膜\tmob\n", "馍\tmoq\n"])

    def test_only_affected_reading_is_reordered(self) -> None:
        lines = ["甲\tnlda\n", "哪\tnlda\n", "呐\tnada\n", "哪\tnada\n"]
        rank = {("哪", "nl"): 0, ("甲", "nl"): 1,
                ("哪", "na"): 0, ("呐", "na"): 1}
        out = self.reorder(lines, rank, only_groups_with={("哪", "na")})
        self.assertEqual(out[:2], lines[:2])
        self.assertEqual(out[2:], ["哪\tnada\n", "呐\tnada\n"])

    def test_only_groups_with_restricted(self) -> None:
        # 简码无重频序重排：只动含被推字（吧）的组，无关组保持原序。
        lines = ["叭\tbad\n", "吧\tbad\n", "疤\tbaa\n", "靶\tbaa\n"]
        out = self.reorder(lines, {("吧", "ba"): 0, ("叭", "ba"): 1}, only_groups_with={("吧", "ba")})
        self.assertEqual(out[0], "吧\tbad\n")     # 吧(高频)升到组首
        self.assertEqual(out[1], "叭\tbad\n")
        self.assertEqual(out[2:], ["疤\tbaa\n", "靶\tbaa\n"])  # 无关组不动


class UniqueShortcodeTest(unittest.TestCase):
    """简码无重（唯一行为）的逐级降级竞争（纯函数级）。"""

    def setUp(self) -> None:
        sys.path.insert(0, str(ROOT / "tools"))
        sys.path.insert(0, str(ROOT / "tools/custom"))
        from tools.custom.build_custom_scheme import (
            BuildStats,
            _unique_shortcode_pass,
        )

        self.unique_pass = _unique_shortcode_pass
        self.stats_cls = BuildStats

    def run_unique(self, rows, rank):
        stats = self.stats_cls()
        out = self.unique_pass(list(rows), rank, stats)
        return out, stats

    def test_level_downgrade_competition(self) -> None:
        # 不>b>a>b：吧输 b 降 ba、再输把降 bad、bad 级赢叭——叭退全码 badh。
        rows = ["不\tb\n", "吧\tb\n", "吧\tbadm\n", "把\tba\n", "拔\tba\n",
                "拔\tbau\n", "叭\tbad\n", "叭\tbadh\n"]
        rank = {("不", "b"): 0, ("把", "ba"): 1, ("吧", "ba"): 2,
                ("拔", "ba"): 3, ("叭", "ba"): 4}
        out, stats = self.run_unique(rows, rank)
        self.assertIn("不\tb\n", out)
        self.assertNotIn("吧\tb\n", out)      # 一级输给「不」
        self.assertIn("把\tba\n", out)
        self.assertNotIn("拔\tba\n", out)     # 二级输给「把」
        self.assertNotIn("吧\tba\n", out)     # 吧二级也输，不生成 ba 行
        self.assertIn("吧\tbad\n", out)       # 三级赢「叭」：生成行
        self.assertNotIn("叭\tbad\n", out)    # 叭退全码
        self.assertIn("叭\tbadh\n", out)      # 全码行永不动
        self.assertIn("拔\tbau\n", out)       # 拔降到自己的三码 bau 并占位
        self.assertIn("吧\tbadm\n", out)
        self.assertEqual(stats.unique_pushed, {1: 1, 2: 1, 3: 1})

    def test_no_longer_code_kept_as_fallback(self) -> None:
        # 撞位字没有更长码行时保留兜底，否则字会从码表消失。
        rows = ["不\tb\n", "吧\tb\n"]
        out, stats = self.run_unique(rows, {("不", "b"): 0, ("吧", "b"): 1})
        self.assertIn("不\tb\n", out)
        self.assertIn("吧\tb\n", out)
        self.assertEqual(stats.unique_pushed, {})

    def test_polyphone_cascades_use_independent_frequencies(self) -> None:
        from tools.custom import build_custom_scheme as builder
        from tiger_aux import AuxiliaryEntry

        weights = {("哪", "na"): "998544", ("哪", "ne"): "1",
                   ("哪", "nai"): "0", ("哪", "nei"): "0",
                   ("你", "ni"): "3000000", ("那", "na"): "2000000",
                   ("呐", "na"): "48065", ("耐", "nai"): "90000",
                   ("讷", "ne"): "200", ("内", "nei"): "100000"}
        rows = ["哪\tn\n", "你\tn\n", "你\tni\n", "你\tnijy\n",
                "那\tna\n", "那\tnadt\n", "呐\tnad\n", "呐\tnadj\n",
                "耐\tnl\n", "耐\tnlek\n", "讷\tne\n", "讷\tnesn\n",
                "内\tnz\n", "内\tnzmj\n"]
        fullcodes = [f"哪\t{reading}{aux}\n" for reading in ("na", "ne", "nl", "nz")
                     for aux in ("da", "dt", "du")]
        rows += fullcodes
        aux = {"哪": AuxiliaryEntry("da", "dt", "du")}
        with patch.object(builder, "freq_simp_table", weights):
            rank = builder._reading_freq_rank(lambda code: code)
            self.assertLess(rank[("呐", "na")], rank[("哪", "ne")])
            for secondary in ([], ["哪\tnl\n", "哪\tne\n", "哪\tnz\n"]):
                with self.subTest(explicit_secondary=bool(secondary)):
                    out = builder._unique_shortcode_pass(rows + secondary, rank,
                                                         self.stats_cls(), aux)
                    self.assertIn("哪\tnad\n", out)
                    self.assertNotIn("哪\tn\n", out)
                    self.assertNotIn("哪\tna\n", out)
                    self.assertNotIn("哪\tnl\n", out)
                    self.assertNotIn("呐\tnad\n", out)
                    self.assertTrue(set(fullcodes).issubset(out))
                    for reading in ("nl", "ne", "nz"):
                        self.assertEqual(f"哪\t{reading}d\n" in out, bool(secondary))
                    short = [r.split("\t")[1].strip() for r in out
                             if len(r.split("\t")[1].strip()) <= 3]
                    self.assertEqual(len(short), len(set(short)))

    def test_normal_aux_path_and_explicit_compat_shortcode(self) -> None:
        from tiger_aux import AuxiliaryEntry

        rows = ["甲\tba\n", "乙\tba\n", "乙\tbazz\n", "乙\tbaaa\n",
                "乙\tbaz\n", "丙\tbaz\n", "丙\tbazx\n"]
        rank = {("甲", "ba"): 0, ("乙", "ba"): 1, ("丙", "ba"): 2}
        out = self.unique_pass(rows, rank, self.stats_cls(), {"乙": AuxiliaryEntry("aa", "zz")})
        self.assertIn("乙\tbaa\n", out)
        self.assertIn("乙\tbaz\n", out)  # explicit compatibility shortcode still competes
        self.assertNotIn("丙\tbaz\n", out)
        self.assertTrue(set(rows[2:4]).issubset(out))

    def test_source_order_cannot_choose_secondary_for_one_key(self) -> None:
        from tools.custom import build_custom_scheme as builder
        from tiger_aux import AuxiliaryEntry

        rows = ["哪\tn\n", "你\tn\n", "你\tnijy\n", "哪\tnlda\n", "哪\tnada\n"]
        source_rank = {("哪", "nl"): 0, ("你", "n"): 1, ("哪", "na"): 2}
        frequency_rank = {("你", "ni"): 0, ("哪", "na"): 1, ("哪", "nl"): 2}
        out = builder._unique_shortcode_pass(rows, source_rank, self.stats_cls(),
                                            {"哪": AuxiliaryEntry("da")},
                                            frequency_rank=frequency_rank)
        self.assertIn("哪\tna\n", out)
        self.assertNotIn("哪\tnl\n", out)

    def test_one_key_prefers_fullcode_evidence_over_partial_primary(self) -> None:
        rank = {("你", "ni"): 0, ("哪", "na"): 1, ("哪", "ne"): 2}
        for partial in ("na", "nad"):
            with self.subTest(partial=partial):
                rows = ["你\tn\n", "你\tnijy\n", "哪\tn\n",
                        f"哪\t{partial}\n", "哪\tneda\n"]
                out, _stats = self.run_unique(rows, rank)
                self.assertIn("哪\tne\n", out)
                self.assertNotIn("哪\tn\n", out)
                self.assertIn(f"哪\t{partial}\n", out)
                self.assertIn("哪\tneda\n", out)

    def test_one_key_without_fullcode_keeps_deterministic_partial_fallback(self) -> None:
        rows = ["你\tn\n", "你\tnijy\n", "哪\tn\n", "哪\tnad\n", "哪\tned\n"]
        rank = {("你", "ni"): 0, ("哪", "na"): 1, ("哪", "ne"): 2}
        out, _stats = self.run_unique(rows, rank)
        reversed_out, _stats = self.run_unique(list(reversed(rows)), rank)
        self.assertEqual(set(out), set(reversed_out))
        self.assertIn("哪\tna\n", out)
        self.assertNotIn("哪\tne\n", out)
        self.assertIn("哪\tnad\n", out)
        self.assertIn("哪\tned\n", out)

    def test_source_order_allocation_is_per_reading(self) -> None:
        from tools.custom import build_custom_scheme as builder
        from tools.custom.custom_aux import load_coded_chars

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "table.tsv"
            path.write_text("甲\taaxy\n乙\taaxy\n乙\tbbxy\n甲\tbbxy\n", encoding="utf-8")
            coded = load_coded_chars(path)
        source = ("---\nname: mohu_zrm\n...\n甲\taa\n乙\taa\n甲\tbb\n乙\tbb\n"
                  "甲\taaxy\n乙\taaxy\n甲\tbbxy\n乙\tbbxy\n")
        out = builder.convert_master(source, "ordered", lambda code: code,
                                     coded.aux, coded.aux, self.stats_cls(),
                                     char_order=coded.order)
        self.assertIn("甲\taa\n", out)
        self.assertNotIn("乙\taa\n", out)
        self.assertIn("乙\tbb\n", out)
        self.assertNotIn("甲\tbb\n", out)

    def test_reading_frequencies_follow_target_layout(self) -> None:
        from tools.custom import build_custom_scheme as builder

        with patch.object(builder, "freq_simp_table", {
                ("长", "chang"): "100", ("长", "zhang"): "1", ("张", "zhang"): "10"}):
            rank = builder._reading_freq_rank(builder.make_converter(builder.resolve_layout("flypy")))
        self.assertLess(rank[("长", "ih")], rank[("张", "vh")])
        self.assertLess(rank[("张", "vh")], rank[("长", "vh")])

    def test_declared_fly_alias_uses_reading_frequency_and_path(self) -> None:
        from tools.custom import build_custom_scheme as builder
        from tiger_aux import AuxiliaryEntry

        # Both alias rows lack their own four-key row. Canonical fullcodes
        # supply their reachable continuation without changing those rows.
        source = ("---\nname: mohu_zrm\n...\n微\twk\n位\twk\n"
                  "微\twzwv\n位\twzjl\n微\tw\n位\tw\n")
        aux = {"微": AuxiliaryEntry("wv"), "位": AuxiliaryEntry("jl")}
        weights = {("微", "wei"): "10", ("位", "wei"): "100"}
        with patch.object(builder, "freq_simp_table", weights):
            out = builder.convert_master(source, "alias", lambda code: code, aux, aux,
                                         self.stats_cls(), keep_flykeys=True)
        self.assertIn("位\twk\n", out)
        self.assertNotIn("微\twk\n", out)
        self.assertIn("微\twkw\n", out)
        self.assertIn("微\twz\n", out)  # ambiguous w chooses canonical wz
        self.assertIn("微\twzwv\n", out)
        self.assertIn("位\twzjl\n", out)

    def test_equal_rank_and_missing_normal_are_deterministic(self) -> None:
        rows = ["乙\tba\n", "甲\tba\n", "甲\tbazz\n", "甲\tbaaa\n",
                "乙\tbadd\n"]
        rank = {("甲", "ba"): 0, ("乙", "ba"): 0}
        out, _stats = self.run_unique(rows, rank)
        reversed_out, _stats = self.run_unique(list(reversed(rows)), rank)
        self.assertEqual(set(out), set(reversed_out))
        # Equal ranks tie by identity, and the loser takes a stable same-reading
        # longest-code fallback when no normal aux is supplied.
        self.assertIn("乙\tba\n", out)
        self.assertIn("甲\tbaa\n", out)

    def test_full_master_is_deterministic_across_hash_seeds(self) -> None:
        script = '''
import hashlib
from collections import Counter
from tools.custom import build_custom_scheme as b
from custom_aux import _is_single_han
aux = b.load_auxiliary_tsv(b.TIGER_AUX_TSV)
source = (b.ROOT / "mohu_zrm.dict.yaml").read_text()
result = b.convert_master(source, "seedtest", b.make_converter(b.resolve_layout("zrm")),
                          aux, aux, b.BuildStats(), keep_flykeys=True)
def full(text):
    return Counter(line for line in text.splitlines()
                   if "\\t" in line and len(line.split("\\t")[0]) == 1
                   and len(line.split("\\t")[1]) >= 4 and not line.startswith("#"))
assert full(source) == full(result)
rows = [line.split("\\t")[:2] for line in result.splitlines() if "\\t" in line]
short = [code for ch, code in rows if len(ch) == 1 and _is_single_han(ch)
         and code.isalpha() and len(code) <= 3]
assert len(short) == len(set(short))
assert ["哪", "nada"] in rows and ["哪", "nl"] not in rows
print(hashlib.sha256(result.encode()).hexdigest())
'''
        outputs = []
        for seed in (1, 2, 42):
            result = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                                    env={**os.environ, "PYTHONHASHSEED": str(seed)},
                                    text=True, capture_output=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr)
            outputs.append(result.stdout)
        self.assertEqual(len(set(outputs)), 1, outputs)

    def test_keep_shortcodes_competes_once_in_unique_mode(self) -> None:
        from tools.custom import build_custom_scheme as builder
        from tools.custom.custom_aux import CodedCharTable
        from tiger_aux import AuxiliaryEntry

        coded = CodedCharTable(shortcodes=[("吧", "b"), ("不", "b"), ("吧", "b")])
        aux = {"吧": AuxiliaryEntry("dm"), "不": AuxiliaryEntry("cb")}
        source = "---\nname: mohu_zrm\n...\n不\tb\n不\tbucb\n吧\tbadm\n"
        stats = self.stats_cls()
        out = builder.convert_master(source, "keepunique", lambda code: code,
                                     aux, aux, stats,
                                     explicit_shortcodes=coded.shortcodes)
        self.assertEqual(out.count("不\tb\n"), 1)
        self.assertNotIn("吧\tb\n", out)
        self.assertIn("吧\tba\n", out)
        phrases = builder.build_custom_phrases("keepunique", SimpleNamespace(
            keep_shortcodes=True), coded, stats)
        self.assertNotIn("吧\tb\t", phrases)

    def test_keep_shortcodes_rank_before_same_code_words(self) -> None:
        # Issue 27：夜桜「己 ji」追加到文件尾，落在主表二码词「经常 ji」之后，
        # sort: original 下首选成了经常。导入简码必须落在单字区末尾。
        from tools.custom import build_custom_scheme as builder
        from tools.custom.custom_aux import CodedCharTable
        from tiger_aux import AuxiliaryEntry

        coded = CodedCharTable(shortcodes=[("己", "ji")])
        aux = {"己": AuxiliaryEntry("v"), "极": AuxiliaryEntry("s"),
               "及": AuxiliaryEntry("i")}
        source = ("---\nname: mohu_zrm\n...\n"
                  "极\tji\n及\tji\n极\tjixs\n及\tjixi\n己\tjiv\n"
                  "#----------词库----------#\n"
                  "经常\tji\n经常\tjyih\n")
        stats = self.stats_cls()
        out = builder.convert_master(source, "order", lambda code: code,
                                     aux, aux, stats,
                                     char_order={("己", "ji"): 0},
                                     explicit_shortcodes=coded.shortcodes)
        self.assertNotIn("极\tji\n", out)
        self.assertNotIn("及\tji\n", out)
        self.assertLess(out.index("己\tji\n"), out.index("#----------词库"))
        self.assertLess(out.index("己\tji\n"), out.index("经常\tji\n"))

    def test_generated_rows_land_before_same_code_words(self) -> None:
        # 竞争生成的简码行同样不能追加到词行之后。
        rows = ["不\tb\n", "吧\tb\n", "吧\tbadm\n", "把\tba\n", "拔\tba\n",
                "拔\tbau\n", "叭\tbad\n", "叭\tbadh\n", "白的\tbad\n"]
        rank = {("不", "b"): 0, ("把", "ba"): 1, ("吧", "ba"): 2,
                ("拔", "ba"): 3, ("叭", "ba"): 4}
        out, _stats = self.run_unique(rows, rank)
        self.assertIn("吧\tbad\n", out)
        self.assertLess(out.index("吧\tbad\n"), out.index("白的\tbad\n"))
        self.assertEqual(out[-1], "白的\tbad\n")

    def test_table_only_chars_materialized_into_master(self) -> None:
        # 码表独有字（主码表未收，桜 判例）：按本方案双拼+本表辅码物化
        # 四码全码行进单字区；主表已有的字不重复物化。
        from tools.custom import build_custom_scheme as builder
        from tools.custom.custom_aux import CodedCharTable
        from tiger_aux import AuxiliaryEntry

        coded = CodedCharTable()
        coded.aux = {"樱": AuxiliaryEntry("wu"), "桜": AuxiliaryEntry("wu")}
        source = ("---\nname: mohu_zrm\n...\n"
                  "樱\tyyv\n"
                  "#----------词库----------#\n"
                  "经常\tji\n")
        stats = self.stats_cls()
        out = builder.convert_master(
            source, "materialize", lambda code: code, {"樱": AuxiliaryEntry("vv")},
            {"樱": AuxiliaryEntry("wu"), "桜": AuxiliaryEntry("wu")}, stats,
            character_syllables={"樱": {"yy"}, "桜": {"yy"}},
            materialize_aux=coded.aux)
        self.assertIn("桜\tyywu\n", out)
        self.assertNotIn("樱\tyywu\n", out)   # 主表已有的字不重复物化
        self.assertEqual(stats.table_only_chars, 1)
        self.assertLess(out.index("桜\tyywu\n"), out.index("#----------词库"))


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
