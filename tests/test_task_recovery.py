"""Tests for getting back to a known page after a task fails.

The bug (#63): a task that dies partway through leaves the browser wherever it
stopped, which for the search and visual search tasks is a Bing page rather than
the Rewards site. Every later task begins by looking for controls that only
exist on rewards.bing.com, so one failure made all of them report [SKIP] for a
UI that was present and working the whole time.

The stand-ins here are local rather than in fakes.py: that module fakes the DOM
lookups the selectors do, and what these need is a driver that remembers where
it navigated. No browser, so they run anywhere.

	python -m unittest discover -s tests
"""

import logging
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException

import rewards_tasks

HOME = rewards_tasks.REWARDS_HOME_URL
BING_RESULTS = "https://www.bing.com/search?q=weather"


MAIN_TAB = "main-tab"
BING_TAB = "bing-tab"


class RecordingDriver:
	def __init__(self, url=HOME, get_raises=None, handles=None, current_handle=MAIN_TAB):
		self.current_url = url
		self.visited = []
		self.get_raises = get_raises
		self.window_handles = list(handles) if handles is not None else [MAIN_TAB]
		self.current_window_handle = current_handle

	def get(self, url):
		if self.get_raises is not None:
			raise self.get_raises

		self.visited.append(url)
		self.current_url = url


class RecordingTabUtils:
	def __init__(self):
		self.closes = 0
		self.focuses = 0
		self.kept = []

	def close_all_other_tabs(self, exceptions=None):
		self.closes += 1
		self.kept.append(exceptions)

	def ensure_focus(self):
		self.focuses += 1


class StubTasks(rewards_tasks.RewardsTaskUtils):
	"""RewardsTaskUtils with the six tasks replaced, and nothing else stubbed.

	complete_all_tasks and return_to_rewards_home are the real ones, which is
	the whole point.
	"""

	def __init__(self, driver, failures=None):
		self.driver = driver
		self.tab_utils = RecordingTabUtils()
		self.main_window = MAIN_TAB
		self.failures = failures or {}
		self.ran = []

	def _task(self, name, strands_browser_at=None):
		self.ran.append(name)

		if name not in self.failures:
			# A task that finishes switches back to the Rewards tab itself, so
			# only a failure part way through leaves the browser elsewhere.
			return

		if strands_browser_at is not None:
			self.driver.current_url = strands_browser_at

		raise self.failures[name]

	def complete_bing_daily_set(self):
		self._task("Bing daily set")

	def complete_explore_on_bing_tasks(self):
		self._task("Explore on Bing", strands_browser_at=BING_RESULTS)

	def complete_visual_search(self):
		self._task("Visual search", strands_browser_at=BING_RESULTS)

	def complete_misc_cards(self):
		self._task("Misc cards")

	def complete_quests(self):
		self._task("Quests", strands_browser_at=BING_RESULTS)

	def complete_required_searches(self):
		self._task("Required searches")

	def claim_bonus_points(self):
		self._task("Bonus points")


ALL_TASKS = [
	"Bing daily set", "Explore on Bing", "Visual search",
	"Misc cards", "Quests", "Required searches", "Bonus points",
]


class FailedTaskRecoveryTests(unittest.TestCase):
	def test_a_failure_on_a_bing_page_navigates_back(self):
		driver = RecordingDriver()
		tasks = StubTasks(driver, {"Visual search": TimeoutException("no file input")})

		with self.assertLogs(rewards_tasks.logger, level=logging.WARNING):
			tasks.complete_all_tasks()

		self.assertEqual(driver.visited, [HOME])
		self.assertEqual(driver.current_url, HOME)

	def test_the_later_tasks_still_run(self):
		# The point of the fix: one failure used to cost every task after it.
		driver = RecordingDriver()
		tasks = StubTasks(driver, {"Explore on Bing": TimeoutException("slow panel")})

		with self.assertLogs(rewards_tasks.logger, level=logging.WARNING):
			tasks.complete_all_tasks()

		self.assertEqual(tasks.ran, ALL_TASKS)

	def test_a_skip_recovers_too(self):
		# NoSuchElementException is the [SKIP] path rather than [FAIL], and it
		# strands the browser just the same.
		driver = RecordingDriver()
		tasks = StubTasks(driver, {"Visual search": NoSuchElementException("no sidebar")})

		with self.assertLogs(rewards_tasks.logger, level=logging.WARNING):
			tasks.complete_all_tasks()

		self.assertEqual(driver.visited, [HOME])

	def test_a_clean_run_never_navigates(self):
		# Six extra page loads a run would be a real cost for no benefit.
		driver = RecordingDriver()
		tasks = StubTasks(driver)

		tasks.complete_all_tasks()

		self.assertEqual(driver.visited, [])
		self.assertEqual(tasks.ran, ALL_TASKS)

	def test_no_redundant_navigation_when_already_home(self):
		# Plenty of failures happen on the Rewards page itself, e.g. a panel
		# that never renders. Reloading it would only cost time.
		driver = RecordingDriver()
		tasks = StubTasks(driver, {"Misc cards": TimeoutException("no cards")})

		with self.assertLogs(rewards_tasks.logger, level=logging.WARNING):
			tasks.complete_all_tasks()

		self.assertEqual(driver.visited, [])

	def test_tabs_are_still_tidied_after_a_failure(self):
		driver = RecordingDriver()
		tasks = StubTasks(driver, {"Visual search": TimeoutException("no file input")})

		with self.assertLogs(rewards_tasks.logger, level=logging.WARNING):
			tasks.complete_all_tasks()

		self.assertEqual(tasks.tab_utils.closes, len(ALL_TASKS))

	def test_the_rewards_tab_is_the_one_kept(self):
		# The actual bug: close_all_other_tabs() with no argument keeps whatever
		# is focused, and after a task dies on a Bing tab that is the Bing tab,
		# so the cleanup closed the Rewards tab and kept the search results.
		driver = RecordingDriver(handles=[MAIN_TAB, BING_TAB], current_handle=BING_TAB)
		tasks = StubTasks(driver, {"Visual search": TimeoutException("no file input")})

		with self.assertLogs(rewards_tasks.logger, level=logging.WARNING):
			tasks.complete_all_tasks()

		self.assertTrue(all(kept == [MAIN_TAB] for kept in tasks.tab_utils.kept))


