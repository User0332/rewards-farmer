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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import accounts
import browser
import constants
import main
import rewards_tasks
import rewards_state
import run_lock
import run_record
import quest_steps
from selenium.webdriver.common.by import By
from fakes import FakeElement
from test_rewards_state import StateDriver, link


class DecisionTests(unittest.TestCase):
	def test_balance_only_does_not_gate_but_other_codes_stay_strict(self):
		state = rewards_state.RewardsState(unreadable="balance", open_points=15)
		self.assertIsNone(main.decision_state(state).unreadable)
		self.assertEqual(state.unreadable, "balance")
		self.assertEqual(main.decision_state(replace(state, unreadable="searches,balance")).unreadable, "searches")
		for code in main.WHOLE_PAGE_CODES:
			self.assertEqual(main.decision_state(replace(state, unreadable=code)).unreadable, code)

	def test_every_decision_reason_and_threshold_boundary(self):
		plan = main.RunPlan(only_if_open=True, min_points=15)
		for state, expected in (
			(rewards_state.RewardsState(open_points=15), (True, "enough_open")),
			(rewards_state.RewardsState(open_points=14), (False, "below_threshold")),
			(rewards_state.RewardsState(open_points=0), (False, "nothing_open")),
			(rewards_state.RewardsState(quests_open=1), (True, "quest_unlocked")),
			(rewards_state.RewardsState(unreadable="signed_out", open_points=100), (False, "unreadable")),
			(rewards_state.RewardsState(unreadable="balance", open_points=15), (True, "enough_open")),
		):
			with self.subTest(expected=expected):
				self.assertEqual(main.decide(state, plan), expected)
		self.assertEqual(main.decide(rewards_state.RewardsState(unreadable="page_blank"), replace(plan, run_if_unreadable=True)), (True, "unreadable_run_anyway"))


class PartialDecisionTests(unittest.TestCase):
	def setUp(self):
		self.plan = main.RunPlan(only_if_open=True, min_points=15)

	def test_partial_quests_twenty_points_runs_with_enough_open_reason(self):
		state = rewards_state.RewardsState(open_points=20, unreadable="quests")
		self.assertEqual(main.decide(state, self.plan), (True, "enough_open"))
		self.assertEqual(state.unreadable, "quests")

	def test_partial_quests_ten_points_remains_unreadable(self):
		self.assertEqual(main.decide(rewards_state.RewardsState(open_points=10, unreadable="quests"), self.plan), (False, "unreadable"))

	def test_partial_searches_thirty_points_runs_with_enough_open_reason(self):
		self.assertEqual(main.decide(rewards_state.RewardsState(open_points=30, unreadable="searches"), self.plan), (True, "enough_open"))

	def test_partial_with_a_known_unlocked_quest_runs_with_quest_reason(self):
		self.assertEqual(main.decide(rewards_state.RewardsState(quests_open=1, unreadable="searches"), self.plan), (True, "quest_unlocked"))

	def test_combined_quests_cards_with_known_points_runs(self):
		self.assertEqual(main.decide(rewards_state.RewardsState(open_points=20, unreadable="quests,cards"), self.plan), (True, "enough_open"))

	def test_whole_page_codes_keep_unreadable_and_override_behavior(self):
		for code in main.WHOLE_PAGE_CODES:
			with self.subTest(code=code):
				state = rewards_state.RewardsState(open_points=30, quests_open=1, unreadable=code)
				self.assertEqual(main.decide(state, self.plan), (False, "unreadable"))
				self.assertEqual(main.decide(state, replace(self.plan, run_if_unreadable=True)), (True, "unreadable_run_anyway"))

	def test_balance_alone_is_stripped_without_changing_the_recorded_state(self):
		state = rewards_state.RewardsState(open_points=10, unreadable="balance")
		self.assertEqual(main.decide(state, self.plan), (False, "below_threshold"))
		self.assertIsNone(main.decision_state(state).unreadable)
		self.assertEqual(state.unreadable, "balance")
		self.assertEqual(main.decide(replace(state, open_points=20), self.plan), (True, "enough_open"))

	def test_run_if_unreadable_still_overrides_partial_below_threshold(self):
		state = rewards_state.RewardsState(open_points=10, unreadable="quests")
		self.assertEqual(main.decide(state, replace(self.plan, run_if_unreadable=True)), (True, "unreadable_run_anyway"))

	def test_fixed_mode_keeps_one_point_threshold_and_unreadable_fallback(self):
		plan = replace(self.plan, min_points=1, run_if_unreadable=True)
		self.assertEqual(main.decide(rewards_state.RewardsState(open_points=1, unreadable="quests"), plan), (True, "enough_open"))
		self.assertEqual(main.decide(rewards_state.RewardsState(unreadable="quests"), plan), (True, "unreadable_run_anyway"))


