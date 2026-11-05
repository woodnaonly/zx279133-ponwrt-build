"""Offline JS behavior, not visual browser or hardware acceptance."""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PageConsentTests(unittest.TestCase):
    def test_consent_is_cleared_when_session_image_task_or_package_changes(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required for the offline JavaScript safety test")
        subprocess.run([node, str(ROOT/"tests/page_consent.cjs"), str(ROOT/"web/index.html")],
                       check=True, timeout=10, capture_output=True, text=True, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
