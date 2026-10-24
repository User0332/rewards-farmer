"""Quest-only entry and persistent links on synthetic pages, without a browser."""

import contextlib
import logging
import os
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By
import element_selectors
import main
import rewards_state
import rewards_tasks
import run_record
from fakes import FakeElement
from test_quest_diagnostics import Page
from test_rewards_state import StateDriver

EARN = rewards_state.EARN_URL
HALLOWEEN = "/earn/quest/WW_pcparent_HalloweenQuestROWOct26_punchcard"
SPOTIFY = "/earn/quest/WW_evergreen_pcparent_Spotify_punchcard"
MONTHLY = "/earn/quest/ENWW_pcparent_FY27_BingMonthlyPC_Oct_punchcard"
WEEKLY = "/earn/quest/WW_pcparent_RewardsApp_weekly_Exclusive_Octw3_2026_punchcard"
SEARCH = '<a href="https://www.bing.com/search?q=Halloween&amp;form=synthetic">Start searching</a>'
REDEEM = '<a href="https://rewards.bing.com/redeem/000821000039">Redeem now</a>'
TERMS = '<a href="https://www.spotify.com/legal/paid-subscription-terms/">Es gelten die Nutzungsbedingungen</a>'


def activities(fragment):
	return Page("<main>" + fragment + "</main>").find_elements(By.TAG_NAME, "main")[0].find_elements(By.TAG_NAME, "a")


def four_cards():
	return {SPOTIFY: activities(TERMS + REDEEM), HALLOWEEN: activities(SEARCH), MONTHLY: [], WEEKLY: []}


class Clock:
	def __init__(self):
		self.now = 0.0
		self.sleeps = []

	def __call__(self):
		return self.now

	def sleep(self, seconds):
		self.sleeps.append(seconds)
		self.now += seconds


class CloseControl(FakeElement):
	"""StateDriver names its sidebar close element like WebDriver.close()."""
	def __init__(self, driver):
		super().__init__(attributes={"aria-label": "Close"})
		self.driver = driver

	def __call__(self):
		self.driver.window_handles.remove(self.driver.current_window_handle)


class QuestPage(StateDriver):
	"""Render the real DOM lookups late; the task's waits are not replaced."""
	def __init__(self, clock, *, tab_delay=0, quests_delay=0, quests=None):
		super().__init__(balance="1000", quests=four_cards() if quests is None else quests)
		self.clock = clock
		self.tab_delay, self.quests_delay = tab_delay, quests_delay
		self.home_at = 0
		self.tabs_read = self.quests_read = self.focuses = 0
		self.activity_clicks = []
		self.cdp = []
		self.set_page_load_timeout = mock.Mock()
		self.set_script_timeout = mock.Mock()
		self.current_window_handle = "main"
		self.window_handles = ["main"]
		self.switch_to = types.SimpleNamespace(window=self.select_window)
		self.earn_tab = FakeElement(text="Earn")
		self.extra_headers = {}
		self.close = CloseControl(self)

	def get(self, url):
		if url == rewards_tasks.REWARDS_HOME_URL:
			self.home_at = self.clock()
			# The constructor reopens the home document, discarding any old DOM.
			super().get(EARN)
			self.current_url = url
			self.loads[-1] = url
		else:
			super().get(url)
		# A quest page has the same readable balance header as the earn page.
		self.children[(By.TAG_NAME, "header")] = [FakeElement(children={(By.TAG_NAME, "button"):
			[FakeElement(text=self.balance, attributes={"aria-label": "View profile"})]})]

	def find_elements(self, by, selector):
		if (by, selector) == (By.CSS_SELECTOR, '[id$="-tab-/earn"]'):
			self.tabs_read += 1
			return [self.earn_tab] if self.clock() >= self.home_at + self.tab_delay else []
		if (by, selector) == (By.ID, "quests"):
			self.quests_read += 1
			if self.clock() < self.home_at + self.quests_delay:
				return []
		return super().find_elements(by, selector)

	def execute_cdp_cmd(self, command, values):
		self.cdp.append((command, values))
		if command == "Network.setExtraHTTPHeaders":
			self.extra_headers = values["headers"]

	def execute_script(self, script, *args):
		if "document.dispatchEvent(new Event('visibilitychange'))" in script:
			self.focuses += 1
			return
		return super().execute_script(script, *args)

	def click(self, element):
		if element is self.earn_tab:
			self.get(EARN)
		else:
			self.activity_clicks.append((self.current_url, element.text.strip()))
			self.window_handles.append("activity")

	def select_window(self, handle):
		self.current_window_handle = handle


