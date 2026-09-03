"""Tests for what a run says about a task that did not complete.

The reported reason used to be a guess. Every wait that expired and every
lookup that missed produced "not available in this UI variant", so a section
that was on the page and slow read exactly like one this market does not ship,
which is #52. These pin down which failures are absence and which are not.

None of them need a browser.

	python -m unittest discover -s tests
"""

import logging
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from selenium.common.exceptions import (
	NoSuchElementException,
	TimeoutException,
	WebDriverException,
)

import rewards_tasks
from element_selectors import ElementNotReady
from fakes import FakeDriver
from rewards_tasks import ElementNeverAppeared, task_failure_report

# Long enough for one poll, short enough that the suite stays quick.
# WebDriverWait sleeps 0.5s between attempts, so a wait that expires costs
# about that regardless of the timeout asked for.
BRIEF = 0.05


def make_tasks():
	"""A RewardsTaskUtils without the browser its __init__ opens."""
	tasks = rewards_tasks.RewardsTaskUtils.__new__(rewards_tasks.RewardsTaskUtils)

	tasks.driver = FakeDriver()
	tasks.tab_utils = types.SimpleNamespace(close_all_other_tabs=lambda: None)

	return tasks


class WaitClassification(unittest.TestCase):
	"""wait_for_element has to say why it gave up, not just that it did."""

	def test_a_getter_that_never_finds_anything_is_absence(self):
		def missing():
			raise NoSuchElementException("no button containing 'visual search streak'")

		with self.assertRaises(ElementNeverAppeared):
			make_tasks().wait_for_element(missing, timeout=BRIEF)

	def test_a_section_that_is_still_rendering_is_not_absence(self):
		# The id is in the page, no visible copy has content yet. Waiting
		# longer is the answer here, skipping the task is not.
		def not_ready():
			raise ElementNotReady("'moreactivities' is present but no visible copy has content yet")

		with self.assertRaises(TimeoutException) as caught:
			make_tasks().wait_for_element(not_ready, timeout=BRIEF)

		self.assertNotIsInstance(caught.exception, ElementNeverAppeared)

	def test_a_getter_that_rejects_what_it_finds_is_not_absence(self):
		# complete_bing_daily_set holds out for all three activities and
		# returns False until they are there. The panel itself is open.
		with self.assertRaises(TimeoutException) as caught:
			make_tasks().wait_for_element(lambda: [], timeout=BRIEF)

		self.assertNotIsInstance(caught.exception, ElementNeverAppeared)

	def test_an_element_that_arrives_late_is_still_returned(self):
		attempts = []

		def slow():
			attempts.append(None)

			if len(attempts) < 2:
				raise NoSuchElementException("not yet")

			return "the element"

		self.assertEqual(make_tasks().wait_for_element(slow, timeout=5), "the element")
		# A single lucky first attempt would prove nothing about the retry.
		self.assertGreater(len(attempts), 1)

	def test_the_getters_own_reason_survives(self):
		def missing():
			raise NoSuchElementException("no button containing 'points breakdown'")

		with self.assertRaises(ElementNeverAppeared) as caught:
			make_tasks().wait_for_element(missing, timeout=BRIEF)

		self.assertIn("points breakdown", str(caught.exception))


class ExistingTimeoutHandlers(unittest.TestCase):
	"""The new type has to stay catchable where TimeoutException was."""

	def test_it_is_still_a_timeout(self):
		self.assertTrue(issubclass(ElementNeverAppeared, TimeoutException))

	def test_having_no_bonus_points_is_still_only_a_warning(self):
		# There is no Claim button when there is nothing to claim, so this
		# path reaches the wait expecting to be disappointed. If the new type
		# escaped its `except TimeoutException`, an ordinary run would start
		# reporting a failed task every day.
		def bonus_button():
			pass

		def claim_button():
			pass

		tasks = make_tasks()
		tasks.switch_to_dashboard = lambda: None
		tasks.elements = types.SimpleNamespace(
			get_bonus_button_on_dashboard=bonus_button,
			get_claim_bonus_points_button=claim_button,
		)

		def wait_for_then_click(getter, timeout=10):
			if getter is claim_button:
				raise ElementNeverAppeared("nothing matched during the 10s wait")

		tasks.wait_for_then_click = wait_for_then_click

		with self.assertLogs(rewards_tasks.logger, level=logging.WARNING) as captured:
			tasks.claim_bonus_points()

		self.assertIn("no bonus points to claim", "\n".join(captured.output).lower())


