import _bootstrap  # noqa: F401

import contextlib
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
import test_quest_only as producer
import constants
import main
import rewards_tasks
import rfapp
from rfapp import paths, runs, stats
from rfapp.bridge import Bridge
import stats_ledger


class QuestOnlyReaders(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.home = Path(self.temp.name)
		self.stack = contextlib.ExitStack()
		self.addCleanup(self.stack.close)
		self.stack.enter_context(mock.patch.object(paths, "data_home", return_value=self.home))
		self.stack.enter_context(mock.patch.object(paths, "runs_dir", return_value=self.home / "runs"))
		self.stack.enter_context(mock.patch.object(paths, "env_path", return_value=self.home / ".env"))
		self.stack.enter_context(mock.patch.object(constants, "DATA_HOME", str(self.home)))
		self.stack.enter_context(mock.patch.dict(os.environ, {"REWARDS_FARMER_DATA_HOME": str(self.home), "USER_DATA_DIR": str(self.home / "profiles")}))
		self.stack.enter_context(mock.patch.object(runs, "active_run_id", return_value=None))
		self.settings = types.SimpleNamespace(user_data_dir=lambda: self.home / "profiles", active_accounts=lambda: ["sample"], paused_accounts=lambda: [])
		self.stack.enter_context(mock.patch.object(rfapp, "settings", self.settings, create=True))
		self.stack.enter_context(mock.patch.dict(sys.modules, {"rfapp.settings": self.settings}))
		self.prefs = {"account_labels": {"sample": "Synthetic account"}, "goals": {}}
		self.api = Bridge(dependencies={"runs": runs, "settings": self.settings, "prefs": types.SimpleNamespace(load=lambda: self.prefs)})
		self.addCleanup(self.api._close)

	def check_readers(self, gain):
		result = producer.record_run(self.home, main.RunPlan(only_if_open=True), gain=gain)
		now = datetime.now(timezone.utc) + timedelta(seconds=1)
		response = self.api.get_run({"run_id": result.run_id})
		self.assertTrue(response["ok"], response)
		detail = response["data"]
		self.assertEqual(detail, runs.load_run(result.run_id, self.prefs["account_labels"], str(self.home / "profiles")))
		self.assertEqual((detail["kind"], detail["result"], detail["points_earned"]), ("run", "ok", gain))
		account = detail["accounts"][0]
		self.assertEqual(account["decision"]["reason"], "quest_unlocked")
		self.assertEqual([task["task"] for task in account["tasks"]], list(rewards_tasks.TASK_IDS))
		for task in account["tasks"]:
			if task["task"] == "quests":
				self.assertEqual((task["status"], task["points_before"], task["points_after"], task["points"]), ("ok", 1000, 1000 + gain, gain))
			else:
				self.assertEqual((task["status"], task["reason_key"]), ("not_run", "errors.task.not_run"))
				for field in ("points_before", "points_after", "points", "progress", "duration_ms"):
					self.assertIsNone(task[field], (task["task"], field))
		history = runs.history(labels=self.prefs["account_labels"], user_data_dir=str(self.home / "profiles"))
		self.assertEqual(len(history["runs"]), 1)
		self.assertEqual(history["runs"][0]["accounts"][0]["task_counts"], {"ok": 1, "skip": 0, "fail": 0, "off": 0})
		self.assertEqual(history["runs"][0]["points_earned"], gain)
		self.assertEqual(self.api.get_history({}), {"ok": True, "data": history})
		folded = stats_ledger.fold(self.home / "runs", data_home=self.home, user_data_dir=str(self.home / "profiles"), now=now)
		self.assertTrue(folded.ok)
		self.assertFalse(folded.newer)
		self.assertEqual(folded.pending, frozenset())
		self.assertEqual(len(folded.ledger["rows"]), 1)
		row = folded.ledger["rows"][0]
		expected = dict.fromkeys(rewards_tasks.TASK_IDS, 0)
		expected["quests"] = gain
		self.assertEqual(row["counted_sources"], expected)
		self.assertEqual((row["run_count"], row["check_count"], row["_counted_runs"]), (1, 0, [result.run_id]))
		self.assertTrue(all(row["sources"][task] is None for task in rewards_tasks.TASK_IDS if task != "quests"))
		value = stats.get_stats({}, settings={"schedule.mode": "automatic"}, prefs=self.prefs, now=now)
		self.assertEqual(value["sources"], {"points": expected, "runs": 1})
		day = next(day for day in value["days"] if any(run["run_id"] == result.run_id for run in day["runs"]))
		self.assertEqual(day["sources"], value["sources"])
		self.assertEqual(day["runs"][0]["points_earned"], gain)
		self.assertEqual((value["totals"]["run_count"], value["totals"]["check_count"]), (1, 0))

	def test_zero_point_quest_run_preserves_six_not_run_tasks_in_all_readers(self):
		self.check_readers(0)

	def test_quest_gain_cannot_be_counted_for_any_unselected_task(self):
		self.check_readers(5)


if __name__ == "__main__":
	unittest.main()
