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
            "Rime同步助手",
            "解除隔离.command",
            "find dist-zrm dist-flypy -type f -name '*.command' -exec chmod +x {} +",
        ):
            self.assertIn(expected, workflow)

        self.assertNotIn("Qwen", workflow)
        self.assertNotIn("install_mohu", workflow)
        self.assertNotIn("dist-mohu-llm", workflow)
        self.assertIn("rime-mohu-qwen3-0.6b.zip", workflow)
        self.assertIn("rime-mohu-qwen35-0.8b.zip", workflow)
        upload = workflow.split("gh release upload", 1)[1]
        self.assertNotIn("mohu-llm-zrm", upload)
        self.assertNotIn("mohu-llm-flypy", upload)
        self.assertNotIn("qwen", upload.lower())

    def test_workflow_builds_and_packages_windows_runtime_closure(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for expected in (
            "windows-runtime:",
            "runs-on: windows-latest",
            "msys2/setup-msys2@v2",
            "mingw-w64-x86_64-toolchain",
            "shell: msys2 {0}",
            "lua-5.4.8",
            "4f18ddae154e793e46eeab727c59ef1c0c0c2b744e7b94219710d76f530629ae",
            "tools/collect_windows_runtime.py",
            "runtime-preload.txt",
            "mohu-windows-runtime-${{ github.run_number }}-${{ github.run_attempt }}",
            "needs: windows-runtime",
            "path: windows-runtime",
            "TIGER_WINDOWS_RUNTIME=windows-runtime",
            "runtime-manifest.json",
        ):
            self.assertIn(expected, workflow)
        self.assertNotIn("TIGER_ENGINE_DLL", workflow)

    def test_windows_smoke_test_downloads_model_without_github_cli(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        windows_job = workflow.split("  windows-runtime:", 1)[1].split(
            "\n  build:", 1
        )[0]

        self.assertIn("curl --fail --location", windows_job)
        self.assertIn("--retry 3 --retry-all-errors", windows_job)
        self.assertIn(
            "https://github.com/${GITHUB_REPOSITORY}/releases/download/latest/"
            "mohu-sentence-ngram-v5.bin",
            windows_job,
        )
        self.assertNotIn("gh release download", windows_job)


if __name__ == "__main__":
    unittest.main()
