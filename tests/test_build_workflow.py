from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BuildWorkflowTest(unittest.TestCase):
    def test_windows_job_installs_make_before_native_probe(self) -> None:
        workflow = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
        setup_start = workflow.index("uses: msys2/setup-msys2@v2")
        setup_end = workflow.index("- name: Build Lua", setup_start)
        setup = workflow[setup_start:setup_end]

        self.assertIn("make", workflow[setup_end:])
        self.assertRegex(setup, r"(?m)^\s+make\s*$")


if __name__ == "__main__":
    unittest.main()