class Mouse:
	"""Replace pointer hardware only, not page selection or task logic."""
	def __init__(self, driver):
		self.driver = driver
		self.target = None

	def move_to_element(self, target):
		self.target = target

	def human_like_click(self):
		self.driver.click(self.target)


class QuestFixture(unittest.TestCase):
	def setUp(self):
		self.clock = Clock()
		self.stack = contextlib.ExitStack()
		self.addCleanup(self.stack.close)
		self.stack.enter_context(mock.patch.object(rewards_tasks.time, "monotonic", self.clock))
		self.stack.enter_context(mock.patch.object(rewards_tasks.time, "sleep", self.clock.sleep))
		self.stack.enter_context(mock.patch.object(rewards_tasks.mouse_trajectory, "MouseUtils", Mouse))
		self.stack.enter_context(mock.patch.dict(os.environ, {rewards_tasks.SKIP_TASKS_ENV: ""}))
		self.emit = self.stack.enter_context(mock.patch.object(run_record, "emit"))

	def worker(self, **kwargs):
		driver = QuestPage(self.clock, **kwargs)
		worker = rewards_tasks.RewardsTaskUtils(driver)
		self.assertEqual(driver.loads, [rewards_tasks.REWARDS_HOME_URL])
		self.assertEqual(driver.extra_headers["X-Rewards-Source"], "msrewards-desktop")
		return worker

	def run_quests(self, worker, **kwargs):
		with self.assertLogs(rewards_tasks.logger, level=logging.INFO) as output:
			worker.complete_all_tasks(clock=self.clock, sleep=self.clock.sleep, **kwargs)
		return [record.getMessage() for record in output.records]

	def quest_end(self):
		return next(call.kwargs for call in self.emit.call_args_list if call.args == ("task_end",) and call.kwargs["task"] == "quests")