class CliPlanTests(unittest.TestCase):
	def test_all_flags_and_implied_reads(self):
		for flags, expected in (
			([], main.RunPlan()),
			(["--read-state"], main.RunPlan(read_state=True)),
			(["--check-only"], main.RunPlan(read_state=True, check_only=True)),
			(["--only-if-open"], main.RunPlan(read_state=True, only_if_open=True, min_points=15)),
			(["--only-if-open", "--min-points", "7", "--run-if-unreadable"], main.RunPlan(True, True, False, 7, True)),
		):
			with self.subTest(flags=flags), mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(main, "main", return_value=8) as runner, mock.patch.object(main.os.path, "isfile", return_value=False):
				self.assertEqual(main.cli(["--unattended", *flags]), 8)
				runner.assert_called_once_with(unattended=True, plan=expected)

	def test_dotenv_uses_upstream_path_loading_and_preserves_environment_override(self):
		with tempfile.TemporaryDirectory() as folder:
			path = Path(folder) / ".env"
			path.write_text("SYNTHETIC_DEFAULT=file\nSYNTHETIC_OVERRIDE=file\n", encoding="utf-8")
			with mock.patch.dict(os.environ, {"SYNTHETIC_OVERRIDE": "environment"}, clear=True), mock.patch.object(main, "DOTENV_PATH", str(path)), mock.patch.object(main.dotenv, "load_dotenv", wraps=main.dotenv.load_dotenv) as load, mock.patch.object(main, "main", return_value=0) as runner:
				self.assertEqual(main.cli(["--only-if-open"]), 0)
				self.assertEqual(runner.call_args.kwargs["plan"].min_points, 15)
				self.assertEqual(os.environ["SYNTHETIC_DEFAULT"], "file")
				self.assertEqual(os.environ["SYNTHETIC_OVERRIDE"], "environment")
				load.assert_called_once_with(str(path))

	def test_threshold_is_cli_only_even_with_an_old_environment_value(self):
		with mock.patch.dict(os.environ, {"REWARDS_MIN_POINTS": "bad"}, clear=True), mock.patch.object(main.os.path, "isfile", return_value=False), mock.patch.object(main, "main", return_value=0) as runner:
			main.cli(["--only-if-open"])
			self.assertEqual(runner.call_args.kwargs["plan"].min_points, 15)
			main.cli(["--only-if-open", "--min-points", "7"])
			self.assertEqual(runner.call_args.kwargs["plan"].min_points, 7)

	def test_negative_and_noninteger_thresholds_are_parser_errors_before_dotenv(self):
		for flags in (["--min-points", "-1"], ["--min-points", "bad"]):
			with self.subTest(flags=flags), mock.patch.object(main.os.path, "isfile", return_value=False), mock.patch.object(main.dotenv, "load_dotenv") as load, self.assertRaises(SystemExit) as error:
				main.cli(flags)
			self.assertEqual(error.exception.code, 2)
			load.assert_not_called()


class MainRecordTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.home = Path(self.temp.name)
		self.stack = contextlib.ExitStack()
		self.addCleanup(self.stack.close)
		self.stack.enter_context(mock.patch.dict(os.environ, {"USER_DATA_DIR": str(self.home / "profiles")}, clear=True))
		self.stack.enter_context(mock.patch.object(constants, "RUNS_DIR", str(self.home / "runs")))
		self.stack.enter_context(mock.patch.object(main, "HEADLESS", True))
		self.stack.enter_context(mock.patch.object(main.log_utils, "setup_logging", lambda: None))
		self.cleanup_desktop = self.stack.enter_context(mock.patch.object(main.desktop_utils, "cleanup_virtual_desktop"))
		for name, value in (("reset_virtual_desktop_state", None), ("switch_back_after_launch", None), ("prepare_desktop_before_launch", True), ("is_virtual_desktop_enabled", False)):
			self.stack.enter_context(mock.patch.object(main.desktop_utils, name, return_value=value))
		self.driver = mock.Mock()
		self.stack.enter_context(mock.patch.object(browser, "start_driver", return_value=self.driver))
		self.tasks = mock.Mock()
		self.tasks.complete_all_tasks.side_effect = lambda: run_record.emit("task_end", task="daily_set", outcome="ok")
		self.factory = self.stack.enter_context(mock.patch.object(main.rewards_tasks, "RewardsTaskUtils", return_value=self.tasks))
		self.state = rewards_state.RewardsState(balance=100, searches_earned=0, searches_max=60, open_points=60)
		self.reader = self.stack.enter_context(mock.patch.object(rewards_state, "read_state", return_value=self.state))
		old = logging.root.manager.disable
		logging.disable(logging.CRITICAL)
		self.addCleanup(logging.disable, old)

	def events(self):
		ids = run_record.list_run_ids(str(self.home / "runs"))
		self.assertEqual(len(ids), 1)
		return run_record.read_events(str(self.home / "runs" / (ids[0] + ".jsonl")))

	def test_default_keeps_no_extra_browser_reads_and_records_ok(self):
		self.assertEqual(main.main(unattended=True), 0)
		self.reader.assert_not_called()
		self.driver.set_page_load_timeout.assert_not_called()
		self.assertEqual(self.events()[-1]["result"], "ok")
		self.driver.quit.assert_called_once()
		self.assertIsNone(run_lock.status())

	def test_check_signed_out_records_lifecycle_without_tasks_and_quits(self):
		self.reader.return_value = rewards_state.RewardsState(unreadable="signed_out")
		self.assertEqual(main.main(True, main.RunPlan(check_only=True)), 0)
		events = self.events()
		self.assertEqual([e["type"] for e in events], ["run_start", "account_start", "state", "account_end", "run_end"])
		self.assertEqual(events[2]["unreadable"], "signed_out")
		self.assertEqual(events[-1]["result"], "checked")
		self.assertEqual(events[-1]["exit_code"], 0)
		self.factory.assert_not_called()
		self.driver.set_page_load_timeout.assert_called_once_with(60)
		self.driver.set_script_timeout.assert_called_once_with(30)
		self.driver.quit.assert_called_once()

	def test_state_before_task_creation_and_after_headers_clear(self):
		calls = []
		def read(*args, **kwargs):
			calls.append(("state", None))
			return self.state
		self.reader.side_effect = read
		self.factory.side_effect = lambda driver: calls.append(("tasks", None)) or self.tasks
		self.driver.execute_cdp_cmd.side_effect = lambda *args: calls.append(("headers", args))
		self.assertEqual(main.main(True, main.RunPlan(read_state=True)), 0)
		self.assertEqual([name for name, value in calls], ["state", "tasks", "headers", "state"])

	def test_skipped_and_partial_result_table(self):
		self.reader.return_value = replace(self.state, open_points=0)
		self.assertEqual(main.main(True, main.RunPlan(only_if_open=True)), 0)
		self.assertEqual(self.events()[-1]["result"], "skipped")
		self.assertFalse(next(e for e in self.events() if e["type"] == "decision")["should_run"])

	def test_task_failure_is_partial_while_browser_started(self):
		self.tasks.complete_all_tasks.side_effect = lambda: run_record.emit("task_end", task="daily_set", outcome="fail")
		self.assertEqual(main.main(True), 0)
		self.assertEqual(self.events()[-1]["result"], "partial")

	def test_no_browser_is_failed_exit_one(self):
		browser.start_driver.return_value = None
		self.assertEqual(main.main(True), 1)
		self.assertEqual(self.events()[-1]["result"], "failed")

	def test_all_started_accounts_error_is_failed_not_partial(self):
		self.factory.side_effect = ValueError("cannot load")
		self.assertEqual(main.main(True), 1)
		self.assertEqual(self.events()[-1]["result"], "failed")
		self.driver.quit.assert_called_once()

	def test_prestart_error_does_not_make_a_good_account_failed(self):
		os.environ["REWARDS_ACCOUNTS"] = "one,two"
		browser.start_driver.side_effect = [OSError("denied"), self.driver]
		self.assertEqual(main.main(True), 0)
		self.assertEqual(self.events()[-1]["result"], "partial")

	def test_config_error_writes_record_and_exit_two(self):
		os.environ["REWARDS_ACCOUNTS"] = "../bad"
		self.assertEqual(main.main(True), 2)
		self.assertEqual(self.events()[-1]["result"], "config_error")

	def test_busy_writes_record_and_exit_three_without_browser(self):
		with run_lock.acquire("signin", account="one"):
			self.assertEqual(main.main(True), 3)
		self.assertEqual(self.events()[-1]["result"], "busy")
		browser.start_driver.assert_not_called()

	def test_ctrl_c_is_recorded_and_still_reraises(self):
		self.tasks.complete_all_tasks.side_effect = KeyboardInterrupt
		with self.assertRaises(KeyboardInterrupt):
			main.main(True)
		self.assertEqual(self.events()[-1]["result"], "stopped")
		self.assertEqual(self.events()[-1]["exit_code"], 130)
		self.driver.quit.assert_called_once()
		self.assertIsNone(run_lock.status())

	def test_run_unreadable_anyway_runs_tasks_and_records_original_state(self):
		self.reader.return_value = rewards_state.RewardsState(unreadable="page_blank")
		self.assertEqual(main.main(True, main.RunPlan(only_if_open=True, run_if_unreadable=True)), 0)
		events = self.events()
		decision = next(e for e in events if e["type"] == "decision")
		self.assertEqual(decision["reason"], "unreadable_run_anyway")
		self.assertTrue(decision["should_run"])
		self.assertEqual(next(e for e in events if e["type"] == "state")["unreadable"], "page_blank")
		self.tasks.complete_all_tasks.assert_called_once()

	def test_partial_known_work_runs_and_keeps_unreadable_in_recorded_states(self):
		self.reader.return_value = replace(self.state, open_points=20, cards_open=2, unreadable="quests")
		self.assertEqual(main.main(True, main.RunPlan(only_if_open=True, min_points=15)), 0)
		events = self.events()
		decision = next(event for event in events if event["type"] == "decision")
		self.assertTrue(decision["should_run"])
		self.assertEqual(decision["reason"], "enough_open")
		states = [event for event in events if event["type"] == "state"]
		self.assertEqual([event["phase"] for event in states], ["before", "after"])
		self.assertTrue(all(event["unreadable"] == "quests" and event["open_points"] == 20 for event in states))
		self.tasks.complete_all_tasks.assert_called_once()

	def test_quest_only_reason_passes_only_quests_without_changing_full_runs(self):
		self.reader.return_value = replace(self.state, open_points=0, quests_open=1)
		self.tasks.complete_all_tasks.side_effect = None
		self.assertEqual(main.main(True, main.RunPlan(only_if_open=True)), 0)
		self.tasks.complete_all_tasks.assert_called_once_with(only={"quests"})
		decision = next(event for event in self.events() if event["type"] == "decision")
		self.assertEqual(decision["reason"], "quest_unlocked")
		self.assertEqual([event["phase"] for event in self.events() if event["type"] == "state"], ["before", "after"])

	def test_unattended_skips_enter_but_no_flag_keeps_the_original_prompt(self):
		with mock.patch.object(main, "HEADLESS", False), mock.patch("builtins.input", return_value="") as prompt:
			self.assertEqual(main.main(), 0)
			prompt.assert_called_once_with("Press Enter to exit...")
			prompt.reset_mock()
			self.assertEqual(main.main(unattended=True), 0)
			prompt.assert_not_called()

	def test_multi_account_events_keep_account_identity_and_driver_cleanup(self):
		os.environ["REWARDS_ACCOUNTS"] = "one,two"
		self.factory.side_effect = [ValueError("first account failed"), self.tasks]
		self.assertEqual(main.main(True), 0)
		events = self.events()
		self.assertEqual([e["account"] for e in events if e["type"] == "account_end"], ["one", "two"])
		self.assertEqual(events[-1]["accounts_started"], 2)
		self.assertEqual(events[-1]["result"], "partial")
		self.assertEqual(self.driver.quit.call_count, 2)


