from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research.lm_sentence_compare.run_cross_candidate import (
    COMPETITOR_PATCHES,
    SchemeRun,
    WorkerJob,
    _input_units,
    reusable_job_record,
    stage_mohu_template,
    validate_candidate_output,
    validate_model_path,
    validate_root,
    verify_build_artifacts,
    write_benchmark_schema_list,
    write_custom_patch,
    write_shards,
)


class CrossCandidateRunnerTest(unittest.TestCase):
    def test_after_prefix_units_keep_w_and_b_together(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "demo.afterA.tsv"
            source.write_text(
                "W\ti0\taa\t00\nB\ti0\taaaa\taaaaa\taaaaa\taaaaaa\t00\t0\t词\n"
                "W\ti1\tbb\t11\nB\ti1\tbbbb\tbbbbb\tbbbbb\tbbbbbb\t11\t0\t字\n",
                encoding="utf-8",
            )
            units = _input_units(source, "afterA")
            self.assertEqual(len(units), 2)
            shards = write_shards(source, "afterA", root / "chunks", 1)
            self.assertEqual(len(shards), 2)
            self.assertTrue(shards[0].read_text(encoding="utf-8").startswith("W\ti0\t"))
            self.assertIn("\nB\ti0\t", shards[0].read_text(encoding="utf-8"))
            self.assertNotIn("i1", shards[0].read_text(encoding="utf-8"))

    def test_after_prefix_units_reject_mismatched_pair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.tsv"
            path.write_text(
                "W\ti0\taa\t00\nB\ti1\taaaa\taaaaa\taaaaa\taaaaaa\t11\t0\t字\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "case mismatch"):
                _input_units(path, "afterA")

    def test_competitor_patches_disable_learning_without_disabling_context(self) -> None:
        self.assertFalse(COMPETITOR_PATCHES["moran"]["smart/enable_user_dict"])
        self.assertFalse(COMPETITOR_PATCHES["yeying"]["translator/enable_user_dict"])
        self.assertFalse(COMPETITOR_PATCHES["wxpro"]["add_user_dict/enable_auto_phrase"])
        self.assertTrue(
            all(
                "contextual_suggestions" not in key and "context_reorder" not in key
                for patch in COMPETITOR_PATCHES.values()
                for key in patch
            )
        )

    def test_custom_patch_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo.custom.yaml"
            write_custom_patch(path, {"translator/enable_user_dict": False})
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "patch:\n  translator/enable_user_dict: false\n",
            )
            with self.assertRaisesRegex(FileExistsError, "refusing to overwrite"):
                write_custom_patch(path, {"translator/enable_user_dict": True})

    def test_benchmark_schema_list_replaces_template_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "default.custom.yaml"
            path.write_text(
                "patch:\n  schema_list:\n    - schema: unrelated\n",
                encoding="utf-8",
            )
            write_benchmark_schema_list(path, "yeying")
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "patch:\n  schema_list:\n    - schema: yeying\n",
            )

    def test_required_artifacts_include_moran_dynamic_smart_dictionary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory)
            build = template / "build"
            build.mkdir()
            run = SchemeRun(
                "moran", "moranmain2", template, "moran.schema.yaml", "moran"
            )
            with self.assertRaisesRegex(
                RuntimeError, "moran\\.extended\\.table\\.bin"
            ):
                verify_build_artifacts(run)
            for name in (
                "moran.extended.table.bin",
                "moran.prism.bin",
                "moran_fixed_simp.table.bin",
                "moran_english.table.bin",
            ):
                (build / name).write_bytes(name.encode("ascii"))
            artifacts = verify_build_artifacts(run)
            self.assertEqual(set(artifacts), {
                "moran.extended.table.bin",
                "moran.prism.bin",
                "moran_fixed_simp.table.bin",
                "moran_english.table.bin",
            })

    def test_candidate_output_rejects_entirely_empty_streams(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.tsv"
            path.write_text(
                "E\ti0\tpure\t0\t0\t12\nE\ti0\thead\t0\t0\t13\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "every candidate stream is empty"):
                validate_candidate_output(path)
            path.write_text(
                "E\ti0\tpure\t0\t0\t12\nE\ti0\thead\t2\t0\t13\n",
                encoding="utf-8",
            )
            self.assertEqual(
                validate_candidate_output(path),
                {"candidate_streams": 2, "nonempty_candidate_streams": 1},
            )

    def test_reusable_job_requires_complete_candidate_and_prefix_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "afterA.tsv"
            output_path = root / "output.tsv"
            input_path.write_text(
                "W\ti0\tprefix\t00\nB\ti0\ttail1=aaaa\te8af8d\t0\t词\n",
                encoding="utf-8",
            )
            run = SchemeRun("demo", "demo", root, "demo.schema.yaml", "demo")
            job = WorkerJob(run, "afterA", 0, input_path, output_path, root / "run", root / "log")
            output_path.write_text(
                "A\ti0\t1\t00\nC\ti0\ttail1\t1\te8af8d\nE\ti0\ttail1\t1\t0\t12\n",
                encoding="utf-8",
            )
            record = reusable_job_record(job)
            self.assertIsNotNone(record)
            self.assertTrue(record["reused"])

            output_path.write_text(
                "C\ti0\ttail1\t1\te8af8d\nE\ti0\ttail1\t1\t0\t12\n",
                encoding="utf-8",
            )
            self.assertIsNone(reusable_job_record(job))

            output_path.write_text("A\ti0\t1\t00\n", encoding="utf-8")
            self.assertIsNone(reusable_job_record(job))

    def test_mohu_run_uses_report_name_but_keeps_schema_id(self) -> None:
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "mohu_zrm"
            notes: list[str] = []
            with patch(
                "research.lm_sentence_compare.run_cross_candidate.subprocess.run"
            ), patch(
                "research.lm_sentence_compare.run_cross_candidate._copy_file"
            ):
                run = stage_mohu_template(
                    "zrm",
                    "mohu_zrm",
                    destination,
                    Path(directory) / "model.bin",
                    notes,
                )
            self.assertEqual(run.scheme, "mohu_zrm")
            self.assertEqual(run.condition, "mohu_zrm")
            self.assertEqual(run.schema_id, "mohu_llm_zrm")
            self.assertEqual(run.schema_file, "mohu_llm_zrm.schema.yaml")
            self.assertEqual(notes, [])

    def test_strip_missing_import_removes_only_missing_wanxiang_entry(self) -> None:
        from research.lm_sentence_compare.run_cross_candidate import strip_missing_import

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extended = root / "mohu_zrm.extended.dict.yaml"
            extended.write_text(
                "import_tables:\n"
                "  - mohu_zrm.base\n"
                "  - mohu_zrm.wanxiang   # 万象拼音普通词库的可复现改编\n"
                "  - mohu_zrm.words\n",
                encoding="utf-8",
            )
            self.assertTrue(
                strip_missing_import(root, "mohu_zrm.extended", "mohu_zrm.wanxiang")
            )
            self.assertEqual(
                extended.read_text(encoding="utf-8"),
                "import_tables:\n  - mohu_zrm.base\n  - mohu_zrm.words\n",
            )
            self.assertFalse(
                strip_missing_import(root, "mohu_zrm.extended", "mohu_zrm.wanxiang")
            )
            (root / "mohu_zrm.wanxiang.dict.yaml").write_text("---\n", encoding="utf-8")
            extended.write_text(
                "import_tables:\n  - mohu_zrm.wanxiang\n",
                encoding="utf-8",
            )
            self.assertFalse(
                strip_missing_import(root, "mohu_zrm.extended", "mohu_zrm.wanxiang")
            )
            self.assertIn(
                "mohu_zrm.wanxiang", extended.read_text(encoding="utf-8")
            )

    def test_validate_root_rejects_live_rime_and_requires_inputs(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsafe benchmark root"):
            validate_root(Path.home() / "Library/Rime")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "does not contain generated inputs"):
                validate_root(Path(directory))

    def test_validate_model_path_rejects_live_rime(self) -> None:
        model = Path.home() / "Library/Rime/mohu_llm/data/sentence-ngram-mobile.bin"
        with self.assertRaisesRegex(ValueError, "live Rime"):
            validate_model_path(model)


if __name__ == "__main__":
    unittest.main()
