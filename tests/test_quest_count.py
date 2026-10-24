"""Same-day quest scheduling from real task clicks on synthetic pages."""

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import contextlib
import json
import logging
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
import run_record
import test_quest_entry as fixture

DAY = "2026-10-23"
CARD = "https://rewards.bing.com" + fixture.HALLOWEEN
HREF = "https://www.bing.com/search?q=Halloween&form=synthetic"
TEXT = "Start searching"
ROW = (CARD, HREF, TEXT)


class StoreFixture(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.home = Path(self.temp.name)
		self.stack = contextlib.ExitStack()
		self.addCleanup(self.stack.close)
		self.stack.enter_context(mock.patch.object(constants, "DATA_HOME", str(self.home)))
		self.real_today = quest_steps._today
		self.day = self.stack.enter_context(mock.patch.object(quest_steps, "_today", return_value=DAY))
		self.stack.enter_context(run_record.account_context("synthetic"))
		self.path = self.home / quest_steps.FILENAME

	def seed(self, value):
		self.path.write_text(json.dumps(value), encoding="utf-8")

	def value(self, *, date=DAY, account="synthetic", rows=None):
		return {"v": 1, "date": date, "accounts": {account: [list(ROW)] if rows is None else rows}}


class QuestStepStoreTests(StoreFixture):
	def test_atomic_record_uses_data_home_and_round_trips_exact_identity(self):
		observed = []
		original = quest_steps.replace_file
		def replace(source, target):
			self.assertEqual(Path(source).parent, self.home)
			self.assertFalse(self.path.exists())
			observed.append(json.loads(Path(source).read_text(encoding="utf-8")))
			original(source, target)
		with mock.patch.object(quest_steps, "replace_file", side_effect=replace):
			quest_steps.record_click(*ROW)
		self.assertEqual(observed, [self.value()])
		self.assertEqual(quest_steps.clicked_today(), frozenset({ROW}))
		self.assertEqual(list(self.home.iterdir()), [self.path])

	def test_duplicate_writes_do_not_grow_and_same_day_accounts_are_preserved(self):
		for _ in range(3):
			quest_steps.record_click(*ROW)
		with run_record.account_context("other"):
			quest_steps.record_click(CARD, HREF, "Other text")
			self.assertEqual(quest_steps.clicked_today(), frozenset({(CARD, HREF, "Other text")}))
		self.assertEqual(quest_steps.clicked_today(), frozenset({ROW}))
		self.assertEqual(sum(map(len, json.loads(self.path.read_text())["accounts"].values())), 2)

	def test_missing_empty_corrupt_malformed_and_future_records_fail_open(self):
		self.assertEqual(quest_steps.clicked_today(), frozenset())
		for content in ("", "{", "[]", "null", json.dumps(self.value(date="2026-10-24")),
			json.dumps(self.value(date="2026-10-22")), json.dumps(dict(self.value(), v=True)),
			json.dumps(dict(self.value(), v=2)), json.dumps(dict(self.value(), accounts=[])),
			json.dumps(self.value(rows=[[CARD, HREF]])), json.dumps(self.value(rows=[[CARD, HREF, 1]])),
			json.dumps(self.value(rows=[[CARD, HREF, ""]])), json.dumps(dict(self.value(), extra=1)),
			json.dumps({"v": 1, "date": DAY, "accounts": {"synthetic": [list(ROW)], "other": ["bad"]}})):
			with self.subTest(content=content[:70]):
				self.path.write_text(content, encoding="utf-8")
				self.assertEqual(quest_steps.clicked_today(), frozenset())

	def test_unreadable_record_fails_open(self):
		self.seed(self.value())
		with mock.patch.object(quest_steps, "read_file", side_effect=PermissionError("synthetic")):
			self.assertEqual(quest_steps.clicked_today(), frozenset())

	def test_old_day_is_pruned_on_write_including_other_accounts(self):
		self.seed(self.value(date="2026-10-22", account="old-account"))
		quest_steps.record_click(*ROW)
		self.assertEqual(json.loads(self.path.read_text()), self.value())

	def test_future_or_bad_store_is_replaced_with_only_the_current_click(self):
		for value in (self.value(date="2026-10-24", account="future"), {"bad": True}):
			with self.subTest(value=value):
				self.seed(value)
				quest_steps.record_click(*ROW)
				self.assertEqual(json.loads(self.path.read_text()), self.value())

	def test_no_account_context_does_not_touch_a_file(self):
		with run_record.account_context(None), mock.patch.object(quest_steps, "_path", side_effect=AssertionError("no account")):
			quest_steps.record_click(*ROW)
			self.assertEqual(quest_steps.clicked_today(), frozenset())
		self.assertFalse(self.path.exists())

	def test_oversized_record_is_not_used(self):
		self.path.write_text(" " * (quest_steps.MAX_BYTES + 1), encoding="utf-8")
		self.assertEqual(quest_steps.clicked_today(), frozenset())

	def test_entry_limit_does_not_evict_known_steps_or_publish_a_partial_record(self):
		self.seed(self.value())
		before = self.path.read_bytes()
		with mock.patch.object(quest_steps, "MAX_STEPS", 1), self.assertLogs(quest_steps.logger, level="WARNING") as output:
			quest_steps.record_click(CARD, HREF, "New text")
		self.assertEqual(self.path.read_bytes(), before)
		self.assertEqual(len(output.records), 1)
		self.assertEqual(quest_steps.clicked_today(), frozenset({ROW}))

	def test_replace_failure_keeps_old_bytes_and_removes_temporary_file(self):
		self.seed(self.value())
		before = self.path.read_bytes()
		with mock.patch.object(quest_steps, "replace_file", side_effect=PermissionError("synthetic")), self.assertLogs(quest_steps.logger, level="WARNING") as output:
			quest_steps.record_click(CARD, HREF, "New text")
		self.assertEqual(self.path.read_bytes(), before)
		self.assertEqual(list(self.home.iterdir()), [self.path])
		self.assertEqual([record.getMessage() for record in output.records], ["Could not save quest steps clicked today (PermissionError)"])

	def test_local_day_comes_from_machine_zone_not_utc(self):
		for offset, expected in ((2, "2026-10-24"), (-4, "2026-10-23")):
			local = datetime(2026, 10, 23, 22, 30, tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=offset)))
			moment = mock.Mock()
			moment.astimezone.return_value = local
			now = mock.Mock(return_value=moment)
			with mock.patch.object(quest_steps, "datetime", types.SimpleNamespace(now=now)):
				self.assertEqual(self.real_today(), expected)
			moment.astimezone.assert_called_once_with()
			now.assert_called_once_with()


