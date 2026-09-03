from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from research.lm_sentence_compare.build_cross_candidate_cases import (
    Context,
    FrequencyEntry,
    Reading,
    build_homophone_groups,
    encode_case,
    four_modes,
    load_contexts,
    load_frequency_entries,
    select_targets,
    strict_word_pronunciations,
)


class CrossCandidateCaseBuilderTest(unittest.TestCase):
    def test_frequency_parser_preserves_rank_and_rejects_increases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frequency.txt"
            path.write_text("自己 100\n字迹 90\n", encoding="utf-8")
            self.assertEqual(
                load_frequency_entries(path),
                [FrequencyEntry("自己", 1, 100), FrequencyEntry("字迹", 2, 90)],
            )
            path.write_text("自己 90\n字迹 100\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "order increases"):
                load_frequency_entries(path)

    def test_strict_pronunciations_exclude_ambiguous_words(self) -> None:
        readings = {
            "自己": [Reading(("zi", "ji"), ("zi", "ji"), ("o", "v"), 10, 0)],
            "自给": [
                Reading(("zi", "ji"), ("zi", "ji"), ("o", "x"), 10, 1),
                Reading(("zi", "gei"), ("zi", "gz"), ("o", "x"), 9, 2),
            ],
        }
        self.assertEqual(
            strict_word_pronunciations(readings, readings),
            {"自己": ("zi", "ji")},
        )

    def test_groups_require_complete_two_syllable_homophones(self) -> None:
        entries = [
            FrequencyEntry("自己", 1, 100),
            FrequencyEntry("字迹", 2, 90),
            FrequencyEntry("自给", 3, 80),
        ]
        groups = build_homophone_groups(
            entries,
            {
                "自己": ("zi", "ji"),
                "字迹": ("zi", "ji"),
                "自给": ("zi", "gei"),
            },
        )
        self.assertEqual([entry.word for entry in groups[("zi", "ji")]], ["自己", "字迹"])
        self.assertNotIn(("zi", "gei"), groups)

    def test_contexts_keep_whole_words_and_cap_selection_by_frequency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            rows = [
                {"id": "s0", "source": "a", "text": "做自己", "words": "做|自己"},
                {"id": "s1", "source": "b", "text": "看自己", "words": "看|自己"},
                {"id": "s2", "source": "c", "text": "自己看", "words": "自己|看"},
                {"id": "s3", "source": "d", "text": "查字迹", "words": "查|字迹"},
            ]
            path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )
            contexts = load_contexts(path, {"自己", "字迹"})
            self.assertEqual([item.prefix for item in contexts["自己"]], ["做", "看"])
            entries = [FrequencyEntry("自己", 1, 100), FrequencyEntry("字迹", 2, 90)]
            pronunciations = {"自己": ("zi", "ji"), "字迹": ("zi", "ji")}
            groups = build_homophone_groups(entries, pronunciations)
            selected = select_targets(
                entries,
                pronunciations,
                groups,
                contexts,
                target_limit=2,
            )
            self.assertEqual([entry.word for entry in selected], ["自己", "字迹"])

    def test_scheme_encoding_uses_own_auxiliary_codes(self) -> None:
        word = Reading(("gong", "si"), ("gs", "si"), ("sb", "gk"), 10, 0)
        char = {
            "前": [Reading(("qian",), ("qm",), ("x",), 10, 0)],
        }
        encoded = encode_case(
            "公司",
            ("gong", "si"),
            "前",
            {"公司": [word]},
            char,
        )
        self.assertTrue(encoded.available)
        self.assertEqual(encoded.prefix_code, "qm")
        self.assertEqual(
            encoded.modes,
            {"pure": "gssi", "head": "gsssi", "tail": "gssig", "both": "gsssig"},
        )
        self.assertEqual(four_modes(word)["both"], "gsssig")

    def test_missing_word_row_falls_back_to_matching_character_readings(self) -> None:
        chars = {
            "公": [Reading(("gong",), ("gs",), ("hk",), 10, 0)],
            "司": [Reading(("si",), ("si",), ("af",), 10, 1)],
            "前": [Reading(("qian",), ("qm",), ("x",), 10, 2)],
        }
        encoded = encode_case("公司", ("gong", "si"), "前", {}, chars)
        self.assertTrue(encoded.available)
        self.assertEqual(encoded.target_source, "character_fallback")
        self.assertEqual(encoded.modes["both"], "gshsia")


if __name__ == "__main__":
    unittest.main()
