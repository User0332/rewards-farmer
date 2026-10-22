"""Task boundaries over a fake header and tab lifecycle, never a real browser."""

import contextlib
import os
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
from selenium.webdriver.common.by import By

import element_selectors
from fakes import FakeElement, balance_snapshot
import rewards_state
import rewards_tasks
import tab_utils


class Clock:
	def __init__(self):
		self.now = 0.0
		self.sleeps = []

	def __call__(self):
		return self.now

	def sleep(self, seconds):
		assert 0 < seconds <= rewards_tasks.BALANCE_POLL_INTERVAL
		self.sleeps.append(seconds)
		self.now += seconds


class HeaderDriver:
	"""The real balance selector can only see a header after ready_at."""

	def __init__(self, clock, *, ready_at=0, balance=1000):
		self.clock, self.ready_at, self.balance = clock, ready_at, balance
		self.pages = {"main": rewards_tasks.REWARDS_HOME_URL}
		self.current_window_handle = "main"
		self.switch_to = types.SimpleNamespace(window=self._switch)
		self.timeouts = types.SimpleNamespace(page_load=60)
		self.navigations, self.timeout_changes, self.looks = [], [], []
		self.events = []
		self.focused = False
		self.get_duration = 0
		self.get_error = None

	@property
	def current_url(self):
		return self.pages[self.current_window_handle]

	@property
	def window_handles(self):
		return list(self.pages)

	def _switch(self, handle):
		self.current_window_handle = handle

	def close(self):
		del self.pages[self.current_window_handle]

	def execute_script(self, script, *args):
		# TabUtils.ensure_focus adjusts visibility, never clicks a control.
		assert ".click(" not in script
		if script == element_selectors._BALANCE_SNAPSHOT:
			return balance_snapshot(self)
		if "window.innerWidth" in script:
			return [1131, 643]
		assert "visibilityState" in script and "hasFocus" in script
		self.focused = True
		self.events.append(("focus", self.current_window_handle, self.current_url))

	def set_page_load_timeout(self, seconds):
		self.timeout_changes.append(seconds)
		self.timeouts.page_load = seconds

	def get(self, url):
		self.navigations.append((url, self.timeouts.page_load))
		self.events.append(("navigate", url, self.timeouts.page_load))
		self.focused = False
		elapsed = min(self.get_duration, self.timeouts.page_load)
		self.clock.now += elapsed
		self.pages[self.current_window_handle] = url
		if self.get_duration > self.timeouts.page_load:
			raise TimeoutException("synthetic page load exceeded bound")
		if self.get_error is not None:
			raise self.get_error

	def find_elements(self, by, selector):
		self.looks.append((self.clock(), self.current_window_handle, self.current_url))
		self.events.append(("poll", self.current_window_handle, self.focused))
		if (by, selector) != (By.TAG_NAME, "header"):
			return []
		if not self.current_url.startswith(rewards_tasks.REWARDS_HOME_URL) or self.clock() < self.ready_at:
			return []
		button = FakeElement(str(self.balance), {"aria-label": "View profile"})
		return [FakeElement(children={(By.TAG_NAME, "button"): [button]})]


def make_tasks(clock, **driver_options):
	tasks = rewards_tasks.RewardsTaskUtils.__new__(rewards_tasks.RewardsTaskUtils)
	tasks.driver = HeaderDriver(clock, **driver_options)
	tasks.main_window = "main"
	tasks.tab_utils = tab_utils.TabUtils(tasks.driver)
	tasks.elements = element_selectors.ElementSelectionUtils(tasks.driver)
	tasks.balance_reader = lambda: rewards_state.read_balance(tasks.driver, tasks.elements)
	for _, _, method in rewards_tasks.TASKS:
		setattr(tasks, method, lambda: None)
	return tasks


