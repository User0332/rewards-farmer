"""Read measured credit after a lost page element, using no browser or network."""

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
from fakes import FakeElement
import element_selectors
import rewards_tasks


class SearchPage:
	def __init__(self, *, missing_earn=True, clear_after=None, credit=3, recovery_works=True):
		self.earned, self.maximum = 3, 60
		self.missing_after_batch = missing_earn
		self.fail_clear_after = clear_after
		self.credit, self.recovery_works = credit, recovery_works
		self.sent, self.clear_clicks = 0, 0
		self.visited = []
		self.current_url = rewards_tasks.REWARDS_HOME_URL
		self.current_window_handle = "rewards"
		self.panel = self.missing_earn = self.recovering = False
		self.last_target = None
		self.original_error = NoSuchElementException("synthetic returning page has no Earn link")
		self.recovery_error = TimeoutException("synthetic recovery page is unreadable")

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
			self.recovering = True
			self.missing_earn = not self.recovery_works

	def execute_script(self, script):
		pass

	def find_element(self, by, selector):
		if (by, selector) == (By.CSS_SELECTOR, '[id$="-tab-/earn"]'):
			if self.missing_earn:
				raise self.recovery_error if self.recovering else self.original_error
			return self.node("earn")
		if (by, selector) == (By.TAG_NAME, "textarea"):
			return self.node("search")
		if (by, selector) == (By.ID, "sw_clx"):
			if self.sent == self.fail_clear_after:
				self.fail_clear_after = None
				self.pending_clear_failure = True
				return self.node("clear")
			if getattr(self, "pending_clear_failure", False):
				self.pending_clear_failure = False
				self.original_error = NoSuchElementException("synthetic last clear button disappeared")
				raise self.original_error
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
		if self.last_target.kind == "breakdown":
			self.panel = True
		elif self.last_target.kind == "close":
			self.panel = False
		elif self.last_target.kind == "clear":
			self.clear_clicks += 1

	def send_keys(self, value):
		self.sent += 1
		self.earned = min(self.maximum, self.earned + self.credit)


