from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class QqDictionaryWorkflowTest(unittest.TestCase):
    def test_workflow_contract_is_restricted_and_runs_batch(self) -> None:
        path = ROOT / ".github/workflows/qq-dictionary-batch.yml"
        text = path.read_text(encoding="utf-8")
        workflow = yaml.safe_load(text)
        trigger = workflow.get(True, workflow.get("on"))
        self.assertTrue(trigger["workflow_dispatch"]["inputs"]["payload"]["required"])
        self.assertEqual(workflow["permissions"], {"contents": "write"})
        self.assertIn('ref: "${{ github.ref }}"', text)
        self.assertIn("tools/qq_dictionary_batch.py", text)
        self.assertIn("make quick", text)
        self.assertIn("git diff --check", text)
        self.assertIn("git push origin HEAD:${GITHUB_REF_NAME}", text)
        self.assertNotIn("pull-requests: write", text)
        self.assertNotIn("echo ${{ inputs.payload }}", text)


if __name__ == "__main__":
    unittest.main()
