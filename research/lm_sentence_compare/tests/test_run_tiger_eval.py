from __future__ import annotations

import ctypes
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from research.lm_sentence_compare import run_tiger_eval as subject


class FakeTigerLibrary:
    def __init__(self, payload: str, return_count: int) -> None:
        self.payload = payload.encode("utf-8") + b"\0"
        self.return_count = return_count

    def tiger_decode(self, _handle, _raw, _include_early, output, capacity, elapsed) -> int:
        if len(self.payload) > capacity:
            return -1
        ctypes.memmove(output, self.payload, len(self.payload))
        ctypes.cast(elapsed, ctypes.POINTER(ctypes.c_double))[0] = 1.25
        return self.return_count

    def tiger_last_error(self) -> bytes:
        return b"fake error"


class NativeDecodeContractTest(unittest.TestCase):
    def test_parses_valid_header_and_candidate_count(self) -> None:
        library = FakeTigerLibrary(
            "0 0 0 0 1 0 0 0 0 0\n"
            "目标\t目标\t-1.0\t-1.5\t1\t2:2\n",
            return_count=1,
        )
        rows, elapsed = subject.decode(library, 7, "code")
        self.assertEqual(rows, [("目标", "-1.0")])
        self.assertEqual(elapsed, 1250)

    def test_rejects_header_and_return_count_mismatch(self) -> None:
        library = FakeTigerLibrary(
            "0 0 0 0 2 0 0 0 0 0\n"
            "目标\t目标\t-1.0\t-1.5\t1\t2:2\n",
            return_count=1,
        )
        with self.assertRaisesRegex(RuntimeError, "candidate count mismatch"):
            subject.decode(library, 7, "code")

    def test_rejects_malformed_candidate_rows(self) -> None:
        library = FakeTigerLibrary(
            "0 0 0 0 1 0 0 0 0 0\n"
            "目标\t目标\tnot-a-score\n",
            return_count=1,
        )
        with self.assertRaisesRegex(RuntimeError, "candidate row"):
            subject.decode(library, 7, "code")

    def test_rejects_duplicate_candidate_texts(self) -> None:
        library = FakeTigerLibrary(
            "0 0 0 0 2 0 0 0 0 0\n"
            "目标\t目标\t-1.0\t-1.5\t1\t2:2\n"
            "目标\t目标\t-2.0\t-2.5\t2\t2:2\n",
            return_count=2,
        )
        with self.assertRaisesRegex(RuntimeError, "duplicate candidate"):
            subject.decode(library, 7, "code")