class FailureReport(unittest.TestCase):
	def test_a_section_this_variant_does_not_ship_is_skipped(self):
		tag, reason = task_failure_report(
			NoSuchElementException("no element with id 'moreactivities'")
		)

		self.assertEqual(tag, "SKIP")
		self.assertIn("not available in this UI variant", reason)

	def test_a_wait_that_never_saw_the_element_is_skipped(self):
		tag, reason = task_failure_report(ElementNeverAppeared("nothing matched"))

		self.assertEqual(tag, "SKIP")
		self.assertIn("not available in this UI variant", reason)

	def test_a_section_that_never_finished_rendering_is_not_skipped(self):
		tag, reason = task_failure_report(
			ElementNotReady("'moreactivities' is present but no visible copy has content yet")
		)

		self.assertEqual(tag, "FAIL")
		self.assertNotIn("not available", reason)

	def test_an_expired_wait_is_not_skipped(self):
		# The line in #52, reported for a panel that was on screen the whole
		# time: "[SKIP] Required searches: not available in this UI variant
		# (TimeoutException)".
		tag, reason = task_failure_report(TimeoutException("Message: "))

		self.assertEqual(tag, "FAIL")
		self.assertNotIn("not available", reason)

	def test_the_exception_name_is_kept(self):
		# It is the difference between a lookup that missed and a wait that
		# expired, and someone pasting a log should not lose it.
		self.assertIn("ElementNeverAppeared", task_failure_report(ElementNeverAppeared("x"))[1])
		self.assertIn("TimeoutException", task_failure_report(TimeoutException("x"))[1])

	def test_anything_else_keeps_its_own_message(self):
		tag, reason = task_failure_report(WebDriverException("chrome not reachable"))

		self.assertEqual(tag, "FAIL")
		self.assertIn("WebDriverException", reason)
		self.assertIn("chrome not reachable", reason)


class TaskLoop(unittest.TestCase):
	"""complete_all_tasks, with the six tasks replaced by recorded calls."""

	STEPS = (
		("Bing daily set", "complete_bing_daily_set"),
		("Explore on Bing", "complete_explore_on_bing_tasks"),
		("Visual search", "complete_visual_search"),
		("Misc cards", "complete_misc_cards"),
		("Required searches", "complete_required_searches"),
		("Bonus points", "claim_bonus_points"),
	)

	def setUp(self):
		self.ran = []

	def _tasks(self, failures=None):
		failures = failures or {}
		tasks = make_tasks()

		for _, attribute in self.STEPS:
			def step(name=attribute):
				self.ran.append(name)

				if name in failures:
					raise failures[name]

			setattr(tasks, attribute, step)

		return tasks

	def _run(self, failures=None):
		with self.assertLogs(rewards_tasks.logger, level=logging.INFO) as captured:
			self._tasks(failures).complete_all_tasks()

		return "\n".join(captured.output)

	def test_a_failing_task_does_not_stop_the_ones_after_it(self):
		self._run({"complete_visual_search": WebDriverException("chrome not reachable")})

		self.assertEqual(self.ran, [attribute for _, attribute in self.STEPS])

	def test_absence_and_an_expired_wait_read_differently(self):
		output = self._run({
			"complete_visual_search": ElementNeverAppeared("nothing matched"),
			"complete_required_searches": TimeoutException("Message: "),
		})

		self.assertIn("[SKIP] Visual search: not available in this UI variant", output)
		self.assertIn("[FAIL] Required searches: on the page but not ready in time", output)
		self.assertIn("[OK] Bing daily set", output)

	def test_a_section_that_never_rendered_is_not_called_unavailable(self):
		output = self._run({
			"complete_misc_cards": ElementNotReady(
				"'moreactivities' is present but no visible copy has content yet"
			),
		})

		self.assertIn("[FAIL] Misc cards: on the page but not ready in time", output)
		self.assertNotIn("Misc cards: not available", output)

	def test_a_task_this_variant_does_not_ship_is_still_skipped(self):
		output = self._run({
			"complete_explore_on_bing_tasks": NoSuchElementException(
				"no Explore on Bing section in this UI variant"
			),
		})

		self.assertIn("[SKIP] Explore on Bing: not available in this UI variant", output)

	def test_an_unexpected_failure_still_reports_what_went_wrong(self):
		output = self._run({"complete_misc_cards": WebDriverException("chrome not reachable")})

		self.assertIn("[FAIL] Misc cards: WebDriverException", output)
		self.assertIn("chrome not reachable", output)


if __name__ == "__main__":
	unittest.main()
