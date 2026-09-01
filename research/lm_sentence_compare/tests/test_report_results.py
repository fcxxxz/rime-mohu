from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from research.lm_sentence_compare.metrics import CandidateResult
from research.lm_sentence_compare import report_results as subject
from research.lm_sentence_compare.report_results import load_cases, load_results, render_report


class ReportRenderingTest(unittest.TestCase):
    def test_report_contains_domain_length_density_and_error_sections(self) -> None:
        cases = [
            {
                "id": "n1",
                "source": "news",
                "label": "news_tech",
                "text": "人工智能推动产业升级",
                "words": ["人工智能", "推动", "产业", "升级"],
                "aux_counts": {"pure": 0, "sparse": 1, "word1": 2, "char1": 8},
                "aux_ratios": {"pure": 0.0, "sparse": 0.125, "word1": 0.25, "char1": 1.0},
                "modes": {"pure": "a", "sparse": "b", "word1": "c", "char1": "d"},
            }
        ]
        result = {
            model: {
                mode: {
                    {"pure": "a", "sparse": "b", "word1": "c", "char1": "d"}[mode]: CandidateResult(("错误答案",), 10)
                }
                for mode in ("pure", "sparse", "word1", "char1")
            }
            for model in ("bgw", "wx", "tiger")
        }
        with tempfile.TemporaryDirectory() as directory:
            render_report(cases, result, directory)
            report = Path(directory, "summary.md").read_text(encoding="utf-8")
            self.assertIn("新闻领域", report)
            self.assertIn("句长切片", report)
            self.assertIn("辅助码密度", report)
            self.assertIn("错误样例", report)
            self.assertIn("news_tech", report)
            self.assertIn("共同覆盖子集", report)

    def test_report_formats_each_confidence_interval_endpoint_as_percent(self) -> None:
        cases = [
            {
                "id": "ci1",
                "source": "daily",
                "label": "daily",
                "text": "人工智能推动产业升级",
                "modes": {mode: mode for mode in ("pure", "sparse", "word1", "char1")},
            }
        ]
        result = {
            model: {
                mode: {mode: CandidateResult(("错误答案",), 10)}
                for mode in ("pure", "sparse", "word1", "char1")
            }
            for model in ("bgw", "wx", "tiger")
        }
        with tempfile.TemporaryDirectory() as directory:
            render_report(cases, result, directory)
            report = Path(directory, "summary.md").read_text(encoding="utf-8")
            self.assertIn("0.00% [0.00%, 79.35%]", report)

    def test_report_requires_all_models_and_modes(self) -> None:
        cases = [{
            "id": "complete1",
            "source": "daily",
            "label": "daily",
            "text": "人工智能推动产业升级",
            "modes": {mode: mode for mode in ("pure", "sparse", "word1", "char1")},
        }]
        incomplete = {
            "bgw": {
                mode: {mode: CandidateResult(("人工智能推动产业升级",), 10)}
                for mode in ("pure", "sparse", "word1", "char1")
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "missing models"):
                render_report(cases, incomplete, directory)

    def test_report_rejects_parser_error_status(self) -> None:
        cases = [{
            "id": "status1",
            "source": "daily",
            "label": "daily",
            "text": "人工智能推动产业升级",
            "modes": {mode: mode for mode in ("pure", "sparse", "word1", "char1")},
        }]
        results = {
            model: {
                mode: {
                    mode: CandidateResult(
                        ("人工智能推动产业升级",),
                        10,
                        "error" if model == "bgw" and mode == "pure" else "ok",
                    )
                }
                for mode in ("pure", "sparse", "word1", "char1")
            }
            for model in ("bgw", "wx", "tiger")
        }
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "invalid result status"):
                render_report(cases, results, directory)

    def test_report_allows_expected_empty_status_with_diagnostic(self) -> None:
        cases = [{
            "id": "empty1",
            "source": "daily",
            "label": "daily",
            "text": "人工智能推动产业升级",
            "modes": {mode: mode for mode in ("pure", "sparse", "word1", "char1")},
        }]
        results = {
            model: {
                mode: {
                    mode: CandidateResult(
                        () if model == "tiger" and mode == "pure" else ("人工智能推动产业升级",),
                        10,
                        "empty" if model == "tiger" and mode == "pure" else "ok",
                        error="no valid candidates" if model == "tiger" and mode == "pure" else None,
                    )
                }
                for mode in ("pure", "sparse", "word1", "char1")
            }
            for model in ("bgw", "wx", "tiger")
        }
        with tempfile.TemporaryDirectory() as directory:
            render_report(cases, results, directory)

    def test_report_rejects_status_candidate_inconsistency(self) -> None:
        cases = [{
            "id": "inconsistent1",
            "source": "daily",
            "label": "daily",
            "text": "人工智能推动产业升级",
            "modes": {mode: mode for mode in ("pure", "sparse", "word1", "char1")},
        }]
        results = {
            model: {
                mode: {
                    mode: CandidateResult(
                        ("人工智能推动产业升级",),
                        10,
                        "empty" if model == "bgw" and mode == "pure" else "ok",
                    )
                }
                for mode in ("pure", "sparse", "word1", "char1")
            }
            for model in ("bgw", "wx", "tiger")
        }
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "status/candidate mismatch"):
                render_report(cases, results, directory)