class QuestCountTests(fixture.QuestFixture):
	def setUp(self):
		super().setUp()
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.home = Path(self.temp.name)
		self.stack.enter_context(mock.patch.object(constants, "DATA_HOME", str(self.home)))
		self.day = self.stack.enter_context(mock.patch.object(quest_steps, "_today", return_value=DAY))
		self.stack.enter_context(run_record.account_context("synthetic"))
		self.path = self.home / quest_steps.FILENAME

	def read(self, driver, **kwargs):
		return rewards_state.read_state(driver, element_selectors.ElementSelectionUtils(driver), timeout=0, **kwargs)

	def clicked(self):
		worker = self.worker()
		self.run_quests(worker, only={"quests"})
		self.assertEqual(self.quest_end()["outcome"], "ok")
		return worker

	def test_persistent_step_clicked_today_is_not_open_in_a_later_read(self):
		worker = self.worker()
		before = self.read(worker.driver)
		self.assertEqual((before.quests_open, before.open_points), (1, 0))
		self.run_quests(worker, only={"quests"})
		self.assertEqual(self.quest_end()["outcome"], "ok")
		with mock.patch.object(rewards_state.logger, "info", wraps=rewards_state.logger.info) as output:
			after = self.read(worker.driver, phase="after")
		self.assertEqual((after.quests_open, after.open_points), (0, 0))
		self.assertEqual(main.decide(after, main.RunPlan(only_if_open=True, min_points=15)), (False, "nothing_open"))
		output.assert_called_once_with("quest steps already clicked today and unchanged: %s (not counted)", 1)
		for field, value in asdict(before).items():
			if field != "quests_open":
				self.assertEqual(getattr(after, field), value, field)
		# A later check may have a new driver and no recorder, but the same account.
		self.assertEqual(self.read(fixture.QuestPage(self.clock)).quests_open, 0)

	def test_real_account_run_then_same_day_check_does_not_start_another_task(self):
		def run(driver):
			with contextlib.ExitStack() as stack:
				stack.enter_context(mock.patch.object(main, "_plan", main.RunPlan(read_state=True, only_if_open=True, min_points=15)))
				stack.enter_context(mock.patch.object(main, "_account_started", False))
				stack.enter_context(mock.patch.object(main, "_account_ran", False))
				stack.enter_context(mock.patch.object(main.browser, "start_driver", return_value=driver))
				stack.enter_context(mock.patch.object(main.desktop_utils, "prepare_desktop_before_launch", return_value=True))
				stack.enter_context(mock.patch.object(main.desktop_utils, "switch_back_after_launch"))
				stack.enter_context(mock.patch.object(run_record, "cards_left_open_today", return_value=frozenset()))
				self.assertTrue(main.run_account(types.SimpleNamespace(name="synthetic", user_data_dir="synthetic-profile")))
		first, later = fixture.QuestPage(self.clock), fixture.QuestPage(self.clock)
		with self.assertLogs(level="INFO") as output:
			run(first)
			run(later)
		self.assertEqual([call.kwargs["reason"] for call in self.emit.call_args_list if call.args == ("decision",)], ["quest_unlocked", "nothing_open"])
		self.assertEqual([call.kwargs["task"] for call in self.emit.call_args_list if call.args == ("task_start",)], ["quests"])
		self.assertEqual([call.kwargs["quests_open"] for call in self.emit.call_args_list if call.args == ("state",)], [1, 0, 0])
		self.assertEqual(first.activity_clicks, [(CARD, TEXT)])
		self.assertEqual(later.activity_clicks, [])
		self.assertEqual((first.quit_calls, later.quit_calls), (1, 1))
		self.assertEqual(sum(record.getMessage() == "quest steps already clicked today and unchanged: 1 (not counted)" for record in output.records), 2)

	def test_multiple_omissions_log_once_and_do_not_hide_changed_links(self):
		self.clicked()
		monthly = "https://rewards.bing.com" + fixture.MONTHLY
		quest_steps.record_click(monthly, HREF, TEXT)
		driver = fixture.QuestPage(self.clock, quests={fixture.HALLOWEEN: fixture.activities(fixture.SEARCH + fixture.SEARCH),
			fixture.MONTHLY: fixture.activities(fixture.SEARCH + fixture.SEARCH.replace("Start searching", "Different step"))})
		with self.assertLogs(rewards_state.logger, level="INFO") as output:
			state = self.read(driver)
		self.assertEqual(state.quests_open, 1)
		self.assertEqual([record.getMessage() for record in output.records], ["quest steps already clicked today and unchanged: 2 (not counted)"])

	def test_a_read_without_an_exact_match_does_not_emit_an_omission_log(self):
		with mock.patch.object(rewards_state.logger, "info") as info:
			self.assertEqual(self.read(fixture.QuestPage(self.clock)).quests_open, 1)
		info.assert_not_called()

	def test_same_identity_counts_on_the_next_local_day(self):
		worker = self.clicked()
		self.assertEqual(self.read(worker.driver).quests_open, 0)
		self.day.return_value = "2026-10-24"
		self.assertEqual(self.read(worker.driver).quests_open, 1)

	def test_changed_href_or_text_counts_without_changing_other_counts(self):
		worker = self.clicked()
		for fragment in (fixture.SEARCH.replace("Start searching", "Search again"), fixture.SEARCH.replace("q=Halloween", "q=Costumes")):
			with self.subTest(fragment=fragment):
				worker.driver.quests[fixture.HALLOWEEN] = fixture.activities(fragment)
				state = self.read(worker.driver)
				self.assertEqual((state.quests_open, state.open_points), (1, 0))

	def test_same_identity_on_a_different_card_counts(self):
		self.clicked()
		driver = fixture.QuestPage(self.clock, quests={fixture.MONTHLY: fixture.activities(fixture.SEARCH)})
		self.assertEqual(self.read(driver).quests_open, 1)

	def test_another_account_does_not_inherit_the_clicked_step(self):
		worker = self.clicked()
		with run_record.account_context("other"):
			self.assertEqual(self.read(worker.driver).quests_open, 1)
		self.assertEqual(self.read(worker.driver).quests_open, 0)

	def test_corrupt_empty_future_or_unreadable_store_omits_nothing(self):
		worker = self.clicked()
		good = self.path.read_bytes()
		future = json.loads(good)
		future["date"] = "2026-10-24"
		for content in (b"", b"{", b"[]", json.dumps(future).encode("utf-8")):
			with self.subTest(content=content):
				self.path.write_bytes(content)
				self.assertEqual(self.read(worker.driver).quests_open, 1)
		self.path.write_bytes(good)
		with mock.patch.object(quest_steps, "read_file", side_effect=PermissionError("synthetic")):
			self.assertEqual(self.read(worker.driver).quests_open, 1)

	def test_write_failure_keeps_task_ok_logs_one_warning_and_counts_the_step(self):
		worker = self.worker()
		with mock.patch.object(quest_steps, "replace_file", side_effect=OSError("synthetic")), self.assertLogs(quest_steps.logger, level="WARNING") as output:
			self.run_quests(worker, only={"quests"})
		self.assertEqual(self.quest_end()["outcome"], "ok")
		self.assertEqual([record.getMessage() for record in output.records], ["Could not save quest steps clicked today (OSError)"])
		self.assertEqual(list(self.home.iterdir()), [])
		self.assertEqual(self.read(worker.driver).quests_open, 1)

	def test_full_run_still_clicks_every_card_and_may_repeat_the_same_link(self):
		worker = self.clicked()
		self.assertEqual(self.read(worker.driver).quests_open, 0)
		for task, _, method in fixture.rewards_tasks.TASKS:
			if task != "quests":
				setattr(worker, method, mock.Mock())
		self.run_quests(worker)
		self.assertEqual(worker.driver.activity_clicks, [(CARD, TEXT)] * 2)
		self.assertEqual(self.quest_end()["progress"], "worked through 4 of 4 quests")
		for task, _, method in fixture.rewards_tasks.TASKS:
			if task != "quests":
				getattr(worker, method).assert_called_once()

	def test_click_that_raises_does_not_hide_the_step(self):
		worker = self.worker()
		worker.move_to_and_click = mock.Mock(side_effect=ValueError("synthetic click failed"))
		with self.assertRaises(ValueError):
			worker.complete_single_quest(CARD, 1)
		self.assertFalse(self.path.exists())
		self.assertEqual(self.read(worker.driver).quests_open, 1)

	def test_click_is_stored_before_later_wait_or_cleanup_can_fail(self):
		worker = self.worker()
		worker.tab_utils.close_all_other_tabs = mock.Mock(side_effect=RuntimeError("synthetic cleanup failed"))
		with self.assertRaises(RuntimeError):
			worker.complete_single_quest(CARD, 1)
		self.assertEqual(quest_steps.clicked_today(), frozenset({ROW}))


if __name__ == "__main__":
	unittest.main()
