"""tools/custom/custom_aux.py 的单元测试：辅码合并、形码推导规则分支。"""

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from tiger_aux import AuxiliaryEntry  # noqa: E402
from tools.custom.custom_aux import (  # noqa: E402
    AuxError,
    derive_aux_from_shape_codes,
    load_shape_codes,
    load_user_aux,
    merge_aux,
    zrmdb_lines,
)


def write_tsv(rows: str, directory: Path, filename: str = "table.tsv") -> Path:
    path = directory / filename
    path.write_text(rows, encoding="utf-8")
    return path


BASE = {
    "的": AuxiliaryEntry("un", "", "ui"),
    "一": AuxiliaryEntry("fi"),
    "是": AuxiliaryEntry("ot"),
}


class LoadUserAuxTest(unittest.TestCase):
    def test_loads_tiger_aux_format(self) -> None:
        with TemporaryDirectory() as tmp:
            path = write_tsv("# 注释\n的\tdr\n一\tyi\t\tys\n", Path(tmp))
            table = load_user_aux(path)
        self.assertEqual(table["的"].normal, "dr")
        self.assertEqual(table["一"].normal, "yi")
        self.assertEqual(table["一"].compat13, "ys")

    def test_missing_file(self) -> None:
        with self.assertRaises(AuxError):
            load_user_aux(Path("/nonexistent/aux.tsv"))


class ShapeCodeDerivationTest(unittest.TestCase):
    def test_four_letter_primary(self) -> None:
        with TemporaryDirectory() as tmp:
            path = write_tsv("虎\tymfw\n", Path(tmp))
            source = load_shape_codes(path)
        table = derive_aux_from_shape_codes(source)
        # 正常=1-2位，14位=1+4位，13位=1+3位；默认不剪枝。
        self.assertEqual(table["虎"].normal, "ym")
        self.assertEqual(table["虎"].compat14, "yw")
        self.assertEqual(table["虎"].compat13, "ym"[:1] + "f")

    def test_primary_selection_prefers_first_four_letter(self) -> None:
        # 源序首个四码优先于更长的码。
        with TemporaryDirectory() as tmp:
            path = write_tsv("字\tabcd\n字\tabcde\n", Path(tmp))
            source = load_shape_codes(path)
        table = derive_aux_from_shape_codes(source)
        self.assertEqual(table["字"].normal, "ab")
        self.assertEqual(table["字"].compat14, "ad")
        # 非主码进别名候选。
        self.assertEqual(source.aliases["字"], ["abcde"])

    def test_primary_falls_back_to_longest(self) -> None:
        with TemporaryDirectory() as tmp:
            path = write_tsv("字\tabc\n字\tab\n", Path(tmp))
            source = load_shape_codes(path)
        table = derive_aux_from_shape_codes(source)
        # 最长码 abc（首个）；13 位 = a+c；2 根以下无 14 位（码长不足）。
        self.assertEqual(table["字"].normal, "ab")
        self.assertEqual(table["字"].compat13, "ac")
        self.assertEqual(table["字"].compat14, "")

    def test_root_count_column_enables_tiger_pruning(self) -> None:
        # 3 根：丢 14 位（虎码语义：末位是小码）；2 根：连 13 位也丢。
        with TemporaryDirectory() as tmp:
            path = write_tsv("甲\tabcd\t3\n乙\tabc\t2\n", Path(tmp))
            source = load_shape_codes(path)
        table = derive_aux_from_shape_codes(source)
        self.assertEqual(table["甲"].compat14, "")
        self.assertEqual(table["甲"].compat13, "ac")
        self.assertEqual(table["乙"].compat14, "")
        self.assertEqual(table["乙"].compat13, "")

    def test_no_prune_by_default(self) -> None:
        with TemporaryDirectory() as tmp:
            path = write_tsv("甲\tabcd\t3\n", Path(tmp), "with-root.tsv")
            no_root = write_tsv("甲\tabcd\n", Path(tmp), "no-root.tsv")
            pruned = derive_aux_from_shape_codes(load_shape_codes(path))
            plain = derive_aux_from_shape_codes(load_shape_codes(no_root))
        self.assertEqual(pruned["甲"].compat14, "")
        self.assertNotEqual(plain["甲"].compat14, "")

    def test_single_letter_code_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            path = write_tsv("字\ta\n", Path(tmp))
            with self.assertRaises(AuxError) as ctx:
                load_shape_codes(path)
            self.assertIn("1 个字母", str(ctx.exception))

    def test_bad_root_count_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            path = write_tsv("字\tabcd\t三\n", Path(tmp))
            with self.assertRaises(AuxError):
                load_shape_codes(path)

    def test_inconsistent_root_count_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            path = write_tsv("字\tabcd\t3\n字\tabdc\t4\n", Path(tmp))
            with self.assertRaises(AuxError) as ctx:
                load_shape_codes(path)
            self.assertIn("根数列前后不一致", str(ctx.exception))


class MergeAuxTest(unittest.TestCase):
    def test_user_overrides_base(self) -> None:
        user = {"的": AuxiliaryEntry("dr")}
        outcome = merge_aux(user, required_chars=["的", "一", "是"], base=BASE)
        self.assertEqual(outcome.table["的"].normal, "dr")
        self.assertEqual(outcome.user_chars, 1)
        self.assertEqual(outcome.fallback_chars, 2)
        self.assertEqual(outcome.missing, [])

    def test_missing_reported(self) -> None:
        user = {}
        outcome = merge_aux(user, required_chars=["的", "囍"], base=BASE)
        self.assertEqual(outcome.missing, ["囍"])

    def test_zrmdb_lines_format(self) -> None:
        table = {"的": AuxiliaryEntry("un", "", "ui"), "一": AuxiliaryEntry("fi")}
        self.assertEqual(zrmdb_lines(table), "的\tun ui\n一\tfi\n")


if __name__ == "__main__":
    unittest.main()
