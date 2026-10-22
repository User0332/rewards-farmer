"""Post-batch measurement recovery through real task and selector code, no Edge."""

import contextlib
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from fakes import FakeElement
import element_selectors
import browser
import constants
import main
import rewards_tasks
import run_record


class SearchPage:
	def __init__(self, *, before=3, maximum=60, missing_after_batch=True, recovery_works=True, credited_per_query=3, fail_clear_after=None):
		self.earned, self.maximum = before, maximum
		self.missing_after_batch = missing_after_batch
		self.recovery_works = recovery_works
		self.credited_per_query = credited_per_query
		self.fail_clear_after = fail_clear_after
		self.sent, self.clear_clicks, self.earn_misses = 0, 0, 0
		self.visited, self.clicked, self.scripts = [], [], []
		self.current_url = rewards_tasks.REWARDS_HOME_URL
		self.current_window_handle = "rewards"
		self.panel = False
		self.missing_earn = False
		self.last_target = None

	def node(self, kind, text=""):
		element = FakeElement(text=text)
		element.kind = kind
		return element

	def get(self, url):
		self.visited.append(url)
		self.current_url = url
		self.panel = False
		if url == rewards_tasks.REWARDS_HOME_URL and self.sent:
			self.missing_earn = self.missing_after_batch
		elif url == "https://rewards.bing.com/earn":
			self.missing_earn = not self.recovery_works

	def execute_script(self, script):
		self.scripts.append(script)

	def find_element(self, by, selector):
		if (by, selector) == (By.CSS_SELECTOR, '[id$="-tab-/earn"]'):
			if self.missing_earn:
				self.earn_misses += 1
				raise NoSuchElementException("synthetic earn navigation absent after page return")
			return self.node("earn")
		if (by, selector) == (By.TAG_NAME, "textarea"):
			return self.node("search")
		if (by, selector) == (By.ID, "sw_clx"):
			if self.sent == self.fail_clear_after:
				# The wait's first lookup succeeds; the click's fresh lookup misses.
				self.fail_clear_after = None
				self.pending_clear_failure = True
				return self.node("clear")
			if getattr(self, "pending_clear_failure", False):
				self.pending_clear_failure = False
				raise NoSuchElementException("synthetic final clear button disappeared before click")
			return self.node("clear")
		raise AssertionError((by, selector))

	def find_elements(self, by, selector):
		if (by, selector) == (By.TAG_NAME, "button"):
			return [self.node("breakdown", "Points breakdown")]
		if (by, selector) == (By.TAG_NAME, "section") and self.panel:
			close = self.node("close", "Close")
			return [FakeElement(f"Bing search\n{self.earned}/{self.maximum}", {"id": "react-aria-synthetic-panel"}, {
				(By.TAG_NAME, "button"): [close], (By.CSS_SELECTOR, "button[aria-label*='lose']"): [close],
			})]
		return []

	def click(self):
		kind = self.last_target.kind
		self.clicked.append(kind)
		if kind == "breakdown":
			self.panel = True
		elif kind == "close":
			self.panel = False
		elif kind == "clear":
			self.clear_clicks += 1

	def send_keys(self, value):
		self.sent += 1
		self.earned = min(self.maximum, self.earned + self.credited_per_query)


