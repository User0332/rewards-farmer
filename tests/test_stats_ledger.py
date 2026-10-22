import contextlib
from datetime import datetime, timedelta, timezone, tzinfo
import io
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import constants
import run_record
import stats_ledger as ledger


def synthetic_record(folder, stamp, *, suffix="0001", account="account-1", before=1000, gain=30, earned=30, complete=True, unreadable=None, check=False, partial_pair=False, queries="sent 4 searches", result=True):
	"""Invented observations only, with task boundaries matching the protocol."""
	at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
	run_id = at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + suffix
	def event(kind, index, **fields):
		return {"v": 1, "type": kind, "run": run_id, "ts": (at + timedelta(seconds=index)).isoformat().replace("+00:00", "Z"), **fields}
	profiles = str(Path(folder).parent / "profiles" / account)
	state = {"balance": before, "earned_today": max(0, earned - gain) if earned is not None else None,
		"daily_set_open": 0 if complete else 3, "searches_earned": 60 if complete else 0,
		"searches_max": 60, "cards_open": 0, "quests_open": 0, "open_points": 0 if complete else 90, "unreadable": unreadable}
	events = [event("run_start", 0, trigger="scheduled", accounts=[account], plan={"check_only": check}),
		event("account_start", 1, account=account, profile=profiles), event("state", 2, account=account, phase="before", **state)]
	if check:
		events.append(event("decision", 3, account=account, should_run=False, reason="nothing_open"))
	elif partial_pair:
		for index, task, start, end in ((3, "daily_set", before, before + 10), (4, "visual_search", before + 10, None),
			(5, "misc_cards", None, before + 20), (6, "searches", before + 20, before + gain)):
			events.append(event("task_end", index, account=account, task=task, outcome="ok", points_before=start, points_after=end, progress=queries if task == "searches" else None))
	else:
		events.append(event("task_end", 3, account=account, task="searches", outcome="ok", points_before=before, points_after=before + gain, progress=queries))
	if result:
		events += [event("state", 7, account=account, phase="after", **{**state, "balance": before + gain, "earned_today": earned}),
			event("account_end", 8, account=account, started=True, ran_tasks=not check), event("run_end", 9, result="checked" if check else "ok", duration_ms=9000)]
	path = Path(folder, run_id + ".jsonl")
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
	return run_id, events


class Berlin2026(tzinfo):
	"""Explicit EU transition fixture, also usable without host IANA data."""
	def utcoffset(self, value):
		if value is None:
			return timedelta(hours=1)
		plain = value.replace(tzinfo=None)
		if datetime(2026, 3, 29, 3) <= plain < datetime(2026, 10, 25, 2):
			return timedelta(hours=2)
		if datetime(2026, 10, 25, 2) <= plain < datetime(2026, 10, 25, 3):
			return timedelta(hours=1 if value.fold else 2)
		return timedelta(hours=1)

	def dst(self, value):
		return self.utcoffset(value) - timedelta(hours=1)

	def fromutc(self, value):
		plain = value.replace(tzinfo=None)
		summer = datetime(2026, 3, 29, 1) <= plain < datetime(2026, 10, 25, 1)
		fold = datetime(2026, 10, 25, 1) <= plain < datetime(2026, 10, 25, 2)
		return (value + timedelta(hours=2 if summer else 1)).replace(fold=int(fold))


class LedgerTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.home = Path(self.temp.name)
		self.folder = self.home / "runs"
		self.folder.mkdir()
		self.now = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
		self.user_dir = str(self.home / "profiles")
		self.stack = contextlib.ExitStack()
		self.addCleanup(self.stack.close)
		self.stack.enter_context(mock.patch.object(constants, "RUNS_DIR", str(self.folder)))
		self.stack.enter_context(mock.patch.dict(os.environ, {"USER_DATA_DIR": self.user_dir}))
		case = self
		class FixedDateTime(datetime):
			@classmethod
			def now(cls, tz=None):
				return case.now.astimezone(tz) if tz else case.now.replace(tzinfo=None)
		self.stack.enter_context(mock.patch.object(ledger, "datetime", FixedDateTime))
		self.stack.enter_context(mock.patch.object(run_record, "datetime", FixedDateTime))

	def fold(self, **kwargs):
		return ledger.fold(str(self.folder), now=self.now, user_data_dir=self.user_dir, zone=timezone.utc, **kwargs)

	def one(self):
		result = self.fold()
		self.assertTrue(result.ok)
		self.assertEqual(len(result.ledger["rows"]), 1)
		return result.ledger["rows"][0]

	def test_fold_idempotent_and_index_skips_unchanged_records(self):
		synthetic_record(self.folder, "2026-10-16T08:00:00Z")
		first = self.fold()
		path = self.home / "stats-ledger.json"
		original = path.read_bytes()
		with mock.patch.object(ledger, "_read_record", side_effect=AssertionError("unchanged record reread")):
			second = self.fold()
		self.assertTrue(second.ok)
		self.assertEqual(first.ledger, second.ledger)
		self.assertEqual(path.read_bytes(), original)
		self.assertEqual(second.ledger["rows"][0]["run_count"], 1)

	def test_open_day_recomputes_all_neighbors_without_double_counting_replay(self):
		first, _ = synthetic_record(self.folder, "2026-10-16T08:00:00Z")
		self.fold()
		second, _ = synthetic_record(self.folder, "2026-10-16T09:00:00Z", before=1030, gain=10, earned=40)
		with mock.patch.object(ledger, "_read_record", wraps=ledger._read_record) as read:
			row = self.one()
		self.assertEqual({call.args[0].stem for call in read.call_args_list}, {first, second})
		self.assertEqual((row["earned"], row["_run_earned"], row["run_count"]), (40, 40, 2))
		path = self.folder / (second + ".jsonl")
		path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
		self.assertEqual(self.one(), row)

	def test_sealed_day_keeps_pruned_contribution_and_adds_new_run_once(self):
		old, _ = synthetic_record(self.folder, "2026-10-16T08:00:00Z", earned=None)
		self.fold()
		(self.folder / (old + ".jsonl")).unlink()
		new, _ = synthetic_record(self.folder, "2026-10-16T09:00:00Z", gain=10, earned=None)
		row = self.one()
		self.assertEqual((row["earned"], row["earned_basis"], row["run_count"]), (40, "runs", 2))
		self.assertEqual(set(row["run_ids"]), {old, new})
		path = self.folder / (new + ".jsonl")
		path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
		with mock.patch.object(ledger, "_read_record", side_effect=AssertionError("sealed replay reread")):
			self.assertEqual(self.one(), row)

	def test_sealed_day_newer_page_and_balance_replace_older_reading(self):
		old, _ = synthetic_record(self.folder, "2026-10-16T08:00:00Z", earned=30)
		self.fold()
		(self.folder / (old + ".jsonl")).unlink()
		synthetic_record(self.folder, "2026-10-16T09:00:00Z", gain=10, before=1030, earned=40)
		row = self.one()
		self.assertEqual((row["earned"], row["balance"], row["_run_earned"]), (40, 1040, 40))

	def test_corrupt_ledger_moves_aside_and_rebuilds_without_losing_original(self):
		synthetic_record(self.folder, "2026-10-16T08:00:00Z")
		(self.home / "stats-ledger.json").write_text("bad synthetic ledger", encoding="utf-8")
		result = self.fold()
		self.assertTrue(result.ok)
		self.assertEqual(result.ledger["rebuilt_at"], "2026-10-16T12:00:00.000Z")
		bad = list(self.home.glob("stats-ledger.bad-*.json"))
		self.assertEqual(len(bad), 1)
		self.assertEqual(bad[0].read_text(), "bad synthetic ledger")

	def test_newer_version_is_untouched_and_prune_still_proceeds(self):
		run_id, _ = synthetic_record(self.folder, "2026-07-01T08:00:00Z")
		path = self.home / "stats-ledger.json"
		path.write_text(json.dumps({"v": ledger.VERSION + 1, "future": "preserve"}), encoding="utf-8")
		before = path.read_bytes()
		result = self.fold()
		self.assertTrue(result.ok and result.newer)
		with mock.patch("run_lock.status", return_value=None):
			self.assertEqual(run_record.prune(str(self.folder), keep_max=0), 1)
		self.assertFalse((self.folder / (run_id + ".jsonl")).exists())
		self.assertEqual(path.read_bytes(), before)

	def test_lock_timeout_blocks_fold_and_prune_without_real_wait(self):
		run_id, _ = synthetic_record(self.folder, "2026-07-01T08:00:00Z")
		now = [0.0]
		sleeps = []
		def sleep(seconds):
			sleeps.append(seconds)
			now[0] += seconds
		with mock.patch.object(ledger, "_lock_file", side_effect=OSError("synthetic busy")):
			result = self.fold(clock=lambda: now[0], sleep=sleep)
		self.assertFalse(result.ok)
		self.assertEqual(now[0], 5)
		self.assertTrue(all(0 < seconds <= 0.05 for seconds in sleeps))
		with mock.patch.object(ledger, "fold", return_value=result):
			self.assertEqual(run_record.prune(str(self.folder), keep_max=0), 0)
		self.assertTrue((self.folder / (run_id + ".jsonl")).exists())

	def test_fold_exception_blocks_prune_and_logs_only_a_code(self):
		run_id, _ = synthetic_record(self.folder, "2026-07-01T08:00:00Z")
		with mock.patch.object(ledger, "fold", side_effect=OSError("private-path-and-identity")), mock.patch.object(ledger, "_log") as log:
			self.assertEqual(run_record.prune(str(self.folder), keep_max=0), 0)
		log.assert_called_once_with("ledger_fold_failed")
		self.assertTrue((self.folder / (run_id + ".jsonl")).exists())

	def test_both_prune_calls_fold_before_deletion_and_keep_sealed_rows(self):
		old, _ = synthetic_record(self.folder, "2026-07-01T08:00:00Z")
		with mock.patch("run_lock.status", return_value=None):
			self.assertEqual(run_record.prune(str(self.folder), keep_max=0), 1)
		self.assertFalse((self.folder / (old + ".jsonl")).exists())
		first = self.fold().ledger["rows"]
		self.assertEqual(first[0]["earned"], 30)
		new, _ = synthetic_record(self.folder, "2026-07-02T08:00:00Z", earned=20, gain=20)
		with mock.patch("run_lock.status", return_value=None):
			self.assertEqual(run_record.prune(str(self.folder), keep_max=0), 1)
		self.assertFalse((self.folder / (new + ".jsonl")).exists())
		rows = self.fold().ledger["rows"]
		self.assertEqual([row["earned"] for row in rows], [30, 20])

	def test_partial_core_readable_and_later_open_reading_supersedes_done(self):
		synthetic_record(self.folder, "2026-10-16T08:00:00Z", unreadable="quests", complete=True)
		self.assertTrue(self.one()["done"])
		synthetic_record(self.folder, "2026-10-16T09:00:00Z", unreadable="quests", complete=False)
		self.assertFalse(self.one()["done"])
		synthetic_record(self.folder, "2026-10-16T10:00:00Z", unreadable="searches", complete=True)
		self.assertFalse(self.one()["done"])

	def test_nothing_open_check_proves_done_and_is_not_a_run(self):
		synthetic_record(self.folder, "2026-10-16T08:00:00Z", gain=0, earned=0, check=True)
		row = self.one()
		self.assertTrue(row["done"])
		self.assertEqual((row["run_count"], row["check_count"]), (0, 1))

	def test_sources_combined_pair_and_unreconciled_adjustment(self):
		synthetic_record(self.folder, "2026-10-16T08:00:00Z", partial_pair=True)
		row = self.one()
		self.assertEqual(row["sources"], {"daily_set": 10, "explore_on_bing": None, "visual_search": None, "misc_cards": None, "quests": None, "searches": 10, "bonus": None, "other": 10})
		self.assertEqual(row["combined"], [["visual_search", "misc_cards"]])
		self.assertTrue(row["source_complete"])
		run_id, events = synthetic_record(self.folder, "2026-10-16T09:00:00Z", gain=-5)
		row = self.one()
		self.assertFalse(row["source_complete"])

	def test_query_counter_is_strict_and_missing_counts_are_unknown(self):
		synthetic_record(self.folder, "2026-10-16T08:00:00Z", queries="sent 1 search")
		self.assertEqual(self.one()["batch_queries_sent"], 1)
		synthetic_record(self.folder, "2026-10-16T09:00:00Z", queries="sent 2 searches then failed")
		row = self.one()
		self.assertEqual(row["batch_queries_sent"], 1)
		self.assertFalse(row["query_count_complete"])

	def test_zone_assignment_survives_zone_change_and_new_same_day_record(self):
		synthetic_record(self.folder, "2026-10-15T23:00:00Z")
		first = ledger.fold(str(self.folder), now=self.now, user_data_dir=self.user_dir, zone=timezone(timedelta(hours=2)))
		self.assertEqual(first.ledger["rows"][0]["date"], "2026-10-16")
		synthetic_record(self.folder, "2026-10-15T23:30:00Z", earned=60)
		second = self.fold()
		self.assertEqual(len(second.ledger["rows"]), 1)
		self.assertEqual(second.ledger["rows"][0]["date"], "2026-10-16")
		self.assertEqual(second.ledger["rows"][0]["earned"], 60)

	def test_both_dst_changes_assign_real_zone_dates_not_fixed_offsets(self):
		try:
			zone = ZoneInfo("Europe/Berlin")
		except ZoneInfoNotFoundError:
			zone = Berlin2026()
		for stamp, date, offset in (("2026-03-28T23:30:00Z", "2026-03-29", 1), ("2026-03-29T01:30:00Z", "2026-03-29", 2),
			("2026-10-25T00:30:00Z", "2026-10-25", 2), ("2026-10-25T01:30:00Z", "2026-10-25", 1)):
			with self.subTest(stamp=stamp):
				run_id, events = synthetic_record(self.folder, stamp)
				rows = ledger.reduce_records({run_id: events}, now=datetime(2026, 12, 1, tzinfo=timezone.utc), zone=zone, user_data_dir=self.user_dir)
				self.assertEqual({key[0] for key in rows}, {date})
				row = next(iter(rows.values()))
				hours = (ledger._at(row["_day_end"]) - ledger._at(row["_day_start"])).total_seconds() / 3600
				self.assertEqual(hours, 23 if date == "2026-03-29" else 25)

	def test_new_record_on_sealed_day_is_added_even_when_retained_neighbor_changed(self):
		old, _ = synthetic_record(self.folder, "2026-10-16T08:00:00Z", earned=None)
		kept, _ = synthetic_record(self.folder, "2026-10-16T09:00:00Z", earned=None, gain=10)
		self.fold()
		(self.folder / (old + ".jsonl")).unlink()
		path = self.folder / (kept + ".jsonl")
		path.write_text(path.read_text() + "\n", encoding="utf-8")
		synthetic_record(self.folder, "2026-10-16T10:00:00Z", earned=None, gain=5)
		with mock.patch.object(ledger, "_read_record", wraps=ledger._read_record) as read:
			row = self.one()
		self.assertEqual(row["earned"], 45)
		self.assertEqual(row["run_count"], 3)
		self.assertNotIn(kept, {call.args[0].stem for call in read.call_args_list})

	def test_prune_failure_keeps_log_and_stop_bundles_not_only_jsonl(self):
		run_id, _ = synthetic_record(self.folder, "2026-07-01T08:00:00Z")
		for suffix in (".log", ".stop"):
			(self.folder / (run_id + suffix)).write_text("synthetic", encoding="utf-8")
		with mock.patch.object(ledger, "fold", return_value=ledger.FoldResult({}, False)):
			self.assertEqual(run_record.prune(str(self.folder), keep_max=0), 0)
		self.assertTrue(all((self.folder / (run_id + suffix)).exists() for suffix in (".jsonl", ".log", ".stop")))

	def test_atomic_replace_failure_preserves_ledger_and_prune_inputs(self):
		synthetic_record(self.folder, "2026-10-15T08:00:00Z")
		self.fold()
		path = self.home / "stats-ledger.json"
		before = path.read_bytes()
		synthetic_record(self.folder, "2026-10-16T08:00:00Z")
		with mock.patch.object(ledger, "replace_file", side_effect=PermissionError("synthetic sharing")):
			self.assertFalse(self.fold().ok)
		self.assertEqual(path.read_bytes(), before)
		self.assertFalse(any(item.name.startswith("tmp") for item in self.home.iterdir()))

	def test_real_writer_lock_serializes_two_contenders_without_metadata_side_effects(self):
		path = self.home / "stats-ledger.lock"
		with ledger._writer(path):
			clock = [0.0]
			with self.assertRaises(TimeoutError):
				with ledger._writer(path, clock=lambda: clock[0], sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds)):
					self.fail("second writer entered")
		with ledger._writer(path):
			self.assertFalse((self.home / ".rewards-farmer.lock.json").exists())

	def test_new_record_for_other_account_on_sealed_date_is_not_lost(self):
		old, _ = synthetic_record(self.folder, "2026-10-16T08:00:00Z")
		self.fold()
		(self.folder / (old + ".jsonl")).unlink()
		synthetic_record(self.folder, "2026-10-16T09:00:00Z", account="account-2")
		result = self.fold()
		self.assertTrue(result.ok)
		self.assertEqual({row["account_id"] for row in result.ledger["rows"]}, {"account-1", "account-2"})

	def test_unreadable_core_cannot_award_done_from_filled_numeric_fields(self):
		for code in ("daily_set", "searches", "signed_out", "terms_of_use", "page_blank"):
			with self.subTest(code=code):
				run_id, events = synthetic_record(self.folder, "2026-10-16T08:00:00Z", complete=True, unreadable=code)
				rows = ledger.reduce_records({run_id: events}, now=self.now, zone=timezone.utc, user_data_dir=self.user_dir)
				self.assertIsNone(next(iter(rows.values()))["done"])

	def test_cross_midnight_changed_run_recomputes_open_date_without_replaying_sealed_date(self):
		old, _ = synthetic_record(self.folder, "2026-10-15T12:00:00Z", earned=10, gain=10)
		cross, events = synthetic_record(self.folder, "2026-10-15T23:59:56Z", earned=30, gain=30)
		before = self.fold().ledger["rows"]
		(self.folder / (old + ".jsonl")).unlink()
		for event in events:
			if event.get("type") == "state" and event.get("phase") == "after":
				event["earned_today"] = 40
		path = self.folder / (cross + ".jsonl")
		path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
		after = self.fold().ledger["rows"]
		self.assertEqual(before[0], after[0])
		self.assertEqual(after[1]["earned"], 40)

	def test_supervisor_fold_resolves_custom_root_from_dotenv_without_storing_path(self):
		custom = str(self.home / "custom-profile")
		run_id, events = synthetic_record(self.folder, "2026-10-16T08:00:00Z", account="default")
		for event in events:
			if event.get("type") == "account_start":
				event["profile"] = custom
		(self.folder / (run_id + ".jsonl")).write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
		(self.home / ".env").write_text("USER_DATA_DIR=" + custom.replace("\\", "/") + "\n", encoding="utf-8")
		with mock.patch.dict(os.environ, {}, clear=True):
			result = ledger.fold(str(self.folder), now=self.now, zone=timezone.utc)
		self.assertTrue(result.ok)
		self.assertEqual(result.ledger["rows"][0]["account_id"], "@root")
		self.assertNotIn(custom, (self.home / "stats-ledger.json").read_text(encoding="utf-8"))

	def test_coverage_includes_a_record_with_no_account_and_no_run_start(self):
		path = self.folder / "20261014T200000Z-0001.jsonl"
		path.write_text('{"v":1,"type":"deferred","run":"20261014T200000Z-0001","ts":"2026-10-14T20:00:00Z","state":3,"waited_s":60,"gave_up":false}\n', encoding="utf-8")
		result = self.fold()
		self.assertTrue(result.ok)
		self.assertEqual(result.ledger["coverage_from"], "2026-10-14")
		self.assertEqual(result.ledger["rows"], [])

	def test_structurally_invalid_ledger_is_also_moved_aside_not_reused(self):
		synthetic_record(self.folder, "2026-10-16T08:00:00Z")
		value = self.fold().ledger
		value["rows"][0]["_day_start"] = "invalid timestamp"
		(self.home / "stats-ledger.json").write_text(json.dumps(value), encoding="utf-8")
		result = self.fold()
		self.assertTrue(result.ok)
		self.assertEqual(result.ledger["rows"][0]["earned"], 30)
		self.assertIsNotNone(result.ledger["rebuilt_at"])
		self.assertEqual(len(list(self.home.glob("stats-ledger.bad-*.json"))), 1)

	def test_free_text_in_unknown_fields_or_bad_numeric_values_cannot_enter_ledger(self):
		run_id, events = synthetic_record(self.folder, "2026-10-16T08:00:00Z")
		sentinel = "synthetic-private-person@example.invalid"
		for event in events:
			event["unknown_future_field"] = sentinel
			if event.get("type") == "state":
				event["earned_today"] = sentinel
			if event.get("type") == "task_end":
				event["progress"] = sentinel
		(self.folder / (run_id + ".jsonl")).write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
		result = self.fold()
		self.assertTrue(result.ok)
		self.assertNotIn(sentinel, (self.home / "stats-ledger.json").read_text(encoding="utf-8"))
		self.assertEqual(result.ledger["rows"][0]["earned"], 30)

	def test_newer_version_does_not_block_prune_if_raw_reduction_fails(self):
		run_id, _ = synthetic_record(self.folder, "2026-07-01T08:00:00Z")
		path = self.home / "stats-ledger.json"
		path.write_text('{"v":7}', encoding="utf-8")
		with mock.patch.object(ledger, "_read_record", side_effect=PermissionError("synthetic denied")), mock.patch("run_lock.status", return_value=None):
			self.assertEqual(run_record.prune(str(self.folder), keep_max=0), 1)
		self.assertFalse((self.folder / (run_id + ".jsonl")).exists())
		self.assertEqual(path.read_text(encoding="utf-8"), '{"v":7}')

	def test_farmer_prune_after_open_run_skips_own_unfinished_record_and_index(self):
		# Keep real process identity, but derive record time from its birth rather
		# than the wall date, which can change between creation and settlement.
		created = ledger._created(os.getpid())
		self.assertIsInstance(created, int)
		self.now = datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=created // 10, seconds=2)
		self.stack.enter_context(mock.patch.object(run_record, "_timestamp", return_value=ledger._iso(self.now)))
		self.addCleanup(run_record.close_run, "checked", 0)
		recorder = run_record.open_run("cli", ["account-1"], {"check_only": True}, [], run_id=run_record.new_run_id(self.now), directory=str(self.folder))
		import run_lock
		with run_lock.acquire("farmer", recorder.run_id, directory=self.user_dir) as lock:
			self.assertIsNotNone(lock)
			self.assertEqual(run_record.prune(str(self.folder)), 0)
		value = json.loads((self.home / "stats-ledger.json").read_text(encoding="utf-8"))
		self.assertNotIn(recorder.run_id, value["index"])
		self.assertEqual(value["rows"], [])
		run_record.close_run("checked", 0)
		result = self.fold()
		self.assertTrue(result.ok)
		self.assertIn(recorder.run_id, result.ledger["index"])
		self.assertEqual(result.ledger["rows"][0]["check_count"], 1)

	def _unfinished(self):
		run_id, events = synthetic_record(self.folder, "2026-10-16T09:00:00Z", result=False)
		events[0].update(pid=None, supervisor_pid=123)
		path = self.folder / (run_id + ".jsonl")
		path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
		return run_id, events, path

	def test_live_supervisor_holds_unfinished_record_out_until_run_end_on_open_date(self):
		run_id, events, path = self._unfinished()
		with mock.patch.object(ledger, "_created", side_effect=lambda pid: 100 if pid == 123 else None):
			first = self.fold()
			self.assertNotIn(run_id, first.ledger["index"])
			self.assertEqual(first.ledger["rows"], [])
			with mock.patch.object(run_record, "_timestamp", return_value="2026-10-16T09:00:10.000Z"):
				run_record.append_event(str(path), "run_end", run_id, result="crashed", exit_code=9, duration_ms=1)
			second = self.fold()
		self.assertIn(run_id, second.ledger["index"])
		self.assertEqual(second.ledger["rows"][0]["run_count"], 1)

	def test_live_supervisor_on_sealed_day_is_folded_once_only_after_settlement(self):
		old, _ = synthetic_record(self.folder, "2026-10-16T08:00:00Z", earned=None)
		self.fold()
		(self.folder / (old + ".jsonl")).unlink()
		run_id, events, path = self._unfinished()
		for event in events:
			if event.get("type") == "state":
				event["earned_today"] = None
		path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
		with mock.patch.object(ledger, "_created", side_effect=lambda pid: 100 if pid == 123 else None):
			self.assertEqual(self.one()["earned"], 30)
			self.assertNotIn(run_id, self.fold().ledger["index"])
			events.append({"v": 1, "type": "run_end", "run": run_id, "ts": "2026-10-16T09:00:10.000Z", "result": "crashed"})
			path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
			row = self.one()
		self.assertEqual((row["earned"], row["run_count"]), (60, 2))
		self.assertEqual(self.one(), row)

	def test_dead_owners_without_run_end_fold_once_and_replay_is_unchanged(self):
		run_id, _, _ = self._unfinished()
		with mock.patch.object(ledger, "_created", return_value=None):
			first = self.fold()
			self.assertIn(run_id, first.ledger["index"])
			self.assertEqual(first.ledger["rows"][0]["run_count"], 1)
			with mock.patch.object(ledger, "_read_record", side_effect=AssertionError("settled interrupted record reread")):
				second = self.fold()
		self.assertEqual(first.ledger, second.ledger)

	def test_pid_reused_after_start_does_not_hold_record_back(self):
		run_id, events, _ = self._unfinished()
		at = ledger._at(events[0]["ts"]) + timedelta(seconds=1)
		elapsed = at - datetime(1601, 1, 1, tzinfo=timezone.utc)
		created = (elapsed.days * 86400 + elapsed.seconds) * 10_000_000
		with mock.patch.object(ledger, "_created", return_value=created):
			result = self.fold()
		self.assertIn(run_id, result.ledger["index"])
		self.assertEqual(result.ledger["rows"][0]["run_count"], 1)

	def test_creation_read_is_passive_and_real_current_process_predates_record(self):
		with mock.patch.object(os, "kill", side_effect=AssertionError("liveness must never signal")):
			created = ledger._created(os.getpid())
			self.assertIsInstance(created, int)
			after_creation = datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=created // 10, seconds=1)
			self.assertTrue(ledger._before(os.getpid(), after_creation))

	def test_record_farmer_pid_is_checked_even_without_lock_info(self):
		run_id, events, path = self._unfinished()
		events[0].update(pid=124, supervisor_pid=None)
		path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
		with mock.patch.object(ledger, "_created", side_effect=lambda pid: 100 if pid == 124 else None):
			result = self.fold()
		self.assertNotIn(run_id, result.ledger["index"])
		self.assertEqual(result.pending, frozenset({run_id}))

	def test_lock_info_supervisor_can_hold_a_record_without_start_pid(self):
		run_id, events, path = self._unfinished()
		events[0].update(pid=None, supervisor_pid=None)
		path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
		info = types.SimpleNamespace(run_id=run_id, pid=125, pid_created=90, supervisor_pid=126)
		with mock.patch.object(ledger, "_holder", return_value=info), mock.patch.object(ledger, "_created", side_effect=lambda pid: 100 if pid == 126 else None):
			result = self.fold()
		self.assertNotIn(run_id, result.ledger["index"])
		self.assertEqual(result.pending, frozenset({run_id}))

	def test_prune_keeps_live_supervisor_record_even_after_farmer_lock_is_gone(self):
		run_id, _, path = self._unfinished()
		with mock.patch.object(ledger, "_created", side_effect=lambda pid: 100 if pid == 123 else None), mock.patch("run_lock.status", return_value=None):
			self.assertEqual(run_record.prune(str(self.folder), keep_max=0), 0)
		self.assertTrue(path.exists())
		value = json.loads((self.home / "stats-ledger.json").read_text(encoding="utf-8"))
		self.assertNotIn(run_id, value["index"])

	def test_shared_reducer_places_day_after_run_before_daylight_midnight_correctly(self):
		run_id, events = synthetic_record(self.folder, "2026-03-29T21:59:56Z", gain=30, earned=30)
		rows = ledger.reduce_records({run_id: events}, now=datetime(2026, 3, 30, 12, tzinfo=timezone.utc), zone=Berlin2026(), user_data_dir=self.user_dir)
		self.assertEqual(set(date for date, _ in rows), {"2026-03-29", "2026-03-30"})
		self.assertEqual(rows[("2026-03-29", "account-1")]["earned"], 0)
		self.assertEqual(rows[("2026-03-30", "account-1")]["earned"], 30)

	def test_sealed_newer_unreadable_core_does_not_erase_previous_completion(self):
		old, _ = synthetic_record(self.folder, "2026-10-16T08:00:00Z", complete=True)
		self.fold()
		(self.folder / (old + ".jsonl")).unlink()
		synthetic_record(self.folder, "2026-10-16T09:00:00Z", complete=False, unreadable="searches")
		row = self.one()
		self.assertTrue(row["done"])
		self.assertEqual(row["done_at"], "2026-10-16T08:00:07.000Z")

	def test_failure_never_prints_and_file_diagnostic_is_code_only(self):
		output = io.StringIO()
		logfile = self.home / "proof.log"
		handler = logging.FileHandler(logfile, encoding="utf-8")
		root = logging.getLogger()
		root.addHandler(handler)
		try:
			with mock.patch.object(ledger, "_load", side_effect=OSError("private-person-path")), contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
				self.assertFalse(self.fold().ok)
		finally:
			root.removeHandler(handler)
			handler.close()
		self.assertEqual(output.getvalue(), "")
		self.assertEqual(logfile.read_text(encoding="utf-8").strip(), "ledger_fold_failed")


if __name__ == "__main__":
	unittest.main()
