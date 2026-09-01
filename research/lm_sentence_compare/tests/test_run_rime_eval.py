from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research.lm_sentence_compare import run_rime_eval as subject

run_rime = subject.run_rime


class RunnerContractTest(unittest.TestCase):
    def test_rejects_unsafe_output_root(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsafe output root"):
            subject.validate_output_root(Path.home() / "Library" / "Rime")
        with self.assertRaisesRegex(ValueError, "unsafe output root"):
            subject.validate_output_root(Path("/tmp"))

    def test_rejects_symlinked_output_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir()
            alias = root / "alias"
            alias.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "unsafe output root"):
                subject.validate_output_root(alias / "new")

    def test_rejects_symlinked_output_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.write_text("keep", encoding="utf-8")
            output = root / "output"
            output.mkdir()
            (output / "rime_bgw_pure.tsv").symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "symlinked output file"):
                subject.validate_output_file(output / "rime_bgw_pure.tsv", output)

    def test_rejects_candidate_limit_below_top20(self) -> None:
        self.assertEqual(subject.validate_candidate_limit(20), 20)
        with self.assertRaisesRegex(ValueError, "at least 20"):
            subject.validate_candidate_limit(5)

    def test_rejects_duplicate_modes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "duplicate mode"):
                run_rime(
                    data_dir=Path(directory) / "data",
                    inputs_dir=Path(directory) / "inputs",
                    results_dir=Path(directory) / "results",
                    modes=("pure", "pure"),
                )

    def test_rejects_empty_mode_list(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "at least one mode"):
                run_rime(
                    data_dir=Path(directory) / "data",
                    inputs_dir=Path(directory) / "inputs",
                    results_dir=Path(directory) / "results",
                    modes=(),
                )

    def test_refuses_live_rime_directory(self) -> None:
        with self.assertRaisesRegex(ValueError, "live Rime"):
            run_rime(
                data_dir=Path.home() / "Library" / "Rime",
                inputs_dir=Path("/tmp/no-inputs"),
                results_dir=Path("/tmp/no-results"),
            )

    def test_refuses_unmarked_rime_data_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "unmarked Rime staging data"):
                subject.validate_staging_data_dir(Path(directory) / "data")

    def test_probe_abi_rejects_homebrew_dependency(self) -> None:
        with patch.object(subject.sys, "platform", "darwin"):
            with self.assertRaisesRegex(ValueError, "non-Squirrel"):
                subject.validate_probe_dependencies(
                    "\t/opt/homebrew/opt/librime/lib/librime.1.dylib (compatibility version 1.0.0)\n"
                )

    def test_probe_abi_accepts_squirrel_rpath_dependency(self) -> None:
        with patch.object(subject.sys, "platform", "darwin"):
            subject.validate_probe_dependencies(
                "\t@rpath/librime.1.dylib (compatibility version 1.0.0)\n"
            )

    def test_checks_output_row_count_and_records_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            staging.mkdir()
            (staging / subject.STAGING_MARKER).write_text(
                subject.STAGING_MARKER_CONTENT, encoding="utf-8"
            )
            data = staging / "data"
            inputs = root / "inputs"
            results = root / "results"
            data.mkdir()
            inputs.mkdir()
            probe = root / "probe"
            probe.write_text("", encoding="utf-8")
            plugins = root / "plugins"
            plugins.mkdir()
            (root / "librime.1.dylib").write_text("", encoding="utf-8")
            (plugins / "librime-lua.dylib").write_text("", encoding="utf-8")
            (plugins / "librime-octagram.dylib").write_text("", encoding="utf-8")
            (data / "mohu_zrm_sentence.schema.yaml").write_text("schema_id", encoding="utf-8")
            (inputs / "pure.tsv").write_text("id\tcode\n", encoding="utf-8")

            def fake_run(command, stdout, stderr, check):
                output = Path(command[-2])
                output.write_text("E\tcode\t0\t0\t12\n", encoding="utf-8")
                return type("Completed", (), {"returncode": 0})()

            with patch.object(subject, "validate_probe_abi"):
                with patch("research.lm_sentence_compare.run_rime_eval.subprocess.run", side_effect=fake_run):
                    manifest = run_rime(
                        data_dir=data,
                        inputs_dir=inputs,
                        results_dir=results,
                        probe=probe,
                        shared_dir=Path("/tmp/shared"),
                        plugins_dir=plugins,
                        models=("bgw",),
                        modes=("pure",),
                        expected_probe_sha256=None,
                        expected_librime_sha256=None,
                        expected_lua_sha256=None,
                        expected_octagram_sha256=None,
                    )
            self.assertEqual(manifest["runs"][0]["output_rows"], 1)


class OutputValidationTest(unittest.TestCase):
    def test_probe_failure_record_includes_elapsed_field(self) -> None:
        source = (Path(subject.__file__).parent / "probes" / "rime_candidate_dump.cc").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "output << \"E\\t\" << code << \"\\t0\\t0\\t\" << elapsed << '\\n'",
            source,
        )
        self.assertIn(
            "if (max_candidates < 20)",
            source,
        )

    def test_rejects_duplicate_end_raw_even_when_row_count_matches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "output.tsv"
            path.write_text(
                "E\tcode\t0\t0\t1\n"
                "E\tcode\t0\t0\t2\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate end raw"):
                subject._validate_rime_output(path, {"code", "other"})

    def test_rejects_candidate_count_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "output.tsv"
            path.write_text(
                "C\tcode\t1\te794b2\t\n"
                "E\tcode\t2\t0\t1\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "candidate count mismatch"):
                subject._validate_rime_output(path, {"code"})

    def test_rejects_duplicate_candidate_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "output.tsv"
            encoded = "e9878de5a48d"
            path.write_text(
                f"C\tcode\t1\t{encoded}\t\n"
                f"C\tcode\t2\t{encoded}\t\n"
                "E\tcode\t2\t0\t1\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate candidate"):
                subject._validate_rime_output(path, {"code"})

    def test_rejects_failure_end_row_without_latency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "output.tsv"
            path.write_text("E\tcode\t0\t0\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "exactly 5 fields"):
                subject._validate_rime_output(path, {"code"})

    def test_rejects_invalid_end_flags_and_negative_latency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "output.tsv"
            path.write_text("E\tcode\t0\t2\t1\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "truncation flag"):
                subject._validate_rime_output(path, {"code"})
            path.write_text("E\tcode\t0\t0\t-1\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "non-negative"):
                subject._validate_rime_output(path, {"code"})

    def test_rejects_duplicate_input_raw(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.tsv"
            path.write_text("a\tcode\nb\tcode\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate input raw"):
                subject._input_raws(path)


if __name__ == "__main__":
    unittest.main()
