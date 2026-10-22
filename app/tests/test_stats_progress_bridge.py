import _bootstrap  # noqa: F401

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
from test_stats import StatsTests
from test_stats_progress import record, write_record
from rfapp import runs
import stats_ledger


class ProgressBridgeTests(unittest.TestCase):
	setUp = StatsTests.setUp
	get = StatsTests.get

	def put(self, *args, **kwargs):
		run_id, events = record(*args, **kwargs)
		write_record(self.folder, run_id, events)
		self.active = ["sample"]
		return run_id, events

	def test_old_unassigned_patterns_keep_day_totals_but_leave_counted_sources_empty(self):
		self.now = datetime(2026, 10, 18, 20, tzinfo=timezone.utc)
		one, _ = self.put("2026-10-16T08:00:00Z", before=1000, after=1041, earned_after=41, windows=(
			("daily_set", 1000, 1030), ("visual_search", 1030, None), ("misc_cards", None, 1038), ("searches", 1038, 1038)))
		two, _ = self.put("2026-10-16T10:00:00Z", before=1041, after=1125, earned_before=41, earned_after=125, windows=(
			("daily_set", 1041, 1071), ("visual_search", 1071, None), ("misc_cards", None, 1091), ("quests", 1091, 1101), ("searches", 1101, 1125)))
		three, _ = self.put("2026-10-17T08:00:00Z", before=1125, after=1212, earned_after=87,
			windows=tuple((task, None, None, "fail" if task == "searches" else "ok", "element_missing" if task == "searches" else None) for task in stats_ledger.TASKS), result="partial")
		four, _ = self.put("2026-10-18T08:00:00Z", before=1212, after=None, earned_after=98,
			windows=tuple((task, None, None) for task in stats_ledger.TASKS))
		value = self.get({"range": 7})
		self.assertEqual(value["sources"], {"points": dict.fromkeys(stats_ledger.TASKS, 0), "runs": 0})
		days = value["days"][-3:]
		self.assertEqual([(day["earned"], day["open_points"], day["possible"]) for day in days], [(125, 0, 125), (87, 0, 87), (98, 0, 98)])
		self.assertEqual([len(day["runs"]) for day in days], [2, 1, 1])
		self.assertEqual(days[1]["runs"][0]["problems"], [{"task": "searches", "reason_key": "errors.task.element_missing"}])
		self.assertEqual(days[2]["runs"][0]["points_earned"], 98)
		for day in days:
			for summary in day["runs"]:
				detail = runs.load_run(summary["run_id"], {}, str(self.home / "profiles"))
				for field in ("run_id", "started_at", "kind", "trigger", "active", "result", "result_key", "points_earned"):
					self.assertEqual(summary[field], detail[field], (summary["run_id"], field))

	def test_check_only_day_crash_account_error_and_active_run(self):
		self.now = datetime(2026, 10, 18, 20, tzinfo=timezone.utc)
		check, _ = self.put("2026-10-16T08:00:00Z", before=1000, after=1000, earned_before=0, earned_after=0, check=True, result="skipped")
		crash, _ = self.put("2026-10-17T08:00:00Z", result="crashed", phase_after=False)
		error, _ = self.put("2026-10-18T08:00:00Z", result="partial", error="error")
		active, events = self.put("2026-10-18T10:00:00Z", result=None, phase_after=False)
		events[0]["supervisor_pid"] = 123
		write_record(self.folder, active, events)
		with mock.patch.object(stats_ledger, "_created", side_effect=lambda pid: 100 if pid == 123 else None):
			value = self.get()
		days = {day["date"]: day for day in value["days"]}
		self.assertEqual(days["2026-10-16"]["runs"][0]["kind"], "check")
		self.assertEqual(days["2026-10-16"]["runs"][0]["problems"], [])
		self.assertEqual(days["2026-10-17"]["runs"][0]["problems"], [{"task": None, "reason_key": "errors.run.crashed"}])
		self.assertEqual(days["2026-10-18"]["runs"][0]["problems"], [{"task": None, "reason_key": "errors.run.failed"}])
		self.assertEqual(days["2026-10-18"]["runs"][1]["run_id"], active)
		self.assertTrue(days["2026-10-18"]["runs"][1]["active"])
		self.assertIsNone(days["2026-10-18"]["runs"][1]["result"])
		self.assertIsNone(days["2026-10-18"]["runs"][1]["result_key"])
		self.assertTrue(all(day["runs"] == [] for day in value["days"] if day["date"] < value["coverage_from"]))
		import json
		persisted = json.loads((self.home / "stats-ledger.json").read_text(encoding="utf-8"))
		self.assertNotIn(active, persisted["index"])
		self.assertFalse(any(run["active"] for row in persisted["rows"] for run in row["runs"] or []))

	def test_latest_unreadable_core_does_not_replace_progress_and_unknown_possible_stays_null(self):
		self.now = datetime(2026, 10, 18, 20, tzinfo=timezone.utc)
		self.put("2026-10-18T08:00:00Z", before=1000, after=1030, earned_after=30, open_points=20, quests=2)
		run_id, events = self.put("2026-10-18T10:00:00Z", before=1030, after=1040, earned_before=30, earned_after=40, open_points=0, quests=0)
		for event in events:
			if event["type"] == "state":
				event["unreadable"] = "searches"
		write_record(self.folder, run_id, events)
		day = self.get()["days"][-1]
		self.assertEqual((day["earned"], day["open_points"], day["quests_open"], day["possible"]), (40, 20, 2, 60))
		self.assertIsNone(self.get()["days"][-2]["possible"])

	def test_multi_account_run_list_merges_selected_contributions_once(self):
		self.now = datetime(2026, 10, 18, 20, tzinfo=timezone.utc)
		run_id, events = self.put("2026-10-18T08:00:00Z", before=1000, after=1030, earned_after=30,
			windows=(("searches", 1000, 1030, "fail", "quota_short"),), result="partial")
		events[0]["accounts"].append("second")
		copies = [{**event, "account": "second", **({"profile": "invented-profile/second"} if event["type"] == "account_start" else {})}
			for event in events if event.get("account") == "sample"]
		events[-1:-1] = copies
		write_record(self.folder, run_id, events)
		self.active = ["sample", "second"]
		day = self.get()["days"][-1]
		self.assertEqual(len(day["runs"]), 1)
		self.assertEqual(day["runs"][0]["points_earned"], 60)
		self.assertEqual(day["runs"][0]["problems"], [{"task": "searches", "reason_key": "errors.task.quota_short"}] * 2)
		self.assertEqual(day["possible"], 60)
		self.assertEqual(self.get({"account_id": "sample"})["days"][-1]["runs"][0]["points_earned"], 30)

	def test_crash_before_account_start_has_a_run_link_without_invented_account_values(self):
		self.now = datetime(2026, 10, 18, 20, tzinfo=timezone.utc)
		run_id, events = record("2026-10-18T08:00:00Z", result="crashed")
		events = [event for event in events if event["type"] in ("run_start", "run_end")]
		events[0]["accounts"] = []
		write_record(self.folder, run_id, events)
		day = self.get()["days"][-1]
		self.assertIsNone(day["earned"])
		self.assertIsNone(day["possible"])
		self.assertEqual(day["runs"][0]["problems"], [{"task": None, "reason_key": "errors.run.crashed"}])

	def test_first_active_run_is_listed_without_advancing_coverage_or_daily_totals(self):
		self.now = datetime(2026, 10, 18, 20, tzinfo=timezone.utc)
		run_id, events = self.put("2026-10-18T08:00:00Z", result=None, phase_after=False)
		events[0]["supervisor_pid"] = 123
		write_record(self.folder, run_id, events)
		with mock.patch.object(stats_ledger, "_created", side_effect=lambda pid: 100 if pid == 123 else None):
			value = self.get()
		self.assertIsNone(value["coverage_from"])
		self.assertIsNone(value["totals"]["earned"])
		self.assertEqual(value["totals"]["run_count"], 0)
		self.assertEqual(value["days"][-1]["runs"][0]["run_id"], run_id)
		self.assertTrue(value["days"][-1]["runs"][0]["active"])

	def test_producer_shape_passes_shared_mock_contract_and_aliases_fail(self):
		from test_mock_shapes import stats_problems
		self.put("2026-10-16T08:00:00Z", windows=(("visual_search", 1000, None), ("misc_cards", None, 1100)))
		value = self.get()
		self.assertEqual(stats_problems(value), [])
		value["sources"]["count"] = value["sources"].pop("runs")
		self.assertTrue(stats_problems(value))


del StatsTests
