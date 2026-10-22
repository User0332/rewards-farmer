"""F73 provenance and migration from invented page counters only."""

import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import random
import hashlib
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import stats_ledger as ledger

NOW = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
ZONE = timezone(timedelta(hours=2))
ADDITIONS = {"parts", "open_points", "quests_open", "runs"}


def record(stamp="2026-10-16T08:00:00Z", *, suffix="0001", before=1000, after=1100,
	earned_before=0, earned_after=100, windows=(), result="ok", check=False,
	error=None, quests=0, open_points=0, phase_after=True, seconds=60):
	"""Small raw protocol, not a preassembled ledger or bridge response."""
	at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
	run_id = at.strftime("%Y%m%dT%H%M%SZ-") + suffix
	def event(kind, offset, **values):
		return {"v": 1, "type": kind, "run": run_id, "ts": ledger._iso(at + timedelta(seconds=offset)), **values}
	state = {"balance": before, "earned_today": earned_before, "daily_set_open": 0, "searches_earned": 60,
		"searches_max": 60, "open_points": open_points, "quests_open": quests, "unreadable": None}
	events = [event("run_start", 0, accounts=["sample"], trigger="app", plan={"check_only": check}),
		event("account_start", 1, account="sample", profile="invented-profile/sample"),
		event("state", 2, account="sample", phase="before", **state)]
	if check:
		events.append(event("decision", 3, account="sample", should_run=False, reason="nothing_open"))
	for index, window in enumerate(windows):
		task, begin, end = window[:3]
		outcome, reason = window[3:] if len(window) == 5 else ("ok", None)
		events.append(event("task_end", 5 + index, account="sample", task=task, outcome=outcome,
			points_before=begin, points_after=end, reason_key=reason, progress="sent 19 searches" if task == "searches" else None))
	if phase_after:
		events.append(event("state", seconds - 2, account="sample", phase="after", **{**state, "balance": after, "earned_today": earned_after}))
	if result is not None:
		events += [event("account_end", seconds - 1, account="sample", error_code=error), event("run_end", seconds, result=result)]
	return run_id, events


def write_record(folder, run_id, events):
	path = Path(folder, run_id + ".jsonl")
	path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
	return path


def reduce_one(*args, **kwargs):
	run_id, events = record(*args, **kwargs)
	rows = ledger.reduce_records({run_id: events}, now=NOW, zone=ZONE)
	return rows, run_id, events


