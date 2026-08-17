from pathlib import Path
import unittest


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
            "gh release delete-asset latest rime-mohu-latest.zip",
        ):
            self.assertIn(expected, workflow)

        self.assertNotIn("zip -r ../rime-mohu-latest.zip .", workflow)


if __name__ == "__main__":
    unittest.main()