class SearchQuotaRecoveryTests(unittest.TestCase):
	def setUp(self):
		self.stack = contextlib.ExitStack()
		self.addCleanup(self.stack.close)
		self.stack.enter_context(mock.patch.object(rewards_tasks.time, "sleep", lambda _: None))
		self.stack.enter_context(mock.patch.object(rewards_tasks.queries, "related_queries", lambda count: (f"synthetic query {index}" for index in range(count))))
		self.stack.enter_context(mock.patch.dict(os.environ, {rewards_tasks.SKIP_TASKS_ENV: ""}))
		self.stack.enter_context(mock.patch.object(run_record, "_timestamp", return_value="2026-10-16T12:00:00.000Z"))
		new_id = run_record.new_run_id
		self.stack.enter_context(mock.patch.object(run_record, "new_run_id", side_effect=lambda now=None: new_id(now or datetime(2026, 10, 16, 12, tzinfo=timezone.utc))))

	def tasks(self, **kwargs):
		driver = SearchPage(**kwargs)
		tasks = rewards_tasks.RewardsTaskUtils.__new__(rewards_tasks.RewardsTaskUtils)
		tasks.driver = driver
		tasks.elements = element_selectors.ElementSelectionUtils(driver)
		tasks.mouse = types.SimpleNamespace(move_to_element=lambda element: setattr(driver, "last_target", element), human_like_click=driver.click)
		tasks.keyboard = types.SimpleNamespace(send_keys=driver.send_keys)
		tasks.tab_utils = types.SimpleNamespace(ensure_focus=lambda: driver.execute_script("synthetic visibility update"))
		tasks.main_window = "rewards"
		tasks.progress = None
		return tasks

	def test_record_sequence_nineteen_queries_then_missing_earn_link_recovers_measured_quota(self):
		tasks = self.tasks()
		with mock.patch.object(run_record, "emit") as emit, self.assertLogs(rewards_tasks.logger, level="INFO") as logs:
			tasks.complete_required_searches()
		self.assertEqual((tasks.driver.sent, tasks.driver.clear_clicks, tasks.driver.earn_misses), (19, 19, 1))
		self.assertEqual(tasks.driver.earned, 60)
		self.assertEqual(tasks.progress, "sent 19 searches")
		self.assertEqual(tasks.driver.visited, ["https://www.bing.com/", rewards_tasks.REWARDS_HOME_URL, "https://rewards.bing.com/earn"])
		self.assertEqual([call.kwargs for call in emit.call_args_list if call.args == ("search_points",)], [
			{"earned": 3, "max": 60, "round": 0}, {"earned": 60, "max": 60, "round": 1},
		])
		self.assertIn("Round 1: 19 searches -> 60/60", "\n".join(logs.output))
		self.assertIn("Search quota complete: 60/60", "\n".join(logs.output))

	def test_recovered_searches_task_records_ok_not_fail_through_real_task_loop(self):
		tasks = self.tasks()
		tasks.restore_main_tab = lambda: None
		for task, _, method in rewards_tasks.TASKS:
			if task != "searches":
				setattr(tasks, method, lambda: None)
		with tempfile.TemporaryDirectory() as folder:
			recorder = run_record.open_run("cli", ["synthetic"], {}, [], directory=folder)
			try:
				with run_record.account_context("synthetic"), self.assertLogs(rewards_tasks.logger, level="INFO") as logs:
					tasks.complete_all_tasks()
				event = next(event for event in run_record.read_events(recorder.path) if event["type"] == "task_end" and event["task"] == "searches")
				self.assertEqual((event["outcome"], event["reason_key"], event["progress"]), ("ok", None, "sent 19 searches"))
				self.assertEqual(recorder.stats["task_fail"], 0)
				self.assertIn("[OK] Required searches", "\n".join(logs.output))
			finally:
				run_record.close_run("ok", 0)

	def test_morning_sequence_is_unchanged_without_recovery(self):
		tasks = self.tasks(before=6, missing_after_batch=False)
		with self.assertLogs(rewards_tasks.logger, level="INFO") as logs:
			tasks.complete_required_searches()
		self.assertEqual((tasks.driver.sent, tasks.driver.earned), (18, 60))
		self.assertNotIn("https://rewards.bing.com/earn", tasks.driver.visited)
		self.assertNotIn("reloading once", "\n".join(logs.output))

	def test_initial_missing_earn_link_fails_without_sending_queries_or_recovery(self):
		tasks = self.tasks()
		tasks.driver.missing_earn = True
		with self.assertRaises(NoSuchElementException):
			tasks.complete_required_searches()
		self.assertEqual(tasks.driver.sent, 0)
		self.assertEqual(tasks.driver.visited, [])

	def test_initial_read_failure_is_recorded_as_fail_not_a_completed_or_absent_task(self):
		tasks = self.tasks()
		tasks.driver.missing_earn = True
		tasks.restore_main_tab = lambda: None
		for task, _, method in rewards_tasks.TASKS:
			if task != "searches":
				setattr(tasks, method, lambda: None)
		with mock.patch.object(run_record, "emit") as emit, self.assertLogs(rewards_tasks.logger, level="INFO"):
			tasks.complete_all_tasks()
		end = next(call.kwargs for call in emit.call_args_list if call.args == ("task_end",) and call.kwargs.get("task") == "searches")
		self.assertEqual((end["outcome"], end["reason_key"], end["progress"]), ("fail", "element_missing", "sent 0 searches"))
		self.assertEqual(tasks.driver.sent, 0)

	def test_persistent_post_batch_missing_element_fails_after_one_recovery(self):
		tasks = self.tasks(recovery_works=False)
		with self.assertLogs(rewards_tasks.logger, level="WARNING"), self.assertRaises(NoSuchElementException):
			tasks.complete_required_searches()
		self.assertEqual(tasks.driver.visited.count("https://rewards.bing.com/earn"), 1)
		self.assertEqual(tasks.driver.sent, 19)

	def test_recovered_read_below_maximum_is_not_called_complete(self):
		tasks = self.tasks(credited_per_query=0)
		with self.assertLogs(rewards_tasks.logger, level="WARNING") as logs, self.assertRaises(NoSuchElementException):
			tasks.complete_required_searches(max_rounds=1)
		self.assertIn("reloading once", "\n".join(logs.output))
		self.assertNotIn("Search quota complete", "\n".join(logs.output))

	def test_batch_error_is_not_retried_or_overridden_by_a_full_quota(self):
		tasks = self.tasks()
		tasks.run_search_batch = mock.Mock(side_effect=WebDriverException("synthetic browser closed"))
		with self.assertRaises(WebDriverException):
			tasks.complete_required_searches()
		self.assertEqual(tasks.driver.visited, [])

	def test_ctrl_c_from_post_batch_read_is_not_retried(self):
		tasks = self.tasks()
		tasks.read_search_points = mock.Mock(side_effect=[(3, 60), KeyboardInterrupt])
		with self.assertRaises(KeyboardInterrupt):
			tasks.complete_required_searches()
		self.assertNotIn("https://rewards.bing.com/earn", tasks.driver.visited)

	def test_timeout_after_batch_is_recovered_once_not_turned_into_assumed_points(self):
		tasks = self.tasks(missing_after_batch=False)
		tasks.read_search_points = mock.Mock(side_effect=[(3, 60), TimeoutException("synthetic unreadable row"), (60, 60)])
		with self.assertLogs(rewards_tasks.logger, level="WARNING"):
			tasks.complete_required_searches()
		self.assertEqual(tasks.read_search_points.call_count, 3)
		self.assertEqual(tasks.driver.visited.count("https://rewards.bing.com/earn"), 1)

	def test_round_without_points_warns_then_raises_the_last_measured_short_quota(self):
		tasks = self.tasks(missing_after_batch=False, credited_per_query=0)
		with self.assertLogs(rewards_tasks.logger, level="WARNING") as logs, self.assertRaises(rewards_tasks.SearchQuotaShort) as raised:
			tasks.complete_required_searches()
		self.assertEqual((raised.exception.earned, raised.exception.maximum), (3, 60))
		self.assertEqual(tasks.driver.sent, 19)
		self.assertEqual(logs.output, [
			"WARNING:rewards_tasks:Round produced no points, stopping instead of searching pointlessly.",
			"WARNING:rewards_tasks:Search quota not filled: 3/60",
		])

	def test_loop_exhaustion_raises_last_measured_quota_without_f69_recovery(self):
		tasks = self.tasks(missing_after_batch=False, credited_per_query=1)
		with self.assertLogs(rewards_tasks.logger, level="INFO") as logs, self.assertRaises(rewards_tasks.SearchQuotaShort) as raised:
			tasks.complete_required_searches(max_rounds=2)
		self.assertEqual((raised.exception.earned, raised.exception.maximum), (34, 60))
		self.assertEqual(tasks.progress, "sent 31 searches")
		self.assertEqual(tasks.driver.visited.count("https://www.bing.com/"), 2)
		self.assertNotIn("https://rewards.bing.com/earn", tasks.driver.visited)
		self.assertNotIn("reloading once", "\n".join(logs.output))
		self.assertIn("Search quota not filled: 34/60", logs.output[-1])

	def test_zero_maximum_and_already_full_quota_send_nothing_and_stay_complete(self):
		for before, maximum in ((0, 0), (60, 60)):
			with self.subTest(before=before, maximum=maximum):
				tasks = self.tasks(before=before, maximum=maximum)
				with self.assertLogs(rewards_tasks.logger, level="INFO") as logs:
					tasks.complete_required_searches()
				self.assertEqual(tasks.driver.sent, 0)
				self.assertEqual(tasks.driver.visited, [])
				self.assertEqual(tasks.progress, "sent 0 searches")
				self.assertIn(f"Search quota complete: {before}/{maximum}", "\n".join(logs.output))

	def test_short_quota_records_fail_with_detail_progress_and_the_new_cli_line(self):
		tasks = self.tasks(missing_after_batch=False, credited_per_query=0)
		tasks.restore_main_tab = lambda: None
		for task, _, method in rewards_tasks.TASKS:
			if task != "searches":
				setattr(tasks, method, lambda: None)
		with tempfile.TemporaryDirectory() as folder:
			recorder = run_record.open_run("cli", ["synthetic"], {}, [], directory=folder)
			try:
				with run_record.account_context("synthetic"), self.assertLogs(rewards_tasks.logger, level="INFO") as logs:
					tasks.complete_all_tasks()
				end = next(event for event in run_record.read_events(recorder.path) if event["type"] == "task_end" and event["task"] == "searches")
				self.assertEqual((end["outcome"], end["reason_key"]), ("fail", "quota_short"))
				self.assertEqual(end["progress"], "sent 19 searches")
				self.assertEqual(end["detail"], "sent 19 searches, then the search points stayed at 3/60")
				self.assertEqual(recorder.stats["task_fail"], 1)
				self.assertIn("Search quota not filled: 3/60", "\n".join(logs.output))
				self.assertIn("ERROR:rewards_tasks:[FAIL] Required searches: sent 19 searches, then the search points stayed at 3/60", logs.output)
				self.assertNotIn("[OK] Required searches", "\n".join(logs.output))
			finally:
				run_record.close_run("partial", 0)

	def test_real_main_derives_partial_from_short_search_task_and_still_quits(self):
		tasks = self.tasks(missing_after_batch=False, credited_per_query=0)
		tasks.restore_main_tab = lambda: None
		for task, _, method in rewards_tasks.TASKS:
			if task != "searches":
				setattr(tasks, method, lambda: None)
		tasks.driver.quit = mock.Mock()
		with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
			home = Path(folder)
			stack.enter_context(mock.patch.dict(os.environ, {"USER_DATA_DIR": str(home / "profiles")}, clear=True))
			stack.enter_context(mock.patch.object(constants, "RUNS_DIR", str(home / "runs")))
			stack.enter_context(mock.patch.object(main.log_utils, "setup_logging"))
			stack.enter_context(mock.patch.object(main, "HEADLESS", True))
			for name, value in (("reset_virtual_desktop_state", None), ("prepare_desktop_before_launch", True), ("switch_back_after_launch", None), ("cleanup_virtual_desktop", None), ("is_virtual_desktop_enabled", False)):
				stack.enter_context(mock.patch.object(main.desktop_utils, name, return_value=value))
			stack.enter_context(mock.patch.object(browser, "start_driver", return_value=tasks.driver))
			stack.enter_context(mock.patch.object(main.rewards_tasks, "RewardsTaskUtils", return_value=tasks))
			with self.assertLogs(rewards_tasks.logger, level="INFO"):
				self.assertEqual(main.main(unattended=True), 0)
			ids = run_record.list_run_ids(str(home / "runs"))
			self.assertEqual(len(ids), 1)
			events = run_record.read_events(str(home / "runs" / (ids[0] + ".jsonl")))
			self.assertEqual((events[-1]["type"], events[-1]["result"], events[-1]["exit_code"]), ("run_end", "partial", 0))
			end = next(event for event in events if event["type"] == "task_end" and event["task"] == "searches")
			self.assertEqual((end["outcome"], end["reason_key"]), ("fail", "quota_short"))
			self.assertTrue(any(event["type"] == "task_end" and event["task"] == "bonus" and event["outcome"] == "ok" for event in events))
			tasks.driver.quit.assert_called_once_with()

	def test_last_clear_lookup_failure_after_nineteenth_query_checks_measured_quota(self):
		tasks = self.tasks(fail_clear_after=19)
		with mock.patch.object(run_record, "emit") as emit, self.assertLogs(rewards_tasks.logger, level="INFO") as logs:
			tasks.complete_required_searches()
		self.assertEqual((tasks.driver.sent, tasks.driver.clear_clicks, tasks.driver.earned), (19, 18, 60))
		self.assertEqual(tasks.driver.visited, ["https://www.bing.com/", "https://rewards.bing.com/earn"])
		self.assertIn("Round 1: 19 searches -> 60/60", "\n".join(logs.output))
		self.assertIn(mock.call("search_points", earned=60, max=60, round=1), emit.call_args_list)

	def test_final_clear_failure_is_ok_in_task_record_only_after_full_quota_read(self):
		tasks = self.tasks(fail_clear_after=19)
		tasks.restore_main_tab = lambda: None
		for task, _, method in rewards_tasks.TASKS:
			if task != "searches":
				setattr(tasks, method, lambda: None)
		with mock.patch.object(run_record, "emit") as emit, self.assertLogs(rewards_tasks.logger, level="INFO"):
			tasks.complete_all_tasks()
		end = next(call.kwargs for call in emit.call_args_list if call.args == ("task_end",) and call.kwargs.get("task") == "searches")
		self.assertEqual((end["outcome"], end["reason_key"], end["progress"]), ("ok", None, "sent 19 searches"))
		self.assertEqual(tasks.driver.earned, tasks.driver.maximum)

	def test_clear_failure_before_quota_is_full_preserves_original_failure(self):
		tasks = self.tasks(fail_clear_after=18)
		with self.assertLogs(rewards_tasks.logger, level="WARNING"), self.assertRaisesRegex(NoSuchElementException, "final clear button"):
			tasks.complete_required_searches()
		self.assertEqual((tasks.driver.sent, tasks.driver.earned), (18, 57))
		self.assertEqual(tasks.driver.visited.count("https://rewards.bing.com/earn"), 1)

	def test_batch_missing_element_before_first_query_is_not_reconciled(self):
		tasks = self.tasks()
		tasks.run_search_batch = mock.Mock(side_effect=NoSuchElementException("synthetic initial search bar missing"))
		with self.assertRaisesRegex(NoSuchElementException, "initial search bar"):
			tasks.complete_required_searches()
		self.assertEqual(tasks.progress, "sent 0 searches")
		self.assertEqual(tasks.driver.visited, [])

	def test_unreadable_quota_after_final_clear_failure_preserves_failure_not_guessed_success(self):
		tasks = self.tasks(fail_clear_after=19, recovery_works=False)
		with self.assertLogs(rewards_tasks.logger, level="WARNING"), self.assertRaisesRegex(NoSuchElementException, "final clear button"):
			tasks.complete_required_searches()
		self.assertEqual(tasks.driver.earned, 60)
		self.assertEqual(tasks.driver.visited.count("https://rewards.bing.com/earn"), 1)

	def test_failed_post_batch_read_with_short_fresh_read_preserves_original_error(self):
		tasks = self.tasks(missing_after_batch=False)
		error = NoSuchElementException("synthetic missing quota after submitted queries")
		tasks.read_search_points = mock.Mock(side_effect=[(3, 60), error, (57, 60)])
		with self.assertLogs(rewards_tasks.logger, level="WARNING"), self.assertRaises(NoSuchElementException) as raised:
			tasks.complete_required_searches(max_rounds=1)
		self.assertIs(raised.exception, error)
		self.assertEqual(tasks.read_search_points.call_count, 3)
		self.assertEqual(tasks.driver.sent, 19)

	def test_numeric_sent_counter_not_progress_text_drives_batch_recovery(self):
		tasks = self.tasks(fail_clear_after=19)
		click = tasks.move_to_and_click
		def localized_progress(element):
			try:
				return click(element)
			except NoSuchElementException:
				tasks.progress = "synthetic nonnumeric progress"
				raise
		tasks.move_to_and_click = localized_progress
		with self.assertLogs(rewards_tasks.logger, level="INFO") as logs:
			tasks.complete_required_searches()
		self.assertEqual((tasks._searches_sent, tasks.driver.sent), (19, 19))
		self.assertIn("Round 1: 19 searches -> 60/60", "\n".join(logs.output))

	def test_run_search_batch_keeps_cumulative_counter_when_clear_lookup_fails(self):
		tasks = self.tasks(fail_clear_after=1)
		with self.assertRaises(NoSuchElementException):
			tasks.run_search_batch(1, already_sent=7)
		self.assertEqual(tasks._searches_sent, 8)
		self.assertEqual(tasks.progress, "sent 8 searches")


if __name__ == "__main__":
	unittest.main()
