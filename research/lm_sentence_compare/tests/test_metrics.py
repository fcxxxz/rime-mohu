from __future__ import annotations

import binascii
import tempfile
import unittest
from pathlib import Path

from research.lm_sentence_compare.metrics import (
    evaluate_cases,
    parse_rime_dump,
    parse_tiger_dump,
    paired_bootstrap_delta,
)


def hx(text: str) -> str:
    return binascii.hexlify(text.encode()).decode()


class ParserTest(unittest.TestCase):
    def test_parse_rime_keeps_mode_independent_raw_and_latency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rime.tsv"
            path.write_text(
                "C\tcode\t1\t" + hx("正确") + "\t\n"
                "C\tcode\t2\t" + hx("错误") + "\t\n"
                "E\tcode\t2\t1\t1234\n",
                encoding="utf-8",
            )
            parsed = parse_rime_dump(path)
            self.assertEqual(parsed["code"].candidates, ("正确", "错误"))
            self.assertEqual(parsed["code"].elapsed_us, 1234)
            self.assertEqual(parsed["code"].status, "ok")

    def test_parse_rime_rejects_duplicate_end_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rime.tsv"
            path.write_text(
                "E\tcode\t0\t0\t1\nE\tcode\t0\t0\t2\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate end record"):
                parse_rime_dump(path)

    def test_parse_tiger_rejects_malformed_candidate_cells(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tiger.tsv"
            path.write_text("code\t正确\x1f-1.0\tbad\n", encoding="utf-8")
            latency = Path(directory) / "tiger.latency.tsv"
            latency.write_text("code\t4321\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid Tiger candidate cell"):
                parse_tiger_dump(path, latency_path=latency)

    def test_parse_rime_rejects_malformed_rows_and_invalid_end_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rime.tsv"
            path.write_text("E\tcode\t0\t2\t1\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "truncation flag"):
                parse_rime_dump(path)
            path.write_text("X\tcode\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid Rime row"):
                parse_rime_dump(path)

    def test_parse_rime_rejects_duplicate_candidate_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rime.tsv"
            encoded = hx("重复")
            path.write_text(
                f"C\tcode\t1\t{encoded}\t\n"
                f"C\tcode\t2\t{encoded}\t\n"
                "E\tcode\t2\t0\t1\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate Rime candidate"):
                parse_rime_dump(path)

    def test_parse_tiger_rejects_duplicate_raws_and_latency_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tiger.tsv"
            latency = Path(directory) / "tiger.latency.tsv"
            path.write_text("code\t正确\x1f-1\ncode\t错误\x1f-2\n", encoding="utf-8")
            latency.write_text("other\t4321\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate Tiger raw"):
                parse_tiger_dump(path, latency_path=latency)

            path.write_text("code\t正确\x1f-1\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "latency raw set mismatch"):
                parse_tiger_dump(path, latency_path=latency)

            latency.write_text("code\t-1\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "non-negative"):
                parse_tiger_dump(path, latency_path=latency)


class MetricsTest(unittest.TestCase):
    def test_evaluate_reports_topk_coverage_character_and_missing(self) -> None:
        cases = [
            {"id": "a", "source": "news", "label": "tech", "text": "正确答案", "modes": {"pure": "x"}},
            {"id": "b", "source": "daily", "label": "daily", "text": "日常用语", "modes": {"pure": "y"}},
            {"id": "c", "source": "daily", "label": "daily", "text": "缺失候选", "modes": {"pure": "z"}},
        ]
        results = {
            "bgw": {"pure": {
                "x": {"candidates": ("正确答案", "别的"), "elapsed_us": 10, "status": "ok"},
                "y": {"candidates": ("日常用", "日常用语"), "elapsed_us": 20, "status": "ok"},
                "z": {"candidates": (), "elapsed_us": None, "status": "empty"},
            }}
        }
        rows, summary = evaluate_cases(cases, results)
        stat = summary["bgw"]["pure"]
        self.assertEqual(stat["n"], 3)
        self.assertEqual(stat["top1"], 1)
        self.assertEqual(stat["top5"], 2)
        self.assertEqual(stat["coverage"], 2)
        self.assertEqual(stat["missing"], 0)
        self.assertEqual(stat["empty"], 1)
        self.assertIn("mrr", stat)
        self.assertIn("top1_ci_low", stat)
        self.assertIn("top1_ci_high", stat)
        self.assertLessEqual(stat["top1_ci_low"], stat["top1_ci_high"])
        self.assertGreater(stat["char_accuracy"], 0.5)
        self.assertEqual(len(rows), 3)

    def test_paired_bootstrap_is_deterministic(self) -> None:
        baseline = [True, False, True, False]
        challenger = [True, True, False, False]
        first = paired_bootstrap_delta(baseline, challenger, samples=500, seed=7)
        second = paired_bootstrap_delta(baseline, challenger, samples=500, seed=7)
        self.assertEqual(first, second)
        self.assertEqual(first["observed_delta"], 0.0)
        self.assertLessEqual(first["ci_low"], first["ci_high"])


if __name__ == "__main__":
    unittest.main()
