from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
SPEC = importlib.util.spec_from_file_location(
    "import_classics", ROOT / "tools" / "import_classics.py"
)
assert SPEC and SPEC.loader
import_classics = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = import_classics
SPEC.loader.exec_module(import_classics)


class ClassicsImportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "classics"
        self.raw = self.data / "raw" / "test-source" / "123.wikitext"
        self.raw.parent.mkdir(parents=True)
        self.raw.write_text("天地玄黄", encoding="utf-8")
        self.manifest = self.data / "sources.yaml"
        self.entries = self.data / "entries.tsv"
        self.overrides = self.data / "pinyin_overrides.tsv"
        self.long_entries = self.data / "long_entries.txt"
        self.output = self.root / "mohu_zrm.classics.dict.yaml"
        self.auxiliary = self.root / "tiger_aux.txt"
        self.readings = self.root / "pinyin_simp.txt"
        self.active = self.root / "active.dict.yaml"
        self.auxiliary.write_text(
            "天\ttt\n地\ttd\n玄\ttx\n黄\tth\n", encoding="utf-8"
        )
        self.readings.write_text(
            "天\ttian\t1\n地\tdi\t1\n地\tde\t1\n玄\txuan\t1\n黄\thuang\t1\n",
            encoding="utf-8",
        )
        self.active.write_text(
            "---\nname: active\nsort: by_weight\ncolumns:\n  - text\n  - weight\n  - code\n...\n",
            encoding="utf-8",
        )
        self.long_entries.write_text("", encoding="utf-8")
        self.overrides.write_text(
            "entry-1\ttian di xuan huang\tTest fixture reading\n",
            encoding="utf-8",
        )
        self.entries.write_text(
            "entry-1\ttest-source\t千字文\t句1\tline\t天地玄黄\ttian di xuan huang\t1\tapproved\n",
            encoding="utf-8",
        )
        self.write_manifest()
        self.patch = mock.patch.multiple(
            import_classics,
            DATA=self.data,
            MANIFEST=self.manifest,
            ENTRIES=self.entries,
            OVERRIDES=self.overrides,
            LONG_ENTRIES=self.long_entries,
            OUTPUT=self.output,
            ACTIVE_TABLES=(self.active,),
            AUXILIARY=self.auxiliary,
            READINGS=self.readings,
        )
        self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()
        self.temp.cleanup()

    def source(self) -> dict[str, object]:
        return {
            "id": "test-source",
            "title": "千字文",
            "url": "https://example.invalid/test-only",
            "page_title": "千字文",
            "revision": "123",
            "base_text": "test fixture",
            "proofread": "validated",
            "license": "CC-BY-SA 4.0",
            "redistribution": "approved",
            "retrieved": "2026-08-19T00:00:00Z",
            "raw_path": "raw/test-source/123.wikitext",
            "sha256": hashlib.sha256(self.raw.read_bytes()).hexdigest(),
            "status": "verified",
        }

    def write_manifest(self, source: dict[str, object] | None = None) -> None:
        value = {"sources": [source or self.source()]}
        self.manifest.write_text(
            json.dumps(value, ensure_ascii=False), encoding="utf-8"
        )

    def test_builds_reviewed_entry_and_check_is_read_only(self) -> None:
        self.assertEqual(0, import_classics.main(["build"]))
        text = self.output.read_text(encoding="utf-8")
        self.assertIn("# license: CC-BY-SA 4.0\n", text)
        self.assertIn("天地玄黄\ttm;tt di;td xr;tx hd;th\t1\n", text)
        before = self.output.read_bytes()
        self.assertEqual(0, import_classics.main(["check"]))
        self.assertEqual(before, self.output.read_bytes())

    def test_filters_text_already_present_in_an_active_table(self) -> None:
        self.active.write_text(
            self.active.read_text(encoding="utf-8") + "天地玄黄\t1\n",
            encoding="utf-8",
        )
        sources = import_classics.load_manifest()
        entries = import_classics.load_entries(sources)
        rendered, stats = import_classics.render(entries, sources)
        self.assertNotIn("天地玄黄\t", rendered)
        self.assertEqual(1, stats["duplicate_existing"])

    def test_rejects_unverified_source(self) -> None:
        source = self.source()
        source["status"] = "fetched-unreviewed"
        self.write_manifest(source)
        with self.assertRaisesRegex(ValueError, "is not verified"):
            import_classics.load_entries(import_classics.load_manifest())

    def test_rejects_snapshot_outside_raw_directory(self) -> None:
        outside = self.data / "outside.wikitext"
        outside.write_text("天地玄黄", encoding="utf-8")
        source = self.source()
        source["raw_path"] = "outside.wikitext"
        source["sha256"] = hashlib.sha256(outside.read_bytes()).hexdigest()
        self.assertFalse(import_classics.source_is_verified(source))

    def test_rejects_snapshot_hash_mismatch(self) -> None:
        source = self.source()
        source["sha256"] = "0" * 64
        self.assertFalse(import_classics.source_is_verified(source))

    def test_polyphonic_character_requires_evidenced_override(self) -> None:
        self.overrides.write_text("", encoding="utf-8")
        sources = import_classics.load_manifest()
        entries = import_classics.load_entries(sources)
        with self.assertRaisesRegex(ValueError, "polyphonic entry"):
            import_classics.render(entries, sources)

    def test_rejects_non_public_sync_target(self) -> None:
        private = [(None, None, None, None, ("127.0.0.1", 443))]
        with mock.patch.object(import_classics.socket, "getaddrinfo", return_value=private):
            with self.assertRaisesRegex(ValueError, "non-public"):
                import_classics.public_addresses(import_classics.WIKISOURCE_HOST)

    def test_rejects_non_numeric_revision_before_network_access(self) -> None:
        with mock.patch.object(import_classics, "public_addresses") as resolve:
            with self.assertRaisesRegex(ValueError, "positive integer"):
                import_classics.fetch_wikisource_revision("latest")
        resolve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
