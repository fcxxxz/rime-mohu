import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SCHEMAS = {
    "zrm": ["mohu_zrm"],
    "flypy": ["mohu_flypy"],
}


def schema_ids(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return re.findall(r"^\s*- schema: (\S+)\s*$", text, re.MULTILINE)


class SplitDistributionTest(unittest.TestCase):
    def test_split_output_directories_are_ignored(self) -> None:
        for output in ("dist-zrm", "dist-flypy"):
            with self.subTest(output=output):
                result = subprocess.run(
                    ["git", "check-ignore", "-q", output],
                    cwd=ROOT,
                    check=False,
                )
                self.assertEqual(0, result.returncode)

    def test_split_distributions_build_with_isolated_schemas(self) -> None:
        for scheme in EXPECTED_SCHEMAS:
            output = ROOT / f"dist-{scheme}"
            if output.exists():
                shutil.rmtree(output)

        result = subprocess.run(
            ["make", "dist-zrm", "dist-flypy"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

        for scheme, expected_schemas in EXPECTED_SCHEMAS.items():
            with self.subTest(scheme=scheme):
                output = ROOT / f"dist-{scheme}"
                other = "flypy" if scheme == "zrm" else "zrm"

                self.assertEqual(expected_schemas, schema_ids(output / "default.yaml"))
                schema_entries = (
                    f"mohu_{scheme}_core.schema.yaml",
                    f"mohu_{scheme}_fixed.schema.yaml",
                    f"mohu_{scheme}_fixed_legacy.schema.yaml",
                    f"mohu_{scheme}_sentence_core.schema.yaml",
                )
                for relative in (
                    f"mohu_{scheme}.schema.yaml",
                    *schema_entries,
                    f"mohu_{scheme}.extended.dict.yaml",
                    f"mohu_{scheme}.classics.dict.yaml",
                    f"mohu_{scheme}.base.dict.yaml",
                    f"mohu_{scheme}.chars.dict.yaml",
                    f"mohu_{scheme}_fixed.dict.yaml",
                    f"mohu_{scheme}_fixed_legacy.dict.yaml",
                    "mohu.yaml",
                    "mohu_charset.schema.yaml",
                    "mohu_pinyin.schema.yaml",
                    "tiger.schema.yaml",
                    "lua/zrmdb.txt",
                    "opencc/mohu_chaifen.ocd2",
                    "opencc/mohu_emoji.ocd2",
                ):
                    self.assertTrue((output / relative).is_file(), relative)

                self.assertEqual([], sorted(output.glob(f"mohu_{other}*")))
                self.assertTrue(
                    (output / "lua" / f"four_code_yield_pairs_{scheme}.txt").is_file()
                )
                self.assertFalse(
                    (output / "lua" / f"four_code_yield_pairs_{other}.txt").exists()
                )
                self.assertEqual([], sorted(output.glob("*.userdb*")))
                self.assertFalse((output / "zh-hans-t-essay-bgw.gram").exists())
                self.assertFalse((output / "zh-hans-t-essay-bgc.gram").exists())


if __name__ == "__main__":
    unittest.main()
