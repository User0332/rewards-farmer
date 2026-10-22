import _bootstrap  # noqa: F401

from datetime import datetime, time as day_time, timedelta, timezone
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
from test_stats import StatsTests
from test_stats_progress import record, write_record
import stats_ledger


class DaySourcesTests(unittest.TestCase):
	setUp = StatsTests.setUp
	get = StatsTests.get

	def ledger(self):
		self.now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
		self.active, self.paused = ["first"], ["second"]
		today = self.now.astimezone().date()
		value = stats_ledger._empty(self.now)
		value["coverage_from"] = (today - timedelta(days=60)).isoformat()
		for number, offset in enumerate((0, 3, 6, 7, 29, 30, 60), 1):
			date = (today - timedelta(days=offset)).isoformat()
			shared = date.replace("-", "") + f"T080000Z-{number:04x}"
			for account, factor in (("first", number), ("second", number * 10), ("@run", 10000), ("retired", 100000)):
				row = stats_ledger._row(date, account, None, None)
				row.update(earned=factor * 28, balance=1000 + factor * 28, balance_at=date + "T12:00:00Z",
					counted_sources={task: factor * (index + 1) for index, task in enumerate(stats_ledger.TASKS)},
					_counted_runs=[shared], _runs=[shared], run_ids=[shared])
				value["rows"].append(row)
		# An observed day can have earned points and runs without counted credit.
		date = (today - timedelta(days=1)).isoformat()
		row = stats_ledger._row(date, "first", None, None)
		row.update(earned=999, _runs=["20261019T080000Z-ffff"], run_ids=["20261019T080000Z-ffff"])
		value["rows"].append(row)
		return value

	def empty_sources(self):
		return {"points": dict.fromkeys(stats_ledger.TASKS, 0), "runs": 0}

	def test_each_date_uses_only_selected_accounts_including_paused_and_never_run_rows(self):
		ledger = self.ledger()
		with mock.patch.object(stats_ledger, "fold", return_value=stats_ledger.FoldResult(ledger, True)):
			value = self.get({"range": 30})
		self.assertEqual(value["accounts"][1]["account_id"], "second")
		self.assertTrue(value["accounts"][1]["paused"])
		days = {day["date"]: day for day in value["days"]}
		for number, offset in enumerate((0, 3, 6, 7, 29, 30, 60), 1):
			date = (self.now.astimezone().date() - timedelta(days=offset)).isoformat()
			expected = {"points": {task: number * 11 * (index + 1) for index, task in enumerate(stats_ledger.TASKS)}, "runs": 1}
			self.assertEqual(days[date]["sources"], expected, date)
			self.assertTrue(all(type(points) is int and points >= 0 for points in days[date]["sources"]["points"].values()))
			self.assertIs(type(days[date]["sources"]["runs"]), int)
		for offset in (1, 2, 61, 89):
			date = (self.now.astimezone().date() - timedelta(days=offset)).isoformat()
			self.assertEqual(days[date]["sources"], self.empty_sources(), date)
		self.assertEqual(days[(self.now.astimezone().date() - timedelta(days=61)).isoformat()]["state"], "before_tracking")

	def test_range_sources_equal_day_sums_for_seven_thirty_and_ninety_days(self):
		ledger = self.ledger()
		with mock.patch.object(stats_ledger, "fold", return_value=stats_ledger.FoldResult(ledger, True)):
			for period, factor, count in ((7, 66, 3), (30, 165, 5), (90, 308, 7)):
				with self.subTest(period=period):
					value = self.get({"range": period})
					days = [day for day in value["days"] if value["from"] <= day["date"] <= value["today"]]
					expected = {"points": {task: factor * (index + 1) for index, task in enumerate(stats_ledger.TASKS)}, "runs": count}
					self.assertEqual(value["sources"], expected)
					self.assertEqual({task: sum(day["sources"]["points"][task] for day in days) for task in stats_ledger.TASKS}, expected["points"])
					self.assertEqual(sum(day["sources"]["runs"] for day in days), expected["runs"])

	def test_single_active_or_paused_account_never_borrows_another_accounts_sources(self):
		ledger = self.ledger()
		with mock.patch.object(stats_ledger, "fold", return_value=stats_ledger.FoldResult(ledger, True)):
			for account, factor in (("first", 1), ("second", 10)):
				with self.subTest(account=account):
					value = self.get({"range": 90, "account_id": account})
					self.assertEqual(value["sources"], {"points": {task: factor * 28 * (index + 1) for index, task in enumerate(stats_ledger.TASKS)}, "runs": 7})
					self.assertEqual(value["days"][-1]["sources"], {"points": {task: factor * (index + 1) for index, task in enumerate(stats_ledger.TASKS)}, "runs": 1})
					self.assertEqual(sum(day["sources"]["runs"] for day in value["days"]), 7)
					self.assertEqual({task: sum(day["sources"]["points"][task] for day in value["days"]) for task in stats_ledger.TASKS}, value["sources"]["points"])

	def test_empty_before_tracking_sources_are_zero_and_do_not_share_mutable_points(self):
		value = self.get()
		self.assertTrue(all(day["state"] == "before_tracking" and day["sources"] == self.empty_sources() for day in value["days"]))
		value["days"][0]["sources"]["points"]["searches"] = 100
		self.assertEqual(value["days"][1]["sources"], self.empty_sources())
		self.assertEqual(value["sources"], self.empty_sources())

	def test_counted_zero_gain_is_a_counted_run_not_an_untracked_day(self):
		self.now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
		self.active = ["sample"]
		run_id, events = record("2026-10-20T08:00:00Z", after=1000, earned_after=0, windows=(("searches", 1000, 1000),))
		write_record(self.folder, run_id, events)
		self.assertEqual(self.get()["days"][-1]["sources"], {"points": dict.fromkeys(stats_ledger.TASKS, 0), "runs": 1})

	def test_missing_paused_account_day_keeps_known_sources_when_earned_is_unknown(self):
		self.now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
		self.active, self.paused = ["sample"], ["second"]
		run_id, events = record("2026-10-20T08:00:00Z", after=1009, earned_after=9, windows=(("searches", 1000, 1009),))
		write_record(self.folder, run_id, events)
		value = self.get()
		day = value["days"][-1]
		self.assertIsNone(day["earned"])
		self.assertEqual(day["sources"], {"points": {**dict.fromkeys(stats_ledger.TASKS, 0), "searches": 9}, "runs": 1})
		self.assertEqual(value["sources"], day["sources"])

	def test_cross_midnight_record_has_two_rows_but_counted_sources_only_on_terminal_date(self):
		self.now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
		self.active = ["sample"]
		midnight = datetime.combine(self.now.astimezone().date(), day_time.min).astimezone(timezone.utc)
		run_id, events = record((midnight - timedelta(seconds=4)).isoformat(), seconds=12, after=1009,
			earned_after=9, windows=(("searches", 1000, 1009),))
		write_record(self.folder, run_id, events)
		value = self.get({"range": 7})
		ledger = stats_ledger.fold(str(self.folder), data_home=self.home, user_data_dir=str(self.home / "profiles"), now=self.now).ledger
		rows = [row for row in ledger["rows"] if run_id in row["run_ids"]]
		self.assertEqual(len(rows), 2)
		self.assertEqual([row["date"] for row in rows if run_id in row["_counted_runs"]], [value["today"]])
		self.assertEqual(value["days"][-2]["sources"], self.empty_sources())
		self.assertEqual(value["days"][-1]["sources"], {"points": {**dict.fromkeys(stats_ledger.TASKS, 0), "searches": 9}, "runs": 1})
		self.assertEqual(value["sources"], value["days"][-1]["sources"])
		self.assertEqual(sum(day["sources"]["runs"] for day in value["days"][-7:]), 1)

	def test_pending_run_only_changes_run_list_not_counted_day_or_range_sources(self):
		self.now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
		self.active = ["sample"]
		run_id, events = record("2026-10-20T08:00:00Z", result=None, after=1030, earned_after=30,
			windows=(("searches", 1000, 1030),))
		events[0]["supervisor_pid"] = 123
		write_record(self.folder, run_id, events)
		with mock.patch.object(stats_ledger, "_created", side_effect=lambda pid: 100 if pid == 123 else None):
			value = self.get()
		self.assertTrue(value["days"][-1]["runs"][0]["active"])
		self.assertEqual(value["days"][-1]["sources"], self.empty_sources())
		self.assertEqual(value["sources"], self.empty_sources())


del StatsTests
