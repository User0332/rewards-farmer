import _bootstrap  # noqa: F401

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
from test_stats import StatsTests
from test_stats_counted import counted_record
from test_stats_progress import record, write_record
from rfapp import runs
import stats_ledger


class RunTaskPointsTests(unittest.TestCase):
	setUp = StatsTests.setUp
	get = StatsTests.get

	def detail(self, run):
		run_id, events = run
		write_record(self.folder, run_id, events)
		self.now = datetime(2026, 10, 19, 20, tzinfo=timezone.utc)
		self.active = ["sample"]
		return runs.load_run(run_id, {}, str(self.home / "profiles"))

	def points(self, account):
		return {task["task"]: task["points"] for task in account["tasks"]}

	def test_counted_run_uses_stats_attribution_including_covered_late_search_credit(self):
		account = self.detail(counted_record())["accounts"][0]
		points = self.points(account)
		self.assertEqual(points, {"daily_set": 30, "explore_on_bing": 0, "visual_search": 5,
			"misc_cards": 0, "quests": 10, "searches": 60, "bonus": 0})
		self.assertEqual(points, self.get()["sources"]["points"])
		self.assertEqual(sum(points.values()), account["points_earned"])
		search = account["tasks"][5]
		self.assertEqual((search["points_before"], search["points_after"]), (1045, 1102))
		self.assertEqual(account["points_earned"], 105)

	def test_missing_pair_keeps_other_measured_tasks_without_assigning_outside_points(self):
		run_id, events = counted_record()
		next(event for event in events if event.get("task") == "visual_search")["points_after"] = None
		next(event for event in events if event.get("task") == "misc_cards")["points_before"] = None
		account = self.detail((run_id, events))["accounts"][0]
		self.assertEqual(self.points(account), {"daily_set": 30, "explore_on_bing": 0, "visual_search": None,
			"misc_cards": None, "quests": 10, "searches": 57, "bonus": 0})
		self.assertEqual(account["points_earned"], 105)
		self.assertEqual(self.get()["sources"]["runs"], 0)

	def test_all_missing_task_readings_stay_null_even_with_known_run_total(self):
		run_id, events = counted_record()
		for event in events:
			if event.get("type") == "task_end":
				event.update(points_before=None, points_after=None)
		account = self.detail((run_id, events))["accounts"][0]
		self.assertEqual(self.points(account), dict.fromkeys(stats_ledger.TASKS))
		self.assertEqual(account["points_earned"], 105)

	def test_uncovered_remainder_is_not_added_to_searches(self):
		account = self.detail(counted_record(late=4))["accounts"][0]
		self.assertEqual(self.points(account)["searches"], 57)
		self.assertEqual(sum(self.points(account).values()), 102)
		self.assertEqual(account["points_earned"], 106)
		self.assertEqual(self.get()["sources"]["runs"], 0)

	def test_turned_off_and_absent_tasks_stay_null_even_with_readings(self):
		account = self.detail(record(before=1000, after=1005, earned_after=5,
			windows=(("visual_search", 1000, 1005), ("bonus", 1005, 1005, "off", "turned_off"))))["accounts"][0]
		expected = dict.fromkeys(stats_ledger.TASKS)
		expected["visual_search"] = 5
		self.assertEqual(self.points(account), expected)
		self.assertEqual(sum(value for value in self.points(account).values() if value is not None), account["points_earned"])
		self.assertEqual(account["tasks"][-1]["status"], "off")
		self.assertEqual(account["tasks"][0]["status"], "not_run")

	def test_multi_account_uses_each_accounts_eligibility_without_borrowing_credit(self):
		run_id, events = counted_record()
		events[0]["accounts"].append("second")
		copies = [{**event, "account": "second", **({"profile": "invented-profile/second"} if event["type"] == "account_start" else {})}
			for event in events if event.get("account") == "sample"]
		next(event for event in copies if event.get("task") == "visual_search")["points_after"] = None
		next(event for event in copies if event.get("task") == "misc_cards")["points_before"] = None
		detail = self.detail((run_id, events + copies))
		one, two = detail["accounts"]
		self.assertEqual((self.points(one)["searches"], self.points(two)["searches"]), (60, 57))
		self.assertEqual(sum(self.points(one).values()), one["points_earned"])
		self.assertIsNone(self.points(two)["visual_search"])
		self.assertIsNone(self.points(two)["misc_cards"])
		self.assertEqual((one["points_earned"], two["points_earned"], detail["points_earned"]), (105, 105, 210))

	def test_noninteger_or_negative_gain_is_not_a_task_measurement(self):
		for begin, end in ((None, 1030), (1000, None), (True, 1030), (1000, False), (1000.0, 1030), (1000, "1030"), (1030, 1000)):
			with self.subTest(begin=begin, end=end):
				account = self.detail(record(windows=(("daily_set", begin, end),)))["accounts"][0]
				self.assertIsNone(self.points(account)["daily_set"])

	def test_active_pending_and_started_tasks_have_null_points(self):
		run_id, events = record(windows=(), phase_after=False, result=None)
		events.append({"v": 1, "type": "task_start", "run": run_id, "ts": "2026-10-16T08:00:04Z",
			"account": "sample", "task": "daily_set", "points_before": 1000})
		self.detail((run_id, events))
		with mock.patch.object(runs, "active_run_id", return_value=run_id):
			detail = runs.load_run(run_id, {}, str(self.home / "profiles"))
		self.assertTrue(detail["active"])
		self.assertEqual(detail["accounts"][0]["tasks"][0]["status"], "running")
		self.assertEqual(self.points(detail["accounts"][0]), dict.fromkeys(stats_ledger.TASKS))


del StatsTests