class SearchQuotaRecoveryTests(unittest.TestCase):
	def setUp(self):
		self.stack = contextlib.ExitStack()
		self.addCleanup(self.stack.close)
		self.stack.enter_context(mock.patch.object(rewards_tasks.time, "sleep", lambda _: None))
		self.stack.enter_context(mock.patch.object(rewards_tasks.queries, "related_queries", lambda count: (f"synthetic query {index}" for index in range(count))))

	def tasks(self, **kwargs):
		driver = SearchPage(**kwargs)
		tasks = rewards_tasks.RewardsTaskUtils.__new__(rewards_tasks.RewardsTaskUtils)
		tasks.driver = driver
		tasks.elements = element_selectors.ElementSelectionUtils(driver)
		tasks.mouse = types.SimpleNamespace(move_to_element=lambda element: setattr(driver, "last_target", element), human_like_click=driver.click)
		tasks.keyboard = types.SimpleNamespace(send_keys=driver.send_keys)
		tasks.tab_utils = types.SimpleNamespace(ensure_focus=lambda: None)
		tasks.main_window, tasks.progress = "rewards", None
		return tasks

	def run_searches(self, tasks):
		with self.assertLogs(rewards_tasks.logger, level="INFO") as logs:
			try:
				tasks.complete_required_searches()
			except Exception as exc:
				return exc, logs
		return None, logs

	def test_missing_earn_after_nineteen_queries_accepts_only_full_measured_quota(self):
		tasks = self.tasks()
		error, logs = self.run_searches(tasks)
		self.assertIsNone(error)
		self.assertEqual((tasks.driver.sent, tasks.driver.clear_clicks, tasks.driver.earned), (19, 19, 60))
		self.assertEqual(tasks.driver.visited, ["https://www.bing.com/", rewards_tasks.REWARDS_HOME_URL, "https://rewards.bing.com/earn"])
		warning = "WARNING:rewards_tasks:Search result could not be verified after 19 searches (NoSuchElementException), reloading once"
		self.assertIn(warning, logs.output)
		print(warning)
		self.assertIn("INFO:rewards_tasks:Search quota complete: 60/60", logs.output)

	def test_last_query_credited_before_clear_button_disappears(self):
		tasks = self.tasks(clear_after=19)
		error, logs = self.run_searches(tasks)
		self.assertIsNone(error)
		self.assertEqual((tasks.driver.sent, tasks.driver.clear_clicks, tasks.driver.earned), (19, 18, 60))
		self.assertEqual(tasks.driver.visited, ["https://www.bing.com/", "https://rewards.bing.com/earn"])
		self.assertIn("INFO:rewards_tasks:Round 1: 19 searches -> 60/60", logs.output)

	def test_short_recovery_preserves_original_error_without_guessing_credit(self):
		for clear_after in (None, 18):
			with self.subTest(clear_after=clear_after):
				tasks = self.tasks(clear_after=clear_after, credit=0)
				error, logs = self.run_searches(tasks)
				self.assertIs(error, tasks.driver.original_error)
				self.assertEqual(tasks.driver.visited.count("https://rewards.bing.com/earn"), 1)
				self.assertFalse(any("Search quota complete" in line for line in logs.output))

	def test_unreadable_recovery_preserves_original_error_not_the_recovery_error(self):
		for clear_after in (None, 19):
			with self.subTest(clear_after=clear_after):
				tasks = self.tasks(clear_after=clear_after, recovery_works=False)
				error, _ = self.run_searches(tasks)
				self.assertIs(error, tasks.driver.original_error)
				self.assertEqual(tasks.driver.visited.count("https://rewards.bing.com/earn"), 1)

	def test_timeout_after_batch_recovers_once_when_fresh_quota_is_full(self):
		tasks = self.tasks(missing_earn=False)
		tasks.read_search_points = mock.Mock(side_effect=[(3, 60), TimeoutException("synthetic row timed out"), (60, 60)])
		error, _ = self.run_searches(tasks)
		self.assertIsNone(error)
		self.assertEqual(tasks.read_search_points.call_count, 3)
		self.assertEqual(tasks.driver.visited.count("https://rewards.bing.com/earn"), 1)

	def test_timeout_inside_batch_is_not_retried_but_can_be_measured_complete(self):
		tasks = self.tasks()
		def batch(count, already_sent=0):
			tasks._searches_sent = 1
			tasks.progress = "sent 1 search"
			raise TimeoutException("synthetic clear wait timed out")
		tasks.run_search_batch = batch
		tasks.read_search_points = mock.Mock(side_effect=[(57, 60), (60, 60)])
		error, logs = self.run_searches(tasks)
		self.assertIsNone(error)
		self.assertEqual(tasks.driver.visited, ["https://rewards.bing.com/earn"])
		warning = "WARNING:rewards_tasks:Search result could not be verified after 1 search (TimeoutException), reloading once"
		self.assertIn(warning, logs.output)
		print(warning)
		self.assertIn("INFO:rewards_tasks:Round 1: 1 searches -> 60/60", logs.output)

	def test_failure_before_first_query_does_not_recover(self):
		tasks = self.tasks()
		error = NoSuchElementException("synthetic search bar missing")
		tasks.run_search_batch = mock.Mock(side_effect=error)
		caught, _ = self.run_searches(tasks)
		self.assertIs(caught, error)
		self.assertEqual(tasks.driver.visited, [])

	def test_initial_read_preserves_upstreams_unset_progress_and_skip_report(self):
		tasks = self.tasks()
		error = NoSuchElementException("synthetic first Earn link missing")
		tasks.read_search_points = mock.Mock(side_effect=error)
		with self.assertRaises(NoSuchElementException) as raised:
			tasks.complete_required_searches()
		self.assertIs(raised.exception, error)
		self.assertIsNone(tasks.progress)
		self.assertEqual(rewards_tasks.task_failure_report(error, tasks.progress)[0], "SKIP")
		self.assertEqual(tasks.driver.sent, 0)

	def test_non_selenium_error_is_not_recovered(self):
		tasks = self.tasks()
		error = WebDriverException("synthetic browser closed")
		tasks.run_search_batch = mock.Mock(side_effect=error)
		caught, _ = self.run_searches(tasks)
		self.assertIs(caught, error)
		self.assertEqual(tasks.driver.visited, [])

	def test_ordinary_short_quota_still_warns_without_raising(self):
		tasks = self.tasks(missing_earn=False, credit=0)
		error, logs = self.run_searches(tasks)
		self.assertIsNone(error)
		self.assertIn("WARNING:rewards_tasks:Search quota not filled: 3/60", logs.output)
		self.assertNotIn("https://rewards.bing.com/earn", tasks.driver.visited)

	def test_healthy_batch_needs_no_recovery_navigation(self):
		tasks = self.tasks(missing_earn=False)
		error, _ = self.run_searches(tasks)
		self.assertIsNone(error)
		self.assertEqual(tasks.driver.sent, 19)
		self.assertNotIn("https://rewards.bing.com/earn", tasks.driver.visited)


if __name__ == "__main__":
	unittest.main()