class QuestEntryTests(QuestFixture):
	def test_real_run_account_selects_only_quests_after_state_read_and_constructor(self):
		driver = QuestPage(self.clock, tab_delay=3, quests_delay=4)
		# The reader sees a ready document; the task constructor reloads it later.
		driver.home_at = -100
		reader = rewards_state.read_state
		with contextlib.ExitStack() as stack:
			stack.enter_context(mock.patch.object(main, "_plan", main.RunPlan(read_state=True, only_if_open=True, min_points=15)))
			stack.enter_context(mock.patch.object(main, "_account_started", False))
			stack.enter_context(mock.patch.object(main, "_account_ran", False))
			stack.enter_context(mock.patch.object(main.browser, "start_driver", return_value=driver))
			stack.enter_context(mock.patch.object(main.desktop_utils, "prepare_desktop_before_launch", return_value=True))
			stack.enter_context(mock.patch.object(main.desktop_utils, "switch_back_after_launch"))
			stack.enter_context(mock.patch.object(run_record, "cards_left_open_today", return_value=frozenset()))
			reads = stack.enter_context(mock.patch.object(rewards_state, "read_state", wraps=reader))
			with self.assertLogs(level=logging.INFO):
				self.assertTrue(main.run_account(types.SimpleNamespace(name="synthetic", user_data_dir="synthetic-profile")))
		self.assertEqual(self.quest_end()["outcome"], "ok")
		self.assertEqual([call.kwargs["reason"] for call in self.emit.call_args_list if call.args == ("decision",)], ["quest_unlocked"])
		self.assertEqual([call.kwargs["task"] for call in self.emit.call_args_list if call.args == ("task_start",)], ["quests"])
		self.assertEqual([call.kwargs["phase"] for call in reads.call_args_list], ["before", "after"])
		self.assertEqual(driver.activity_clicks, [("https://rewards.bing.com" + HALLOWEEN, "Start searching")])
		self.assertEqual(driver.quit_calls, 1)

	def test_quest_only_waits_for_earn_tab_after_constructor_reloads_home(self):
		worker = self.worker(tab_delay=3)
		messages = self.run_quests(worker, only={"quests"})
		self.assertEqual(self.quest_end()["outcome"], "ok", messages)
		self.assertEqual(self.quest_end()["progress"], "worked through 4 of 4 quests")
		self.assertGreater(worker.driver.tabs_read, 1)
		self.assertEqual(worker.driver.activity_clicks, [("https://rewards.bing.com" + HALLOWEEN, "Start searching")])
		self.assertEqual([call.kwargs["task"] for call in self.emit.call_args_list if call.args == ("task_start",)], ["quests"])

	def test_quest_only_waits_for_quest_links_after_the_earn_tab_click(self):
		worker = self.worker(quests_delay=4)
		messages = self.run_quests(worker, only={"quests"})
		self.assertEqual(self.quest_end()["outcome"], "ok", messages)
		self.assertEqual(self.quest_end()["progress"], "worked through 4 of 4 quests")
		self.assertGreater(worker.driver.quests_read, 1)
		self.assertIn("4 quest(s) found", messages)

	def test_same_render_delays_work_in_full_loop_after_earlier_task_time(self):
		worker = self.worker(tab_delay=3, quests_delay=4)
		previous = []
		for task, _, method in rewards_tasks.TASKS:
			if task != "quests":
				def step(task_id=task):
					previous.append(task_id)
					self.clock.sleep(1)
				setattr(worker, method, step)
		self.run_quests(worker)
		self.assertEqual(self.quest_end()["outcome"], "ok")
		self.assertEqual(previous, [task for task in rewards_tasks.TASK_IDS if task != "quests"])
		self.assertEqual(worker.driver.activity_clicks, [("https://rewards.bing.com" + HALLOWEEN, "Start searching")])

	def test_ready_quest_page_keeps_the_same_card_loop_and_tab_cleanup(self):
		worker = self.worker()
		messages = self.run_quests(worker, only={"quests"})
		self.assertEqual(self.quest_end()["outcome"], "ok")
		self.assertEqual(worker.driver.window_handles, ["main"])
		self.assertEqual(worker.driver.current_window_handle, "main")
		self.assertEqual(worker.driver.loads, [rewards_tasks.REWARDS_HOME_URL, EARN,
			"https://rewards.bing.com" + SPOTIFY, "https://rewards.bing.com" + HALLOWEEN,
			"https://rewards.bing.com" + HALLOWEEN, "https://rewards.bing.com" + MONTHLY, "https://rewards.bing.com" + WEEKLY])
		self.assertIn("Quest activity done: 'Start searching'", messages)
		self.assertIn("1 activity/activities completed on " + HALLOWEEN.rsplit("/", 1)[-1], messages)
		self.assertNotIn("Redeem now", [label for _, label in worker.driver.activity_clicks])

	def test_missing_quests_still_reports_unavailable_and_never_clicks_an_activity(self):
		worker = self.worker(quests={})
		messages = self.run_quests(worker, only={"quests"})
		self.assertEqual((self.quest_end()["outcome"], self.quest_end()["reason_key"]), ("skip", "not_available"), messages)
		self.assertEqual(worker.driver.activity_clicks, [])
		self.assertLessEqual(self.clock(), 13)

	def test_earn_tab_that_never_appears_is_bounded_and_unavailable(self):
		worker = self.worker(tab_delay=100)
		messages = self.run_quests(worker, only={"quests"})
		self.assertEqual((self.quest_end()["outcome"], self.quest_end()["reason_key"]), ("skip", "not_available"), messages)
		self.assertEqual(worker.driver.activity_clicks, [])
		self.assertEqual(worker.driver.loads, [rewards_tasks.REWARDS_HOME_URL])
		self.assertLessEqual(self.clock(), 13)

	def test_quest_off_in_settings_does_not_add_navigation_or_wait(self):
		worker = self.worker(tab_delay=100)
		with mock.patch.dict(os.environ, {rewards_tasks.SKIP_TASKS_ENV: "quests"}):
			self.run_quests(worker, only={"quests"})
		self.assertEqual(self.quest_end()["outcome"], "off")
		self.assertEqual(worker.driver.tabs_read, 0)
		self.assertEqual(worker.driver.loads, [rewards_tasks.REWARDS_HOME_URL])
		self.assertEqual(self.clock(), 2)


