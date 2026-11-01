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
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from selenium.common.exceptions import (
	NoSuchElementException,
	StaleElementReferenceException,
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


class FailureReportAfterProgress(unittest.TestCase):
	"""A task that stopped part way, the other half of #52."""

	def test_absence_after_progress_is_a_failure(self):
		# Two cards went through, so the section was there. Whatever was
		# missing after that is not this market lacking the task.
		tag, reason = task_failure_report(
			ElementNeverAppeared("nothing matched"), progress="searched 2 of 3 cards"
		)

		self.assertEqual(tag, "FAIL")
		self.assertEqual(
			reason,
			"searched 2 of 3 cards, then the next element never appeared (ElementNeverAppeared)",
		)

	def test_a_lookup_that_missed_after_progress_is_a_failure(self):
		tag, reason = task_failure_report(
			NoSuchElementException("no element"), progress="opened the sidebar"
		)

		self.assertEqual(tag, "FAIL")
		self.assertNotIn("not available", reason)

	def test_an_expired_wait_after_progress_keeps_its_reason(self):
		tag, reason = task_failure_report(TimeoutException("Message: "), progress="sent 3 searches")

		self.assertEqual(tag, "FAIL")
		self.assertEqual(
			reason, "sent 3 searches, then on the page but not ready in time (TimeoutException)"
		)

	def test_anything_else_after_progress_keeps_its_message(self):
		tag, reason = task_failure_report(
			WebDriverException("chrome not reachable"), progress="opened 1 card"
		)

		self.assertEqual(tag, "FAIL")
		self.assertTrue(reason.startswith("opened 1 card, then WebDriverException"))
		self.assertIn("chrome not reachable", reason)


class TaskLoop(unittest.TestCase):
	"""complete_all_tasks, with the six tasks replaced by recorded calls."""

	STEPS = (
		("Bing daily set", "complete_bing_daily_set"),
		("Explore on Bing", "complete_explore_on_bing_tasks"),
		("Visual search", "complete_visual_search"),
		("Misc cards", "complete_misc_cards"),
		("Quests", "complete_quests"),
		("Required searches", "complete_required_searches"),
		("Bonus points", "claim_bonus_points"),
	)

	def setUp(self):
		self.ran = []

	def _tasks(self, failures=None, progress=None):
		failures = failures or {}
		progress = progress or {}
		tasks = make_tasks()

		for _, attribute in self.STEPS:
			def step(name=attribute):
				self.ran.append(name)

				if name in progress:
					tasks.progress = progress[name]

				if name in failures:
					raise failures[name]

			setattr(tasks, attribute, step)

		return tasks

	def _run(self, failures=None, progress=None):
		with self.assertLogs(rewards_tasks.logger, level=logging.INFO) as captured:
			self._tasks(failures, progress).complete_all_tasks()

		return "\n".join(captured.output)

	def test_a_task_that_got_part_way_says_how_far(self):
		output = self._run(
			failures={"complete_explore_on_bing_tasks": ElementNeverAppeared("nothing matched")},
			progress={"complete_explore_on_bing_tasks": "searched 2 of 3 cards"},
		)

		self.assertIn(
			"[FAIL] Explore on Bing: searched 2 of 3 cards, then the next element never appeared",
			output,
		)

	def test_how_far_one_task_got_does_not_carry_into_the_next(self):
		# The daily set finishes and leaves its progress behind. Explore on
		# Bing then finds no section at all, which is still absence.
		output = self._run(
			failures={
				"complete_explore_on_bing_tasks": NoSuchElementException("no Explore on Bing section"),
			},
			progress={"complete_bing_daily_set": "opened 3 of 3 activities"},
		)

		self.assertIn("[SKIP] Explore on Bing: not available in this UI variant", output)

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


def failing_on(call_number, exc, value=None):
	"""A stand-in that returns value until its call_number-th call, which raises exc."""
	calls = []

	def stand_in(*args, **kwargs):
		calls.append(args)

		if len(calls) == call_number:
			raise exc

		return value

	return stand_in


def wait_once(getter, timeout=10):
	"""wait_for_element without the waiting: one look, then the same verdict."""
	try:
		found = getter()
	except ElementNotReady as exc:
		# There but still rendering, which the real wait lets out as the
		# plain TimeoutException.
		raise TimeoutException(str(exc)) from exc
	except NoSuchElementException as exc:
		raise ElementNeverAppeared(str(exc)) from exc

	if not found:
		raise TimeoutException("found something and rejected it")

	return found


class ProgressInsideTasks(unittest.TestCase):
	"""The real task methods, stopped part way through.

	Only what each task reaches for is stood in for. The task body,
	complete_all_tasks and task_failure_report are the real ones.
	"""

	def setUp(self):
		# The tasks pause between steps to look human. Not needed here.
		sleep = mock.patch.object(rewards_tasks.time, "sleep", lambda seconds: None)
		sleep.start()
		self.addCleanup(sleep.stop)

	def _tasks(self, **elements):
		tasks = make_tasks()

		tasks.driver = types.SimpleNamespace(current_window_handle="main", get=lambda url: None)
		tasks.elements = types.SimpleNamespace(**elements)
		tasks.tab_utils = types.SimpleNamespace(
			close_all_other_tabs=lambda exceptions=None: None,
			switch_to_other_tab=lambda: None,
			ensure_focus=lambda: None,
		)
		tasks.keyboard = types.SimpleNamespace(send_keys=lambda keys: None)
		tasks.mouse = types.SimpleNamespace(
			wheel_scroll_element_into_view=lambda element: None,
			wheel_scroll_to_top=lambda: None,
		)
		tasks.switch_to_earn_page = lambda: None
		tasks.switch_to_dashboard = lambda: None
		tasks.move_to_and_click = lambda target: None
		tasks.restore_main_tab = lambda: None
		tasks.return_to_rewards_home = lambda: None

		return tasks

	def _report(self, tasks, attribute):
		"""What complete_all_tasks logs with only this one task left real."""
		for _, other in TaskLoop.STEPS:
			if other != attribute:
				setattr(tasks, other, lambda: None)

		with self.assertLogs(rewards_tasks.logger, level=logging.INFO) as captured:
			tasks.complete_all_tasks()

		return "\n".join(captured.output)

	def test_explore_on_bing_says_how_many_cards_it_searched(self):
		tasks = self._tasks(
			get_explore_on_bing_elements=lambda: ["card 1", "card 2", "card 3"],
			extract_card_descriptions=lambda card: card,
			get_bing_search_bar=lambda: "search bar",
			card_is_complete=lambda card: True,
		)
		# The search bar on the third card's tab never shows up.
		tasks.wait_for_element = failing_on(3, ElementNeverAppeared("nothing matched"))

		with mock.patch.object(rewards_tasks.queries, "search_query_for_task", lambda desc: "query"):
			output = self._report(tasks, "complete_explore_on_bing_tasks")

		self.assertIn(
			"[FAIL] Explore on Bing: searched 2 of 3 cards, then the next element never appeared",
			output,
		)

	def _quests(self, pages, sticky=()):
		"""The quests task over punch card pages: url -> labels still unlocked.

		Clicking an activity uses it up, except the labels in sticky, which
		stay clickable the way some cards keep their link after the click.
		"""
		class Link:
			def __init__(self, text, href):
				self.text, self.href = text, href

			def get_dom_attribute(self, name):
				return self.href if name == "href" else None

		current = [None]
		clicked = []

		def click(activity):
			clicked.append(activity.text)

			if activity.text not in sticky:
				pages[current[0]].remove(activity.text)

		tasks = self._tasks(
			get_quest_links=lambda: [Link("card", url) for url in ["/earn/quest/a", "/earn/quest/a", "/earn/quest/b"]],
			get_quest_activity_links=lambda: [Link(text, f"https://www.bing.com/search?q={text}") for text in pages[current[0]]],
		)
		tasks.driver.get = lambda url: current.__setitem__(0, url)
		tasks.wait_for_element = wait_once
		tasks.move_to_and_click = click

		return tasks, clicked

	def test_quests_click_each_unlocked_activity_once(self):
		pages = {
			"https://rewards.bing.com/earn/quest/a": ["a1", "always open", "a2"],
			"https://rewards.bing.com/earn/quest/b": ["b1"],
		}
		tasks, clicked = self._quests(pages, sticky={"always open"})

		output = self._report(tasks, "complete_quests")

		self.assertEqual(clicked, ["a1", "always open", "a2", "b1"])
		self.assertIn("2 quest(s) found", output)
		self.assertIn("[OK] Quests", output)

	def test_no_quests_section_is_skipped(self):
		tasks = self._tasks(get_quest_links=lambda: [])

		output = self._report(tasks, "complete_quests")

		self.assertIn("[SKIP] Quests: not available in this UI variant", output)

	def test_explore_on_bing_without_a_section_is_still_skipped(self):
		tasks = self._tasks(get_explore_on_bing_elements=lambda: [])

		output = self._report(tasks, "complete_explore_on_bing_tasks")

		self.assertIn("[SKIP] Explore on Bing: not available in this UI variant", output)

	def test_explore_on_bing_without_a_section_is_not_kept_waiting(self):
		# Most accounts do not have the section. Asking once is enough there,
		# a wait would only add its length to every run.
		tasks = self._tasks(get_explore_on_bing_elements=lambda: [])
		waited = []
		tasks.wait_for_element = lambda getter, timeout=10: waited.append(getter)

		self._report(tasks, "complete_explore_on_bing_tasks")

		self.assertEqual(waited, [])

	def _explore_rendering(self, ready_after):
		"""Explore on Bing on a page whose section renders after ready_after looks."""
		looks = []

		def explore():
			looks.append(1)

			if len(looks) <= ready_after:
				raise ElementNotReady("'exploreonbing' is present but no visible copy has content yet")

			return ["card 1"]

		tasks = self._tasks(
			get_explore_on_bing_elements=explore,
			extract_card_descriptions=lambda card: card,
			get_bing_search_bar=lambda: "search bar",
			card_is_complete=lambda card: True,
		)
		tasks.wait_for_element = wait_once

		return tasks

	def test_explore_on_bing_waits_for_a_section_that_is_still_rendering(self):
		tasks = self._explore_rendering(ready_after=1)

		with mock.patch.object(rewards_tasks.queries, "search_query_for_task", lambda desc: "query"):
			output = self._report(tasks, "complete_explore_on_bing_tasks")

		self.assertIn("[OK] Explore on Bing", output)

	def test_explore_on_bing_that_never_finishes_rendering_is_a_failure(self):
		tasks = self._explore_rendering(ready_after=99)

		output = self._report(tasks, "complete_explore_on_bing_tasks")

		self.assertIn(
			"[FAIL] Explore on Bing: on the page but not ready in time (TimeoutException)",
			output,
		)

	def test_explore_on_bing_survives_the_section_rendering_again(self):
		# #102: coming back from each search tab re-renders the section, so a
		# card read before the first search is stale by the second click.
		render = [0]
		clicked = []

		def cards():
			return [(f"card {i}", render[0]) for i in range(1, 4)]

		def click(card):
			if card[1] != render[0]:
				raise StaleElementReferenceException("stale element not found in the current frame")

			clicked.append(card[0])

		def back_on_the_earn_page(exceptions=None):
			render[0] += 1

		tasks = self._tasks(
			get_explore_on_bing_elements=cards,
			extract_card_descriptions=lambda card: card[0],
			get_bing_search_bar=lambda: "search bar",
			card_is_complete=lambda card: True,
		)
		tasks.move_to_and_click = click
		tasks.tab_utils.close_all_other_tabs = back_on_the_earn_page
		tasks.wait_for_element = wait_once

		with mock.patch.object(rewards_tasks.queries, "search_query_for_task", lambda desc: "query"):
			output = self._report(tasks, "complete_explore_on_bing_tasks")

		self.assertEqual(clicked, ["card 1", "card 2", "card 3"])
		self.assertIn("[OK] Explore on Bing", output)

	def _visual_search(self, link=None, done_today=False):
		"""The visual search task with its sidebar open.

		link is what the panel offers to click, None for no link at all.
		Returns the task and what it clicked.
		"""
		def search_now():
			if link is None:
				raise NoSuchElementException("visual search sidebar has no usable link")

			return link

		def no_camera_button():
			raise NoSuchElementException("no camera button")

		tasks = self._tasks(
			get_open_visual_search_sidebar=lambda: "opener",
			get_search_now_link_from_visual_search_sidebar=search_now,
			visual_search_done_today=lambda: done_today,
			get_visual_search_button=no_camera_button,
		)

		clicked = []
		tasks.move_to_and_click = lambda target: clicked.append(target() if callable(target) else target)
		tasks.wait_for_then_click = lambda getter, timeout=10: tasks.move_to_and_click(
			tasks.wait_for_element(getter, timeout)
		)
		tasks.wait_for_element = wait_once

		return tasks, clicked

	def test_visual_search_that_opened_its_sidebar_is_not_called_unavailable(self):
		# The sidebar is there, the link inside it is not, and the panel does
		# not say the search is done either.
		tasks, clicked = self._visual_search(link=None, done_today=False)

		with mock.patch.object(rewards_tasks, "VISUAL_SEARCH_IMAGE_PATH", __file__):
			output = self._report(tasks, "complete_visual_search")

		self.assertIn(
			"[FAIL] Visual search: opened the sidebar, then the next element never appeared",
			output,
		)

	def test_visual_search_done_for_today_is_not_a_failure(self):
		# What a second run on the same day sees: no link, and search now
		# rendered disabled.
		tasks, clicked = self._visual_search(link=None, done_today=True)

		with mock.patch.object(rewards_tasks, "VISUAL_SEARCH_IMAGE_PATH", __file__):
			output = self._report(tasks, "complete_visual_search")

		self.assertIn("[OK] Visual search", output)
		self.assertIn("Visual search is already done for today", output)
		self.assertEqual(clicked, ["opener"])

	def test_visual_search_with_a_link_still_goes_to_bing(self):
		# A running streak is not mistaken for a finished one. The task clicks
		# the link and only stops at the camera button stood in here.
		tasks, clicked = self._visual_search(link="search now", done_today=False)

		with mock.patch.object(rewards_tasks, "VISUAL_SEARCH_IMAGE_PATH", __file__):
			output = self._report(tasks, "complete_visual_search")

		self.assertEqual(clicked, ["opener", "search now"])
		self.assertIn(
			"[FAIL] Visual search: opened the visual search page, then the next element never appeared",
			output,
		)

	def test_visual_search_without_its_sidebar_is_still_skipped(self):
		tasks = self._tasks(get_open_visual_search_sidebar=object())

		def wait_for_then_click(getter, timeout=10):
			raise ElementNeverAppeared("nothing matched")

		tasks.wait_for_then_click = wait_for_then_click

		with mock.patch.object(rewards_tasks, "VISUAL_SEARCH_IMAGE_PATH", __file__):
			output = self._report(tasks, "complete_visual_search")

		self.assertIn("[SKIP] Visual search: not available in this UI variant", output)

	def test_required_searches_count_every_search_across_rounds(self):
		# 0/30 asks for ten searches, 15/30 after them for five more. The
		# clear button goes missing after the second search of that round.
		tasks = self._tasks(
			get_bing_search_bar=lambda: "search bar",
			get_clear_bing_search_query_button=lambda: "clear",
		)
		readings = iter([(0, 30), (15, 30)])
		tasks.read_search_points = lambda: next(readings)
		tasks.wait_for_element = lambda getter, timeout=10: "search bar"
		tasks.wait_for_then_click = failing_on(12, ElementNeverAppeared("nothing matched"))

		with mock.patch.object(rewards_tasks.queries, "related_queries", lambda count: ["query"] * count):
			output = self._report(tasks, "complete_required_searches")

		self.assertIn(
			"[FAIL] Required searches: sent 12 searches, then the next element never appeared",
			output,
		)

	def test_a_source_that_comes_back_short_is_counted_as_it_was(self):
		# The trends source can return fewer queries than a round asks for.
		# 0/30 asks for ten and gets three, 9/30 then asks for seven and gets
		# three again. The clear button goes missing after the second search of
		# that round, which is the fifth search sent, not the twelfth.
		tasks = self._tasks(
			get_bing_search_bar=lambda: "search bar",
			get_clear_bing_search_query_button=lambda: "clear",
		)
		readings = iter([(0, 30), (9, 30)])
		tasks.read_search_points = lambda: next(readings)
		tasks.wait_for_element = lambda getter, timeout=10: "search bar"
		tasks.wait_for_then_click = failing_on(5, ElementNeverAppeared("nothing matched"))

		with mock.patch.object(rewards_tasks.queries, "related_queries", lambda count: ["query"] * min(count, 3)):
			output = self._report(tasks, "complete_required_searches")

		self.assertIn("Round 1: 3 searches -> 9/30", output)
		self.assertIn(
			"[FAIL] Required searches: sent 5 searches, then the next element never appeared",
			output,
		)

	def test_misc_cards_say_how_many_they_opened(self):
		cards = ["card 1", "card 2", "card 3"]
		tasks = self._tasks(
			# Three reads inside the loop, the fourth is the check after it.
			get_all_misc_cards=failing_on(4, NoSuchElementException("no cards"), value=cards),
			card_is_complete=lambda card: False,
			get_card_point_value=lambda card: 10,
		)
		tasks.wait_for_element = lambda getter, timeout=10: cards

		output = self._report(tasks, "complete_misc_cards")

		self.assertIn(
			"[FAIL] Misc cards: opened 3 cards, then the next element never appeared",
			output,
		)

	def test_the_daily_set_says_how_many_activities_it_opened(self):
		tasks = self._tasks(
			get_open_daily_set_button=lambda: "button",
			get_daily_set_elements=lambda: ["a", "b", "c"],
			get_daily_set_element_by_index=lambda index: "activity",
		)
		tasks.wait_for_then_click = lambda getter, timeout=10: None
		tasks.wait_for_element = lambda getter, timeout=10: getter()
		# The tab the second activity opened does not close.
		tasks.tab_utils.close_all_other_tabs = failing_on(2, WebDriverException("no such window"))

		output = self._report(tasks, "complete_bing_daily_set")

		self.assertIn("[FAIL] Bing daily set: opened 2 of 3 activities, then WebDriverException", output)

	def test_a_daily_set_panel_that_opened_empty_is_not_called_unavailable(self):
		def no_activities():
			raise NoSuchElementException("no daily set activities")

		def panel_never_fills(getter, timeout=10):
			raise TimeoutException("Message: ")

		tasks = self._tasks(
			get_open_daily_set_button=lambda: "button",
			get_daily_set_elements=no_activities,
		)
		tasks.wait_for_then_click = lambda getter, timeout=10: None
		tasks.wait_for_element = panel_never_fills

		output = self._report(tasks, "complete_bing_daily_set")

		self.assertIn(
			"[FAIL] Bing daily set: opened the panel, then the next element never appeared",
			output,
		)

	def test_a_claim_button_gone_after_the_panel_opened_is_a_failure(self):
		bonus, claim = object(), object()
		tasks = self._tasks(get_bonus_button_on_dashboard=bonus, get_claim_bonus_points_button=claim)

		def wait_for_then_click(getter, timeout=10):
			if getter is claim:
				# Seen by the wait, gone by the click.
				raise NoSuchElementException("no claim button")

		tasks.wait_for_then_click = wait_for_then_click

		output = self._report(tasks, "claim_bonus_points")

		self.assertIn(
			"[FAIL] Bonus points: opened the bonus panel, then the next element never appeared",
			output,
		)


if __name__ == "__main__":
	unittest.main()
