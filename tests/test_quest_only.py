"""F84 selects quests per account without changing the run-record schema."""

import contextlib
from dataclasses import replace
import logging
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock
from selenium.webdriver.common.by import By

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import constants
import element_selectors
import main
import rewards_state
import rewards_tasks
import run_record
from test_rewards_state import StateDriver, link
from fakes import FakeElement


class QuestDriver(StateDriver):
	"""The state reader uses its real sections and captures on this fake page."""
	def __init__(self):
		super().__init__(balance="1000", quests={"/earn/quest/sample": [link("https://www.bing.com/search?q=quest", "Start searching")]})
		self.earned_today = 0
		self.captures = 0
		self.set_page_load_timeout = mock.Mock()
		self.set_script_timeout = mock.Mock()
		self.execute_cdp_cmd = mock.Mock()

	def _open_panel(self, text, activities=()):
		super()._open_panel(text.replace("2,345", str(self.earned_today)), activities)

	def get(self, url):
		super().get(url)
		# Rewards quest pages keep the same balance header as the earn page.
		if self.current_url.startswith(rewards_tasks.REWARDS_HOME_URL):
			self.children[(By.TAG_NAME, "header")] = [FakeElement(children={(By.TAG_NAME, "button"):
				[FakeElement(text=self.balance, attributes={"aria-label": "View profile"})]})]

	def execute_script(self, script, *args):
		if script == rewards_state._STREAK_SNAPSHOT:
			self.captures += 1
			return {"status": "ok", "reason": None, "sections": [{"id": "streaks", "text": "Daily Set Streak Day 2 of 7", "nodes": []}], "truncated": False}
		return super().execute_script(script, *args)


def record_run(home, plan=None, *, state_changes=None, gain=0, skipped="", quest_error=None, names=("sample",)):
	"""Run the real main, decision, task loop and recorder with fake task bodies."""
	home = Path(home)
	folder, profiles = home / "runs", home / "profiles"
	drivers, tasks, invoked, log_lines = {}, {}, [], []
	task_class = rewards_tasks.RewardsTaskUtils
	class Handler(logging.Handler):
		def emit(self, record):
			log_lines.append((record.name, record.levelno, record.getMessage()))
	def make_driver(account):
		driver = QuestDriver()
		driver.account_name = account.name
		drivers[account.name] = driver
		return driver
	def make_tasks(driver):
		worker = task_class.__new__(task_class)
		worker.driver = driver
		worker.elements = element_selectors.ElementSelectionUtils(driver)
		worker.restore_main_tab = mock.Mock()
		worker.return_to_rewards_home = mock.Mock()
		for task, _, method in rewards_tasks.TASKS:
			def step(task_id=task):
				invoked.append((driver.account_name, task_id))
				if task_id == "quests":
					if quest_error is not None:
						raise quest_error
					driver.balance = str(int(driver.balance) + gain)
					driver.earned_today += gain
					driver.find_element(By.TAG_NAME, "header").find_element(By.TAG_NAME, "button").text = driver.balance
			setattr(worker, method, step)
		tasks[driver.account_name] = worker
		return worker
	read_state = rewards_state.read_state
	def read(driver, elements, **kwargs):
		state = read_state(driver, elements, timeout=0, **kwargs)
		changes = state_changes(driver.account_name) if callable(state_changes) else state_changes
		return replace(state, **changes) if changes else state
	with contextlib.ExitStack() as stack:
		stack.enter_context(mock.patch.dict(os.environ, {"REWARDS_FARMER_DATA_HOME": str(home), "USER_DATA_DIR": str(profiles),
			"REWARDS_ACCOUNTS": ",".join(names), rewards_tasks.SKIP_TASKS_ENV: skipped, run_record.RUN_ID_ENV: "20261022T100000Z-0084"}))
		stack.enter_context(mock.patch.object(constants, "DATA_HOME", str(home)))
		stack.enter_context(mock.patch.object(constants, "RUNS_DIR", str(folder)))
		stack.enter_context(mock.patch.object(main, "HEADLESS", True))
		stack.enter_context(mock.patch.object(main.log_utils, "setup_logging", lambda: None))
		for name, value in (("prepare_desktop_before_launch", True), ("switch_back_after_launch", None),
			("cleanup_virtual_desktop", None), ("reset_virtual_desktop_state", None), ("is_virtual_desktop_enabled", False)):
			stack.enter_context(mock.patch.object(main.desktop_utils, name, return_value=value))
		stack.enter_context(mock.patch.object(main.browser, "start_driver", side_effect=make_driver))
		stack.enter_context(mock.patch.object(rewards_tasks, "RewardsTaskUtils", side_effect=make_tasks))
		reader = stack.enter_context(mock.patch.object(rewards_state, "read_state", side_effect=read))
		stack.enter_context(mock.patch.object(rewards_state.time, "sleep", lambda _: None))
		stack.enter_context(mock.patch.object(main, "_plan", main._plan))
		root = logging.getLogger()
		old_level, old_disable = root.level, root.manager.disable
		handler = Handler()
		root.addHandler(handler)
		root.setLevel(logging.INFO)
		logging.disable(logging.NOTSET)
		try:
			code = main.main(unattended=True, plan=plan)
		finally:
			root.removeHandler(handler)
			root.setLevel(old_level)
			logging.disable(old_disable)
	ids = run_record.list_run_ids(str(folder))
	assert len(ids) == 1, ids
	return types.SimpleNamespace(code=code, run_id=ids[0], events=run_record.read_events(str(folder / (ids[0] + ".jsonl"))),
		drivers=drivers, tasks=tasks, invoked=invoked, log_lines=log_lines, state_calls=reader.call_args_list, home=home)


