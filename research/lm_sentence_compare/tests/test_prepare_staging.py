from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research.lm_sentence_compare import prepare_staging as subject
from research.lm_sentence_compare.prepare_staging import (
    STAGING_MARKER,
    verify_file_hash,
    _reset_staging_data,
    build_inputs,
)


class StagingSafetyTest(unittest.TestCase):
    def test_verify_file_hash_rejects_unpinned_grammar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wanxiang.gram"
            path.write_bytes(b"wrong grammar")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                verify_file_hash(path, "0" * 64, label="Wanxiang grammar")

    def test_refuses_to_remove_unmarked_existing_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory) / "dedicated-staging"
            data = staging / "data"
            data.mkdir(parents=True)
            sentinel = data / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "unmarked staging"):
                _reset_staging_data(staging)

            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_refuses_broad_temporary_root(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsafe staging root"):
            _reset_staging_data(Path(tempfile.gettempdir()))

    def test_refuses_symlinked_staging_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir()
            alias = root / "alias"
            alias.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlinked staging root"):
                _reset_staging_data(alias)

    def test_refuses_symlinked_staging_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir()
            alias = root / "alias"
            alias.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlinked staging root"):
                _reset_staging_data(alias / "new")

    def test_prepare_data_checks_lexical_staging_root_before_resolving(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir()
            alias = root / "alias"
            alias.symlink_to(target, target_is_directory=True)
            with patch.object(subject, "verify_file_hash", return_value="pinned"):
                with self.assertRaisesRegex(ValueError, "symlinked staging root"):
                    subject.prepare_data(staging=alias, wanxiang_gram=root / "gram")
            self.assertFalse((target / STAGING_MARKER).exists())

    def test_replaces_data_owned_by_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory) / "dedicated-staging"
            data = staging / "data"
            data.mkdir(parents=True)
            (staging / STAGING_MARKER).write_text(
                "mohu-lm-sentence-benchmark-v1\n", encoding="utf-8"
            )
            sentinel = data / "old.txt"
            sentinel.write_text("old", encoding="utf-8")

            reset = _reset_staging_data(staging)

            self.assertEqual(reset, data.resolve())
            self.assertTrue(reset.is_dir())
            self.assertFalse(sentinel.exists())
            self.assertTrue((staging / STAGING_MARKER).is_file())

    def test_refuses_marker_with_unrecognized_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory) / "dedicated-staging"
            data = staging / "data"
            data.mkdir(parents=True)
            (staging / STAGING_MARKER).write_text("some other tool\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "invalid staging ownership marker"):
                _reset_staging_data(staging)

    def test_build_inputs_rejects_symlinked_inputs_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "dedicated-staging"
            staging.mkdir()
            (staging / STAGING_MARKER).write_text(
                "mohu-lm-sentence-benchmark-v1\n", encoding="utf-8"
            )
            outside = root / "outside"
            outside.mkdir()
            inputs = staging / "inputs"
            inputs.symlink_to(outside, target_is_directory=True)
            cases = root / "cases.jsonl"
            cases.write_text(
                json.dumps({
                    "id": "a",
                    "modes": {mode: mode for mode in ("pure", "sparse", "word1", "char1")},
                }) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "symlinked staging inputs"):
                build_inputs(cases_file=cases, inputs=inputs)

            self.assertEqual(list(outside.iterdir()), [])

    def test_build_inputs_rejects_symlinked_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / "real-staging"
            real.mkdir()
            (real / STAGING_MARKER).write_text(
                "mohu-lm-sentence-benchmark-v1\n", encoding="utf-8"
            )
            alias = root / "alias"
            alias.symlink_to(real, target_is_directory=True)
            cases = root / "cases.jsonl"
            cases.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "symlinked staging inputs"):
                build_inputs(cases_file=cases, inputs=alias / "inputs")


if __name__ == "__main__":
    unittest.main()
