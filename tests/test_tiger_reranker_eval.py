from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.evaluate_tiger_reranker import (
    DEFAULT_BOOTSTRAP_SAMPLES,
    DEFAULT_SEED,
    RowValidationError,
    build_manifest,
    evaluate_rows,
    load_case_rows,
    paired_bootstrap,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "tiger_reranker_cases.jsonl"
MANIFEST = ROOT / "tiger_sentence_native" / "eval" / "corpus-manifest.json"
SCRIPT = ROOT / "tools" / "evaluate_tiger_reranker.py"


def candidate(
    text: str,
    base_score: float,
    confidence: float = 0.5,
    segmented: str = "",
) -> dict[str, object]:
    return {
        "text": text,
        "base_score": base_score,
        "confidence": confidence,
        "segmented": segmented,
    }


def dump_row(
    *,
    case_id: str,
    source: str,
    mode: str,
    raw: str,
    expected: str,
    candidates: list[dict[str, object]],
    reranked: list[str],
) -> dict[str, object]:
    return {
        "id": case_id,
        "source": source,
        "mode": mode,
        "raw": raw,
        "expected": expected,
        "candidates": candidates,
        "reranked": reranked,
    }


class TigerRerankerFixtureTest(unittest.TestCase):
    def test_checked_in_fixture_has_required_hesitation_case(self) -> None:
        rows = load_case_rows(FIXTURE)

        self.assertTrue(
            any(
                row["raw"] == "najqmzufmekeybyudele" and row["expected"] == "那就没什么可犹豫的了"
                for row in rows
            )
        )

    def test_checked_in_manifest_truthfully_describes_fixture(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        source = manifest["sources"][0]

        self.assertEqual(source["path"], "tests/fixtures/tiger_reranker_cases.jsonl")
        self.assertEqual(source["line_count"], len(load_case_rows(FIXTURE)))
        self.assertEqual(source["sha256"], hashlib.sha256(FIXTURE.read_bytes()).hexdigest())
        self.assertTrue(source["source"])
        self.assertTrue(source["license_note"])
        self.assertRegex(manifest["generated_date"], r"^\d{4}-\d{2}-\d{2}$")


class TigerRerankerValidationTest(unittest.TestCase):
    def test_loader_reports_file_line_and_missing_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.jsonl"
            path.write_text(
                '{"id":"bad","source":"fixture","mode":"plain","raw":"abc"}\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                RowValidationError,
                rf"{path}:1: missing required field 'expected'",
            ):
                load_case_rows(path)

    def test_loader_reports_invalid_json_with_file_and_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.jsonl"
            path.write_text("{not-json}\n", encoding="utf-8")

            with self.assertRaisesRegex(RowValidationError, rf"{path}:1: invalid JSON"):
                load_case_rows(path)

    def test_dump_requires_explicit_reranked_order(self) -> None:
        row = {
            "id": "missing-rerank",
            "source": "fixture",
            "mode": "plain",
            "raw": "abc",
            "expected": "甲",
            "candidates": [candidate("甲", 10.0)],
        }
        with self.assertRaisesRegex(RowValidationError, "missing required field 'reranked'"):
            evaluate_rows([row])

    def test_dump_rejects_non_text_reranked_items(self) -> None:
        row = dump_row(
            case_id="object-rerank",
            source="fixture",
            mode="plain",
            raw="abc",
            expected="甲",
            candidates=[candidate("甲", 10.0)],
            reranked=[{"text": "甲"}],  # type: ignore[list-item]
        )
        with self.assertRaisesRegex(RowValidationError, r"reranked\[0\].*string"):
            evaluate_rows([row])

    def test_dump_rejects_reranked_values_outside_candidate_set(self) -> None:
        row = dump_row(
            case_id="unknown-rerank",
            source="fixture",
            mode="plain",
            raw="abc",
            expected="甲",
            candidates=[candidate("甲", 10.0), candidate("乙", 9.0)],
            reranked=["甲", "丙"],
        )
        with self.assertRaises(RowValidationError) as context:
            evaluate_rows([row])
        self.assertIn("full permutation", str(context.exception))
        self.assertIn("unknown_reranked_indices", str(context.exception))
        self.assertNotIn("丙", str(context.exception))

    def test_dump_rejects_integer_score_that_overflows_float(self) -> None:
        row = dump_row(
            case_id="huge-score",
            source="fixture",
            mode="plain",
            raw="abc",
            expected="甲",
            candidates=[candidate("甲", 10**400)],
            reranked=["甲"],
        )
        with self.assertRaisesRegex(RowValidationError, "finite number"):
            evaluate_rows([row])

    def test_dump_accepts_any_finite_confidence(self) -> None:
        for confidence in (-12.5, 2.0):
            with self.subTest(confidence=confidence):
                row = dump_row(
                    case_id=f"confidence-{confidence}",
                    source="fixture",
                    mode="plain",
                    raw="abc",
                    expected="甲",
                    candidates=[candidate("甲", 10.0, confidence=confidence)],
                    reranked=["甲"],
                )

                self.assertEqual(evaluate_rows([row])["baseline_correct"], 1)

    def test_manifest_rejects_path_outside_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "root"
            root.mkdir()
            outside = parent / "outside.jsonl"
            outside.write_text("fixture\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "outside manifest root"):
                build_manifest([outside], root=root)


class TigerRerankerMetricsTest(unittest.TestCase):
    @staticmethod
    def rows() -> list[dict[str, object]]:
        return [
            dump_row(
                case_id="correction",
                source="alpha",
                mode="plain",
                raw="abc",
                expected="甲",
                candidates=[candidate("乙", 20.0), candidate("甲", 10.0)],
                reranked=["甲", "乙"],
            ),
            dump_row(
                case_id="harm",
                source="alpha",
                mode="full_aux",
                raw="abcdef",
                expected="丙",
                candidates=[candidate("丙", 20.0), candidate("丁", 10.0)],
                reranked=["丁", "丙"],
            ),
            dump_row(
                case_id="stable",
                source="beta",
                mode="plain",
                raw="abcdef",
                expected="戊",
                candidates=[candidate("戊", 20.0), candidate("己", 10.0)],
                reranked=["戊", "己"],
            ),
            dump_row(
                case_id="oracle-miss",
                source="beta",
                mode="plain",
                raw="abcdefghij",
                expected="庚",
                candidates=[candidate("辛", 20.0), candidate("壬", 10.0)],
                reranked=["壬", "辛"],
            ),
        ]

    def test_evaluate_rows_reports_totals_and_slices(self) -> None:
        result = evaluate_rows(self.rows())

        self.assertEqual(result["total"], 4)
        self.assertEqual(result["baseline_correct"], 2)
        self.assertEqual(result["reranked_correct"], 2)
        self.assertEqual(result["oracle_correct"], 3)
        self.assertEqual(result["corrections"], 1)
        self.assertEqual(result["harms"], 1)
        self.assertEqual(result["totals"]["accuracy_delta"], 0.0)

        self.assertEqual(result["slices"]["source"]["alpha"]["total"], 2)
        self.assertEqual(result["slices"]["source"]["beta"]["oracle_correct"], 1)
        self.assertEqual(result["slices"]["mode"]["plain"]["reranked_correct"], 2)
        self.assertEqual(result["slices"]["mode"]["full_aux"]["harms"], 1)
        self.assertEqual(result["slices"]["raw_length_band"]["0-5"]["corrections"], 1)
        self.assertEqual(result["slices"]["raw_length_band"]["6-9"]["total"], 2)
        self.assertEqual(result["slices"]["raw_length_band"]["10-15"]["total"], 1)
        self.assertNotIn("raw_length", result["slices"])

    def test_baseline_uses_user_visible_candidate_order(self) -> None:
        row = dump_row(
            case_id="score-order",
            source="fixture",
            mode="plain",
            raw="abc",
            expected="甲",
            candidates=[candidate("甲", 1.0), candidate("乙", 3.0), candidate("丙", 3.0)],
            reranked=["乙", "甲", "丙"],
        )

        result = evaluate_rows([row])

        self.assertEqual(result["baseline_correct"], 1)

    def test_paired_bootstrap_is_reproducible(self) -> None:
        first = paired_bootstrap(self.rows(), samples=500, seed=DEFAULT_SEED)
        second = paired_bootstrap(self.rows(), samples=500, seed=DEFAULT_SEED)

        self.assertEqual(DEFAULT_SEED, 20260826)
        self.assertEqual(DEFAULT_BOOTSTRAP_SAMPLES, 10_000)
        self.assertEqual(first, second)
        self.assertEqual(first["observed_delta"], 0.0)
        self.assertEqual(first["samples"], 500)
        self.assertEqual(first["seed"], DEFAULT_SEED)
        self.assertEqual(len(first["confidence_interval_95"]), 2)

    def test_paired_bootstrap_rejects_empty_rows(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one row"):
            paired_bootstrap([], samples=10)


class TigerRerankerCliTest(unittest.TestCase):
    def test_manifest_subcommand_writes_machine_readable_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "manifest.json"
            result = subprocess.run(
                [
                    "uv",
                    "run",
                    "python",
                    str(SCRIPT),
                    "manifest",
                    str(FIXTURE),
                    "--source-label",
                    "checked-in-smoke",
                    "--license-note",
                    "repository GPL-3.0 test fixture",
                    "--generated-date",
                    "2026-08-26",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            expected = build_manifest(
                [FIXTURE],
                source_labels=["checked-in-smoke"],
                license_notes=["repository GPL-3.0 test fixture"],
                generated_date="2026-08-26",
                root=ROOT,
            )
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), expected)

    def test_evaluate_subcommand_reads_dump_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dump = Path(directory) / "dump.jsonl"
            output = Path(directory) / "metrics.json"
            dump.write_text(
                "".join(
                    json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                    for row in TigerRerankerMetricsTest.rows()
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "uv",
                    "run",
                    "python",
                    str(SCRIPT),
                    "evaluate",
                    str(dump),
                    "--bootstrap-samples",
                    "50",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["total"], 4)
            self.assertEqual(payload["bootstrap"]["samples"], 50)

    def test_evaluate_cli_reports_malformed_dump_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dump = Path(directory) / "bad.jsonl"
            dump.write_text("{}\n", encoding="utf-8")
            result = subprocess.run(
                ["uv", "run", "python", str(SCRIPT), "evaluate", str(dump)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn(f"{dump}:1: missing required field 'id'", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_evaluate_cli_reports_overflowing_score_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dump = Path(directory) / "overflow.jsonl"
            row = dump_row(
                case_id="huge-score",
                source="fixture",
                mode="plain",
                raw="abc",
                expected="甲",
                candidates=[candidate("甲", 10**400)],
                reranked=["甲"],
            )
            dump.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            result = subprocess.run(
                ["uv", "run", "python", str(SCRIPT), "evaluate", str(dump)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("finite number", result.stderr)
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
