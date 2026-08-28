import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEXICON = ROOT / "tiger_sentence_native" / "mohu_tiger.lexicon.txt"
CHARS_DICT = ROOT / "mohu_zrm.chars.dict.yaml"
TOOL = ROOT / "tools" / "fix_tiger_lexicon_fly.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("fix_tiger_lexicon_fly", TOOL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TigerLexiconFlyCoverageTest(unittest.TestCase):
    """原生整句码表的飞键行必须与 mohu 飞键规则（wz→wk, xq→xo, qx→qo）对齐。

    历史缺陷：码表只携带了上游自带的高频飞键，导致飞键输入下引擎看不见
    大部分同音字（如 wk 只剩「为」），整句组出「万为淘汰」这类候选。
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.tool = load_tool()
        cls.rows, _, _ = cls.tool.parse_lexicon(LEXICON)
        cls.syllables = cls.tool.load_syllables(CHARS_DICT)

    def test_no_missing_fly_rows(self) -> None:
        new_rows, _stats = self.tool.compute_missing(self.rows, self.syllables)
        self.assertEqual(
            [], new_rows,
            f"码表缺少 {len(new_rows)} 行飞键条目，"
            f"运行 `uv run tools/fix_tiger_lexicon_fly.py` 补齐，"
            f"首条缺失: {new_rows[0] if new_rows else None}")

    def test_fly_codes_mirror_normal_codes(self) -> None:
        by_code_text = {(r[0], r[1]): r for r in self.rows}
        for src, dst in self.tool.FLY.items():
            src_bare = [r for r in self.rows if r[0] == src]
            dst_bare = [r for r in self.rows if r[0] == dst]
            self.assertEqual(
                len(src_bare), len(dst_bare),
                f"裸码 {src}({len(src_bare)} 行) 与飞键 {dst}({len(dst_bare)} 行) 条目数不一致")
            for r in src_bare:
                mirror = by_code_text.get((dst, r[1]))
                self.assertIsNotNone(
                    mirror, f"飞键 {dst} 缺少「{r[1]}」（源行 {src} rank={r[2]}）")
                if mirror:
                    self.assertEqual(r[2], mirror[2],
                                     f"「{r[1]}」在 {src} 与 {dst} 下 rank 不一致")


if __name__ == "__main__":
    unittest.main()
