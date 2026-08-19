from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.qq_dictionary_batch import (
    ACTIVE_WORD_TABLES,
    BatchConflict,
    BatchValidationError,
    DictionaryIndex,
    RimeTable,
    apply_batch,
    load_batch,
)


def table_text(columns: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    columns_text = "\n".join(f"  - {column}" for column in columns)
    body = "".join("\t".join(row) + "\n" for row in rows)
    return f"""---
name: fixture
columns:
{columns_text}
...
{body}"""


class DictionaryBatchTest(unittest.TestCase):
    def test_load_batch_rejects_control_characters(self) -> None:
        with self.assertRaisesRegex(BatchValidationError, "control character"):
            load_batch(
                {
                    "version": 1,
                    "batch_id": "20260819-000001",
                    "base_sha": "a" * 40,
                    "operations": [
                        {
                            "kind": "word_add",
                            "word": "坏\n词",
                            "expected": None,
                            "desired": 1,
                        }
                    ],
                }
            )

    def test_rime_table_uses_declared_weight_column(self) -> None:
        table = RimeTable.parse(
            """---
name: sample
columns:
  - text
  - weight
  - code
...
打印机\t1763\tda;ua yn;bf ji;eo
"""
        )
        self.assertEqual(table.rows_for("打印机")[0].weight, 1763)

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        (self.root / "mohu_zrm_fixed.dict.yaml").write_text(
            table_text(
                ("text", "code", "stem", "weight"),
                [("甲", "abc", "", "0"), ("乙", "abc", "", "0"), ("丙", "abd", "", "0")],
            ),
            encoding="utf-8",
        )
        for filename in ACTIVE_WORD_TABLES:
            (self.root / filename).write_text(
                table_text(("text", "code", "weight"), []), encoding="utf-8"
            )
        (self.root / ACTIVE_WORD_TABLES[0]).write_text(
            table_text(
                ("text", "code", "weight"),
                [("打印机", "dayinji", "1763"), ("大妖精", "dayaojing", "0")],
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def batch(self, *operations: dict[str, object]) -> object:
        return load_batch(
            {
                "version": 1,
                "batch_id": "20260819-000001",
                "base_sha": "a" * 40,
                "operations": list(operations),
            }
        )

    def test_fixed_reorder_requires_expected_order(self) -> None:
        batch = self.batch(
            {
                "kind": "fixed_reorder",
                "code": "abc",
                "word": "甲",
                "other_word": "乙",
                "expected_order": ["甲", "乙"],
                "desired_order": ["乙", "甲"],
            }
        )
        apply_batch(self.root, batch)
        table = RimeTable.from_path(self.root / "mohu_zrm_fixed.dict.yaml")
        self.assertEqual([row.text for row in table.rows_for_code("abc")], ["乙", "甲"])

    def test_fixed_reorder_conflict_does_not_write(self) -> None:
        before = (self.root / "mohu_zrm_fixed.dict.yaml").read_bytes()
        batch = self.batch(
            {
                "kind": "fixed_reorder",
                "code": "abc",
                "word": "甲",
                "other_word": "乙",
                "expected_order": ["乙", "甲"],
                "desired_order": ["甲", "乙"],
            }
        )
        with self.assertRaises(BatchConflict):
            apply_batch(self.root, batch)
        self.assertEqual((self.root / "mohu_zrm_fixed.dict.yaml").read_bytes(), before)

    def test_fixed_add_and_delete_preserve_other_rows(self) -> None:
        added = self.batch(
            {
                "kind": "fixed_add",
                "code": "abc",
                "word": "丁",
                "expected_order": ["甲", "乙"],
                "desired_order": ["甲", "乙", "丁"],
            }
        )
        apply_batch(self.root, added)
        table = RimeTable.from_path(self.root / "mohu_zrm_fixed.dict.yaml")
        self.assertEqual([row.text for row in table.rows_for_code("abc")], ["甲", "乙", "丁"])

        deleted = self.batch(
            {
                "kind": "fixed_delete",
                "code": "abc",
                "word": "乙",
                "expected_order": ["甲", "乙", "丁"],
                "desired_order": ["甲", "丁"],
            }
        )
        apply_batch(self.root, deleted)
        table = RimeTable.from_path(self.root / "mohu_zrm_fixed.dict.yaml")
        self.assertEqual([row.text for row in table.rows_for_code("abc")], ["甲", "丁"])

    def test_added_rows_do_not_write_trailing_empty_columns(self) -> None:
        batch = self.batch(
            {
                "kind": "fixed_add",
                "code": "abc",
                "word": "丁",
                "expected_order": ["甲", "乙"],
                "desired_order": ["甲", "乙", "丁"],
            },
            {
                "kind": "word_add",
                "word": "新词",
                "expected": None,
                "desired": 1,
            },
        )
        apply_batch(self.root, batch)
        fixed_line = next(
            line for line in (self.root / "mohu_zrm_fixed.dict.yaml").read_text(encoding="utf-8").splitlines()
            if line.startswith("丁\t")
        )
        word_line = next(
            line for line in (self.root / ACTIVE_WORD_TABLES[1]).read_text(encoding="utf-8").splitlines()
            if line.startswith("新词\t")
        )
        self.assertFalse(fixed_line.endswith("\t"))
        self.assertFalse(word_line.endswith("\t"))

    def test_word_delete_removes_exact_duplicates_from_active_tables(self) -> None:
        (self.root / ACTIVE_WORD_TABLES[1]).write_text(
            table_text(("text", "weight", "code"), [("打印机", "1763", "")]),
            encoding="utf-8",
        )
        batch = self.batch(
            {"kind": "word_delete", "word": "打印机", "expected": 1763, "desired": None}
        )
        apply_batch(self.root, batch)
        self.assertIsNone(DictionaryIndex.load(self.root).effective_weight("打印机"))

    def test_frequency_swap_updates_effective_rows_and_adds_word(self) -> None:
        batch = self.batch(
            {
                "kind": "word_frequency",
                "word": "打印机",
                "expected": 1763,
                "desired": 0,
            },
            {
                "kind": "word_frequency",
                "word": "大妖精",
                "expected": 0,
                "desired": 1763,
            },
            {
                "kind": "word_add",
                "word": "新词",
                "expected": None,
                "desired": 1,
            },
        )
        apply_batch(self.root, batch)
        index = DictionaryIndex.load(self.root)
        self.assertEqual(index.effective_weight("打印机"), 0)
        self.assertEqual(index.effective_weight("大妖精"), 1763)
        self.assertEqual(index.effective_weight("新词"), 1)

    def test_pronunciation_set_updates_phrase_code_and_hint(self) -> None:
        path = self.root / ACTIVE_WORD_TABLES[0]
        path.write_text(
            table_text(("text", "code", "weight"), [("滞塞", "vi;kl sl;wq", "8")]),
            encoding="utf-8",
        )
        (self.root / "opencc").mkdir()
        (self.root / "opencc/mohu_pinyinhint.txt").write_text(
            "滞\t〔zhì〕\n塞\t〔sāi〕\n", encoding="utf-8"
        )
        batch = self.batch(
            {
                "kind": "pronunciation_set",
                "word": "滞塞",
                "expected": ["zhi sai"],
                "desired": ["zhì sè"],
            }
        )
        apply_batch(self.root, batch)
        self.assertIn("vi;kl se;wq", path.read_text(encoding="utf-8"))
        self.assertIn("滞塞\t〔zhìsè〕", (self.root / "opencc/mohu_pinyinhint.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