class CountedQuestTests(QuestFixture):
	def read(self, driver, phase="before"):
		return rewards_state.read_state(driver, element_selectors.ElementSelectionUtils(driver), timeout=0, phase=phase)

	def test_spotify_terms_and_redeem_do_not_count_or_start_a_run(self):
		driver = QuestPage(self.clock, quests={SPOTIFY: activities(TERMS + REDEEM)})
		state = self.read(driver)
		self.assertEqual((state.quests_open, state.open_points, state.unreadable), (0, 0, None))
		self.assertEqual(main.decide(state, main.RunPlan(only_if_open=True, min_points=15)), (False, "nothing_open"))
		self.assertEqual(driver.activity_clicks, [])

	def test_visible_enabled_start_searching_counts_without_a_points_requirement(self):
		driver = QuestPage(self.clock, quests={HALLOWEEN: activities(SEARCH)})
		state = self.read(driver)
		self.assertEqual((state.quests_open, state.open_points, state.unreadable), (1, 0, None))
		self.assertEqual(main.decide(state, main.RunPlan(only_if_open=True, min_points=15)), (True, "quest_unlocked"))
		self.assertEqual(driver.activity_clicks, [])

	def test_duplicate_hidden_and_disabled_search_links_do_not_inflate_the_count(self):
		fragment = SEARCH + SEARCH + SEARCH.replace('<a ', '<a hidden ') + SEARCH.replace('<a ', '<a aria-disabled="true" ')
		driver = QuestPage(self.clock, quests={HALLOWEEN: activities(fragment)})
		state = self.read(driver)
		self.assertEqual((state.quests_open, state.open_points, state.unreadable), (1, 0, None))

	def test_four_worked_cards_with_unchanged_halloween_link_still_count_one_after(self):
		driver = QuestPage(self.clock)
		before = self.read(driver)
		worker = rewards_tasks.RewardsTaskUtils(driver)
		messages = self.run_quests(worker, only={"quests"})
		after = self.read(driver, phase="after")
		self.assertEqual((before.quests_open, after.quests_open, after.open_points, after.unreadable), (1, 1, 0, None))
		self.assertEqual(before.balance, after.balance)
		self.assertEqual(self.quest_end()["progress"], "worked through 4 of 4 quests")
		self.assertEqual(driver.activity_clicks, [("https://rewards.bing.com" + HALLOWEEN, "Start searching")])
		self.assertEqual(main.decide(after, main.RunPlan(only_if_open=True, min_points=15)), (True, "quest_unlocked"))
		self.assertIn("4 quest(s) found", messages)
		driver.get("https://rewards.bing.com" + HALLOWEEN)
		self.assertEqual([link.text.strip() for link in worker.elements.get_quest_activity_links()], ["Start searching"])

	def test_unchanged_step_is_clicked_once_again_on_the_next_invocation(self):
		worker = self.worker()
		for _ in range(2):
			self.run_quests(worker, only={"quests"})
		self.assertEqual(worker.driver.activity_clicks, [("https://rewards.bing.com" + HALLOWEEN, "Start searching")] * 2)
		self.assertEqual(self.read(worker.driver, phase="after").quests_open, 1)

	def test_consumed_search_link_no_longer_counts_on_the_after_read(self):
		driver = QuestPage(self.clock)
		self.assertEqual(self.read(driver).quests_open, 1)
		driver.quests[HALLOWEEN] = []
		state = self.read(driver, phase="after")
		self.assertEqual((state.quests_open, state.open_points), (0, 0))
		self.assertEqual(main.decide(state, main.RunPlan(only_if_open=True, min_points=15)), (False, "nothing_open"))


if __name__ == "__main__":
	unittest.main()