class RestoreMainTabTests(unittest.TestCase):
	def test_names_the_main_tab_rather_than_the_focused_one(self):
		driver = RecordingDriver(handles=[MAIN_TAB, BING_TAB], current_handle=BING_TAB)
		tasks = StubTasks(driver)

		tasks.restore_main_tab()

		self.assertEqual(tasks.tab_utils.kept, [[MAIN_TAB]])

	def test_falls_back_when_the_main_tab_is_gone(self):
		# A task can close it, and a stale handle would only raise.
		driver = RecordingDriver(handles=[BING_TAB], current_handle=BING_TAB)
		tasks = StubTasks(driver)

		tasks.restore_main_tab()

		self.assertEqual(tasks.tab_utils.kept, [[BING_TAB]])

	def test_no_windows_left_is_not_an_error(self):
		driver = RecordingDriver(handles=[], current_handle=MAIN_TAB)
		tasks = StubTasks(driver)

		tasks.restore_main_tab()

		self.assertEqual(tasks.tab_utils.kept, [])

	def test_a_failure_to_tidy_is_survivable(self):
		driver = RecordingDriver(handles=[MAIN_TAB])
		tasks = StubTasks(driver)

		def boom(exceptions=None):
			raise WebDriverException("browser gone")

		tasks.tab_utils.close_all_other_tabs = boom

		with self.assertLogs(rewards_tasks.logger, level=logging.WARNING) as logged:
			tasks.restore_main_tab()

		self.assertTrue(any("Could not tidy" in line for line in logged.output))


class ReturnToRewardsHomeTests(unittest.TestCase):
	def test_navigates_and_refocuses(self):
		driver = RecordingDriver(url=BING_RESULTS)
		tasks = StubTasks(driver)

		tasks.return_to_rewards_home()

		self.assertEqual(driver.visited, [HOME])
		self.assertEqual(tasks.tab_utils.focuses, 1)

	def test_already_home_is_left_alone(self):
		driver = RecordingDriver(url=HOME + "?foo=1")
		tasks = StubTasks(driver)

		tasks.return_to_rewards_home()

		self.assertEqual(driver.visited, [])
		self.assertEqual(tasks.tab_utils.focuses, 0)

	def test_a_driver_that_cannot_navigate_is_survivable(self):
		# Recovery is best effort. Raising here would replace the real failure
		# with the tidy-up's own, and take the rest of the run down with it.
		driver = RecordingDriver(url=BING_RESULTS, get_raises=WebDriverException("browser gone"))
		tasks = StubTasks(driver)

		with self.assertLogs(rewards_tasks.logger, level=logging.WARNING) as logged:
			tasks.return_to_rewards_home()

		self.assertTrue(any("Could not return" in line for line in logged.output))

	def test_a_failing_recovery_does_not_end_the_run(self):
		driver = RecordingDriver(get_raises=WebDriverException("browser gone"))
		tasks = StubTasks(driver, {"Explore on Bing": TimeoutException("slow panel")})

		with self.assertLogs(rewards_tasks.logger, level=logging.WARNING):
			tasks.complete_all_tasks()

		self.assertEqual(tasks.ran, ALL_TASKS)


