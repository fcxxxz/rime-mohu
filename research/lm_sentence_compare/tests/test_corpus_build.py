from __future__ import annotations

import gzip
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from research.lm_sentence_compare.corpus.build_corpus import (
    clean_text,
    read_lccc,
    read_tnews,
    sample_corpus,
    sha256_file,
    _stage_source,
    write_corpus,
)


class CleaningTest(unittest.TestCase):
    def test_clean_text_removes_layout_noise_and_rejects_mixed_text(self) -> None:
        self.assertEqual(clean_text("  中国 经济  稳步增长。 "), "中国经济稳步增长")
        self.assertEqual(clean_text("遺產继承可以吗"), "遗产继承可以吗")
        self.assertIsNone(clean_text("含有English的句子"))
        self.assertIsNone(clean_text("太短"))


class SourceReaderTest(unittest.TestCase):
    def test_read_tnews_keeps_labels_and_deduplicates_clean_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tnews.zip"
            rows = [
                {"label": "109", "label_desc": "news_tech", "sentence": "人工智能推动产业升级", "keywords": ""},
                {"label": "109", "label_desc": "news_tech", "sentence": "人工智能推动产业升级", "keywords": ""},
                {"label": "104", "label_desc": "news_finance", "sentence": "股票市场今日表现平稳", "keywords": ""},
            ]
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("train.json", "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
            result = read_tnews(path)
            self.assertEqual([row["label"] for row in result], ["news_tech", "news_finance"])
            self.assertEqual(len({row["text"] for row in result}), 2)

    def test_read_lccc_flattens_dialogues_and_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lccc.jsonl.gz"
            payload = [["我们今天一起吃饭", "好的那就晚上见"], ["好的那就晚上见", "路上注意安全"]]
            with gzip.open(path, "wt", encoding="utf-8") as stream:
                for row in payload:
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            result = read_lccc(path)
            self.assertEqual([row["text"] for row in result], ["我们今天一起吃饭", "好的那就晚上见", "路上注意安全"])
            self.assertTrue(all(row["source"] == "daily" for row in result))


class SourceStagingTest(unittest.TestCase):
    def test_stage_source_rejects_bytes_with_unexpected_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.bin"
            destination = Path(directory) / "staged" / "source.bin"
            source.write_bytes(b"not the pinned source")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                _stage_source(source, destination, "0" * 64)

    def test_stage_source_preserves_existing_cache_when_hash_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.bin"
            destination = Path(directory) / "staged" / "source.bin"
            source.write_bytes(b"bad source")
            destination.parent.mkdir()
            destination.write_bytes(b"known good cache")
            expected = sha256_file(destination)

            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                _stage_source(source, destination, expected)

            self.assertEqual(destination.read_bytes(), b"known good cache")


class SamplingTest(unittest.TestCase):
    def _rows(self, prefix: str, count: int, source: str) -> list[dict[str, str]]:
        return [
            {"id": f"{prefix}-{index}", "source": source, "label": f"label-{index % 2}",
             "text": f"{prefix}这是测试句子编号{index}"}
            for index in range(count)
        ]

    def test_sampling_is_deterministic_and_exact(self) -> None:
        first = sample_corpus(self._rows("n", 12, "news"), self._rows("d", 12, "daily"), news_count=10, daily_count=10, seed=9)
        second = sample_corpus(self._rows("n", 12, "news"), self._rows("d", 12, "daily"), news_count=10, daily_count=10, seed=9)
        self.assertEqual(first, second)
        self.assertEqual(len(first[0]), 10)
        self.assertEqual(len(first[1]), 10)

    def test_sampling_reports_insufficient_source(self) -> None:
        with self.assertRaisesRegex(ValueError, "news quota"):
            sample_corpus(self._rows("n", 2, "news"), self._rows("d", 12, "daily"), news_count=10, daily_count=10, seed=1)

    def test_sampling_keeps_exact_quotas_when_sources_share_text(self) -> None:
        news = [
            {"id": "n0", "source": "news", "label": "a", "text": "共同"},
            {"id": "n1", "source": "news", "label": "b", "text": "共同"},
        ]
        daily = [
            {"id": "d0", "source": "daily", "label": "daily", "text": "共同"},
            {"id": "d1", "source": "daily", "label": "daily", "text": "日常"},
        ]
        selected_news, selected_daily = sample_corpus(
            news, daily, news_count=1, daily_count=1, seed=1
        )
        self.assertEqual(len(selected_news), 1)
        self.assertEqual(len(selected_daily), 1)
        self.assertTrue({row["text"] for row in selected_news}.isdisjoint(
            row["text"] for row in selected_daily
        ))


class OutputTest(unittest.TestCase):
    def test_write_corpus_assigns_unique_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sentences.jsonl"
            rows = [{"source": "news", "label": "news_tech", "text": "人工智能推动产业升级"}]
            self.assertEqual(write_corpus(path, rows), 1)
            written = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(written["id"], "news-00000")


if __name__ == "__main__":
    unittest.main()
