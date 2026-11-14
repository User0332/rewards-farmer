"""Replace each query through its real input, with no clear-button dependency."""
import os
import sys
import types
import unittest
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException
import rewards_tasks
import element_selectors
from fakes import FakeDriver, FakeElement


class Input(FakeElement):
	def __init__(self):
		super().__init__()
		self.selected = False
		self.focused = False
		self.value = "previous query"
		self.preparations = 0

	def send_keys(self, *keys):
		self.preparations += 1
		self.focused = True
		self.selected = keys == (Keys.CONTROL, "a", Keys.NULL)


class SearchWithoutClear(unittest.TestCase):
	def make_tasks(self, *, fail_prepare=False):
		field = Input()
		driver = FakeDriver(children={(By.ID, "sb_form_q"): [field], (By.TAG_NAME, "textarea"): [field]})
		driver.visited = []
		driver.get = driver.visited.append
		tasks = rewards_tasks.RewardsTaskUtils.__new__(rewards_tasks.RewardsTaskUtils)
		tasks.driver, tasks.elements = driver, element_selectors.ElementSelectionUtils(driver)
		tasks.tab_utils = types.SimpleNamespace(ensure_focus=lambda: None)
		tasks.dismiss_cookie_consent = lambda: None
		tasks.progress = None
		submitted = []
		def type_query(value):
			self.assertTrue(field.focused, "Typing must target the actual search input")
			self.assertTrue(field.selected, "The previous query must be selected before typing")
			field.value = value
			submitted.append(value)
			field.focused = field.selected = False
		tasks.keyboard = types.SimpleNamespace(send_keys=type_query)
		# Only the pointer and clock are synthetic; the getter, input prep and
		# query loop remain real. There is deliberately no sw_clx node.
		tasks.wait_for_element = lambda getter, timeout=10: getter()
		tasks.wait_for_then_click = lambda getter, timeout=10: getter()
		if fail_prepare:
			field.send_keys = mock.Mock(side_effect=StaleElementReferenceException("input replaced"))
		return tasks, field, submitted

	def test_two_queries_without_clear_button_refocus_and_replace_each_time(self):
		tasks, field, submitted = self.make_tasks()
		with mock.patch.object(rewards_tasks.queries, "related_queries", return_value=iter(["weather", "mountains"])), mock.patch.object(rewards_tasks.time, "sleep", return_value=None):
			self.assertEqual(tasks.run_search_batch(2, already_sent=4), 2)
		self.assertEqual(submitted, [f"weather -noai{Keys.ENTER}", f"mountains -noai{Keys.ENTER}"])
		self.assertEqual(field.preparations, 2)
		self.assertEqual(tasks._searches_sent, 6)
		self.assertEqual(tasks.progress, "sent 6 searches")
		self.assertEqual(tasks.driver.visited, ["https://www.bing.com/", rewards_tasks.REWARDS_HOME_URL])

	def test_results_input_is_found_by_id_without_a_textarea(self):
		tasks, field, _ = self.make_tasks()
		tasks.driver.children.pop((By.TAG_NAME, "textarea"))
		self.assertIs(tasks.elements.get_bing_search_bar(), field)

	def test_failed_preparation_sends_no_search_and_does_not_retry_query(self):
		tasks, _, submitted = self.make_tasks(fail_prepare=True)
		with mock.patch.object(rewards_tasks.queries, "related_queries", return_value=iter(["weather"])), mock.patch.object(rewards_tasks.time, "sleep", return_value=None), self.assertRaises(StaleElementReferenceException):
			tasks.run_search_batch(1, already_sent=4)
		self.assertEqual(submitted, [])
		self.assertEqual(tasks._searches_sent, 4)


if __name__ == "__main__":
	unittest.main()
