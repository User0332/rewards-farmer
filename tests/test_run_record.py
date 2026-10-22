import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
import json
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import constants
import run_record


class RunRecordTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.directory = self.temp.name
		self.addCleanup(run_record.close_run, "ok", 0)

	def opened(self):
		return run_record.open_run("cli", ["default"], {}, [], directory=self.directory)

	def test_envelope_context_flush_and_close_stats(self):
		recorder = self.opened()
		with run_record.account_context("default"):
			for type, fields in (
				("account_start", {"profile": "synthetic"}),
				("state", {"phase": "before", "balance": 100, "earned_today": None}),
				("task_start", {"task": "searches", "name": "Required searches", "points_before": 100}),
				("task_end", {"task": "searches", "outcome": "fail", "reason_key": "error"}),
				("account_end", {"started": True, "ran_tasks": True, "error_code": None}),
			):
				run_record.emit(type, **fields)
			self.assertEqual(run_record.read_events(recorder.path)[-1]["account"], "default")
		with mock.patch.object(run_record.os, "fsync", wraps=os.fsync) as sync:
			run_record.close_run("partial", 0)
			sync.assert_called_once()
		events = run_record.read_events(recorder.path)
		for event in events:
			self.assertEqual(event["v"], 1)
			self.assertEqual(event["run"], recorder.run_id)
			self.assertRegex(event["ts"], r"\.\d{3}Z$")
		self.assertEqual(events[-1]["accounts_started"], 1)
		self.assertEqual(events[-1]["accounts_ran"], 1)
		self.assertEqual(recorder.stats["task_fail"], 1)
		self.assertNotIn(b"\r\n", Path(recorder.path).read_bytes())
		self.assertIsNone(run_record.current())
		recorder.close("failed", 1)
		self.assertEqual(len(run_record.read_events(recorder.path)), len(events))

	def test_all_events_remain_structured_and_unknown_fields_survive(self):
		recorder = self.opened()
		for type in ("browser_failed", "decision", "search_points", "card_left_open", "warning", "stop_requested", "notified", "future_event"):
			recorder.emit(type, future="東京", should_run=False)
		self.assertEqual(run_record.read_events(recorder.path)[-1]["future"], "東京")
		self.assertEqual(recorder.stats["accounts_decided_no"], 1)

	def test_append_and_tolerant_truncated_reader(self):
		path = Path(self.directory, "record.jsonl")
		run_record.append_event(str(path), "run_end", "run", result="crashed", written_by="supervisor")
		with path.open("ab") as stream:
			stream.write(b'{"type":')
		self.assertEqual(len(run_record.read_events(str(path))), 1)
		self.assertEqual(run_record.read_events(str(path))[0]["result"], "crashed")
		self.assertEqual(run_record.read_events(str(path.with_name("missing"))), [])
		run_record.append_event(str(path), "run_end", "run", result="stopped", written_by="app")
		self.assertEqual(run_record.read_events(str(path))[-1]["result"], "stopped")
		self.assertEqual(len(run_record.read_events(str(path))), 2)

	def test_unwritable_record_disables_once_and_emit_never_raises(self):
		with mock.patch("builtins.open", side_effect=PermissionError("read only")), self.assertLogs(run_record.logger, level="DEBUG") as logs:
			recorder = self.opened()
			recorder.emit("warning", code="signed_out")
			recorder.close("checked", 0)
		self.assertEqual(len(logs.output), 1)
		with mock.patch.object(recorder, "emit", side_effect=RuntimeError()):
			run_record.emit("future")

	def test_warning_handler_exact_pairs_and_once_per_account(self):
		self.assertEqual(run_record.WARNING_LOG_PAIRS, (
			("llm_utils", "LLM is offline or unavailable", "llm_unavailable"),
			("queries", "No query source reachable", "search_terms_unavailable"),
		))
		recorder = self.opened()
		for account in ("one", "two"):
			with run_record.account_context(account):
				for _ in range(2):
					logging.getLogger("llm_utils").warning("LLM is offline or unavailable (%s)", "test")
				logging.getLogger("queries").warning("No query source reachable (%s)", "test")
				logging.getLogger("unrelated").warning("LLM is offline or unavailable")
		warnings = [e for e in run_record.read_events(recorder.path) if e["type"] == "warning"]
		self.assertEqual([(e["account"], e["code"]) for e in warnings], [("one", "llm_unavailable"), ("one", "search_terms_unavailable"), ("two", "llm_unavailable"), ("two", "search_terms_unavailable")])

	def test_local_day_and_per_account_cards_include_current_flushed_record(self):
		recorder = self.opened()
		with run_record.account_context("one"):
			recorder.emit("card_left_open", description="Sticky card")
		recorder.emit("card_left_open", account="two", description="Other")
		today = datetime.now(timezone.utc).astimezone().date()
		self.assertEqual(run_record.cards_left_open_today("one", self.directory, today), frozenset({"Sticky card"}))
		self.assertEqual(run_record.cards_left_open_today("one", self.directory, today - timedelta(days=1)), frozenset())

	def test_retention_removes_bundles_but_never_active_or_signin(self):
		now = datetime.now(timezone.utc)
		old = run_record.new_run_id(now - timedelta(days=91))
		new = run_record.new_run_id(now)
		for run_id in (old, new):
			for suffix in (".jsonl", ".log", ".stop"):
				Path(self.directory, run_id + suffix).write_text("", encoding="utf-8")
		Path(self.directory, "signin-root.json").write_text("{}", encoding="utf-8")
		with mock.patch("run_lock.status", return_value=mock.Mock(run_id=new)):
			self.assertEqual(run_record.prune(self.directory, keep_max=0), 1)
		self.assertFalse(Path(self.directory, old + ".log").exists())
		self.assertTrue(Path(self.directory, new + ".jsonl").exists())
		self.assertTrue(Path(self.directory, "signin-root.json").exists())
		self.assertEqual(run_record.list_run_ids(self.directory), [new])

	def test_run_id_uses_utc_and_four_lowercase_hex(self):
		self.assertRegex(run_record.new_run_id(datetime(2026, 10, 14, tzinfo=timezone.utc)), r"^20261014T000000Z-[0-9a-f]{4}$")

	def test_current_recorder_env_identity_and_stats_cover_browser_errors(self):
		with mock.patch.dict(os.environ, {run_record.RUN_ID_ENV: "20261014T090000Z-0001", "REWARDS_SUPERVISOR_PID": "42", run_record.APP_VERSION_ENV: "0.1.0"}):
			recorder = self.opened()
		self.assertIs(run_record.current(), recorder)
		first = run_record.read_events(recorder.path)[0]
		self.assertEqual(first["supervisor_pid"], 42)
		self.assertEqual(first["app_version"], "0.1.0")
		self.assertEqual(recorder.run_id, "20261014T090000Z-0001")
		recorder.emit("browser_failed", account="one", code="unknown", detail="synthetic")
		recorder.emit("account_end", account="one", started=False, ran_tasks=False, error_code="error")
		self.assertEqual(recorder.stats["browser_failed"], 1)
		self.assertEqual(recorder.stats["account_error"], 1)
		self.assertEqual(recorder.stats["accounts_started"], 0)

	def test_unserializable_event_disables_then_never_fails_close(self):
		recorder = self.opened()
		with self.assertLogs(run_record.logger, level="DEBUG") as logs:
			recorder.emit("unknown", invalid=object())
			recorder.emit("warning", code="signed_out")
			recorder.close("ok", 0)
		self.assertEqual(len(logs.output), 1)

	def test_fsync_failure_does_not_fail_a_run(self):
		self.opened()
		with mock.patch.object(run_record.os, "fsync", side_effect=OSError("disk failed")), self.assertLogs(run_record.logger, level="DEBUG"):
			run_record.close_run("ok", 0)
		self.assertIsNone(run_record.current())

	def test_newest_first_and_keep_max_preserve_only_requested_records(self):
		now = datetime.now(timezone.utc)
		ids = [run_record.new_run_id(now - timedelta(seconds=step)) for step in range(3)]
		for run_id in ids:
			Path(self.directory, run_id + ".jsonl").write_text("", encoding="utf-8")
		self.assertEqual(run_record.list_run_ids(self.directory), ids)
		with mock.patch("run_lock.status", return_value=None):
			self.assertEqual(run_record.prune(self.directory, keep_max=1), 2)
		self.assertEqual(run_record.list_run_ids(self.directory), ids[:1])


class ResourceAndStatePaths(unittest.TestCase):
	def test_resource_root_and_writable_home_are_independent(self):
		import importlib.util
		resource = str(Path(constants.__file__).resolve().parent.parent)
		with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {"REWARDS_FARMER_RESOURCE_ROOT": resource, "REWARDS_FARMER_DATA_HOME": folder}):
			spec = importlib.util.spec_from_file_location("isolated_constants", constants.__file__)
			module = importlib.util.module_from_spec(spec)
			spec.loader.exec_module(module)
			self.assertEqual(module.REPO_ROOT, resource)
			self.assertEqual(module.DATA_HOME, folder)
			self.assertEqual(module.DOTENV_PATH, str(Path(folder, ".env")))
			self.assertEqual(module.RUNS_DIR, str(Path(folder, "runs")))
			self.assertEqual(module.DEFAULT_ROOT_DATA_DIR, str(Path(folder, "data-dir")))