class ResultLoadingTest(unittest.TestCase):
    def test_results_root_rejects_broad_or_symlinked_directory(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsafe results root"):
            subject.validate_results_root(Path("/tmp"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir()
            alias = root / "alias"
            alias.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "unsafe results root"):
                subject.validate_results_root(alias)

    def test_results_root_rejects_symlinked_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir()
            alias = root / "alias"
            alias.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "unsafe results root"):
                subject.validate_results_root(alias / "new")

    def test_report_output_rejects_symlinked_file(self) -> None:
        cases = [{
            "id": "report-symlink",
            "source": "daily",
            "label": "daily",
            "text": "人工智能推动产业升级",
            "modes": {mode: mode for mode in ("pure", "sparse", "word1", "char1")},
        }]
        results = {
            model: {
                mode: {mode: CandidateResult(("人工智能推动产业升级",), 10)}
                for mode in ("pure", "sparse", "word1", "char1")
            }
            for model in ("bgw", "wx", "tiger")
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "report"
            root.mkdir()
            outside = Path(directory) / "outside.json"
            outside.write_text("keep", encoding="utf-8")
            (root / "summary.json").symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "symlinked report file"):
                render_report(cases, results, root)
            self.assertEqual(outside.read_text(encoding="utf-8"), "keep")

    def test_artifact_hash_validation_rejects_changed_raw_dump(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "rime_bgw_pure.tsv"
            raw.write_text("E\tcode\t0\t0\t1\n", encoding="utf-8")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "outputs": {
                    "external_raw": {
                        "files": {
                            "rime_bgw_pure.tsv": {
                                "bytes": raw.stat().st_size,
                                "sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
                            }
                        }
                    }
                }
            }), encoding="utf-8")
            subject.validate_artifact_hashes(
                root,
                manifest,
                required_files=("rime_bgw_pure.tsv",),
            )
            raw.write_text("E\tcode\t0\t0\t2\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                subject.validate_artifact_hashes(
                    root,
                    manifest,
                    required_files=("rime_bgw_pure.tsv",),
                )

    def test_artifact_hash_validation_rejects_missing_or_escaping_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "results"
            root.mkdir()
            manifest = Path(directory) / "manifest.json"
            outside = Path(directory) / "outside.tsv"
            outside.write_text("outside", encoding="utf-8")
            (root / "rime_bgw_pure.tsv").symlink_to(outside)
            manifest.write_text(json.dumps({
                "outputs": {"external_raw": {"files": {
                    "rime_bgw_pure.tsv": {"bytes": 7, "sha256": "0" * 64}
                }}},
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "escapes results directory"):
                subject.validate_artifact_hashes(
                    root,
                    manifest,
                    required_files=("rime_bgw_pure.tsv",),
                )
            (root / "rime_bgw_pure.tsv").unlink()
            with self.assertRaises(FileNotFoundError):
                subject.validate_artifact_hashes(
                    root,
                    manifest,
                    required_files=("rime_bgw_pure.tsv",),
                )

    def test_load_cases_requires_canonical_corpus_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            row = {
                "id": "only-one",
                "source": "daily",
                "text": "人工智能推动产业升级",
                "modes": {mode: mode for mode in ("pure", "sparse", "word1", "char1")},
            }
            path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "canonical benchmark requires 20000"):
                load_cases(path)
            self.assertEqual(len(load_cases(path, require_canonical=False)), 1)

    def test_load_cases_rejects_duplicate_raw_per_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            rows = [
                {
                    "id": "a",
                    "text": "人工智能推动产业升级",
                    "modes": {mode: "same" for mode in ("pure", "sparse", "word1", "char1")},
                },
                {
                    "id": "b",
                    "text": "股票市场今日表现平稳",
                    "modes": {mode: "same" for mode in ("pure", "sparse", "word1", "char1")},
                },
            ]
            path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate raw"):
                load_cases(path)

    def test_load_results_rejects_missing_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "rime_bgw_pure.tsv").write_text(
                "E\tcode\t0\t0\t1\n", encoding="utf-8"
            )
            with self.assertRaises(FileNotFoundError):
                load_results(root, models=("bgw",))

    def test_load_results_requires_tiger_latency_for_every_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for mode in ("pure", "sparse", "word1", "char1"):
                (root / f"tiger_{mode}.tsv").write_text(
                    "code\t目标\x1f-1\n", encoding="utf-8"
                )
            with self.assertRaisesRegex(FileNotFoundError, "latency"):
                load_results(root, models=("tiger",))


if __name__ == "__main__":
    unittest.main()
