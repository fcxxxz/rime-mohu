from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.collect_windows_runtime import (
    RuntimeClosureError,
    collect_runtime,
    parse_imports,
)


class WindowsRuntimeClosureTest(unittest.TestCase):
    def write_runtime_files(self, root: Path, *names: str) -> dict[str, Path]:
        files = {}
        for name in names:
            path = root / name
            path.write_bytes(name.encode("ascii"))
            files[name.casefold()] = path
        return files

    def test_collects_recursive_non_host_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            runtime.mkdir()
            files = self.write_runtime_files(
                runtime, "libtigerengine.dll", "alpha.dll", "beta.dll"
            )
            imports = {
                "libtigerengine.dll": ["ALPHA.dll", "KERNEL32.dll"],
                "alpha.dll": ["beta.dll", "rime.dll"],
                "beta.dll": ["api-ms-win-core-file-l1-1-0.dll"],
            }

            result = collect_runtime(
                files["libtigerengine.dll"],
                [runtime],
                root / "output",
                import_reader=lambda path: imports[path.name.casefold()],
            )

            self.assertEqual(
                {"libtigerengine.dll", "alpha.dll", "beta.dll"},
                {path.name for path in result.files},
            )
            self.assertEqual(
                {
                    "libtigerengine.dll",
                    "alpha.dll",
                    "beta.dll",
                    "runtime-manifest.json",
                    "runtime-preload.txt",
                },
                {path.name for path in (root / "output").iterdir()},
            )
            manifest = json.loads((root / "output" / "runtime-manifest.json").read_text())
            self.assertEqual("libtigerengine.dll", manifest["entry"])
            self.assertEqual(
                [
                    {"from": "alpha.dll", "to": "beta.dll"},
                    {"from": "libtigerengine.dll", "to": "alpha.dll"},
                ],
                manifest["imports"],
            )
            self.assertEqual(["beta.dll", "alpha.dll"], manifest["preload"])
            self.assertEqual(
                "beta.dll\nalpha.dll\n",
                (root / "output" / "runtime-preload.txt").read_text(),
            )

    def test_reports_the_complete_chain_for_missing_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = self.write_runtime_files(root, "libtigerengine.dll", "alpha.dll")
            imports = {
                "libtigerengine.dll": ["alpha.dll"],
                "alpha.dll": ["missing.dll"],
            }

            with self.assertRaisesRegex(
                RuntimeClosureError,
                r"libtigerengine\.dll -> alpha\.dll -> missing\.dll",
            ):
                collect_runtime(
                    files["libtigerengine.dll"],
                    [root],
                    root / "output",
                    import_reader=lambda path: imports[path.name.casefold()],
                )
            self.assertFalse((root / "output").exists())

    def test_parse_imports_reads_only_pe_import_records(self) -> None:
        output = """\
DLL Name: KERNEL32.dll
    DLL Name: alpha.dll
not an import: ignored.dll
"""
        self.assertEqual(["KERNEL32.dll", "alpha.dll"], parse_imports(output))


if __name__ == "__main__":
    unittest.main()
