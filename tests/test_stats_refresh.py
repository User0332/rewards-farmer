"""F89 refreshes stored sentences without replaying any daily accounting."""

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import stats_ledger as ledger
from test_stats_counted import counted_record
from test_stats_progress import record, write_record

NOW = datetime(2026, 10, 24, 9, tzinfo=timezone.utc)
MARKER = "result_key_refresh"


def check_record(stamp, *, reason="unreadable", accounts=("sample",)):
	run_id, events = record(stamp, check=True, result="skipped", before=1105, after=1105,
		earned_before=105, earned_after=105)
	events[0]["accounts"] = list(accounts)
	events = [events[0]] + [dict(event, account=account) for account in accounts for event in events[1:-1]] + [events[-1]]
	for event in events:
		if event["type"] == "account_start":
			event["profile"] = "synthetic-profiles/" + event["account"]
		if event["type"] == "decision":
			event["reason"] = reason if event["account"] == accounts[-1] else "nothing_open"
	return run_id, events


def without_refresh(value, changed):
	value = copy.deepcopy(value)
	value.pop(MARKER, None)
	for row in value["rows"]:
		for summary in row["runs"] or []:
			if summary["run_id"] in changed:
				summary.pop("result_key")
	return value


class ResultKeyRefreshTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.home = Path(self.temp.name)
		self.folder = self.home / "runs"
		self.folder.mkdir()
		self.path = self.home / "stats-ledger.json"
		self.profiles = str(self.home / "profiles")

	def fold(self, *, module=ledger):
		return module.fold(self.folder, data_home=self.home, user_data_dir=self.profiles, now=NOW, zone=timezone.utc)

	def old_ledger(self):
		# Preserve real reducer output, including nonzero counted points and
		# unsplit parts. Only the historical summary keys are made pre-F88.
		cases = {
			"counted": counted_record("2026-10-23T06:00:00Z"),
			"parts": record("2026-10-23T07:00:00Z", before=1105, after=1112, earned_before=105,
				earned_after=112, result="partial", windows=(("visual_search", 1105, None, "fail", "not_ready"), ("misc_cards", None, 1112))),
			"unreadable": check_record("2026-10-23T08:00:00Z", accounts=("sample", "second")),
			"nothing_open": check_record("2026-10-23T09:00:00Z", reason="nothing_open"),
			"gone": check_record("2026-10-23T10:00:00Z"),
			"sealed": check_record("2026-10-22T08:00:00Z"),
		}
		rows = ledger.reduce_records(dict(cases.values()), user_data_dir=self.profiles, now=NOW, zone=timezone.utc)
		value = ledger._empty(NOW - timedelta(days=1))
		value["coverage_from"] = "2026-10-01"
		value["rows"] = [rows[key] for key in sorted(rows)]
		for name, (run_id, events) in cases.items():
			if name not in ("gone", "sealed"):
				path = write_record(self.folder, run_id, events)
				stat = path.stat()
				value["index"][run_id] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
		for row in value["rows"]:
			for summary in row["runs"]:
				summary["result_key"] = "errors.run." + summary["result"]
		self.path.write_text(json.dumps(value), encoding="utf-8")
		self.cases = cases
		return copy.deepcopy(value)

	def summaries(self, value, name):
		run_id = self.cases[name][0]
		return [summary for row in value["rows"] for summary in row["runs"] or [] if summary["run_id"] == run_id]

	def test_refresh_only_keys_once_preserving_every_other_field_and_warm_bytes(self):
		before = self.old_ledger()
		self.assertTrue(any(row["parts"] for row in before["rows"]))
		self.assertTrue(any(summary["problems"] for row in before["rows"] for summary in row["runs"]))
		self.assertEqual(sum(sum(row["counted_sources"].values()) for row in before["rows"]), 105)
		with mock.patch.object(ledger, "_read_record", wraps=ledger._read_record) as read, \
			mock.patch.object(ledger, "result_key", wraps=ledger.result_key) as shared:
			result = self.fold()
		self.assertTrue(result.ok)
		self.assertFalse(result.newer)
		self.assertEqual([summary["result_key"] for summary in self.summaries(result.ledger, "unreadable")], ["errors.run.unreadable"] * 2)
		for name in ("nothing_open", "gone", "sealed"):
			self.assertEqual(self.summaries(result.ledger, name), self.summaries(before, name))
		self.assertEqual(result.ledger["v"], 3)
		self.assertEqual(result.ledger[MARKER], 1)
		self.assertEqual(read.call_count, 4)
		self.assertEqual({call.args[0].stem for call in read.call_args_list}, set(before["index"]))
		self.assertEqual(shared.call_count, 5)
		self.assertEqual(without_refresh(result.ledger, {self.cases["unreadable"][0]}),
			without_refresh(before, {self.cases["unreadable"][0]}))
		persisted = self.path.read_bytes()
		self.assertEqual(json.loads(persisted), result.ledger)
		with mock.patch.object(ledger, "_read_record", wraps=ledger._read_record) as warm, \
			mock.patch.object(ledger, "replace_file", side_effect=AssertionError("warm write")):
			second = self.fold()
		self.assertTrue(second.ok)
		self.assertEqual(warm.call_count, 0)
		self.assertEqual(second.ledger, result.ledger)
		self.assertEqual(self.path.read_bytes(), persisted)

	def test_unreadable_record_keeps_its_key_does_not_fail_or_retry_on_warm_fold(self):
		for error in (PermissionError, FileNotFoundError, UnicodeError, RecursionError):
			with self.subTest(error=error.__name__):
				before = self.old_ledger()
				unreadable = self.cases["unreadable"][0]
				real_read = ledger._read_record
				def read(path):
					if path.stem == unreadable:
						raise error("synthetic unreadable record")
					return real_read(path)
				with mock.patch.object(ledger, "_read_record", side_effect=read) as reads, mock.patch.object(ledger, "_log") as log:
					result = self.fold()
				self.assertTrue(result.ok)
				self.assertEqual(reads.call_count, 4)
				self.assertEqual(without_refresh(result.ledger, set()), before)
				log.assert_called_once_with("ledger_result_key_refresh_unreadable:" + unreadable)
				with mock.patch.object(ledger, "_read_record", side_effect=AssertionError("refresh retried")):
					self.assertEqual(self.fold().ledger, result.ledger)

	def test_empty_or_unparseable_record_keeps_stored_key(self):
		for contents in ("", "not a JSON record\n[1,2]\n"):
			with self.subTest(contents=contents):
				before = self.old_ledger()
				run_id = self.cases["unreadable"][0]
				path = self.folder / (run_id + ".jsonl")
				path.write_text(contents, encoding="utf-8")
				stat = path.stat()
				before["index"][run_id] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
				self.path.write_text(json.dumps(before), encoding="utf-8")
				with mock.patch.object(ledger, "_log") as log:
					result = self.fold()
				self.assertTrue(result.ok)
				self.assertEqual(without_refresh(result.ledger, set()), before)
				log.assert_called_once_with("ledger_result_key_refresh_unreadable:" + run_id)

	def test_sealed_day_refreshes_retained_summary_without_replaying_totals(self):
		before = self.old_ledger()
		with mock.patch.object(ledger, "reduce_records", side_effect=AssertionError("sealed replay")):
			result = self.fold()
		self.assertTrue(result.ok)
		self.assertEqual(self.summaries(result.ledger, "unreadable")[0]["result_key"], "errors.run.unreadable")
		self.assertEqual(without_refresh(result.ledger, {self.cases["unreadable"][0]}),
			without_refresh(before, {self.cases["unreadable"][0]}))

	def test_atomic_write_failure_keeps_old_bytes_and_marker_absent_until_retry(self):
		before = self.old_ledger()
		original = self.path.read_bytes()
		with mock.patch.object(ledger, "replace_file", side_effect=PermissionError("synthetic sharing")), \
			mock.patch.object(ledger, "_log") as log:
			self.assertFalse(self.fold().ok)
		log.assert_called_once_with("ledger_fold_failed")
		self.assertEqual(self.path.read_bytes(), original)
		self.assertNotIn(MARKER, json.loads(original))
		self.assertEqual(sorted(path.name for path in self.home.iterdir()), ["runs", "stats-ledger.json", "stats-ledger.lock"])
		result = self.fold()
		self.assertTrue(result.ok)
		self.assertEqual(result.ledger[MARKER], 1)
		self.assertEqual(without_refresh(result.ledger, {self.cases["unreadable"][0]}),
			without_refresh(before, {self.cases["unreadable"][0]}))

	def test_refresh_commits_through_same_directory_fsync_and_replace(self):
		self.old_ledger()
		original = self.path.read_bytes()
		seen = []
		replace = ledger.replace_file
		fsync = ledger.os.fsync
		def synced(fd):
			seen.append("fsync")
			return fsync(fd)
		def atomic(source, target):
			self.assertEqual(Path(source).parent, self.home)
			self.assertEqual(target, self.path)
			self.assertEqual(self.path.read_bytes(), original)
			self.assertEqual(seen, ["fsync"])
			self.assertEqual(json.loads(Path(source).read_text(encoding="utf-8"))[MARKER], 1)
			seen.append("replace")
			return replace(source, target)
		with mock.patch.object(ledger.os, "fsync", side_effect=synced), mock.patch.object(ledger, "replace_file", side_effect=atomic):
			self.assertTrue(self.fold().ok)
		self.assertEqual(seen, ["fsync", "replace"])

	def test_new_records_use_normal_fold_after_refresh(self):
		self.old_ledger()
		first = self.fold()
		self.assertTrue(first.ok)
		run_id, events = check_record("2026-10-24T08:00:00Z")
		write_record(self.folder, run_id, events)
		with mock.patch.object(ledger, "_read_record", wraps=ledger._read_record) as reads:
			result = self.fold()
		self.assertTrue(result.ok)
		self.assertEqual(reads.call_count, 1)
		new = next(row for row in result.ledger["rows"] if row["date"] == "2026-10-24")
		self.assertEqual(new["runs"][0]["result_key"], "errors.run.unreadable")
		self.assertEqual(new["check_count"], 1)
		self.assertEqual(result.ledger["built_at"], ledger._iso(NOW))
		old_rows = [row for row in result.ledger["rows"] if row["date"] != "2026-10-24"]
		self.assertEqual(old_rows, first.ledger["rows"])

	def test_rows_without_summaries_are_not_reconstructed(self):
		before = self.old_ledger()
		for index, row in enumerate(before["rows"]):
			row["runs"] = None if index % 2 else []
		self.path.write_text(json.dumps(before), encoding="utf-8")
		with mock.patch.object(ledger, "_read_record", side_effect=AssertionError("no summary to refresh")):
			result = self.fold()
		self.assertTrue(result.ok)
		self.assertEqual(without_refresh(result.ledger, set()), before)
		self.assertEqual(result.ledger[MARKER], 1)

	def test_b21_loads_refreshed_v3_without_rebuild_or_record_reads(self):
		fixture = (Path(__file__).parent / "fixtures/stats_ledger_v3.py").read_bytes()
		self.assertEqual(hashlib.sha256(fixture.replace(b"\r\n", b"\n")).hexdigest(),
			"cbf60212bb9074555007ab5e6f2525c79eb1e096b6cb65df6b171805118728f9")
		module = types.ModuleType("installed_stats_ledger_b21")
		with mock.patch.dict(sys.modules, {module.__name__: module}):
			exec(compile(fixture, "ae7748a-stats_ledger.py", "exec"), module.__dict__)
			before = self.old_ledger()
			current = self.fold()
			self.assertTrue(current.ok)
			self.assertEqual(current.ledger[MARKER], 1)
			stored = self.path.read_bytes()
			with mock.patch.object(module, "_log") as log, mock.patch.object(module, "replace_file", side_effect=AssertionError("B21 rewrote ledger")), \
				mock.patch.object(module, "_read_record", wraps=module._read_record) as read:
				loaded, newer = module._load(self.path, NOW)
				rollback = self.fold(module=module)
			self.assertFalse(newer)
			self.assertTrue(rollback.ok)
			self.assertFalse(rollback.newer)
			self.assertEqual(read.call_count, 0)
			self.assertEqual(loaded, current.ledger)
			self.assertEqual(rollback.ledger, current.ledger)
			self.assertEqual(without_refresh(rollback.ledger, {self.cases["unreadable"][0]}),
				without_refresh(before, {self.cases["unreadable"][0]}))
			self.assertEqual(self.path.read_bytes(), stored)
			log.assert_not_called()
			self.assertEqual(list(self.home.glob("stats-ledger.bad-*.json")), [])
			# B21 keeps the marker when it folds a later check with its old key.
			run_id, events = check_record("2026-10-24T08:00:00Z")
			write_record(self.folder, run_id, events)
			with mock.patch.object(module, "_log") as log:
				later = self.fold(module=module)
			self.assertTrue(later.ok and not later.newer)
			self.assertEqual(later.ledger[MARKER], 1)
			new = next(row for row in later.ledger["rows"] if row["date"] == "2026-10-24")
			self.assertEqual(new["runs"][0]["result_key"], "errors.run.skipped")
			self.assertEqual([row for row in later.ledger["rows"] if row["date"] != "2026-10-24"], current.ledger["rows"])
			log.assert_not_called()
			self.assertEqual(list(self.home.glob("stats-ledger.bad-*.json")), [])
			with mock.patch.object(ledger, "_read_record", side_effect=AssertionError("upgrade retried refresh")):
				self.assertEqual(self.fold().ledger, later.ledger)


if __name__ == "__main__":
	unittest.main()
