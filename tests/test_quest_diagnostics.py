"""Diagnostics over synthetic page fixtures, without changing quest clicks."""

from html.parser import HTMLParser
import json
import logging
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from selenium.common.exceptions import StaleElementReferenceException, TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from element_selectors import ElementSelectionUtils
import log_utils
import rewards_tasks

FIXTURES = json.loads((Path(__file__).with_name("fixtures") / "quest_controls.json").read_text(encoding="utf-8"))
CARD = "https://rewards.bing.com/earn/quest/synthetic-card"


class Element:
	def __init__(self, tag, attrs=(), parent=None):
		self.tag_name = tag
		self.attrs = {key: value if value is not None else "true" for key, value in attrs}
		self.parent = parent
		self.children = []
		self.content = []
		self.text_reads = 0

	@property
	def text(self):
		self.text_reads += 1
		return " ".join(str(child) if isinstance(child, str) else child.text for child in self.content)

	def get_dom_attribute(self, name):
		return self.attrs.get(name)

	def is_displayed(self):
		return "hidden" not in self.attrs and (self.parent is None or self.parent.is_displayed())

	def find_elements(self, by, selector):
		found = []
		for child in self.children:
			if by == By.TAG_NAME and child.tag_name == selector or by == By.CSS_SELECTOR and (child.tag_name in ("a", "button") or child.attrs.get("role") == "button"):
				found.append(child)
			found.extend(child.find_elements(by, selector))
		return found


class Page(HTMLParser):
	def __init__(self, html):
		super().__init__()
		self.root = Element("document")
		self.current = self.root
		self.feed(html)
		self.current_url = CARD
		self.loads = []

	def handle_starttag(self, tag, attrs):
		node = Element(tag, attrs, self.current)
		self.current.children.append(node)
		self.current.content.append(node)
		self.current = node

	def handle_endtag(self, tag):
		if self.current.parent is not None:
			self.current = self.current.parent

	def handle_data(self, data):
		self.current.content.append(data)

	def find_elements(self, by, selector):
		return self.root.find_elements(by, selector)

	def get(self, url):
		self.loads.append(url)
		self.current_url = url


