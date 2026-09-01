from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research.lm_sentence_compare.merge_tiger_shards import merge_shards


class MergeTest(unittest.TestCase):
    def test_merges_each_mode_and_rejects_duplicate_raw(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shards"
            out = Path(directory) / "out"
            for shard in range(2):
                target = root / f"tiger_shard{shard}"
                target.mkdir(parents=True)
                (target / f"tiger_pure_shard{shard}.tsv").write_text(
                    f"raw{shard}\t候选\x1f-1\n", encoding="utf-8"
                )
                (target / f"tiger_pure_shard{shard}.latency.tsv").write_text(
                    f"raw{shard}\t10\n", encoding="utf-8"
                )
            result = merge_shards(root, out, modes=("pure",), shard_count=2, expected_rows=2)
            self.assertEqual(result["pure"], 2)
            self.assertEqual((out / "tiger_pure.tsv").read_text(encoding="utf-8").count("raw"), 2)

    def test_duplicate_raw_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shards"
            for shard in range(2):
                target = root / f"tiger_shard{shard}"
                target.mkdir(parents=True)
                (target / f"tiger_pure_shard{shard}.tsv").write_text("same\t候选\x1f-1\n", encoding="utf-8")
                (target / f"tiger_pure_shard{shard}.latency.tsv").write_text("same\t10\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate raw"):
                merge_shards(root, Path(directory) / "out", modes=("pure",), shard_count=2, expected_rows=2)


if __name__ == "__main__":
    unittest.main()
