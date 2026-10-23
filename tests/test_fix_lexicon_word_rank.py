import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "fix_lexicon_word_rank.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("fix_lexicon_word_rank", TOOL)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class CompositeRankTest(unittest.TestCase):
    """四码组复合规则：二字全码词按词频居首，声母简词保持成语优先继承序。"""

    def setUp(self) -> None:
        self.tool = load_tool()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.source = root / "mohu_tiger.lexicon.txt"
        self.freq = root / "mohu_zrm.lexicon.txt"
        for patch in (
            mock.patch.object(self.tool, "LEXICON", self.source),
            mock.patch.object(self.tool, "FREQ_SOURCE", self.freq),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def run_tool(self) -> list[str]:
        with contextlib.redirect_stdout(io.StringIO()):
            self.tool.main([])
        return self.source.read_text(encoding="utf-8").splitlines()

    def test_two_char_promoted_abbreviation_order_kept(self) -> None:
        # yywu：鹦鹉（二字全码词 1490）升顶；云雨巫山/医药卫生（简词）
        # 保持继承相对序（成语在前）；单字樱的行、rank 原样。
        self.source.write_text(
            "yywu\t云雨巫山\t1\t20001\n"
            "yywu\t樱\t2\t300\t68000\n"
            "yywu\t鹦鹉\t3\t20001\n"
            "yywu\t医药卫生\t4\t20001\n",
            encoding="utf-8")
        self.freq.write_text(
            "yywu\t鹦鹉\t1\t1490\n"
            "yywu\t医药卫生\t5\t158\n",
            encoding="utf-8")
        rows = self.run_tool()
        self.assertEqual(rows, [
            "yywu\t鹦鹉\t1\t20001",
            "yywu\t樱\t2\t300\t68000",
            "yywu\t云雨巫山\t3\t20001",
            "yywu\t医药卫生\t4\t20001",
        ])

    def test_two_char_tier_sorted_by_frequency(self) -> None:
        # 词头内部按语料词频降序（aive 判例：挨着 3027 > 爱着 909）。
        self.source.write_text(
            "aive\t爱着\t1\t20001\n"
            "aive\t暗潮汹涌\t2\t20001\n"
            "aive\t挨着\t3\t20001\n",
            encoding="utf-8")
        self.freq.write_text(
            "aive\t爱着\t1\t909\n"
            "aive\t挨着\t2\t3027\n",
            encoding="utf-8")
        rows = self.run_tool()
        # 词头（挨着 3027 > 爱着 909）整体在简词（暗潮汹涌）之前。
        self.assertEqual(
            [row.split("\t")[1] for row in rows], ["挨着", "爱着", "暗潮汹涌"])

    def test_pure_abbreviation_group_untouched(self) -> None:
        # 无二字真词的组（adly：安堵乐业/澳大利亚）整组保持继承序。
        original = (
            "adly\t安堵乐业\t1\t20001\n"
            "adly\t澳大利亚\t2\t20001\n"
        )
        self.source.write_text(original, encoding="utf-8")
        self.freq.write_text("adly\t澳大利亚\t1\t6502\n", encoding="utf-8")
        self.assertEqual(self.run_tool(), original.splitlines())

    def test_rank99_and_freq_column_untouched(self) -> None:
        # 注入层（rank 99）与哨兵频列不参与竞争、不改写。
        original = (
            "aadb\t嗷嗷待哺\t1\t20001\n"
            "aadb\t阿斗\t2\t20001\n"
            "aadb\t澳暗德比\t99\t20001\n"
        )
        self.source.write_text(original, encoding="utf-8")
        self.freq.write_text("aadb\t阿斗\t1\t396\n", encoding="utf-8")
        rows = self.run_tool()
        self.assertEqual(rows, [
            "aadb\t阿斗\t1\t20001",
            "aadb\t嗷嗷待哺\t2\t20001",
            "aadb\t澳暗德比\t99\t20001",
        ])

    def test_idempotent(self) -> None:
        self.source.write_text(
            "yywu\t云雨巫山\t1\t20001\n"
            "yywu\t鹦鹉\t3\t20001\n"
            "yywu\t医药卫生\t4\t20001\n",
            encoding="utf-8")
        self.freq.write_text(
            "yywu\t鹦鹉\t1\t1490\n"
            "yywu\t医药卫生\t2\t158\n",
            encoding="utf-8")
        first = self.run_tool()
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.tool.main([])
        self.assertIn("reordered: 0", stdout.getvalue())
        self.assertEqual(self.source.read_text(encoding="utf-8").splitlines(), first)


if __name__ == "__main__":
    unittest.main()