class ConditionalRunIntegration(unittest.TestCase):
	def test_real_reader_quest_task_and_record_settle_then_same_day_run_skips(self):
		card = "https://rewards.bing.com/earn/quest/card"
		href = "https://www.bing.com/search?q=quest"
		class Page(StateDriver):
			def __init__(self):
				super().__init__(quests={"/earn/quest/card": [link(href, "Start searching")]})
				self.current_window_handle = "main"
				self.window_handles = ["main"]
				self.earn_tab = FakeElement(text="Earn")
				self.activity_clicks = []
			def get(self, url):
				super().get(rewards_state.EARN_URL if url == rewards_tasks.REWARDS_HOME_URL else url)
				if self.current_url == rewards_state.EARN_URL:
					self.children[(By.CSS_SELECTOR, '[id$="-tab-/earn"]')] = [self.earn_tab]
			def execute_script(self, script, *args):
				if "click()" in script and args and args[0] is self.earn_tab:
					self.get(rewards_state.EARN_URL)
					return
				return super().execute_script(script, *args)
			def execute_cdp_cmd(self, *args):
				return {}
			def set_page_load_timeout(self, value):
				pass
			def set_script_timeout(self, value):
				pass
		def mouse(driver):
			def click():
				if driver.target is driver.earn_tab:
					driver.get(rewards_state.EARN_URL)
				else:
					driver.activity_clicks.append((driver.current_url, driver.target.text))
			return types.SimpleNamespace(move_to_element=lambda element: setattr(driver, "target", element),
				human_like_click=click)
		first, later = Page(), Page()
		read = rewards_state.read_state
		wait = rewards_tasks.RewardsTaskUtils.wait_for_element
		with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
			stack.enter_context(mock.patch.dict(os.environ, {"USER_DATA_DIR": str(Path(folder, "profile"))}, clear=True))
			stack.enter_context(mock.patch.object(constants, "REPO_ROOT", folder))
			stack.enter_context(mock.patch.object(constants, "RUNS_DIR", str(Path(folder, "runs"))))
			stack.enter_context(mock.patch.object(main.browser, "start_driver", side_effect=[first, later]))
			stack.enter_context(mock.patch.object(main, "HEADLESS", True))
			stack.enter_context(mock.patch.object(main.desktop_utils, "prepare_desktop_before_launch", return_value=True))
			for name in ("reset_virtual_desktop_state", "switch_back_after_launch", "cleanup_virtual_desktop"):
				stack.enter_context(mock.patch.object(main.desktop_utils, name))
			stack.enter_context(mock.patch.object(main.desktop_utils, "is_virtual_desktop_enabled", return_value=False))
			stack.enter_context(mock.patch.object(quest_steps, "_today", return_value="2026-10-24"))
			stack.enter_context(mock.patch.object(rewards_state, "read_state", side_effect=lambda driver, elements: read(driver, elements, timeout=0)))
			stack.enter_context(mock.patch.object(rewards_tasks.RewardsTaskUtils, "wait_for_element", lambda self, getter: wait(self, getter, timeout=0)))
			stack.enter_context(mock.patch.object(rewards_tasks.mouse_trajectory, "MouseUtils", side_effect=mouse))
			stack.enter_context(mock.patch.object(rewards_tasks.tab_utils, "TabUtils", side_effect=lambda driver: types.SimpleNamespace(ensure_focus=lambda: None, close_all_other_tabs=lambda **kwargs: None)))
			stack.enter_context(mock.patch.object(rewards_tasks.time, "sleep"))
			stack.enter_context(mock.patch.object(main.log_utils, "setup_logging"))
			old = logging.root.manager.disable
			logging.disable(logging.CRITICAL)
			stack.callback(logging.disable, old)
			plan = main.RunPlan(only_if_open=True)
			self.assertEqual(main.main(unattended=True, plan=plan), 0)
			first_id = run_record.list_run_ids()[0]
			before = run_record.read_events(str(Path(constants.RUNS_DIR, first_id + ".jsonl")))
			self.assertEqual([event["task"] for event in before if event["type"] == "task_start"], ["quests"])
			self.assertEqual([event["outcome"] for event in before if event["type"] == "task_end"], ["ok"])
			self.assertEqual([event["quests_open"] for event in before if event["type"] == "state"], [1, 0])
			self.assertEqual(before[-1]["result"], "ok")
			self.assertEqual(before[-1]["accounts_ran"], 1)
			self.assertEqual(main.main(unattended=True, plan=plan), 0)
			second_id = next(run_id for run_id in run_record.list_run_ids() if run_id != first_id)
			after = run_record.read_events(str(Path(constants.RUNS_DIR, second_id + ".jsonl")))
			self.assertEqual(next(event["reason"] for event in after if event["type"] == "decision"), "nothing_open")
			self.assertFalse(any(event["type"] == "task_start" for event in after))
			self.assertEqual(after[-1]["result"], "skipped")
			self.assertEqual(after[-1]["accounts_ran"], 0)
			self.assertIsNone(run_lock.status())
		self.assertEqual(first.activity_clicks, [(card, "Start searching")])
		self.assertEqual(later.activity_clicks, [])
		self.assertEqual((first.quit_calls, later.quit_calls), (1, 1))
