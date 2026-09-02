import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import rewards_tasks


class FakeRewardsTasks:
    complete_all_tasks = rewards_tasks.RewardsTaskUtils.complete_all_tasks

    def __init__(self, fail_required=False, enable_mobile=False):
        self.calls = []
        self.fail_required = fail_required
        self.enable_mobile = enable_mobile
        self.tab_utils = type("Tabs", (), {"close_all_other_tabs": lambda self: None})()

    def _record(self, name):
        self.calls.append(name)

    def complete_required_searches(self):
        self._record("Required searches")
        if self.fail_required:
            raise rewards_tasks.TimeoutException("breakdown did not open")

    def complete_bing_daily_set(self):
        self._record("Bing daily set")

    def complete_explore_on_bing_tasks(self):
        self._record("Explore on Bing")

    def complete_visual_search(self):
        self._record("Visual search")

    def complete_misc_cards(self):
        self._record("Misc cards")

    def complete_mobile_searches(self):
        self._record("Mobile searches")

    def complete_read_to_earn(self):
        self._record("Read to Earn")

    def claim_bonus_points(self):
        self._record("Bonus points")


class CompleteAllTasksTests(unittest.TestCase):
    @patch.object(rewards_tasks, "human_idle_delay", lambda *args, **kwargs: None)
    @patch.object(rewards_tasks.random, "shuffle", lambda items: items.reverse())
    def test_required_searches_always_run_first(self):
        fake = FakeRewardsTasks()

        fake.complete_all_tasks()

        self.assertEqual(fake.calls[0], "Required searches")

    @patch.object(rewards_tasks, "human_idle_delay", lambda *args, **kwargs: None)
    @patch.object(rewards_tasks.random, "shuffle", lambda items: None)
    def test_returns_skipped_status_for_required_search_timeout(self):
        fake = FakeRewardsTasks(fail_required=True)

        results = fake.complete_all_tasks()

        self.assertEqual(results["Required searches"], "skipped")


    @patch.object(rewards_tasks, "human_idle_delay", lambda *args, **kwargs: None)
    @patch.object(rewards_tasks.random, "shuffle", lambda items: None)
    def test_mobile_tasks_run_when_enabled(self):
        fake = FakeRewardsTasks(enable_mobile=True)

        results = fake.complete_all_tasks()

        self.assertIn("Mobile searches", fake.calls)
        self.assertIn("Read to Earn", fake.calls)
        self.assertEqual(results["Read to Earn"], "ok")

    @patch.object(rewards_tasks, "human_idle_delay", lambda *args, **kwargs: None)
    @patch.object(rewards_tasks.random, "shuffle", lambda items: None)
    def test_mobile_tasks_absent_when_disabled(self):
        fake = FakeRewardsTasks(enable_mobile=False)

        fake.complete_all_tasks()

        self.assertNotIn("Mobile searches", fake.calls)
        self.assertNotIn("Read to Earn", fake.calls)


if __name__ == "__main__":
    unittest.main()
