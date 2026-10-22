import _bootstrap  # noqa: F401

import contextlib
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock
import zipfile

from rfapp import diagnostics, paths, runs
import element_selectors
import rewards_state
import rewards_tasks
import run_record
import stats_ledger


class TaskBalanceRecordTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.home = Path(self.temp.name)
		self.folder = self.home / "runs"
		self.user_dir = str(self.home / "profiles")
		self.at = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
		self.run_id = "20261017T120000Z-0071"
		self.time = 0.0
		self.stack = contextlib.ExitStack()
		self.addCleanup(self.stack.close)
		self.stack.enter_context(mock.patch.dict(os.environ, {rewards_tasks.SKIP_TASKS_ENV: ""}))
		self.stack.enter_context(mock.patch.object(paths, "runs_dir", return_value=self.folder))
		self.stack.enter_context(mock.patch.object(runs, "active_run_id", return_value=None))
		self.stack.enter_context(mock.patch.object(run_record, "_timestamp", side_effect=self.timestamp))
		self.stack.enter_context(mock.patch.object(diagnostics, "_run", side_effect=AssertionError("Unpatched diagnostics process")))

	def timestamp(self):
		return (self.at + timedelta(seconds=self.time)).isoformat().replace("+00:00", "Z")

	def sleep(self, seconds):
		self.time += seconds

	def produce(self, *, missing):
		tasks = rewards_tasks.RewardsTaskUtils.__new__(rewards_tasks.RewardsTaskUtils)
		tasks.main_window = "main"
		tasks.driver = types.SimpleNamespace(current_url="https://rewards.bing.com/earn", window_handles=["main"])
		tasks.tab_utils = types.SimpleNamespace(close_all_other_tabs=lambda **kwargs: None)
		balance, ready_at = [1000], [0.5]
		if missing:
			# Exercise the producer, not a preassembled diagnostic field. The
			# renderer masks this name before it can enter a run or export.
			dom = types.SimpleNamespace(execute_script=lambda *args: {"headers": 1, "header_values": [], "inner_size": [1131, 643],
				"buttons": [{"displayed": True, "text_shape": "aaaaaaaaa aaaa 99!\naaaaaaa...", "content_shape": "aaaaaaaaa aaaa 99!\naaaaaaa...",
					"text_value": None, "content_value": None, "part_values": []}]})
			tasks.elements = element_selectors.ElementSelectionUtils(dom)
			tasks.balance_reader = lambda: rewards_state.read_balance(dom, tasks.elements)
		else:
			tasks.balance_reader = lambda: None if self.time < ready_at[0] else balance[0]
		for _, _, method in rewards_tasks.TASKS:
			def task():
				balance[0] += 1
				ready_at[0] = self.time + 0.5
			setattr(tasks, method, task)
		run_record.open_run("app", ["account-1"], {}, [], run_id=self.run_id, directory=str(self.folder))
		try:
			with run_record.account_context("account-1"), mock.patch.object(rewards_tasks.logger, "info"):
				run_record.emit("account_start", profile=str(Path(self.user_dir, "account-1")))
				run_record.emit("state", phase="before", balance=1000)
				tasks.complete_all_tasks(clock=lambda: self.time, sleep=self.sleep)
				run_record.emit("state", phase="after", balance=balance[0])
				run_record.emit("account_end", started=True, ran_tasks=True, error_code=None)
		finally:
			run_record.close_run("ok", 0)
		self.record = self.folder / (self.run_id + ".jsonl")
		return run_record.read_events(str(self.record))

	def reduce(self, events):
		return stats_ledger.reduce_records({self.run_id: events}, user_data_dir=self.user_dir,
			now=self.at + timedelta(hours=1), zone=timezone.utc)

	def test_history_and_ledger_ignore_missing_reading_diagnostics_without_changing_meaning(self):
		events = self.produce(missing=True)
		ends = [event for event in events if event["type"] == "task_end"]
		self.assertEqual(len(ends), 7)
		self.assertTrue(all(set(event["balance_read"]) == {"before", "after"} for event in ends))
		with_diagnostics = runs.load_run(self.run_id, {"account-1": "Synthetic"}, self.user_dir)
		ledger_with = self.reduce(events)
		stripped = [{key: value for key, value in event.items() if key != "balance_read"} for event in events]
		self.record.write_text("".join(json.dumps(event) + "\n" for event in stripped), encoding="utf-8")
		self.assertEqual(runs.load_run(self.run_id, {"account-1": "Synthetic"}, self.user_dir), with_diagnostics)
		self.assertEqual(self.reduce(stripped), ledger_with)
		row = next(iter(ledger_with.values()))
		self.assertEqual(row["sources"]["other"], 7)
		self.assertEqual(with_diagnostics["points_earned"], 7)
		self.assertTrue(all(task["status"] == "ok" for task in with_diagnostics["accounts"][0]["tasks"]))

	def test_delayed_task_measurements_feed_unchanged_source_rules_individually(self):
		events = self.produce(missing=False)
		ends = [event for event in events if event["type"] == "task_end"]
		self.assertTrue(all("balance_read" not in event for event in ends))
		row = next(iter(self.reduce(events).values()))
		self.assertEqual(row["sources"], {**dict.fromkeys(rewards_tasks.TASK_IDS, 1), "other": 0})
		self.assertEqual(row["combined"], [])
		self.assertTrue(row["source_complete"])
		self.assertEqual(self.time, 4)

	def test_diagnostics_export_retains_the_new_safe_task_reading_evidence(self):
		events = self.produce(missing=True)
		index = self.home / "index.json"
		index.write_text('{"locales":[{"code":"en"}]}', encoding="utf-8")
		fake_paths = types.SimpleNamespace(resource=lambda *parts: index, runs_dir=lambda: self.folder,
			data_home=lambda: self.home, env_path=lambda: self.home / ".env", prefs_path=lambda: self.home / "app-prefs.json",
			task_name=lambda: "RewardsFarmerApp_test0000", is_frozen=lambda: False)
		modules = {"rfapp.prefs": types.SimpleNamespace(load=lambda: {}),
			"rfapp.i18n": types.SimpleNamespace(resolve=lambda *args: ("en", "ltr", "en-US"))}
		with mock.patch.object(diagnostics, "paths", fake_paths), mock.patch.dict(sys.modules, modules), \
			mock.patch.object(diagnostics, "_run", return_value=types.SimpleNamespace(returncode=1, stdout=b"")), \
			mock.patch.object(diagnostics, "_edge_version", return_value=None), \
			mock.patch.object(diagnostics, "_identity", return_value=("SyntheticUser", "SyntheticPc")):
			diagnostics.export(self.home / "diagnostics.zip")
		with zipfile.ZipFile(self.home / "diagnostics.zip") as archive:
			exported = [json.loads(line) for line in archive.read("runs/" + self.record.name).decode().splitlines()]
		self.assertEqual([event["balance_read"] for event in exported if event["type"] == "task_end"],
			[event["balance_read"] for event in events if event["type"] == "task_end"])
		self.assertNotIn("?", json.dumps([event["balance_read"] for event in exported if event["type"] == "task_end"]))
		encoded = json.dumps([event["balance_read"] for event in exported if event["type"] == "task_end"])
		self.assertNotIn("Synthetic", encoded)
		self.assertNotIn("Name", encoded)
		self.assertNotIn("42", encoded)
		observation = next(event for event in exported if event["type"] == "task_end")["balance_read"]["after"]["observation"]
		self.assertEqual(observation["headers"], 1)
		self.assertEqual(observation["profile_buttons"], 1)
		self.assertEqual(observation["buttons"][0]["text_shape"], "aaaaaaaaa aaaa 99!\naaaaaaa...")
		self.assertEqual(observation["inner_size"], [1131, 643])
