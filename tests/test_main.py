import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import main


class CriticalTaskStatusTests(unittest.TestCase):
    def test_required_search_skip_is_not_success(self):
        self.assertFalse(main.critical_tasks_succeeded({"Required searches": "skipped"}))

    def test_required_search_success_is_success(self):
        self.assertTrue(main.critical_tasks_succeeded({"Required searches": "ok"}))


if __name__ == "__main__":
    unittest.main()
