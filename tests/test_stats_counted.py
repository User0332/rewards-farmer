"""F75 counted runs use invented balances and explicit search counters."""

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import random
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import stats_ledger as ledger
from test_stats_progress import record, write_record, ZONE

NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


def counted_record(stamp="2026-10-19T07:00:00Z", *, lead=3, late=3, before_counter=0, high=60, result="ok", suffix="0001"):
	base = 1000
	windows = (("daily_set", base, base + 30), ("explore_on_bing", base + 30, base + 30, "skip", "not_available"),
		("visual_search", base + 30, base + 35), ("misc_cards", base + 35, base + 35),
		("quests", base + 35, base + 45), ("searches", base + 45, base + 102), ("bonus", base + 102, base + 102))
	run_id, events = record(stamp, suffix=suffix, before=base, after=base + 102 + late,
		earned_before=0, earned_after=102 + late, windows=windows, result=result)
	for event in events:
		if event["type"] == "state":
			event["searches_earned"] = before_counter if event["phase"] == "before" else high
		if event.get("task") == "searches":
			at = ledger._at(event["ts"])
			for offset, value, round in ((-0.2, lead, 0), (-0.1, high, 1)):
				events.append({"v": 1, "type": "search_points", "run": run_id, "ts": ledger._iso(at + timedelta(seconds=offset)),
					"account": "sample", "earned": value, "max": 60, "round": round})
			break
	return run_id, events


