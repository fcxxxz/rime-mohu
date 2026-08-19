from __future__ import annotations

import subprocess
import sys
import unittest


class QqDictionaryDependenciesTest(unittest.TestCase):
    def test_batch_runtime_can_import_yaml(self) -> None:
        result = subprocess.run(
            [sys.executable, "-c", "import yaml"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