class BalanceWaitTests(unittest.TestCase):
	def setUp(self):
		self.clock = Clock()

	def read(self, tasks, **kwargs):
		return tasks._read_balance(clock=self.clock, sleep=self.clock.sleep, **kwargs)

	def test_header_that_appears_after_delay_is_read_by_the_real_selector(self):
		tasks = make_tasks(self.clock, ready_at=0.75, balance=1234)
		self.assertEqual(self.read(tasks), 1234)
		self.assertEqual(self.clock.sleeps, [0.25] * 3)
		self.assertEqual(len(tasks.driver.looks), 4)
		self.assertIsNone(tasks._balance_read_diagnostic)
		self.assertEqual(tasks.driver.navigations, [])

	def test_a_readable_zero_adds_no_wait(self):
		tasks = make_tasks(self.clock, balance=0)
		self.assertEqual(self.read(tasks), 0)
		self.assertEqual(self.clock.sleeps, [])
		self.assertEqual(len(tasks.driver.looks), 1)

	def test_header_never_appears_returns_none_at_the_bound(self):
		tasks = make_tasks(self.clock, ready_at=float("inf"))
		self.assertIsNone(self.read(tasks))
		self.assertEqual(self.clock(), 7.5)
		self.assertEqual(self.clock.sleeps, [0.25] * 30)
		self.assertEqual(tasks._balance_read_diagnostic, {"status": "unreadable", "attempts": 31,
			"elapsed_ms": 7500, "initial_host": "rewards.bing.com", "host": "rewards.bing.com",
			"navigation": "not_attempted", "reader_error": False, "wait_mode": "poll", "budget_remaining_ms": 52500,
			"observation": {"headers": 0, "profile_buttons": 0, "buttons": [], "inner_size": [1131, 643], "complete": True}})

	def test_reading_at_the_exact_deadline_is_still_accepted(self):
		tasks = make_tasks(self.clock, ready_at=7.5)
		self.assertEqual(self.read(tasks), 1000)
		self.assertEqual(self.clock(), 7.5)

	def test_invalid_or_failed_readings_never_escape_or_become_zero(self):
		for value in (None, False, True, -1, "1000", 1.5, ValueError("private-message")):
			with self.subTest(value=value):
				tasks = make_tasks(self.clock)
				def reader():
					if isinstance(value, Exception):
						raise value
					return value
				tasks.balance_reader = reader
				self.assertIsNone(self.read(tasks, timeout=0))
				self.assertNotIn("private-message", str(tasks._balance_read_diagnostic))

	def test_reader_exception_is_retried_then_a_real_measurement_wins(self):
		tasks = make_tasks(self.clock)
		tasks.balance_reader = mock.Mock(side_effect=[WebDriverException("temporary"), None, 1007])
		self.assertEqual(self.read(tasks), 1007)
		self.assertEqual(self.clock(), 0.5)

	def test_no_reader_is_disabled_without_wait_or_navigation(self):
		tasks = make_tasks(self.clock)
		del tasks.balance_reader
		tasks.driver.pages["main"] = "https://www.bing.com/"
		self.assertIsNone(self.read(tasks, prepare=True))
		self.assertEqual((self.clock(), tasks.driver.navigations, tasks.driver.looks), (0, [], []))

	def test_rewards_subpages_are_not_reloaded_for_a_missing_header(self):
		for path in ("earn", "dashboard", "earn/quest/synthetic"):
			with self.subTest(path=path):
				tasks = make_tasks(self.clock, ready_at=float("inf"))
				tasks.driver.pages["main"] = rewards_tasks.REWARDS_HOME_URL + path
				self.assertIsNone(self.read(tasks, prepare=True, timeout=0))
				self.assertEqual(tasks.driver.navigations, [])

	def test_navigation_shares_the_reading_deadline_and_restores_page_timeout(self):
		tasks = make_tasks(self.clock, ready_at=float("inf"))
		tasks.driver.pages["main"] = "https://www.bing.com/search?q=synthetic"
		tasks.driver.get_duration = 100
		self.assertIsNone(self.read(tasks, prepare=True))
		self.assertEqual(self.clock(), 7.5)
		self.assertEqual(tasks.driver.navigations, [(rewards_tasks.REWARDS_HOME_URL, 7.5)])
		self.assertEqual(tasks.driver.timeout_changes, [7.5, 60])
		self.assertEqual(self.clock.sleeps, [])
		self.assertEqual(tasks._balance_read_diagnostic["navigation"], "timed_out")

	def test_a_navigation_timeout_can_leave_a_readable_header_at_the_bound(self):
		tasks = make_tasks(self.clock)
		tasks.driver.pages["main"] = "https://www.bing.com/"
		tasks.driver.get_duration = 100
		self.assertEqual(self.read(tasks, prepare=True), 1000)
		self.assertEqual(self.clock(), 7.5)
		self.assertEqual(tasks.driver.timeouts.page_load, 60)

	def test_reading_navigation_restores_document_focus_before_the_first_poll(self):
		for get_duration in (0, 100):
			with self.subTest(get_duration=get_duration):
				self.clock = Clock()
				tasks = make_tasks(self.clock)
				tasks.driver.pages["main"] = "https://www.bing.com/"
				tasks.driver.get_duration = get_duration
				self.assertEqual(self.read(tasks, prepare=True), 1000)
				self.assertEqual(tasks.driver.events, [
					("navigate", rewards_tasks.REWARDS_HOME_URL, 7.5),
					("focus", "main", rewards_tasks.REWARDS_HOME_URL),
					("poll", "main", True),
				])
				self.assertEqual(tasks.driver.timeout_changes, [7.5, 60])

	def test_reading_navigation_focus_failure_does_not_escape_or_skip_polling(self):
		for get_duration in (0, 100):
			with self.subTest(get_duration=get_duration):
				self.clock = Clock()
				tasks = make_tasks(self.clock)
				tasks.driver.pages["main"] = "https://www.bing.com/"
				tasks.driver.get_duration = get_duration
				with mock.patch.object(tasks.tab_utils, "ensure_focus", side_effect=WebDriverException("synthetic focus failure")) as focus:
					self.assertEqual(self.read(tasks, prepare=True), 1000)
				focus.assert_called_once_with()
				self.assertEqual(len(tasks.driver.looks), 1)
				self.assertEqual(tasks.driver.timeouts.page_load, 60)

	def test_navigation_failure_is_diagnostic_not_a_new_task_failure(self):
		tasks = make_tasks(self.clock, ready_at=float("inf"))
		tasks.driver.pages["main"] = "https://login.live.com/private?identity=synthetic"
		tasks.driver.get_error = WebDriverException("private-exception-identity")
		self.assertIsNone(self.read(tasks, prepare=True))
		diagnostic = tasks._balance_read_diagnostic
		self.assertEqual(diagnostic["initial_host"], "login.live.com")
		self.assertEqual(diagnostic["navigation"], "failed")
		self.assertEqual(self.clock(), 7.5)
		self.assertNotIn("identity", str(diagnostic))
		self.assertNotIn("private", str(diagnostic))

	def test_unknown_host_or_missing_window_cannot_leak_identity_or_raise(self):
		tasks = make_tasks(self.clock, ready_at=float("inf"))
		tasks.driver.pages["main"] = "https://synthetic-private-tenant.invalid/profile?token=private"
		self.assertIsNone(self.read(tasks, timeout=0))
		self.assertEqual(tasks._balance_read_diagnostic["host"], "other")
		self.assertNotIn("private", str(tasks._balance_read_diagnostic))
		tasks.driver.pages.clear()
		self.assertIsNone(self.read(tasks, prepare=True, timeout=0))
		self.assertIsNone(tasks._balance_read_diagnostic["host"])


