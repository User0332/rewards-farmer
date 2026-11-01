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
import rewards_tasks
from selenium.common.exceptions import NoSuchElementException, TimeoutException


class RunRecordTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.directory = self.temp.name
		self.addCleanup(run_record.close_run, "ok", 0)

	def opened(self):
		return run_record.open_run("cli", ["default"], {}, directory=self.directory)

	def test_envelope_context_flush_and_close_stats(self):
		recorder = self.opened()
		with run_record.account_context("default"):
			for type, fields in (
				("account_start", {"profile": "synthetic"}),
				("state", {"phase": "before", "balance": 100, "earned_today": None}),
				("task_start", {"task": "searches", "name": "Required searches"}),
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
		for type in ("browser_failed", "decision", "warning", "future_event"):
			recorder.emit(type, future="東京", should_run=False)
		self.assertEqual(run_record.read_events(recorder.path)[-1]["future"], "東京")
		self.assertEqual(recorder.stats["accounts_decided_no"], 1)

	def test_tolerant_truncated_reader(self):
		path = Path(self.directory, "record.jsonl")
		path.write_bytes(b'{"type":"run_end","result":"crashed"}\n{"type":')
		self.assertEqual(len(run_record.read_events(str(path))), 1)
		self.assertEqual(run_record.read_events(str(path))[0]["result"], "crashed")
		self.assertEqual(run_record.read_events(str(path.with_name("missing"))), [])

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

	def test_run_id_uses_utc_and_four_lowercase_hex(self):
		self.assertRegex(run_record.new_run_id(datetime(2026, 10, 14, tzinfo=timezone.utc)), r"^20261014T000000Z-[0-9a-f]{4}$")

	def test_explicit_record_identity_and_stats_cover_browser_errors(self):
		recorder = run_record.open_run("cli", ["default"], {}, run_id="20261014T090000Z-0001", directory=self.directory)
		self.assertIs(run_record.current(), recorder)
		first = run_record.read_events(recorder.path)[0]
		self.assertEqual(first["pid"], os.getpid())
		self.assertNotIn("skip_tasks", first)
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

	def test_records_list_newest_first(self):
		now = datetime.now(timezone.utc)
		ids = [run_record.new_run_id(now - timedelta(seconds=step)) for step in range(3)]
		for run_id in ids:
			Path(self.directory, run_id + ".jsonl").write_text("", encoding="utf-8")
		self.assertEqual(run_record.list_run_ids(self.directory), ids)


class ResourceAndStatePaths(unittest.TestCase):
	def test_cli_paths_use_repo_root_without_app_environment(self):
		import importlib.util
		resource = str(Path(constants.__file__).resolve().parent.parent)
		with mock.patch.dict(os.environ, {}, clear=True):
			spec = importlib.util.spec_from_file_location("isolated_constants", constants.__file__)
			module = importlib.util.module_from_spec(spec)
			spec.loader.exec_module(module)
			self.assertEqual(module.REPO_ROOT, resource)
			self.assertEqual(module.DOTENV_PATH, str(Path(resource, ".env")))
			self.assertEqual(module.RUNS_DIR, str(Path(resource, "runs")))
			self.assertEqual(module.DEFAULT_ROOT_DATA_DIR, str(Path(resource, "data-dir")))


class TaskRecords(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.recorder = run_record.open_run("cli", ["sample"], {}, directory=self.temp.name)
		self.addCleanup(run_record.close_run, "ok", 0)
		self.tasks = rewards_tasks.RewardsTaskUtils.__new__(rewards_tasks.RewardsTaskUtils)
		self.tasks.restore_main_tab = mock.Mock()
		self.tasks.return_to_rewards_home = mock.Mock()
		for _, _, method in rewards_tasks.TASKS:
			setattr(self.tasks, method, mock.Mock())
		self.environment = mock.patch.dict(os.environ, {}, clear=True)
		self.environment.start()
		self.addCleanup(self.environment.stop)
		old = logging.root.manager.disable
		logging.disable(logging.CRITICAL)
		self.addCleanup(logging.disable, old)

	def run_tasks(self, **kwargs):
		with run_record.account_context("sample"):
			self.tasks.complete_all_tasks(**kwargs)
		return [event for event in run_record.read_events(self.recorder.path) if event["type"].startswith("task_")]

	def test_default_records_all_seven_tasks_without_extra_browser_reads(self):
		events = self.run_tasks()
		self.assertEqual([(event["type"], event["task"]) for event in events],
			[(kind, task_id) for task_id in rewards_tasks.TASK_IDS for kind in ("task_start", "task_end")])
		self.assertTrue(all(event["account"] == "sample" for event in events))
		for event in events[1::2]:
			self.assertEqual(event["outcome"], "ok")
			self.assertGreaterEqual(event["duration_ms"], 0)
		self.assertTrue(all("points_before" not in event and "points_after" not in event for event in events))
		self.assertEqual(self.tasks.restore_main_tab.call_count, 7)
		self.tasks.return_to_rewards_home.assert_not_called()

	def test_skip_and_fail_preserve_reports_progress_and_following_tasks(self):
		self.tasks.complete_bing_daily_set.side_effect = NoSuchElementException("not shipped")
		def timeout():
			self.tasks.progress = "opened the sidebar"
			raise TimeoutException("synthetic timeout")
		self.tasks.complete_visual_search.side_effect = timeout
		events = [event for event in self.run_tasks() if event["type"] == "task_end"]
		self.assertEqual([event["outcome"] for event in events], ["skip", "ok", "fail", "ok", "ok", "ok", "ok"])
		self.assertIn("NoSuchElementException", events[0]["detail"])
		self.assertEqual(events[2]["progress"], "opened the sidebar")
		self.assertIsNone(events[3]["progress"])
		self.assertEqual(self.recorder.stats["task_fail"], 1)
		self.assertEqual(self.tasks.return_to_rewards_home.call_count, 2)

	def test_only_quests_records_no_other_task_and_calls_no_other_method(self):
		events = self.run_tasks(only={"quests"})
		self.assertEqual([event["task"] for event in events], ["quests", "quests"])
		for task, _, method in rewards_tasks.TASKS:
			self.assertEqual(getattr(self.tasks, method).call_count, int(task == "quests"))
