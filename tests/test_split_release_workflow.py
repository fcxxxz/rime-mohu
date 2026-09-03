import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/build.yml"


class SplitReleaseWorkflowTest(unittest.TestCase):
    def test_workflow_publishes_only_split_archives(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")

        for expected in (
            "make dist-zrm dist-flypy",
            "rime-mohu-zrm-${{ github.run_number }}-${{ github.run_attempt }}",
            "rime-mohu-flypy-${{ github.run_number }}-${{ github.run_attempt }}",
            "rime-mohu-zrm-latest.zip",
            "rime-mohu-flypy-latest.zip",
            "dist-mohu-llm-zrm/base/mohu_zrm.schema.yaml",
            "dist-mohu-llm-flypy/base/mohu_flypy.schema.yaml",
            "dist-mohu-llm-zrm/base/opencc/mohu_emoji.json",
            "dist-mohu-llm-flypy/base/opencc/mohu_emoji.json",
            "dist-mohu-llm-zrm/base/lua/zrmdb.txt",
            "dist-mohu-llm-flypy/base/lua/zrmdb.txt",
            "dist-mohu-llm-zrm/base/opencc/mohu_chaifen.ocd2",
            "dist-mohu-llm-flypy/base/opencc/mohu_chaifen.ocd2",
            "dist-mohu-llm-zrm/runtime/libtigerengine.dll",
            "dist-mohu-llm-flypy/runtime/lua54.dll",
            "./tests/test_mohu_llm_windows.ps1",
            "gh release delete-asset latest rime-mohu-latest.zip",
        ):
            self.assertIn(expected, workflow)

        self.assertEqual(2, workflow.count("./tests/test_mohu_llm_windows.ps1"))
        self.assertIn("shell: pwsh", workflow)
        self.assertIn("shell: powershell", workflow)
        self.assertNotIn("zip -r ../rime-mohu-latest.zip .", workflow)


if __name__ == "__main__":
    unittest.main()