class SourcePartsTests(unittest.TestCase):
	def row(self, **kwargs):
		rows, _, _ = reduce_one(**kwargs)
		self.assertEqual(len(rows), 1)
		return next(iter(rows.values()))

	def test_shared_pair_and_between_points_have_distinct_measured_parts(self):
		row = self.row(before=1000, after=1041, earned_after=41, windows=(
			("daily_set", 1000, 1030), ("visual_search", 1030, None),
			("misc_cards", None, 1038), ("searches", 1038, 1038)))
		self.assertEqual(row["sources"]["other"], 11)
		self.assertEqual([(part["kind"], part["points"], part["tasks"]) for part in row["parts"]],
			[("tasks", 8, ["visual_search", "misc_cards"]), ("between", 3, [])])
		self.assertTrue(row["source_complete"])
		for part in row["parts"]:
			self.assertEqual(set(part), {"kind", "points", "tasks", "date", "run_id", "started_at"})
			self.assertEqual(part["date"], "2026-10-16")

	def test_first_and_last_task_missing_readings_are_not_attributed_individually(self):
		for windows in (
			(("daily_set", None, 1010), ("searches", 1010, 1020)),
			(("daily_set", 1000, 1010), ("searches", 1010, None)),
		):
			with self.subTest(windows=windows):
				row = self.row(before=1000, after=1020, earned_after=20, windows=windows)
				self.assertEqual(row["sources"]["other"], 10)
				self.assertEqual(len(row["parts"]), 1)
				self.assertEqual(row["parts"][0]["kind"], "tasks")
				self.assertEqual(row["parts"][0]["tasks"], ["daily_set" if windows[0][1] is None else "searches"])

	def test_whole_run_without_task_readings_uses_run_balance_not_task_outcomes(self):
		row = self.row(before=1000, after=1087, earned_after=87,
			windows=tuple((task, None, None) for task in ledger.TASKS))
		self.assertEqual(row["sources"]["other"], 87)
		self.assertEqual([(part["kind"], part["points"], part["tasks"]) for part in row["parts"]], [("run", 87, list(ledger.TASKS))])

	def test_a_short_last_task_fallback_is_not_mislabeled_as_the_whole_run(self):
		row = self.row(before=1000, after=None, earned_before=None, earned_after=None,
			windows=(("visual_search", None, 1010), ("misc_cards", None, None)))
		self.assertEqual(row["sources"]["other"], 10)
		self.assertTrue(all(part["kind"] != "run" for part in row["parts"]))

	def test_missing_after_balance_falls_back_to_same_day_earned_counter(self):
		row = self.row(after=None, earned_before=10, earned_after=108,
			windows=tuple((task, None, None) for task in ledger.TASKS))
		self.assertEqual(row["sources"]["other"], 98)
		self.assertEqual([(part["kind"], part["points"]) for part in row["parts"]], [("run", 98)])
		self.assertEqual(row["_run_earned"], 98)
		self.assertTrue(row["source_complete"])

	def test_earned_counter_never_crosses_two_local_days_or_a_counter_reset(self):
		for stamp, before, after in (("2026-10-16T21:59:40Z", 0, 98), ("2026-10-16T08:00:00Z", 98, 0)):
			with self.subTest(stamp=stamp):
				rows, _, _ = reduce_one(stamp, after=None, earned_before=before, earned_after=after,
					windows=(("searches", None, None),))
				self.assertTrue(all(row["sources"]["other"] is None and row["parts"] == [] for row in rows.values()))

	def test_zero_parts_omitted_and_unknown_or_negative_boundaries_do_not_invent_credit(self):
		row = self.row(before=1000, after=1000, earned_after=0, windows=(("searches", None, None),))
		self.assertEqual(row["sources"]["other"], 0)
		self.assertEqual(row["parts"], [])
		row = self.row(before=None, after=None, earned_before=None, earned_after=None, windows=(("searches", None, None),))
		self.assertIsNone(row["sources"]["other"])
		self.assertEqual(row["parts"], [])

	def test_turned_off_task_is_not_listed_as_a_task_that_ran(self):
		row = self.row(before=1000, after=1010, earned_after=10,
			windows=(("visual_search", None, None, "off", "turned_off"), ("searches", None, None)))
		self.assertEqual(row["parts"][0]["tasks"], ["searches"])

	def test_parts_sum_rule_over_randomized_readings_and_coherent_full_runs(self):
		rng = random.Random(730173)
		for _ in range(600):
			balance = rng.randrange(1000, 2000)
			before = balance
			windows = []
			for task in ledger.TASKS:
				balance += rng.randrange(4)
				begin = balance
				balance += rng.randrange(10)
				end = balance
				windows.append((task, begin if rng.choice((True, False)) else None, end if rng.choice((True, False)) else None))
			balance += rng.randrange(4)
			row = self.row(before=before, after=balance, earned_after=balance - before, windows=windows)
			self.assertTrue(row["source_complete"])
			self.assertEqual(sum(part["points"] for part in row["parts"]), row["sources"]["other"])
			self.assertEqual(sum(value for value in row["sources"].values() if value is not None), balance - before)
			self.assertTrue(all(part["points"] > 0 for part in row["parts"]))

	def test_random_missing_endpoints_and_decreasing_readings_keep_only_measured_parts(self):
		rng = random.Random(730274)
		for _ in range(400):
			windows = [(task, rng.choice((None, rng.randrange(900, 1100))), rng.choice((None, rng.randrange(900, 1100)))) for task in ledger.TASKS]
			row = self.row(before=rng.choice((None, 1000)), after=rng.choice((None, 1050)),
				earned_before=None, earned_after=None, windows=windows)
			if row["sources"]["other"] is not None:
				self.assertEqual(row["sources"]["other"], sum(part["points"] for part in row["parts"]))
			self.assertTrue(all(part["points"] > 0 for part in row["parts"]))

	def test_day_progress_uses_latest_core_readable_state(self):
		run_id, events = record(windows=(("searches", 1000, 1100),))
		events.append({**events[-3], "ts": "2026-10-16T10:00:00.000Z", "open_points": 33, "quests_open": 2, "daily_set_open": 1, "unreadable": "searches"})
		row = next(iter(ledger.reduce_records({run_id: events}, now=NOW, zone=ZONE).values()))
		self.assertEqual((row["open_points"], row["quests_open"], row["done"]), (0, 0, True))
		events[-1]["unreadable"] = "quests"
		row = next(iter(ledger.reduce_records({run_id: events}, now=NOW, zone=ZONE).values()))
		self.assertEqual((row["open_points"], row["quests_open"], row["done"]), (33, 2, False))

	def test_incomplete_task_start_is_listed_in_a_whole_run_part(self):
		run_id, events = record(windows=(("visual_search", None, None),))
		events.insert(4, {"v": 1, "type": "task_start", "run": run_id, "ts": "2026-10-16T08:00:10.000Z", "account": "sample", "task": "searches", "points_before": None})
		row = next(iter(ledger.reduce_records({run_id: events}, now=NOW, zone=ZONE).values()))
		self.assertEqual(row["parts"][0]["tasks"], ["visual_search", "searches"])

	def test_unrecognized_free_text_cannot_enter_new_summary_fields(self):
		run_id, events = record(windows=(("searches", None, None, "fail", "synthetic-private-name"),), error="synthetic-private-name")
		events[0]["trigger"] = "synthetic-private-name"
		row = next(iter(ledger.reduce_records({run_id: events}, now=NOW, zone=ZONE).values()))
		self.assertNotIn("synthetic-private-name", json.dumps(row))
		self.assertEqual(row["runs"][0]["problems"], [{"task": "searches", "reason_key": "errors.task.error"}, {"task": None, "reason_key": "errors.run.failed"}])

	def test_compact_summary_preserves_every_frozen_history_result(self):
		for result in ("ok", "partial", "failed", "skipped", "checked", "stopped", "busy", "crashed", "config_error", "interrupted", "unknown"):
			with self.subTest(result=result):
				run_id, events = record(result=result)
				summary = ledger.run_summary(run_id, events)
				self.assertEqual(summary["result"], result)
				self.assertEqual(summary["result_key"], "errors.run." + result)
				self.assertEqual(summary["problems"], [] if result in ("ok", "partial", "skipped") else [{"task": None, "reason_key": "errors.run." + result}])


class ProgressMigrationTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.home = Path(self.temp.name)
		self.folder = self.home / "runs"
		self.folder.mkdir()

	def fold(self):
		return ledger.fold(str(self.folder), user_data_dir=str(self.home / "profiles"), now=NOW, zone=ZONE)

	def v1(self, records):
		for run_id, events in records:
			write_record(self.folder, run_id, events)
		value = self.fold().ledger
		value["v"] = 1
		for row in value["rows"]:
			for field in ADDITIONS | {"counted_sources", "_counted_runs"}:
				row.pop(field)
		(self.home / "stats-ledger.json").write_text(json.dumps(value), encoding="utf-8")
		return copy.deepcopy(value)

	def test_migration_rebuilds_retained_rows_and_warm_replay_is_idempotent(self):
		old = self.v1([record(windows=(("visual_search", 1000, None), ("misc_cards", None, 1100)))])
		with mock.patch.object(ledger, "_read_record", wraps=ledger._read_record) as read:
			result = self.fold()
		self.assertTrue(result.ok)
		self.assertEqual(result.ledger["v"], ledger.VERSION)
		self.assertEqual(read.call_count, 1)
		self.assertEqual(result.ledger["rows"][0]["parts"][0]["points"], 100)
		self.assertEqual(result.ledger["rows"][0]["open_points"], 0)
		self.assertEqual(len(result.ledger["rows"][0]["runs"]), 1)
		path = self.home / "stats-ledger.json"
		before = path.read_bytes()
		with mock.patch.object(ledger, "_read_record", side_effect=AssertionError("migrated warm reread")), mock.patch.object(ledger, "_holder", side_effect=AssertionError("unchanged index probed holder")):
			self.assertEqual(self.fold().ledger, result.ledger)
		self.assertEqual(path.read_bytes(), before)
		self.assertEqual(result.ledger["coverage_from"], old["coverage_from"])

	def test_migration_keeps_pruned_rows_and_all_old_values_exactly(self):
		run_id, events = record(windows=(("visual_search", 1000, None), ("misc_cards", None, 1100)))
		old = self.v1([(run_id, events)])
		(self.folder / (run_id + ".jsonl")).unlink()
		result = self.fold()
		self.assertTrue(result.ok)
		self.assertEqual(result.ledger["v"], ledger.VERSION)
		self.assertEqual(len(result.ledger["rows"]), 1)
		row = result.ledger["rows"][0]
		self.assertEqual({key: row[key] for key in old["rows"][0]}, old["rows"][0])
		self.assertEqual(row["parts"], [])
		self.assertEqual(row["sources"]["other"], 100)
		self.assertIsNone(row["open_points"])
		self.assertIsNone(row["quests_open"])
		self.assertIsNone(row["runs"])

	def test_partially_pruned_day_keeps_values_and_retained_neighbor_never_replays(self):
		first = record(windows=(("searches", 1000, 1100),))
		second = record("2026-10-16T10:00:00Z", before=1100, after=1120, earned_before=100, earned_after=120,
			windows=(("searches", 1100, 1120),))
		old = self.v1([first, second])
		(self.folder / (first[0] + ".jsonl")).unlink()
		with mock.patch.object(ledger, "_read_record", wraps=ledger._read_record) as read:
			result = self.fold()
		self.assertTrue(result.ok)
		# F75 may inspect the retained neighbor for counted eligibility, but
		# the sealed v1 daily values must never be replayed or replaced.
		self.assertEqual(read.call_count, 1)
		self.assertEqual({key: result.ledger["rows"][0][key] for key in old["rows"][0]}, old["rows"][0])

	def test_cross_midnight_migration_rebuilds_retained_day_without_overwriting_pruned_day(self):
		first = record("2026-10-16T08:00:00Z", windows=(("searches", 1000, 1100),))
		cross = record("2026-10-16T21:59:40Z", before=1100, after=1120, earned_before=100, earned_after=20,
			windows=(("searches", 1100, 1120),))
		old = self.v1([first, cross])
		(self.folder / (first[0] + ".jsonl")).unlink()
		result = self.fold()
		self.assertTrue(result.ok)
		previous = {row["date"]: row for row in old["rows"]}
		rows = {row["date"]: row for row in result.ledger["rows"]}
		self.assertEqual({key: rows["2026-10-16"][key] for key in previous["2026-10-16"]}, previous["2026-10-16"])
		self.assertEqual(rows["2026-10-16"]["parts"], [])
		self.assertIsNone(rows["2026-10-16"]["open_points"])
		self.assertEqual(rows["2026-10-17"]["open_points"], 0)
		self.assertEqual(rows["2026-10-17"]["earned"], 20)
		self.assertEqual(self.fold().ledger, result.ledger)

	def test_new_record_after_pruned_migration_adds_once_and_preserves_legacy_values(self):
		old_run = record(windows=(("searches", 1000, 1100),))
		self.v1([old_run])
		(self.folder / (old_run[0] + ".jsonl")).unlink()
		self.fold()
		new_run = record("2026-10-16T10:00:00Z", before=1100, after=1120, earned_before=100, earned_after=120,
			windows=(("visual_search", 1100, None), ("misc_cards", None, 1120)))
		write_record(self.folder, *new_run)
		result = self.fold()
		self.assertTrue(result.ok)
		row = result.ledger["rows"][0]
		self.assertEqual(row["earned"], 120)
		self.assertEqual(row["sources"]["searches"], 100)
		self.assertEqual(row["sources"]["other"], 20)
		self.assertEqual([part["points"] for part in row["parts"]], [20])
		self.assertEqual([summary["run_id"] for summary in row["runs"]], [new_run[0]])
		self.assertEqual(self.fold().ledger, result.ledger)

	def test_v1_reader_meeting_v2_folds_in_memory_and_never_writes_v2(self):
		# Execute the installed reader's exact source, not VERSION=1 on v2 code.
		root = Path(__file__).resolve().parents[1]
		fixture = (root / "tests/fixtures/stats_ledger_v1.py").read_bytes()
		# Verbatim a1da061 source, Git blob 998d8beac5fa2e7af764663db920cf53d590b0e2.
		self.assertEqual(hashlib.sha256(fixture.replace(b"\r\n", b"\n")).hexdigest(), "92ec9d5263c330834942005b3a7a971ea45665d29300bd11ba5190e72d0fe9e8")
		source = fixture.decode("utf-8")
		module = types.ModuleType("stats_ledger_v1_acceptance")
		with mock.patch.dict(sys.modules, {module.__name__: module}):
			exec(compile(source, "installed-v1-stats-ledger.py", "exec"), module.__dict__)
			run_id, events = record(windows=(("searches", 1000, 1100),))
			write_record(self.folder, run_id, events)
			self.assertTrue(self.fold().ok)
			path = self.home / "stats-ledger.json"
			before = path.read_bytes()
			with mock.patch.object(module, "replace_file", side_effect=AssertionError("v1 wrote v2")):
				result = module.fold(str(self.folder), user_data_dir=str(self.home / "profiles"), now=NOW, zone=ZONE)
			self.assertTrue(result.ok and result.newer)
			self.assertEqual(result.ledger["v"], 1)
			self.assertEqual(result.ledger["rows"][0]["earned"], 100)
			self.assertEqual(path.read_bytes(), before)

	def test_failed_migration_write_preserves_v1_ledger_and_source_records(self):
		run_id, events = record(windows=(("searches", 1000, 1100),))
		self.v1([(run_id, events)])
		path = self.home / "stats-ledger.json"
		before = path.read_bytes()
		with mock.patch.object(ledger, "replace_file", side_effect=PermissionError("synthetic sharing")):
			self.assertFalse(self.fold().ok)
		self.assertEqual(path.read_bytes(), before)
		self.assertTrue((self.folder / (run_id + ".jsonl")).exists())
		self.assertTrue(self.fold().ok)
		self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["v"], ledger.VERSION)

	def test_invalid_parts_are_rebuilt_rather_than_reused_as_provenance(self):
		write_record(self.folder, *record(windows=(("visual_search", None, None),)))
		result = self.fold()
		self.assertTrue(result.ok)
		result.ledger["rows"][0]["parts"][0]["points"] = "synthetic-private-name"
		(self.home / "stats-ledger.json").write_text(json.dumps(result.ledger), encoding="utf-8")
		result = self.fold()
		self.assertTrue(result.ok)
		self.assertIsNotNone(result.ledger["rebuilt_at"])
		self.assertEqual(result.ledger["rows"][0]["parts"][0]["points"], 100)


if __name__ == "__main__":
	unittest.main()