class QuestOnlyTests(unittest.TestCase):
	def run_case(self, plan=None, **kwargs):
		temp = tempfile.TemporaryDirectory()
		self.addCleanup(temp.cleanup)
		return record_run(temp.name, plan, **kwargs)

	def test_quest_decision_runs_only_quests_and_keeps_both_real_reads_and_captures(self):
		result = self.run_case(main.RunPlan(only_if_open=True))
		self.assertEqual(result.code, 0)
		self.assertEqual(result.invoked, [("sample", "quests")])
		decision = next(event for event in result.events if event["type"] == "decision")
		self.assertEqual((decision["reason"], decision["open_points"]), ("quest_unlocked", 0))
		for event_type in ("task_start", "task_end"):
			self.assertEqual([event["task"] for event in result.events if event["type"] == event_type], ["quests"])
		for event_type in ("state", "streak_capture"):
			self.assertEqual([event["phase"] for event in result.events if event["type"] == event_type], ["before", "after"])
		self.assertEqual([call.kwargs["phase"] for call in result.state_calls], ["before", "after"])
		self.assertEqual(result.drivers["sample"].captures, 2)
		self.assertEqual(result.drivers["sample"].quit_calls, 1)
		result.drivers["sample"].execute_cdp_cmd.assert_called_once_with("Network.setExtraHTTPHeaders", {"headers": {}})
		self.assertEqual(result.events[-1]["result"], "ok")
		self.assertTrue(next(event for event in result.events if event["type"] == "account_end")["ran_tasks"])

	def test_quest_only_info_line_immediately_follows_the_decision_line_once(self):
		result = self.run_case(main.RunPlan(only_if_open=True))
		lines = [text for name, level, text in result.log_lines if name == "main" and level == logging.INFO]
		self.assertEqual(lines, ["sample: a quest step is unlocked, running",
			"sample: running quests only because a quest step is unlocked"])

	def test_below_threshold_points_and_partial_sections_still_follow_the_quest_decision(self):
		for changes in ({"open_points": 14}, {"open_points": 0, "unreadable": "searches"}):
			with self.subTest(changes=changes):
				result = self.run_case(main.RunPlan(only_if_open=True), state_changes=changes)
				self.assertEqual(next(event["reason"] for event in result.events if event["type"] == "decision"), "quest_unlocked")
				self.assertEqual(result.invoked, [("sample", "quests")])

	def test_enough_open_unreadable_override_and_run_now_keep_all_seven(self):
		for plan, changes, reason in ((main.RunPlan(only_if_open=True), {"open_points": 15}, "enough_open"),
			(main.RunPlan(only_if_open=True, run_if_unreadable=True), {"unreadable": "page_blank"}, "unreadable_run_anyway"),
			(main.RunPlan(read_state=True), None, None)):
			with self.subTest(reason=reason):
				result = self.run_case(plan, state_changes=changes)
				self.assertEqual(result.invoked, [("sample", task) for task in rewards_tasks.TASK_IDS])
				self.assertEqual([event["reason"] for event in result.events if event["type"] == "decision"], [reason] if reason else [])
				self.assertEqual([event["phase"] for event in result.events if event["type"] == "state"], ["before", "after"])
				self.assertEqual(result.drivers["sample"].captures, 2)
				self.assertFalse(any("running quests only" in text for _, _, text in result.log_lines))

	def test_default_plan_keeps_all_tasks_without_extra_reads(self):
		result = self.run_case()
		self.assertEqual(result.invoked, [("sample", task) for task in rewards_tasks.TASK_IDS])
		self.assertEqual(result.state_calls, [])
		self.assertEqual(result.drivers["sample"].captures, 0)
		self.assertFalse(any("running quests only" in text for _, _, text in result.log_lines))

	def test_filter_is_per_account_not_left_over_for_the_next_account(self):
		changes = lambda name: {"open_points": 0 if name == "one" else 20}
		result = self.run_case(main.RunPlan(only_if_open=True), names=("one", "two"), state_changes=changes)
		self.assertEqual(result.invoked, [("one", "quests")] + [("two", task) for task in rewards_tasks.TASK_IDS])
		self.assertEqual([event["reason"] for event in result.events if event["type"] == "decision"], ["quest_unlocked", "enough_open"])
		self.assertEqual([driver.captures for driver in result.drivers.values()], [2, 2])

	def test_unselected_tasks_have_no_off_events_even_when_disabled_in_settings(self):
		other = set(rewards_tasks.TASK_IDS) - {"quests"}
		result = self.run_case(main.RunPlan(only_if_open=True), skipped=",".join(sorted(other)))
		self.assertEqual(result.invoked, [("sample", "quests")])
		self.assertEqual([(event["task"], event["outcome"]) for event in result.events if event["type"] == "task_end"], [("quests", "ok")])
		self.assertFalse(any("turned off" in text for _, _, text in result.log_lines))

	def test_quest_disabled_in_settings_is_not_overridden(self):
		result = self.run_case(main.RunPlan(only_if_open=True), skipped="quests")
		self.assertEqual(result.invoked, [])
		self.assertEqual([(event["task"], event["outcome"]) for event in result.events if event["type"] == "task_end"], [("quests", "off")])
		self.assertEqual(result.drivers["sample"].captures, 2)

	def test_failed_quest_keeps_partial_outcome_cleanup_and_after_read(self):
		result = self.run_case(main.RunPlan(only_if_open=True), quest_error=ValueError("synthetic quest failed"))
		self.assertEqual(result.invoked, [("sample", "quests")])
		self.assertEqual(result.events[-1]["result"], "partial")
		self.assertEqual([(event["task"], event["outcome"]) for event in result.events if event["type"] == "task_end"], [("quests", "fail")])
		result.tasks["sample"].return_to_rewards_home.assert_called_once()
		self.assertEqual(result.drivers["sample"].captures, 2)

	def test_empty_selection_does_not_read_balances_or_emit_task_events(self):
		worker = rewards_tasks.RewardsTaskUtils.__new__(rewards_tasks.RewardsTaskUtils)
		worker._read_balance = mock.Mock(side_effect=AssertionError("unselected task read a balance"))
		with mock.patch.object(run_record, "emit") as emit:
			worker.complete_all_tasks(only=set())
		emit.assert_not_called()
		worker._read_balance.assert_not_called()


if __name__ == "__main__":
	unittest.main()