class CountedSourcesTests(unittest.TestCase):
	def row(self, run_id, events):
		rows = ledger.reduce_records({run_id: events}, now=NOW, zone=ZONE)
		return next(row for row in rows.values() if row["_runs"] or row["_checks"])

	def test_late_credit_is_assigned_to_searches_only_with_counter_coverage(self):
		run_id, events = counted_record()
		row = self.row(run_id, events)
		self.assertEqual(row["_counted_runs"], [run_id])
		self.assertEqual(row["counted_sources"], {"daily_set": 30, "explore_on_bing": 0, "visual_search": 5,
			"misc_cards": 0, "quests": 10, "searches": 60, "bonus": 0})
		self.assertEqual(sum(row["counted_sources"].values()), 105)

	def test_lead_smaller_than_late_credit_is_not_counted(self):
		run_id, events = counted_record(late=4)
		row = self.row(run_id, events)
		self.assertEqual(row["_counted_runs"], [])
		self.assertEqual(row["counted_sources"], dict.fromkeys(ledger.TASKS, 0))
		self.assertEqual(row["earned"], 106)

	def test_missing_boundary_disqualifies_even_when_counter_covers_the_gap(self):
		for field in ("points_before", "points_after"):
			with self.subTest(field=field):
				run_id, events = counted_record()
				next(event for event in events if event.get("task") == "visual_search")[field] = None
				self.assertEqual(self.row(run_id, events)["_counted_runs"], [])

	def test_missing_pair_and_all_missing_patterns_never_count(self):
		for all_missing, high in ((False, 60), (False, 0), (True, 60)):
			with self.subTest(all_missing=all_missing, high=high):
				run_id, events = counted_record(high=high)
				for event in events:
					if event["type"] == "task_end":
						if all_missing or event["task"] == "visual_search":
							event["points_after"] = None
						if all_missing or event["task"] == "misc_cards":
							event["points_before"] = None
				self.assertEqual(self.row(run_id, events)["_counted_runs"], [])

	def test_null_state_after_stays_excluded_even_when_same_day_total_is_known(self):
		run_id, events = counted_record()
		next(event for event in events if event.get("phase") == "after")["balance"] = None
		row = self.row(run_id, events)
		self.assertEqual(row["_counted_runs"], [])
		self.assertEqual(row["runs"][0]["points_earned"], 105)

	def test_counter_uses_state_before_and_highest_event_not_round_zero(self):
		run_id, events = counted_record(lead=3)
		next(event for event in events if event.get("phase") == "after")["searches_earned"] = 57
		self.assertEqual(self.row(run_id, events)["counted_sources"]["searches"], 60)
		for event in events:
			if event.get("type") == "search_points" and event["round"] == 1:
				event["earned"] = 57
		self.assertEqual(self.row(run_id, events)["_counted_runs"], [])

	def test_nonzero_counter_baseline_is_subtracted_and_unknown_counter_cannot_cover(self):
		run_id, events = counted_record(before_counter=6, lead=9, high=66)
		self.assertEqual(self.row(run_id, events)["counted_sources"]["searches"], 60)
		for event in events:
			if event.get("phase") == "before":
				event["searches_earned"] = None
		self.assertEqual(self.row(run_id, events)["_counted_runs"], [])

	def test_boolean_negative_and_decreasing_readings_are_not_measurements(self):
		for field, value in (("points_before", True), ("points_after", False), ("points_before", -1), ("points_after", 999)):
			with self.subTest(field=field, value=value):
				run_id, events = counted_record()
				next(event for event in events if event.get("task") == "daily_set")[field] = value
				self.assertEqual(self.row(run_id, events)["_counted_runs"], [])

	def test_outside_gains_before_between_and_after_tasks_all_need_coverage(self):
		run_id, events = record(before=1000, after=1012, earned_after=12, windows=(("daily_set", 1001, 1005), ("searches", 1007, 1010)))
		for event in events:
			if event.get("phase") in ("before", "after"):
				event["searches_earned"] = 0 if event["phase"] == "before" else 8
		row = self.row(run_id, events)
		self.assertEqual(row["counted_sources"]["daily_set"], 4)
		self.assertEqual(row["counted_sources"]["searches"], 8)
		self.assertEqual(row["_counted_runs"], [run_id])
		next(event for event in events if event.get("phase") == "after")["searches_earned"] = 7
		self.assertEqual(self.row(run_id, events)["_counted_runs"], [])

	def test_zero_outside_does_not_need_a_counter_and_disabled_tasks_do_not_run(self):
		run_id, events = record(before=1000, after=1005, earned_after=5, windows=(("visual_search", 1000, 1005), ("bonus", None, None, "off", "turned_off")))
		for event in events:
			if event.get("type") == "state":
				event["searches_earned"] = None
		row = self.row(run_id, events)
		self.assertEqual(row["_counted_runs"], [run_id])
		self.assertEqual(row["counted_sources"]["visual_search"], 5)
		self.assertEqual(row["counted_sources"]["bonus"], 0)

	def test_check_only_crash_and_started_but_unfinished_task_do_not_count(self):
		for run_id, events in (record(check=True, windows=(), result="skipped"),
			record(result="crashed", phase_after=False), record(result=None, phase_after=False)):
			with self.subTest(run_id=run_id):
				self.assertEqual(self.row(run_id, events)["_counted_runs"], [])
		run_id, events = counted_record()
		events.append({"v": 1, "type": "task_start", "run": run_id, "ts": "2026-10-19T07:00:20Z", "account": "sample", "task": "unfinished"})
		self.assertEqual(self.row(run_id, events)["_counted_runs"], [])

	def test_a_failed_outcome_does_not_discard_fully_measured_gains(self):
		run_id, events = counted_record(result="partial")
		next(event for event in events if event.get("task") == "searches")["outcome"] = "fail"
		self.assertEqual(self.row(run_id, events)["counted_sources"]["searches"], 60)

	def test_randomized_counted_gain_conservation_and_coverage_boundary(self):
		rng = random.Random(750119)
		for _ in range(400):
			gains = [rng.randrange(0, 31) for _ in ledger.TASKS]
			gaps = [rng.randrange(0, 4) for _ in range(len(ledger.TASKS) + 1)]
			balance, windows = 1000, []
			for task, gain, gap in zip(ledger.TASKS, gains, gaps):
				balance += gap
				windows.append((task, balance, balance + gain))
				balance += gain
			balance += gaps[-1]
			run_id, events = record(after=balance, earned_after=balance - 1000, windows=windows)
			counter = rng.randrange(10)
			for event in events:
				if event.get("type") == "state":
					event["searches_earned"] = counter if event["phase"] == "before" else counter + gains[5] + sum(gaps)
			row = self.row(run_id, events)
			self.assertEqual(row["_counted_runs"], [run_id])
			self.assertEqual(sum(row["counted_sources"].values()), balance - 1000)
			self.assertEqual(row["counted_sources"]["searches"], gains[5] + sum(gaps))
			next(event for event in events if event.get("phase") == "after")["searches_earned"] -= 1
			if sum(gaps):
				self.assertEqual(self.row(run_id, events)["_counted_runs"], [])


class CountedMigrationTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.home = Path(self.temp.name)
		self.folder = self.home / "runs"
		self.folder.mkdir()

	def fold(self):
		return ledger.fold(str(self.folder), user_data_dir=str(self.home / "profiles"), now=NOW, zone=ZONE)

	def test_installed_v2_reader_takes_newer_readonly_path_against_v3(self):
		fixture = (Path(__file__).parent / "fixtures/stats_ledger_v2.py").read_bytes()
		self.assertEqual(hashlib.sha256(fixture.replace(b"\r\n", b"\n")).hexdigest(), "4c321869a448c3f5b75934a023a6e47ea1d151433f27951829cf5f7cc8c002a7")
		module = types.ModuleType("installed_stats_ledger_v2")
		with mock.patch.dict(sys.modules, {module.__name__: module}):
			exec(compile(fixture, "installed_stats_ledger_v2.py", "exec"), module.__dict__)
			write_record(self.folder, *counted_record())
			self.assertTrue(self.fold().ok)
			path = self.home / "stats-ledger.json"
			before = path.read_bytes()
			with mock.patch.object(module, "replace_file", side_effect=AssertionError("v2 wrote v3")):
				result = module.fold(str(self.folder), user_data_dir=str(self.home / "profiles"), now=NOW, zone=ZONE)
			self.assertTrue(result.ok and result.newer)
			self.assertEqual(result.ledger["v"], 2)
			self.assertEqual(path.read_bytes(), before)
			self.assertEqual(result.ledger["rows"][0]["earned"], 105)

	def test_v2_retained_rows_rebuild_counted_data_and_warm_reads_stay_empty(self):
		write_record(self.folder, *counted_record())
		value = self.fold().ledger
		value["v"] = 2
		for row in value["rows"]:
			row.pop("counted_sources")
			row.pop("_counted_runs")
		path = self.home / "stats-ledger.json"
		path.write_text(json.dumps(value), encoding="utf-8")
		result = self.fold()
		self.assertTrue(result.ok)
		self.assertEqual(result.ledger["v"], 3)
		self.assertEqual(result.ledger["rows"][0]["counted_sources"]["searches"], 60)
		with mock.patch.object(ledger, "_read_record", side_effect=AssertionError("warm record reread")):
			self.assertEqual(self.fold().ledger, result.ledger)

	def test_kept_version_one_days_without_records_never_count_and_keep_totals(self):
		run_id, events = counted_record()
		write_record(self.folder, run_id, events)
		value = self.fold().ledger
		value["v"] = 1
		for row in value["rows"]:
			for field in ("parts", "open_points", "quests_open", "runs", "counted_sources", "_counted_runs"):
				row.pop(field)
		old = copy.deepcopy(value["rows"][0])
		(self.home / "stats-ledger.json").write_text(json.dumps(value), encoding="utf-8")
		(self.folder / (run_id + ".jsonl")).unlink()
		result = self.fold()
		self.assertTrue(result.ok)
		row = result.ledger["rows"][0]
		self.assertEqual({field: row[field] for field in old}, old)
		self.assertEqual(row["counted_sources"], dict.fromkeys(ledger.TASKS, 0))
		self.assertEqual(row["_counted_runs"], [])

	def test_partly_pruned_v2_day_only_counts_retained_proven_runs_and_keeps_totals(self):
		first = counted_record("2026-10-19T07:00:00Z")
		second = counted_record("2026-10-19T09:00:00Z")
		for run in (first, second):
			write_record(self.folder, *run)
		value = self.fold().ledger
		value["v"] = 2
		for row in value["rows"]:
			row.pop("counted_sources")
			row.pop("_counted_runs")
		old = copy.deepcopy(value["rows"][0])
		(self.home / "stats-ledger.json").write_text(json.dumps(value), encoding="utf-8")
		(self.folder / (first[0] + ".jsonl")).unlink()
		result = self.fold()
		self.assertTrue(result.ok)
		row = result.ledger["rows"][0]
		self.assertEqual({field: row[field] for field in old}, old)
		self.assertEqual(row["_counted_runs"], [second[0]])
		self.assertEqual(row["counted_sources"]["searches"], 60)
		self.assertEqual(self.fold().ledger, result.ledger)

	def test_failed_v2_upgrade_write_preserves_previous_ledger(self):
		write_record(self.folder, *counted_record())
		value = self.fold().ledger
		value["v"] = 2
		for row in value["rows"]:
			row.pop("counted_sources")
			row.pop("_counted_runs")
		path = self.home / "stats-ledger.json"
		path.write_text(json.dumps(value), encoding="utf-8")
		before = path.read_bytes()
		with mock.patch.object(ledger, "replace_file", side_effect=PermissionError("synthetic sharing")):
			self.assertFalse(self.fold().ok)
		self.assertEqual(path.read_bytes(), before)
		self.assertTrue(self.fold().ok)

	def test_invalid_counted_data_is_rebuilt_from_records(self):
		write_record(self.folder, *counted_record())
		value = self.fold().ledger
		value["rows"][0]["counted_sources"]["searches"] = True
		(self.home / "stats-ledger.json").write_text(json.dumps(value), encoding="utf-8")
		result = self.fold()
		self.assertTrue(result.ok)
		self.assertEqual(result.ledger["rows"][0]["counted_sources"]["searches"], 60)
		self.assertIsNotNone(result.ledger["rebuilt_at"])


if __name__ == "__main__":
	unittest.main()