class TaskBoundaryTests(unittest.TestCase):
	def setUp(self):
		self.clock = Clock()
		self.stack = contextlib.ExitStack()
		self.addCleanup(self.stack.close)
		self.stack.enter_context(mock.patch.dict(os.environ, {rewards_tasks.SKIP_TASKS_ENV: ""}))
		self.emit = self.stack.enter_context(mock.patch.object(rewards_tasks.run_record, "emit"))
		self.stack.enter_context(mock.patch.object(rewards_tasks.logger, "log"))
		self.stack.enter_context(mock.patch.object(rewards_tasks.logger, "info"))

	def run_tasks(self, tasks):
		tasks.complete_all_tasks(clock=self.clock, sleep=self.clock.sleep)
		return [call.kwargs for call in self.emit.call_args_list if call.args == ("task_end",)]

	def test_present_header_has_eight_readings_and_no_sleep_or_navigation(self):
		tasks = make_tasks(self.clock)
		def gain():
			tasks.driver.balance += 5
		for _, _, method in rewards_tasks.TASKS:
			setattr(tasks, method, gain)
		ends = self.run_tasks(tasks)
		self.assertEqual([(row["points_before"], row["points_after"]) for row in ends], [(1000 + i * 5, 1005 + i * 5) for i in range(7)])
		self.assertEqual(len(tasks.driver.looks), 8)
		self.assertEqual((self.clock.sleeps, tasks.driver.navigations), ([], []))
		self.assertTrue(all("balance_read" not in row for row in ends))

	def test_missing_header_adds_at_most_sixty_seconds_per_account_and_run(self):
		for account in range(2):
			with self.subTest(account=account):
				self.emit.reset_mock()
				began = self.clock()
				tasks = make_tasks(self.clock, ready_at=float("inf"))
				ends = self.run_tasks(tasks)
				self.assertEqual(self.clock() - began, 7.5)
				self.assertEqual(len(tasks.driver.looks), 31 + 7)
				self.assertTrue(all(row["points_before"] is None and row["points_after"] is None for row in ends))
				self.assertTrue(all(row["outcome"] == "ok" and row["reason_key"] is None for row in ends))
				self.assertTrue(all(row["duration_ms"] == 0 for row in ends))
				for row in ends:
					self.assertEqual(set(row["balance_read"]), {"before", "after"})

	def test_navigation_never_adds_an_extra_page_timeout_per_task(self):
		tasks = make_tasks(self.clock, ready_at=float("inf"))
		tasks.driver.get_duration = 100
		def leave_rewards():
			tasks.driver.pages["main"] = "https://www.bing.com/search?q=synthetic"
		for _, _, method in rewards_tasks.TASKS:
			setattr(tasks, method, leave_rewards)
		ends = self.run_tasks(tasks)
		self.assertEqual(self.clock(), 7.5)
		self.assertEqual(tasks.driver.navigations, [])
		self.assertTrue(all(row["points_after"] is None for row in ends))

	def test_cleanup_returns_from_a_headerless_tab_before_the_after_reading(self):
		for failure in (None, TimeoutException("synthetic task failure")):
			with self.subTest(failure=failure):
				self.emit.reset_mock()
				tasks = make_tasks(self.clock)
				def visual():
					tasks.driver.balance += 8
					tasks.driver.pages["search"] = "https://www.bing.com/images/search"
					tasks.driver.current_window_handle = "search"
					if failure is not None:
						raise failure
				tasks.complete_visual_search = visual
				ends = self.run_tasks(tasks)
				visual_end, misc = ends[2:4]
				self.assertEqual((visual_end["points_before"], visual_end["points_after"], misc["points_before"]), (1000, 1008, 1008))
				self.assertEqual(visual_end["outcome"], "ok" if failure is None else "fail")
				self.assertTrue(all(handle == "main" for _, handle, _ in tasks.driver.looks))
				self.assertEqual(tasks.driver.window_handles, ["main"])
				self.assertEqual(tasks.driver.navigations, [])

	def test_headerless_main_tab_returns_home_once_then_supplies_both_boundaries(self):
		tasks = make_tasks(self.clock)
		def visual():
			tasks.driver.balance += 8
			tasks.driver.pages["main"] = "https://www.bing.com/images/search"
		tasks.complete_visual_search = visual
		ends = self.run_tasks(tasks)
		self.assertEqual(ends[2]["points_after"], 1008)
		self.assertEqual(ends[3]["points_before"], 1008)
		self.assertEqual(tasks.driver.navigations, [(rewards_tasks.REWARDS_HOME_URL, 7.5)])
		self.assertEqual(tasks.driver.timeouts.page_load, 60)

	def test_failed_task_keeps_recovery_timeout_and_focus_with_or_without_a_reader(self):
		for with_reader in (True, False):
			with self.subTest(with_reader=with_reader):
				self.clock = Clock()
				self.emit.reset_mock()
				tasks = make_tasks(self.clock)
				if not with_reader:
					del tasks.balance_reader
				def fails():
					tasks.driver.balance += 8
					tasks.driver.pages["main"] = "https://www.bing.com/images/search"
					tasks.driver.get_duration = 10
					raise TimeoutException("synthetic task failure")
				tasks.complete_visual_search = fails
				with mock.patch.object(tasks, "return_to_rewards_home", wraps=tasks.return_to_rewards_home) as recover:
					ends = self.run_tasks(tasks)
					recover.assert_called_once_with()
				self.assertEqual(tasks.driver.navigations, [(rewards_tasks.REWARDS_HOME_URL, 60)])
				self.assertEqual(tasks.driver.timeout_changes, [])
				self.assertEqual(tasks.driver.timeouts.page_load, 60)
				navigation = next(i for i, event in enumerate(tasks.driver.events) if event[0] == "navigate")
				expected = [("navigate", rewards_tasks.REWARDS_HOME_URL, 60), ("focus", "main", rewards_tasks.REWARDS_HOME_URL)]
				if with_reader:
					expected.append(("poll", "main", True))
				self.assertEqual(tasks.driver.events[navigation:navigation + len(expected)], expected)
				self.assertEqual(ends[2]["outcome"], "fail")
				self.assertEqual(ends[2]["duration_ms"], 10000)
				self.assertEqual(ends[2]["points_after"], 1008 if with_reader else None)
				self.assertEqual(ends[3]["points_before"], ends[2]["points_after"])

	def test_night_pattern_immediate_reads_empty_delayed_boundaries_present(self):
		def delayed_tasks():
			tasks = make_tasks(self.clock, ready_at=0.5)
			for _, _, method in rewards_tasks.TASKS:
				def task():
					tasks.driver.ready_at = self.clock() + 0.5
					tasks.driver.balance += 3
				setattr(tasks, method, task)
			return tasks
		# Replay the old observation order with the same fake page. No task
		# delay advances this clock, so all fourteen immediate reads miss.
		old = delayed_tasks()
		immediate = []
		for _, _, method in rewards_tasks.TASKS:
			immediate.append(old.balance_reader())
			getattr(old, method)()
			immediate.append(old.balance_reader())
			old.restore_main_tab()
		tasks = delayed_tasks()
		ends = self.run_tasks(tasks)
		self.assertEqual(immediate, [None] * 14)
		self.assertEqual([(row["points_before"], row["points_after"]) for row in ends], [(1000 + 3 * i, 1003 + 3 * i) for i in range(7)])
		self.assertEqual(self.clock(), 4)
		self.assertTrue(all(row["outcome"] == "ok" for row in ends))

	def test_shared_none_is_not_retried_between_tasks_and_off_tasks_do_not_read(self):
		tasks = make_tasks(self.clock, ready_at=float("inf"))
		with mock.patch.dict(os.environ, {rewards_tasks.SKIP_TASKS_ENV: "visual_search,misc_cards"}):
			ends = self.run_tasks(tasks)
		self.assertEqual(self.clock(), 7.5)
		self.assertEqual(len(tasks.driver.looks), 31 + 5)
		self.assertEqual([row["outcome"] for row in ends], ["ok", "ok", "off", "off", "ok", "ok", "ok"])
		self.assertEqual(ends[1]["balance_read"]["after"], ends[4]["balance_read"]["before"])
		self.assertTrue(all("balance_read" not in ends[index] for index in (2, 3)))

	def test_all_tasks_off_performs_no_read_or_cleanup(self):
		tasks = make_tasks(self.clock)
		with mock.patch.dict(os.environ, {rewards_tasks.SKIP_TASKS_ENV: ",".join(rewards_tasks.TASK_IDS)}):
			ends = self.run_tasks(tasks)
		self.assertTrue(all(row["outcome"] == "off" for row in ends))
		self.assertEqual((tasks.driver.looks, self.clock.sleeps, tasks.driver.navigations), ([], [], []))

	def test_task_start_and_end_keep_fields_progress_duration_and_outcome(self):
		tasks = make_tasks(self.clock)
		def fails():
			self.clock.now += 2
			tasks.progress = "opened the sidebar"
			tasks.driver.ready_at = self.clock() + 0.5
			raise NoSuchElementException("synthetic missing control")
		tasks.complete_visual_search = fails
		ends = self.run_tasks(tasks)
		visual = ends[2]
		self.assertEqual(visual, {"task": "visual_search", "name": "Visual search", "outcome": "fail",
			"reason_key": "element_missing", "detail": "opened the sidebar, then the next element never appeared (NoSuchElementException)",
			"progress": "opened the sidebar", "points_before": 1000, "points_after": 1000, "duration_ms": 2500})
		starts = [call.kwargs for call in self.emit.call_args_list if call.args == ("task_start",)]
		self.assertEqual(starts[2], {"task": "visual_search", "name": "Visual search", "points_before": 1000})
		self.assertEqual(self.clock(), 2.5)


class TaskEntryProofTests(unittest.TestCase):
	def test_every_registered_task_selects_its_own_starting_section(self):
		class ReachedOwnSection(Exception):
			pass
		self.assertEqual(set(rewards_tasks.TASK_IDS), {"daily_set", "explore_on_bing", "visual_search", "misc_cards", "quests", "searches", "bonus"})
		for task_id, _, method in rewards_tasks.TASKS:
			with self.subTest(task=task_id):
				tasks = rewards_tasks.RewardsTaskUtils.__new__(rewards_tasks.RewardsTaskUtils)
				tasks.elements = types.SimpleNamespace(get_points_breakdown_button=object())
				tasks.switch_to_earn_page = mock.Mock(side_effect=ReachedOwnSection)
				tasks.switch_to_dashboard = mock.Mock(side_effect=ReachedOwnSection)
				with self.assertRaises(ReachedOwnSection):
					getattr(tasks, method)()
				if task_id == "bonus":
					tasks.switch_to_dashboard.assert_called_once_with()
					tasks.switch_to_earn_page.assert_not_called()
				else:
					tasks.switch_to_earn_page.assert_called_once_with()
					tasks.switch_to_dashboard.assert_not_called()


if __name__ == "__main__":
	unittest.main()