class UnhydratedPageRecoveryTests(unittest.TestCase):
	def setUp(self):
		self.tasks = rewards_tasks.RewardsTaskUtils.__new__(rewards_tasks.RewardsTaskUtils)
		self.tasks.driver = RecordingDriver()
		self.tasks.tab_utils = RecordingTabUtils()
		self.tasks.switch_to_earn_page = mock.Mock()
		self.tasks.switch_to_dashboard = mock.Mock()
		self.tasks.move_to_and_click = mock.Mock()
		self.tasks.main_window = MAIN_TAB
		self.tasks.progress = None

	def test_earn_page_timeout_reloads_once_and_retries_the_same_marker(self):
		marker = mock.Mock(return_value="breakdown")
		self.tasks.wait_for_element = mock.Mock(side_effect=[TimeoutException("SPA not hydrated"), "breakdown"])
		with self.assertLogs(rewards_tasks.logger, level="WARNING"):
			self.assertEqual(self.tasks.reach_earn_page(marker, timeout=30), "breakdown")
		self.tasks.switch_to_earn_page.assert_called_once_with()
		self.assertEqual(self.tasks.driver.visited, ["https://rewards.bing.com/earn"])
		self.assertEqual(self.tasks.tab_utils.focuses, 1)
		self.assertEqual(self.tasks.wait_for_element.call_args_list, [mock.call(marker, timeout=30), mock.call(marker, timeout=30)])

	def test_earn_page_that_is_ready_is_not_reloaded(self):
		marker = mock.Mock()
		self.tasks.wait_for_element = mock.Mock(return_value="breakdown")
		self.assertEqual(self.tasks.reach_earn_page(marker), "breakdown")
		self.tasks.wait_for_element.assert_called_once_with(marker, timeout=20)
		self.assertEqual(self.tasks.driver.visited, [])
		self.assertEqual(self.tasks.tab_utils.focuses, 0)

	def test_earn_retry_timeout_propagates_without_a_second_reload(self):
		self.tasks.wait_for_element = mock.Mock(side_effect=TimeoutException("still not hydrated"))
		with self.assertLogs(rewards_tasks.logger, level="WARNING"), self.assertRaises(TimeoutException):
			self.tasks.reach_earn_page(mock.Mock())
		self.assertEqual(self.tasks.wait_for_element.call_count, 2)
		self.assertEqual(self.tasks.driver.visited, ["https://rewards.bing.com/earn"])

	def test_read_search_points_uses_recovered_earn_marker_then_reads_row(self):
		button = object()
		marker, row, close = mock.Mock(), mock.Mock(), mock.Mock(return_value="close")
		self.tasks.elements = types.SimpleNamespace(get_points_breakdown_button=marker,
			get_points_earned_from_searches_on_points_breakdown=row, get_generic_sidebar_close_button=close)
		self.tasks.wait_for_element = mock.Mock(side_effect=[TimeoutException("SPA not hydrated"), button, (9, 60)])
		with self.assertLogs(rewards_tasks.logger, level="WARNING"):
			self.assertEqual(self.tasks.read_search_points(), (9, 60))
		self.assertEqual(self.tasks.driver.visited, ["https://rewards.bing.com/earn"])
		self.assertEqual(self.tasks.wait_for_element.call_args_list, [mock.call(marker, timeout=30), mock.call(marker, timeout=30), mock.call(row, timeout=30)])
		self.assertEqual(self.tasks.move_to_and_click.call_args_list, [mock.call(button), mock.call("close")])

	def test_dashboard_timeout_reloads_once_and_still_claims_bonus(self):
		bonus, claim = mock.Mock(), mock.Mock()
		self.tasks.elements = types.SimpleNamespace(get_bonus_button_on_dashboard=bonus, get_claim_bonus_points_button=claim)
		self.tasks.wait_for_then_click = mock.Mock(side_effect=[TimeoutException("SPA not hydrated"), None, None])
		with self.assertLogs(rewards_tasks.logger, level="WARNING"):
			self.tasks.claim_bonus_points()
		self.tasks.switch_to_dashboard.assert_called_once_with()
		self.assertEqual(self.tasks.driver.visited, ["https://rewards.bing.com/dashboard"])
		self.assertEqual(self.tasks.tab_utils.focuses, 1)
		self.assertEqual(self.tasks.wait_for_then_click.call_args_list, [mock.call(bonus, timeout=20), mock.call(bonus, timeout=20), mock.call(claim)])
		self.assertEqual(self.tasks.progress, "opened the bonus panel")

	def test_dashboard_that_is_ready_is_not_reloaded(self):
		self.tasks.elements = types.SimpleNamespace(get_bonus_button_on_dashboard=mock.Mock(), get_claim_bonus_points_button=mock.Mock())
		self.tasks.wait_for_then_click = mock.Mock()
		self.tasks.claim_bonus_points()
		self.assertEqual(self.tasks.wait_for_then_click.call_count, 2)
		self.assertEqual(self.tasks.driver.visited, [])
		self.assertEqual(self.tasks.tab_utils.focuses, 0)

	def test_dashboard_retry_timeout_propagates_without_claiming_or_reloading_again(self):
		self.tasks.elements = types.SimpleNamespace(get_bonus_button_on_dashboard=mock.Mock(), get_claim_bonus_points_button=mock.Mock())
		self.tasks.wait_for_then_click = mock.Mock(side_effect=TimeoutException("still not hydrated"))
		with self.assertLogs(rewards_tasks.logger, level="WARNING"), self.assertRaises(TimeoutException):
			self.tasks.claim_bonus_points()
		self.assertEqual(self.tasks.wait_for_then_click.call_count, 2)
		self.assertEqual(self.tasks.driver.visited, ["https://rewards.bing.com/dashboard"])
		self.assertIsNone(self.tasks.progress)


if __name__ == "__main__":
	unittest.main()