class TopLimitTest(unittest.TestCase):
    def test_output_root_rejects_live_or_broad_directories(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsafe output root"):
            subject.validate_output_root(Path.home() / "Library" / "Rime")
        with self.assertRaisesRegex(ValueError, "unsafe output root"):
            subject.validate_output_root(Path("/tmp"))

    def test_output_root_rejects_symlinked_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir()
            alias = root / "alias"
            alias.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "unsafe output root"):
                subject.validate_output_root(alias / "new")

    def test_output_file_rejects_symlink_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.write_text("keep", encoding="utf-8")
            output = root / "output"
            output.mkdir()
            (output / "tiger_pure.tsv").symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "symlinked output file"):
                subject.validate_output_file(output / "tiger_pure.tsv", output)

    def test_main_frees_engine_when_manifest_setup_fails(self) -> None:
        class FakeLibrary:
            def __init__(self) -> None:
                self.freed: list[int] = []

                def free(handle: int) -> None:
                    self.freed.append(handle)

                self.tiger_engine_free = free

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / "inputs"
            inputs.mkdir()
            output = root / "output"
            fake = FakeLibrary()
            argv = [
                "run_tiger_eval.py",
                "--mode",
                "pure",
                "--inputs",
                str(inputs),
                "--out",
                str(output),
                "--allow-unpinned-resources",
            ]
            with patch.object(subject, "load_engine", return_value=(fake, 7)):
                with patch.object(
                    subject,
                    "resource_metadata",
                    side_effect=RuntimeError("manifest setup failed"),
                ):
                    with patch.object(subject.sys, "argv", argv):
                        with self.assertRaisesRegex(RuntimeError, "manifest setup failed"):
                            subject.main()
            self.assertEqual(fake.freed, [7])

    def test_verify_file_hash_rejects_unpinned_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.bin"
            path.write_bytes(b"wrong model")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                subject.verify_file_hash(path, "0" * 64, label="Tiger n-gram")

    def test_engine_hash_is_pinned(self) -> None:
        self.assertEqual(len(subject.TIGER_ENGINE_SHA256), 64)
        self.assertNotEqual(set(subject.TIGER_ENGINE_SHA256), {"0"})

    def test_default_engine_paths_use_worktree_root(self) -> None:
        expected_root = Path(subject.__file__).resolve().parents[2]
        self.assertEqual(subject.REPO, expected_root)
        self.assertEqual(
            subject.DEFAULT_LIB,
            expected_root / "tiger_sentence_native" / "libtigerengine.dylib",
        )

    def test_cli_mode_contract_is_defined(self) -> None:
        self.assertEqual(subject.MODE_NAMES, ("pure", "sparse", "word1", "char1"))

    def test_cli_rejects_duplicate_modes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            argv = [
                "run_tiger_eval.py",
                "--mode",
                "pure,pure",
                "--out",
                str(root / "output"),
                "--allow-unpinned-resources",
            ]
            with patch.object(subject.sys, "argv", argv):
                with self.assertRaisesRegex(SystemExit, "duplicate mode"):
                    subject.main()

    def test_cli_rejects_empty_mode_list(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            argv = [
                "run_tiger_eval.py",
                "--mode",
                "",
                "--out",
                str(Path(directory) / "output"),
                "--allow-unpinned-resources",
            ]
            with patch.object(subject.sys, "argv", argv):
                with self.assertRaisesRegex(SystemExit, "at least one mode"):
                    subject.main()

    def test_default_model_follows_current_mohu_llm_layout(self) -> None:
        self.assertEqual(
            subject.DEFAULT_MODEL,
            Path.home() / "Library" / "Rime" / "mohu_llm" / "data" / "sentence-ngram-mobile.bin",
        )

    def test_top_limit_cannot_understate_top20_metrics(self) -> None:
        self.assertEqual(subject.DEFAULT_TOP, 20)
        self.assertEqual(subject.validate_top_limit(20), 20)
        with self.assertRaisesRegex(ValueError, "at least 20"):
            subject.validate_top_limit(10)

    def test_input_loader_rejects_duplicate_raws(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate input raw"):
            subject.load_input_rows(["a\tcode", "b\tcode"])

    @unittest.skipUnless(
        subject.DEFAULT_LIB.is_file()
        and subject.DEFAULT_MODEL.is_file()
        and subject.DEFAULT_LEXICON.is_file(),
        "native Tiger benchmark assets are unavailable",
    )
    def test_cli_smoke_uses_default_paths_and_writes_top20(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / "inputs"
            outputs = root / "outputs"
            inputs.mkdir()
            case = json.loads(
                Path(subject.REPO / "research/lm_sentence_compare/cases.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()[0]
            )
            (inputs / "pure.tsv").write_text(
                f"{case['id']}\t{case['modes']['pure']}\n", encoding="utf-8"
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(subject.__file__).resolve()),
                    "--mode",
                    "pure",
                    "--inputs",
                    str(inputs),
                    "--out",
                    str(outputs),
                ],
                cwd=subject.REPO,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            line = (outputs / "tiger_pure.tsv").read_text(encoding="utf-8").strip()
            self.assertEqual(len(line.split("\t")) - 1, 20)
            run_manifest = json.loads(
                (outputs / "tiger-run-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(run_manifest["top"], 20)
            self.assertFalse(run_manifest["allow_unpinned_resources"])
            self.assertTrue(run_manifest["resources"]["model"]["pinned"])
            self.assertTrue(run_manifest["resources"]["engine"]["pinned"])


if __name__ == "__main__":
    unittest.main()
