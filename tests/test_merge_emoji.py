import tempfile
import unittest
from pathlib import Path

from tools.merge_emoji import load_entries, merge_entries, render_entries

ROOT = Path(__file__).resolve().parents[1]
MOHU_SOURCE = ROOT / "tools/data/mohu_emoji_base.txt"
TIGER_SOURCE = ROOT / "tools/data/tiger_emoji.txt"
GENERATED = ROOT / "opencc/mohu_emoji.txt"


class MergeEmojiTest(unittest.TestCase):
    def test_merge_preserves_source_order_and_deduplicates_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mohu = root / "mohu.txt"
            tiger = root / "tiger.txt"
            mohu.write_text("甲\t甲 A B\n乙\t乙 C\n", encoding="utf-8")
            tiger.write_text("甲\t甲 B D A\n丙\t丙 E E\n", encoding="utf-8")

            merged = merge_entries(load_entries(mohu), load_entries(tiger))

        self.assertEqual(["甲", "乙", "丙"], list(merged))
        self.assertEqual(["甲", "A", "B", "D"], merged["甲"])
        self.assertEqual(["丙", "E"], merged["丙"])

    def test_real_union_has_expected_coverage(self) -> None:
        merged = merge_entries(load_entries(MOHU_SOURCE), load_entries(TIGER_SOURCE))
        self.assertEqual(13_445, len(merged))
        self.assertIn("頓號", merged)
        self.assertIn("顿号", merged)
        self.assertEqual(len(merged["OK"]), len(dict.fromkeys(merged["OK"])))

    def test_checked_in_dictionary_is_generated_output(self) -> None:
        merged = merge_entries(load_entries(MOHU_SOURCE), load_entries(TIGER_SOURCE))
        self.assertEqual(GENERATED.read_bytes(), render_entries(merged).encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
