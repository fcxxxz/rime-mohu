import unittest
from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/build.yml"


class FlatReleaseWorkflowTest(unittest.TestCase):
    def test_workflow_publishes_only_flat_scheme_archives_and_v5_asset(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for expected in (
            "make dist-zrm dist-flypy",
            "rime-mohu-zrm-${{ github.run_number }}-${{ github.run_attempt }}",
            "rime-mohu-flypy-${{ github.run_number }}-${{ github.run_attempt }}",
            "rime-mohu-zrm-latest.zip",
            "rime-mohu-flypy-latest.zip",
            "mohu-sentence-ngram-v5.bin",
            "mohu/model/",
        ):
            self.assertIn(expected, workflow)

        self.assertNotIn("Qwen", workflow)
        self.assertNotIn("install_mohu", workflow)
        self.assertNotIn("dist-mohu-llm", workflow)
        upload = workflow.split("gh release upload", 1)[1]
        self.assertNotIn("mohu-llm-zrm", upload)
        self.assertNotIn("mohu-llm-flypy", upload)


if __name__ == "__main__":
    unittest.main()