class QuestDiagnosticsTests(unittest.TestCase):
	def tasks(self, fixture):
		page = Page(FIXTURES[fixture] if fixture in FIXTURES else fixture)
		tasks = rewards_tasks.RewardsTaskUtils.__new__(rewards_tasks.RewardsTaskUtils)
		tasks.driver = page
		tasks.elements = ElementSelectionUtils(page)
		tasks.tab_utils = types.SimpleNamespace(ensure_focus=mock.Mock(), close_all_other_tabs=mock.Mock())
		tasks.move_to_and_click = mock.Mock()
		return tasks

	def blocked(self, fixture, *, timeout=True):
		tasks = self.tasks(fixture)
		if timeout:
			tasks.wait_for_element = lambda getter: rewards_tasks.RewardsTaskUtils.wait_for_element(tasks, getter, timeout=0)
		else:
			tasks.wait_for_element = mock.Mock(return_value=[])
		with self.assertLogs(rewards_tasks.logger, level=logging.INFO) as output:
			self.assertEqual(tasks.complete_single_quest(CARD, 8), 0)
		messages = [record.getMessage() for record in output.records]
		self.assertEqual(messages[0], "Nothing left to click on synthetic-card")
		self.assertEqual(len(messages), 2)
		self.assertTrue(messages[1].startswith("Quest controls in main: "))
		self.assertLessEqual(len(messages[1]), 600)
		formatted = logging.Formatter(log_utils.LOG_FORMAT, datefmt=log_utils.DATE_FORMAT).format(output.records[1])
		self.assertLessEqual(len(formatted), 600)
		tasks.move_to_and_click.assert_not_called()
		self.assertEqual(tasks.driver.loads, [CARD])
		tasks.tab_utils.ensure_focus.assert_called_once()
		tasks.tab_utils.close_all_other_tabs.assert_not_called()
		return tasks, messages[1]

	def test_button_activation_and_locked_links_are_described_in_timeout_branch(self):
		_, line = self.blocked("activate_button")
		self.assertIn("button role=- text='Activate quest' href=- aria-disabled=- disabled=- rule=not a link", line)
		self.assertIn("text='Start searching' href=www.bing.com/search aria-disabled=true disabled=- rule=disabled", line)
		self.assertNotIn("?", line)
		self.assertNotIn("#", line)

	def test_rewards_host_and_relative_href_report_exact_filter_rejection(self):
		_, line = self.blocked("rewards_link")
		self.assertIn("role=button", line)
		self.assertIn("href=rewards.bing.com/earn/quest/activate", line)
		self.assertIn("rule=rewards host", line)
		_, line = self.blocked("relative_link")
		self.assertIn("href=rewards.bing.com/earn/quest/activate", line)
		self.assertIn("rule=no bing host", line)
		self.assertNotIn("not-for-log", line)

	def test_only_locked_controls_show_both_disabled_attributes(self):
		_, line = self.blocked("locked")
		self.assertIn("aria-disabled=true disabled=- rule=disabled", line)
		self.assertIn("aria-disabled=true disabled=true rule=not a link", line)

	def test_footer_is_outside_main_and_hidden_or_empty_elements_are_not_reported(self):
		_, line = self.blocked("footer")
		self.assertEqual(line, "Quest controls in main: none with visible text")
		self.assertNotIn("footer", line)
		_, line = self.blocked("hidden_and_empty")
		self.assertIn("div role=button text='Activate quest'", line)
		self.assertNotIn("Hidden", line)

	def test_empty_list_after_wait_uses_same_diagnostic_without_an_extra_wait(self):
		tasks, line = self.blocked("activate_button", timeout=False)
		tasks.wait_for_element.assert_called_once()
		self.assertIn("Activate quest", line)

	def test_clickable_card_has_no_diagnostic_read_or_extra_wait_and_clicks_unchanged(self):
		tasks = self.tasks("clickable")
		tasks.wait_for_element = mock.Mock(side_effect=lambda getter: getter())
		with mock.patch.object(tasks.elements, "describe_quest_controls", side_effect=AssertionError("diagnostic on successful card")), \
			mock.patch.object(rewards_tasks.time, "sleep") as sleep, self.assertLogs(rewards_tasks.logger, level=logging.INFO) as output:
			self.assertEqual(tasks.complete_single_quest(CARD, 1), 1)
		tasks.wait_for_element.assert_called_once()
		tasks.move_to_and_click.assert_called_once()
		self.assertEqual(tasks.move_to_and_click.call_args.args[0].text, "Search now")
		self.assertEqual(sleep.call_count, 2)
		self.assertEqual([record.getMessage() for record in output.records], ["Quest activity done: 'Search now'", "1 activity/activities completed on synthetic-card"])

	def test_later_timeout_after_progress_does_not_report_the_card_as_first_load_empty(self):
		tasks = self.tasks("clickable")
		tasks.wait_for_element = mock.Mock(side_effect=[tasks.elements.get_quest_activity_links(), TimeoutException()])
		with mock.patch.object(tasks.elements, "describe_quest_controls", side_effect=AssertionError("diagnostic after progress")), \
			mock.patch.object(rewards_tasks.time, "sleep"), self.assertLogs(rewards_tasks.logger, level=logging.INFO) as output:
			self.assertEqual(tasks.complete_single_quest(CARD, 2), 1)
		tasks.move_to_and_click.assert_called_once()
		self.assertFalse(any("Quest controls" in record.getMessage() for record in output.records))

	def test_zero_step_budget_does_not_inspect_a_card_that_was_never_loaded(self):
		tasks = self.tasks("activate_button")
		with mock.patch.object(tasks.elements, "describe_quest_controls", side_effect=AssertionError("diagnostic without first load")), self.assertLogs(rewards_tasks.logger, level=logging.INFO) as output:
			self.assertEqual(tasks.complete_single_quest(CARD, 0), 0)
		self.assertEqual(tasks.driver.loads, [])
		self.assertEqual([record.getMessage() for record in output.records], ["Nothing left to click on synthetic-card"])

	def test_twenty_controls_are_bounded_and_overflow_is_explicit(self):
		tasks, line = self.blocked("<main>" + "".join(f"<button role='button'>Activate quest {index} " + "x" * 80 + "</button>" for index in range(20)) + "</main>")
		self.assertLessEqual(line.count("text="), 12)
		self.assertLessEqual(len(line), 600)
		self.assertIn("more not shown", line)
		self.assertNotIn("x" * 41, line)
		controls = tasks.driver.find_elements(By.TAG_NAME, "main")[0].find_elements(By.CSS_SELECTOR, 'a, button, [role="button"]')
		self.assertEqual(sum(control.text_reads for control in controls), 12)
		self.assertTrue(all(control.text_reads == 0 for control in controls[12:]))

	def test_any_at_sign_value_is_masked_before_shortening_or_url_projection(self):
		_, line = self.blocked("masked")
		self.assertNotIn("@", line)
		self.assertNotIn("example.test", line)
		self.assertNotIn("secret", line)
		self.assertIn("text='[masked]'", line)
		self.assertIn("role=[masked]", line)
		self.assertIn("href=[masked]", line)
		self.assertIn("aria-disabled=[masked] disabled=[masked]", line)

	def test_at_sign_beyond_display_cutoff_still_masks_the_whole_value(self):
		_, line = self.blocked("<main><button role='" + "a" * 100 + "@example.test'>" + "Visible label " * 10 + "user@example.test</button></main>")
		self.assertIn("role=[masked] text='[masked]'", line)
		self.assertNotIn("example.test", line)
		self.assertNotIn("Visible label", line)

	def test_no_text_or_disabled_truth_is_not_fabricated_for_a_relative_link(self):
		_, line = self.blocked("<main><a href='/earn/quest/activate' aria-disabled='false' disabled>Activate quest</a><button> \n </button></main>")
		self.assertIn("aria-disabled=false disabled=true rule=no bing host", line)
		self.assertEqual(line.count("text="), 1)

	def test_diagnostic_error_is_info_only_and_does_not_change_empty_result(self):
		tasks = self.tasks("activate_button")
		tasks.wait_for_element = mock.Mock(side_effect=TimeoutException())
		with mock.patch.object(tasks.elements, "describe_quest_controls", side_effect=RuntimeError("private@example.test")), self.assertLogs(rewards_tasks.logger, level=logging.INFO) as output:
			self.assertEqual(tasks.complete_single_quest(CARD, 8), 0)
		self.assertEqual([record.getMessage() for record in output.records], ["Nothing left to click on synthetic-card", "Quest controls in main: unreadable (RuntimeError)"])
		tasks.move_to_and_click.assert_not_called()

	def test_diagnostic_exception_names_only_its_class_and_next_quest_still_runs(self):
		class QuestReadError(WebDriverException):
			pass
		tasks = self.tasks("activate_button")
		next_card = CARD + "-next"
		original_get = tasks.driver.get
		def navigate(url):
			original_get(url)
			tasks.driver.root = Page(FIXTURES["clickable" if url == next_card else "activate_button"]).root
		tasks.driver.get = navigate
		def wait_once(getter):
			found = getter()
			if not found:
				raise TimeoutException()
			return found
		tasks.wait_for_element = wait_once
		tasks.switch_to_earn_page = mock.Mock()
		completed = []
		single_quest = tasks.complete_single_quest
		def record_result(url, max_steps):
			result = single_quest(url, max_steps)
			completed.append((url, result))
			return result
		links = [Element("a", [("href", url)]) for url in (CARD, next_card)]
		with mock.patch.object(tasks.elements, "get_quest_links", return_value=links), \
			mock.patch.object(tasks.elements, "describe_quest_controls", side_effect=QuestReadError("private@example.test\npage text")) as diagnostic, \
			mock.patch.object(tasks, "complete_single_quest", side_effect=record_result), \
			mock.patch.object(rewards_tasks.time, "sleep"), self.assertLogs(rewards_tasks.logger, level=logging.INFO) as output:
			tasks.complete_quests(max_steps=1)
		messages = [record.getMessage() for record in output.records]
		self.assertEqual(completed, [(CARD, 0), (next_card, 1)])
		self.assertEqual(tasks.driver.loads, [CARD, next_card])
		self.assertEqual(tasks.progress, "worked through 2 of 2 quests")
		self.assertIn("Quest controls in main: unreadable (QuestReadError)", messages)
		self.assertFalse(any("private" in message or "@" in message or "page text" in message for message in messages))
		self.assertTrue(all(record.levelno == logging.INFO and not record.exc_info for record in output.records))
		diagnostic.assert_called_once()
		tasks.move_to_and_click.assert_called_once()
		self.assertEqual(tasks.move_to_and_click.call_args.args[0].text, "Search now")

	def test_diagnostic_does_not_silently_fix_native_disabled_or_raw_href_filter(self):
		for fixture in ("native_disabled_link", "literal_href_filter"):
			with self.subTest(fixture=fixture):
				tasks = self.tasks(fixture)
				self.assertEqual(len(tasks.elements.get_quest_activity_links()), 1)
				self.assertIn("rule=eligible", tasks.elements.describe_quest_controls())

	def test_missing_main_has_an_explicit_reason_without_footer_diagnostic(self):
		tasks = self.tasks("<footer><button>Footer action</button></footer>")
		self.assertEqual(tasks.elements.describe_quest_controls(), "Quest controls in main: none (main absent)")

	def test_stale_element_is_skipped_and_the_next_control_is_still_described(self):
		tasks = self.tasks("activate_button")
		main = tasks.driver.find_elements(By.TAG_NAME, "main")[0]
		stale = mock.Mock()
		type(stale).text = mock.PropertyMock(side_effect=StaleElementReferenceException())
		actual = main.find_elements(By.CSS_SELECTOR, 'a, button, [role="button"]')
		with mock.patch.object(main, "find_elements", return_value=[stale] + actual):
			self.assertIn("Activate quest", tasks.elements.describe_quest_controls())
