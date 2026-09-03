from __future__ import annotations

import importlib.util
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
SPEC = importlib.util.spec_from_file_location(
    "write_if_changed", ROOT / "tools" / "write_if_changed.py"
)
assert SPEC and SPEC.loader
write_if_changed = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = write_if_changed
SPEC.loader.exec_module(write_if_changed)


def run_tool(target: Path, text: str, *args: str) -> int:
    stdin = io.StringIO(text)
    with mock.patch.object(sys, "stdin", stdin), redirect_stdout(io.StringIO()):
        return write_if_changed.main([str(target), *args])


class WriteIfChangedTest(unittest.TestCase):
    def test_writes_when_target_missing(self) -> None:
        with TemporaryDirectory() as temp:
            target = Path(temp) / "out.dict.yaml"
            self.assertEqual(run_tool(target, "hello\n"), 0)
            self.assertEqual(target.read_text(encoding="utf-8"), "hello\n")

    def test_writes_when_content_differs(self) -> None:
        with TemporaryDirectory() as temp:
            target = Path(temp) / "out.dict.yaml"
            target.write_text("a\n", encoding="utf-8")
            self.assertEqual(run_tool(target, "b\n", "--ignore-version"), 0)
            self.assertEqual(target.read_text(encoding="utf-8"), "b\n")

    def test_keeps_file_when_only_version_differs(self) -> None:
        with TemporaryDirectory() as temp:
            target = Path(temp) / "out.dict.yaml"
            target.write_text('version: "20260831"\nx\n', encoding="utf-8")
            before = target.read_bytes()
            self.assertEqual(
                run_tool(target, 'version: "20260903"\nx\n', "--ignore-version"), 0
            )
            self.assertEqual(target.read_bytes(), before)

    def test_rewrites_on_version_only_without_flag(self) -> None:
        with TemporaryDirectory() as temp:
            target = Path(temp) / "out.dict.yaml"
            target.write_text('version: "20260831"\n', encoding="utf-8")
            self.assertEqual(run_tool(target, 'version: "20260903"\n'), 0)
            self.assertEqual(target.read_text(encoding="utf-8"), 'version: "20260903"\n')

    def test_keeps_file_when_identical(self) -> None:
        with TemporaryDirectory() as temp:
            target = Path(temp) / "out.dict.yaml"
            target.write_text("same\n", encoding="utf-8")
            before = target.read_bytes()
            self.assertEqual(run_tool(target, "same\n"), 0)
            self.assertEqual(target.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
