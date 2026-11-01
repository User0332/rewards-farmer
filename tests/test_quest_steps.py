"""Persistent quest links cannot trigger another conditional run today."""

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import constants
import element_selectors
import main
import quest_steps
import rewards_state
import rewards_tasks
import run_record
from test_rewards_state import StateDriver, link

DAY = "2026-10-24"
CARD = "https://rewards.bing.com/earn/quest/card"
HREF = "https://www.bing.com/search?q=quest"
TEXT = "Start searching"
ROW = (CARD, HREF, TEXT)


class QuestSteps(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.home = Path(self.temp.name)
		self.runs = self.home / "runs"
		self.runs.mkdir()
		self.stack = ExitStack()
		self.addCleanup(self.stack.close)
		self.stack.enter_context(mock.patch.object(constants, "RUNS_DIR", str(self.runs)))
		self.real_today = quest_steps._today
		self.day = self.stack.enter_context(mock.patch.object(quest_steps, "_today", return_value=DAY))
		self.stack.enter_context(run_record.account_context("sample"))
		self.path = self.runs / quest_steps.FILENAME
		self.stack.enter_context(mock.patch.object(rewards_state.time, "sleep"))

	def read(self, *, href=HREF, text=TEXT, card="/earn/quest/card"):
		driver = StateDriver(quests={card: [link(href, text)]})
		return rewards_state.read_state(driver, element_selectors.ElementSelectionUtils(driver), timeout=0)

	def test_real_task_click_is_recorded_and_same_day_reader_changes_decision(self):
		before = self.read()
		self.assertEqual(main.decide(before, main.RunPlan(only_if_open=True, min_points=15)), (True, "quest_unlocked"))
		driver = StateDriver(quests={"/earn/quest/card": [link(HREF, TEXT)]})
		tasks = rewards_tasks.RewardsTaskUtils.__new__(rewards_tasks.RewardsTaskUtils)
		tasks.driver = driver
		tasks.elements = element_selectors.ElementSelectionUtils(driver)
		tasks.tab_utils = types.SimpleNamespace(ensure_focus=lambda: None, close_all_other_tabs=lambda: None)
		tasks.mouse = mock.Mock()
		with mock.patch.object(tasks, "wait_for_element", side_effect=lambda getter: getter()), mock.patch.object(rewards_tasks.time, "sleep"):
			self.assertEqual(tasks.complete_single_quest(CARD, 1), 1)
		tasks.mouse.human_like_click.assert_called_once()
		self.assertEqual(quest_steps.clicked_today(), frozenset({ROW}))
		after = self.read()
		self.assertEqual((before.quests_open, after.quests_open, after.open_points), (1, 0, 0))
		self.assertEqual(main.decide(after, main.RunPlan(only_if_open=True, min_points=15)), (False, "nothing_open"))

	def test_without_saved_identity_unchanged_visible_link_repeats_run_decision(self):
		quest_steps.record_click(*ROW)
		self.assertEqual(self.read().quests_open, 0)
		with mock.patch.object(quest_steps, "clicked_today", return_value=frozenset()):
			state = self.read()
		self.assertEqual(main.decide(state, main.RunPlan(only_if_open=True, min_points=15)), (True, "quest_unlocked"))

	def test_next_day_changed_link_other_card_or_other_account_stays_open(self):
		quest_steps.record_click(*ROW)
		self.assertEqual(self.read().quests_open, 0)
		self.assertEqual(self.read(href=HREF + "&step=2").quests_open, 1)
		self.assertEqual(self.read(text="Search again").quests_open, 1)
		self.assertEqual(self.read(card="/earn/quest/other").quests_open, 1)
		with run_record.account_context("other"):
			self.assertEqual(self.read().quests_open, 1)
		self.day.return_value = "2026-10-25"
		self.assertEqual(self.read().quests_open, 1)

	def test_atomic_write_deduplicates_and_keeps_accounts(self):
		original = quest_steps.replace_file
		def replace(source, target):
			self.assertEqual(Path(source).parent, self.runs)
			self.assertEqual(target, self.path)
			return original(source, target)
		with mock.patch.object(quest_steps, "replace_file", side_effect=replace) as replaced:
			quest_steps.record_click(*ROW)
		self.assertEqual(replaced.call_count, 1)
		quest_steps.record_click(*ROW)
		with run_record.account_context("other"):
			quest_steps.record_click(*ROW)
		value = json.loads(self.path.read_text(encoding="utf-8"))
		self.assertEqual(value, {"v": 1, "date": DAY, "accounts": {"sample": [list(ROW)], "other": [list(ROW)]}})
		self.assertEqual(list(self.runs.iterdir()), [self.path])
		self.assertFalse((self.home / quest_steps.FILENAME).exists())

	def test_missing_corrupt_wrong_date_or_oversized_store_never_hides_work(self):
		self.assertEqual(quest_steps.clicked_today(), frozenset())
		for text in ("", "{", "[]", "null", json.dumps({"v": 1, "date": "2026-10-25", "accounts": {"sample": [list(ROW)]}}),
			json.dumps({"v": True, "date": DAY, "accounts": {"sample": [list(ROW)]}}), " " * (quest_steps.MAX_BYTES + 1)):
			with self.subTest(text=text[:60]):
				self.path.write_text(text, encoding="utf-8")
				self.assertEqual(quest_steps.clicked_today(), frozenset())
		quest_steps.record_click(*ROW)
		with mock.patch.object(quest_steps, "read_file", side_effect=PermissionError("synthetic")):
			self.assertEqual(quest_steps.clicked_today(), frozenset())

	def test_failed_write_preserves_old_bytes_and_has_no_new_console_warning(self):
		quest_steps.record_click(*ROW)
		before = self.path.read_bytes()
		with mock.patch.object(quest_steps, "replace_file", side_effect=PermissionError("synthetic")), mock.patch.object(quest_steps.logger, "warning") as warning, mock.patch.object(quest_steps.logger, "debug") as diagnostic:
			quest_steps.record_click(CARD, HREF, "new step")
		self.assertEqual(self.path.read_bytes(), before)
		self.assertEqual(list(self.runs.iterdir()), [self.path])
		warning.assert_not_called()
		diagnostic.assert_called_once_with("Could not save quest steps clicked today (%s)", "PermissionError")

	def test_capacity_no_context_and_local_day_bounds(self):
		quest_steps.record_click(*ROW)
		before = self.path.read_bytes()
		with mock.patch.object(quest_steps, "MAX_STEPS", 1):
			quest_steps.record_click(CARD, HREF, "extra")
		self.assertEqual(self.path.read_bytes(), before)
		with run_record.account_context(None), mock.patch.object(quest_steps, "_path", side_effect=AssertionError("no account")):
			quest_steps.record_click(*ROW)
			self.assertEqual(quest_steps.clicked_today(), frozenset())
		self.day.return_value = "2026-10-25"
		quest_steps.record_click(CARD, HREF, "tomorrow")
		self.assertEqual(json.loads(self.path.read_text())["accounts"], {"sample": [[CARD, HREF, "tomorrow"]]})
		moment = datetime(2026, 10, 24, 23, tzinfo=timezone.utc)
		for zone, expected in ((timezone.utc, "2026-10-24"), (timezone(timedelta(hours=2)), "2026-10-25")):
			current = types.SimpleNamespace(astimezone=mock.Mock(return_value=moment.astimezone(zone)))
			with self.subTest(zone=zone), mock.patch.object(quest_steps, "datetime", types.SimpleNamespace(now=lambda: current)):
				self.assertEqual(self.real_today(), expected)
			current.astimezone.assert_called_once_with()


if __name__ == "__main__":
	unittest.main()
