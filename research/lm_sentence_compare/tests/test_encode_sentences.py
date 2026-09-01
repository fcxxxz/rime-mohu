from __future__ import annotations

import unittest

from research.lm_sentence_compare.encode_sentences import (
    MODES,
    encode_text,
    segment_max_match,
)


class SegmentationTest(unittest.TestCase):
    def test_max_match_is_longest_then_lexicographic(self) -> None:
        vocabulary = {"人工智能", "人工", "智能", "推动", "产业", "升级"}
        self.assertEqual(
            segment_max_match("人工智能推动产业升级", vocabulary),
            ["人工智能", "推动", "产业", "升级"],
        )

    def test_unknown_character_falls_back_to_one_character(self) -> None:
        self.assertEqual(segment_max_match("甲乙", {"甲"}), ["甲", "乙"])


class EncodingTest(unittest.TestCase):
    def _tables(self) -> tuple[dict[str, list[dict[str, object]]], set[str], set[str]]:
        readings = {
            "你": [{"yy": "ni", "aux": "jr", "weight": 10}],
            "好": [{"yy": "hk", "aux": "bh", "weight": 9}],
            "吗": [{"yy": "ma", "aux": "jn", "weight": 8}],
            "人": [{"yy": "rf", "aux": "jr", "weight": 7}],
            "工": [{"yy": "gs", "aux": "ug", "weight": 6}],
            "智": [{"yy": "vi", "aux": "xh", "weight": 5}],
            "能": [{"yy": "ng", "aux": "kl", "weight": 4}],
        }
        return readings, set(readings), {"你好", "吗", "人工", "智能"}

    def test_all_modes_have_expected_aux_density_and_are_deterministic(self) -> None:
        readings, charset, vocabulary = self._tables()
        first = encode_text("你好", readings, charset, vocabulary)
        second = encode_text("你好", readings, charset, vocabulary)
        self.assertEqual(first, second)
        self.assertEqual(set(first["modes"]), set(MODES))
        self.assertEqual(first["modes"]["pure"], "nihk")
        self.assertEqual(first["modes"]["word1"], "nijhk")
        self.assertEqual(first["modes"]["char1"], "nijhkb")
        self.assertEqual(first["aux_counts"], {"pure": 0, "sparse": 1, "word1": 1, "char1": 2})

    def test_unreadable_text_returns_explicit_failure(self) -> None:
        readings, charset, vocabulary = self._tables()
        result = encode_text("你好啊", readings, charset, vocabulary)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
