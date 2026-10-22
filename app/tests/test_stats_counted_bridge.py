import _bootstrap  # noqa: F401

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
from test_stats import StatsTests
from test_stats_counted import counted_record
from test_stats_progress import record, write_record
from rfapp import runs
import stats_ledger


class CountedBridgeTests(unittest.TestCase):
	setUp = StatsTests.setUp
	get = StatsTests.get

	def put(self, run):
		run_id, events = run
		write_record(self.folder, run_id, events)
		self.active = ["sample"]
		return run_id, events

	def test_supplied_patterns_only_count_complete_run_and_preserve_history_totals(self):
		self.now = datetime(2026, 10, 19, 20, tzinfo=timezone.utc)
		counted, _ = self.put(counted_record())
		for stamp, high, all_missing in (("2026-10-16T08:00:00Z", 60, False), ("2026-10-16T10:00:00Z", 0, False), ("2026-10-17T08:00:00Z", 60, True)):
			run_id, events = counted_record(stamp, high=high)
			for event in events:
				if event.get("type") == "task_end":
					if all_missing or event["task"] == "visual_search":
						event["points_after"] = None
					if all_missing or event["task"] == "misc_cards":
						event["points_before"] = None
			self.put((run_id, events))
		null_after, _ = self.put(record("2026-10-18T08:00:00Z", after=None, earned_before=0, earned_after=98,
			windows=tuple((task, None, None) for task in stats_ledger.TASKS)))
		later, _ = self.put(record("2026-10-18T10:00:00Z", after=None, earned_before=98, earned_after=108,
			windows=tuple((task, None, None) for task in stats_ledger.TASKS)))
		value = self.get({"range": 7})
		self.assertEqual(value["sources"], {"points": {"daily_set": 30, "explore_on_bing": 0, "visual_search": 5,
			"misc_cards": 0, "quests": 10, "searches": 60, "bonus": 0}, "runs": 1})
		self.assertEqual(value["totals"]["run_count"], 6)
		by_run = {run["run_id"]: run for day in value["days"] for run in day["runs"]}
		for run_id, expected in ((null_after, 98), (later, 10), (counted, 105)):
			self.assertEqual(by_run[run_id]["points_earned"], expected)
			self.assertEqual(runs.load_run(run_id, {}, str(self.home / "profiles"))["points_earned"], expected)
		self.assertNotIn({"code": "sources_partial"}, value["warnings"])
		self.assertEqual(set(value["sources"]), {"points", "runs"})

	def test_history_and_day_summary_fallback_obeys_local_midnight_and_counter_reset(self):
		self.now = datetime(2026, 10, 19, 20, tzinfo=timezone.utc)
		for index, before, after, crosses in ((0, 0, 98, False), (1, 98, 108, False), (2, 98, 0, False), (3, 0, 98, True)):
			with self.subTest(index=index):
				stamp = f"2026-10-18T{8 + index:02d}:00:00Z"
				run_id, events = record(stamp, after=None, earned_before=before, earned_after=after,
					windows=tuple((task, None, None) for task in stats_ledger.TASKS))
				if crosses:
					# Move after to the next real local date on Windows and Linux.
					at = stats_ledger._at(next(event["ts"] for event in events if event.get("phase") == "before"))
					for event in events:
						if event.get("phase") == "after":
							event["ts"] = stats_ledger._iso(at + timedelta(days=1))
				self.put((run_id, events))
				expected = None if crosses or after < before else after - before
				detail = runs.load_run(run_id, {}, str(self.home / "profiles"))
				self.assertEqual(detail["points_earned"], expected)
				self.assertEqual(detail["accounts"][0]["points_earned"], expected)
				day_run = next(run for day in self.get()["days"] for run in day["runs"] if run["run_id"] == run_id)
				self.assertEqual(day_run["points_earned"], expected)
				self.assertEqual(self.get()["sources"]["runs"], 0)

	def test_history_page_fallback_precedes_short_task_endpoint_and_preserves_tasks(self):
		self.now = datetime(2026, 10, 19, 20, tzinfo=timezone.utc)
		run_id, events = self.put(record("2026-10-18T08:00:00Z", before=1000, after=None, earned_before=0, earned_after=98,
			windows=(("daily_set", 1000, 1030), ("searches", None, None))))
		detail = runs.load_run(run_id, {}, str(self.home / "profiles"))
		self.assertEqual(detail["points_earned"], 98)
		self.assertEqual(detail["accounts"][0]["tasks"][0]["points_after"], 1030)
		self.assertEqual(detail["accounts"][0]["points_now"], 1030)
		self.assertEqual(next(run for day in self.get()["days"] for run in day["runs"] if run["run_id"] == run_id)["points_earned"], 98)

	def test_history_fallback_handles_missing_before_and_keeps_known_state_delta_precedence(self):
		self.now = datetime(2026, 10, 19, 20, tzinfo=timezone.utc)
		for hour, before, after, expected in ((8, None, 1098, 98), (9, None, None, 98), (10, 1000, 1030, 30)):
			with self.subTest(before=before, after=after):
				run_id, _ = self.put(record(f"2026-10-18T{hour:02d}:00:00Z", before=before, after=after,
					earned_before=0, earned_after=98, windows=tuple((task, None, None) for task in stats_ledger.TASKS)))
				self.assertEqual(runs.load_run(run_id, {}, str(self.home / "profiles"))["points_earned"], expected)
				self.assertEqual(next(run for day in self.get()["days"] for run in day["runs"] if run["run_id"] == run_id)["points_earned"], expected)

	def test_kept_legacy_days_never_contribute_to_counted_sources(self):
		self.now = datetime(2026, 10, 19, 20, tzinfo=timezone.utc)
		run_id, events = self.put(counted_record())
		self.get()
		path = self.home / "stats-ledger.json"
		value = json.loads(path.read_text(encoding="utf-8"))
		value["v"] = 1
		for row in value["rows"]:
			for field in ("parts", "open_points", "quests_open", "runs", "counted_sources", "_counted_runs"):
				row.pop(field)
		path.write_text(json.dumps(value), encoding="utf-8")
		(self.folder / (run_id + ".jsonl")).unlink()
		value = self.get()
		self.assertEqual(value["sources"], {"points": dict.fromkeys(stats_ledger.TASKS, 0), "runs": 0})
		self.assertEqual(value["totals"]["measured_earned"], 105)
		self.assertEqual(value["totals"]["run_count"], 1)

	def test_request_range_and_account_selection_only_sum_counted_contributions(self):
		self.now = datetime(2026, 10, 19, 20, tzinfo=timezone.utc)
		self.put(counted_record("2026-10-01T08:00:00Z"))
		self.put(counted_record())
		self.assertEqual(self.get({"range": 7})["sources"]["runs"], 1)
		self.assertEqual(self.get({"range": 30})["sources"]["runs"], 2)
		self.paused = ["second"]
		self.assertEqual(self.get({"account_id": "second"})["sources"], {"points": dict.fromkeys(stats_ledger.TASKS, 0), "runs": 0})

	def test_multi_account_run_counts_once_only_if_all_ran_tasks_are_measured(self):
		self.now = datetime(2026, 10, 19, 20, tzinfo=timezone.utc)
		run_id, events = counted_record()
		events[0]["accounts"].append("second")
		copies = [{**event, "account": "second", **({"profile": "invented-profile/second"} if event["type"] == "account_start" else {})}
			for event in events if event.get("account") == "sample"]
		events += copies
		self.put((run_id, events))
		self.active = ["sample", "second"]
		self.assertEqual(self.get()["sources"]["runs"], 1)
		self.assertEqual(self.get()["sources"]["points"]["searches"], 120)
		self.assertEqual(self.get({"account_id": "second"})["sources"]["points"]["searches"], 60)
		next(event for event in events if event.get("account") == "second" and event.get("task") == "visual_search")["points_after"] = None
		write_record(self.folder, run_id, events)
		self.assertEqual(self.get()["sources"], {"points": dict.fromkeys(stats_ledger.TASKS, 0), "runs": 0})
		self.assertEqual(self.get()["totals"]["run_count"], 1)

	def test_check_only_and_crashed_runs_remain_in_days_but_not_sources(self):
		self.now = datetime(2026, 10, 19, 20, tzinfo=timezone.utc)
		check, _ = self.put(record("2026-10-19T08:00:00Z", check=True, result="skipped", before=1000, after=1000, earned_before=0, earned_after=0))
		crash, _ = self.put(record("2026-10-19T10:00:00Z", result="crashed", phase_after=False))
		value = self.get()
		self.assertEqual(value["sources"], {"points": dict.fromkeys(stats_ledger.TASKS, 0), "runs": 0})
		self.assertEqual([run["run_id"] for run in value["days"][-1]["runs"]], [check, crash])
		self.assertEqual(value["totals"]["check_count"], 1)
		self.assertEqual(value["totals"]["run_count"], 1)


del StatsTests
