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

    def test_windows_runtime_smoke_covers_snapshot_replacement(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        windows_job = workflow.split("  windows-runtime:", 1)[1].split(
            "\n  build:", 1
        )[0]

        self.assertIn("engine.atomic_write_snapshot_file", windows_job)
        self.assertIn("engine.read_snapshot_file", windows_job)
        self.assertIn("make tigerengine-snapshot-io", windows_job)

    def test_release_packages_preserve_snapshot_parent_directory(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("test -f dist-zrm/mohu/config/README.md", workflow)
        self.assertIn("test -f dist-flypy/mohu/config/README.md", workflow)
        self.assertIn(
            'unzip -Z1 "$archive" | grep -Fx "mohu/config/README.md"', workflow
        )

    def test_windows_smoke_test_fetches_model_zip_via_gh_api(self) -> None:
        # 2026-09-17 起 runner 直连 release CDN 全线 404，且 latest 上的模型
        # 资产已改为 zip 形态：冒烟模型经 gh api 下载 zip 并解压出裸 bin，
        # curl 官方直链仅作为 ort 的后备路径。
        workflow = WORKFLOW.read_text(encoding="utf-8")
        windows_job = workflow.split("  windows-runtime:", 1)[1].split(
            "\n  build:", 1
        )[0]

        self.assertIn('--pattern \'mohu-sentence-ngram-v5.bin.zip\'', windows_job)
        self.assertIn("--pattern 'mohu-sentence-ngram-v5.bin.zip'", windows_job)
        self.assertIn("python -m zipfile -e model.zip .", windows_job)
        self.assertIn("test -s mohu-sentence-ngram-v5.bin", windows_job)
        # gh 在 msys2 里用绝对路径兜底（不继承 Windows PATH）。
        self.assertIn("GH_BIN=\"$(command -v gh || echo", windows_job)

    def test_windows_runtime_collection_uses_msys2_tools_from_path(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        windows_job = workflow.split("  windows-runtime:", 1)[1].split(
            "\n  build:", 1
        )[0]
        collection_step = windows_job.split(
            "      - name: Collect Windows runtime closure", 1
        )[1].split("\n      - name:", 1)[0]

        self.assertIn("mingw-w64-x86_64-python", windows_job)
        self.assertIn("shell: msys2 {0}", collection_step)
        self.assertIn("python tools/collect_windows_runtime.py", collection_step)
        self.assertIn("--objdump objdump", collection_step)
        self.assertNotIn("TIGER_ENGINE_DLL", collection_step)
        self.assertNotIn("shell: pwsh", windows_job)
        self.assertNotIn(r"C:\msys64", windows_job)


if __name__ == "__main__":
    unittest.main()
