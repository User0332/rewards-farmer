"""Page failures must not read as a finished day or start a run by accident."""

from dataclasses import FrozenInstanceError
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By

from element_selectors import ElementSelectionUtils, ElementNotReady
from fakes import FakeDriver, FakeElement
import rewards_state
from rewards_state import RewardsState, read_balance, read_state, worth_running


def misc_card(points=10, done=False, description="Read this card"):
	status = FakeElement(
		text="Completed" if done else f"+{points}",
		children={(By.TAG_NAME, "p"): [FakeElement(text=f"+{points}")]},
	)
	return FakeElement(text=f"{description}\n{status.text}", children={
		(By.CSS_SELECTOR, "div.flex.w-full.items-center.gap-2"): [status],
		(By.CSS_SELECTOR, "p:nth-child(2)"): [FakeElement(text=description)],
	})


def link(href, text="Activity", disabled=False, displayed=True):
	return FakeElement(text=text, displayed=displayed, attributes={
		"href": href, "aria-disabled": "true" if disabled else "false",
	})


class StateDriver(FakeDriver):
	"""A page-changing fake that exercises the real selectors and their filters."""

	def __init__(self, *, balance="12,345", searches="60/60", daily_done=3,
			cards=None, quests=None, blank_loads=(), redirect=None, daily_text=None,
			close_label="Close", blank_quests=(), partial_quests=False, daily_links=3):
		super().__init__()
		self.balance = balance
		self.searches = searches
		self.daily_done = daily_done
		self.cards = [misc_card(done=True)] if cards is None else cards
		self.quests = quests or {}
		self.blank_loads = set(blank_loads)
		self.blank_quests = set(blank_quests)
		self.redirect = redirect
		self.daily_text = daily_text
		self.daily_links = daily_links
		self.partial_quests = partial_quests
		self.close_label = close_label
		self.current_url = "about:blank"
		self.loads = []
		self.earn_loads = 0
		self.scripts = []
		self.clicks = []
		self.quit_calls = 0
		self.breakdown = FakeElement(text="Points breakdown")
		self.daily_opener = FakeElement(text="Daily Set Streak")
		self.close = FakeElement(text="", attributes={"aria-label": close_label})
		self.panel = None

	def get(self, url):
		self.loads.append(url)
		self.current_url = self.redirect or url
		self.panel = None
		if url == rewards_state.EARN_URL:
			self.earn_loads += 1
			if self.earn_loads in self.blank_loads or self.redirect:
				self.children = {}
				return

			buttons = [self.breakdown, self.daily_opener]
			header = FakeElement(children={(By.TAG_NAME, "button"): [FakeElement(
				text=self.balance, attributes={"aria-label": "View profile"},
			)]})
			self.children = {
				(By.TAG_NAME, "button"): buttons,
				(By.TAG_NAME, "header"): [header],
				(By.TAG_NAME, "body"): [FakeElement(text="Earn rewards " * 50)],
				(By.ID, "moreactivities"): [FakeElement(children={(By.TAG_NAME, "a"): self.cards})],
			}
			if self.quests or self.partial_quests:
				self.children[(By.ID, "quests")] = [FakeElement(children={(By.TAG_NAME, "a"): [
					link(quest_url, "Weekly quest") for quest_url in self.quests
				]})]
		else:
			main = FakeElement(
				text="Quest activity progress" if url not in self.blank_quests else "",
				children={(By.TAG_NAME, "a"): self.quests.get(url.replace("https://rewards.bing.com", ""), [])},
			)
			self.children = {
				(By.TAG_NAME, "body"): [FakeElement(text="Rewards page footer " * 50)],
				(By.TAG_NAME, "main"): [main],
				(By.TAG_NAME, "a"): [link("https://www.bing.com/?rwgbopen=1", "Footer link")],
			}

	def execute_script(self, script, *args):
		self.scripts.append(script)
		if "click()" not in script:
			return

		button = args[0]
		self.clicks.append(button)
		if button is self.breakdown:
			self._open_panel("Points breakdown\nToday's points\n2,345\nBing search\n" + self.searches)
		elif button is self.daily_opener:
			text = self.daily_text
			if text is None:
				text = f"Daily Set Streak\nActivity: {self.daily_done}/3\nDay 2 of 7 streak completed.\n"
				text += "\n".join(["Completed"] * self.daily_done)
			activities = [link(f"https://www.bing.com/search?q=item{index}") for index in range(self.daily_links)]
			self._open_panel(text, activities)
		elif button is self.close:
			self.children[(By.TAG_NAME, "section")] = []
		else:
			raise AssertionError("the state reader clicked an activity")

	def _open_panel(self, text, activities=()):
		self.panel = FakeElement(text=text, attributes={"id": "react-aria-panel"}, children={
			(By.TAG_NAME, "a"): list(activities),
			(By.TAG_NAME, "button"): [self.close],
			(By.CSS_SELECTOR, "button[aria-label*='lose']"): [self.close],
		})
		self.children[(By.TAG_NAME, "section")] = [self.panel]

	def quit(self):
		self.quit_calls += 1


class StateReads(unittest.TestCase):
	def setUp(self):
		pause = mock.patch.object(rewards_state.time, "sleep", lambda _: None)
		pause.start()
		self.addCleanup(pause.stop)

	def read(self, driver, **kwargs):
		return read_state(driver, ElementSelectionUtils(driver), timeout=0, **kwargs)

	def test_fully_done_day(self):
		driver = StateDriver()
		state = self.read(driver)
		self.assertEqual(state, RewardsState(
			balance=12345, searches_earned=60, searches_max=60, earned_today=2345,
		))
		self.assertFalse(worth_running(state, 15))
		self.assertEqual(driver.earn_loads, 4)
		self.assertEqual(driver.quit_calls, 0)

	def test_snapshot_is_frozen(self):
		with self.assertRaises(FrozenInstanceError):
			self.read(StateDriver()).balance = 0

	def test_open_searches_use_the_current_market_maximum(self):
		state = self.read(StateDriver(searches="6/15"))
		self.assertEqual((state.searches_earned, state.searches_max, state.open_points), (6, 15, 9))
		self.assertFalse(worth_running(state, 15))

	def test_search_overshoot_cannot_subtract_from_open_card_points(self):
		state = self.read(StateDriver(searches="63/60", cards=[misc_card(points=20)]))
		self.assertEqual(state.open_points, 20)

	def test_open_daily_set_uses_only_standalone_completed_labels(self):
		state = self.read(StateDriver(daily_done=1))
		self.assertEqual((state.daily_set_open, state.open_points, state.unreadable), (2, 20, None))

	def test_open_cards_use_displayed_values_and_exclude_completed_and_promos(self):
		state = self.read(StateDriver(cards=[
			misc_card(points=20), misc_card(points=15), misc_card(points=10, done=True), misc_card(points=0),
		]))
		self.assertEqual((state.cards_open, state.open_points, state.unreadable), (2, 35, None))

	def test_open_quests_count_only_visible_unlocked_activities_inside_main(self):
		state = self.read(StateDriver(quests={"/earn/quest/weekly": [
			link("https://www.bing.com/search?q=flights", "Flights"),
			link("https://www.bing.com/?form=ML2X", "Home"),
			link("https://www.bing.com/search?q=tomorrow", "Tomorrow", disabled=True),
			link("https://www.bing.com/search?q=hidden", "Hidden copy", displayed=False),
			link("https://rewards.bing.com/earn", "Back"),
		]}))
		self.assertEqual((state.quests_open, state.open_points, state.unreadable), (2, 0, None))
		self.assertTrue(worth_running(state, 15))

	def test_locked_quest_does_not_start_a_run(self):
		state = self.read(StateDriver(quests={"/earn/quest/weekly": [
			link("https://www.bing.com/search?q=tomorrow", disabled=True),
		]}))
		self.assertEqual(state.quests_open, 0)
		self.assertFalse(worth_running(state, 15))

	def test_no_quest_section_is_a_readable_zero(self):
		state = self.read(StateDriver())
		self.assertEqual(state.quests_open, 0)
		self.assertIsNone(state.unreadable)

	def test_empty_quest_section_is_unreadable_not_absent(self):
		state = self.read(StateDriver(partial_quests=True))
		self.assertEqual(state.unreadable, "quests")
		self.assertIsNone(worth_running(state, 15))

	def test_quest_with_only_a_rendered_footer_is_unreadable(self):
		driver = StateDriver(quests={"/earn/quest/weekly": []}, blank_quests={"https://rewards.bing.com/earn/quest/weekly"})
		state = self.read(driver)
		self.assertEqual(state.unreadable, "quests")
		self.assertIsNone(worth_running(state, 15))

	def test_second_quest_is_read_after_the_first_one_fails(self):
		driver = StateDriver(quests={
			"/earn/quest/blank": [],
			"/earn/quest/ready": [link("https://www.bing.com/search?q=ready")],
		}, blank_quests={"https://rewards.bing.com/earn/quest/blank"})
		state = self.read(driver)
		self.assertEqual((state.quests_open, state.unreadable), (1, "quests"))
		self.assertTrue(worth_running(state, 15))

	def test_first_blank_load_is_retried(self):
		driver = StateDriver(blank_loads={1})
		state = self.read(driver)
		self.assertIsNone(state.unreadable)
		self.assertEqual(driver.earn_loads, 5)

	def test_page_stays_blank_after_two_loads(self):
		driver = StateDriver(blank_loads={1, 2})
		state = self.read(driver)
		self.assertEqual(state.unreadable, "page_blank")
		self.assertEqual(driver.earn_loads, 2)
		self.assertIsNone(state.balance)
		self.assertIsNone(state.searches_max)
		self.assertIsNone(worth_running(state, 15))
		self.assertEqual(driver.clicks, [])

	def test_blank_later_sections_do_not_erase_known_searches(self):
		driver = StateDriver(searches="0/60", blank_loads=set(range(2, 8)))
		state = self.read(driver)
		self.assertEqual((state.searches_earned, state.searches_max, state.open_points), (0, 60, 60))
		self.assertEqual(state.unreadable, "page_blank")
		self.assertIsNone(worth_running(state, 15))
		self.assertEqual(driver.earn_loads, 7)

	def test_terms_redirect_is_reported_without_accepting_it(self):
		driver = StateDriver(redirect="https://rewards.bing.com/terms-of-use?token=private")
		state = self.read(driver)
		self.assertEqual(state.unreadable, "terms_of_use")
		self.assertEqual(driver.earn_loads, 2)
		self.assertEqual(driver.clicks, [])
		self.assertNotIn("private", repr(state))

	def test_transient_terms_redirect_recovers_on_the_second_navigation(self):
		class TermsOnceDriver(StateDriver):
			def get(self, url):
				super().get(url)
				self.redirect = None

		driver = TermsOnceDriver(redirect="https://rewards.bing.com/terms-of-use")
		self.assertIsNone(self.read(driver).unreadable)
		self.assertEqual(driver.earn_loads, 5)
		self.assertEqual(driver.clicks, [driver.breakdown, driver.close, driver.daily_opener, driver.close])

	def test_signed_out_redirect_is_reported(self):
		driver = StateDriver(redirect="https://login.live.com/login?token=private")
		self.assertEqual(self.read(driver).unreadable, "signed_out")
		self.assertEqual(driver.clicks, [])

	def test_page_load_timeout_can_still_leave_a_readable_dom(self):
		class TimedOutDriver(StateDriver):
			def get(self, url):
				super().get(url)
				raise TimeoutException("resource still loading")

		self.assertIsNone(self.read(TimedOutDriver()).unreadable)

	def test_failed_navigation_cannot_reuse_a_previous_document(self):
		class BrokenDriver(StateDriver):
			def get(self, url):
				self.loads.append(url)
				raise WebDriverException("navigation failed")

		driver = BrokenDriver()
		StateDriver.get(driver, rewards_state.EARN_URL)
		state = self.read(driver)
		self.assertEqual(state.unreadable, "page_blank")
		self.assertIsNone(state.balance)
		self.assertEqual(driver.clicks, [])

	def test_balance_cannot_be_read_but_other_observations_survive(self):
		state = self.read(StateDriver(balance="Loading...", searches="0/60"))
		self.assertIsNone(state.balance)
		self.assertEqual((state.open_points, state.unreadable), (60, "balance"))
		self.assertTrue(worth_running(state, 15))

	def test_a_missing_search_row_does_not_borrow_another_fraction(self):
		class OtherFractionDriver(StateDriver):
			def _open_panel(self, text, activities=()):
				if text.startswith("Points breakdown"):
					text = "Points breakdown\nOffers\n5/50\nBing search\nLoading..."
				super()._open_panel(text, activities)

		state = self.read(OtherFractionDriver(cards=[misc_card(points=20)]))
		self.assertEqual(state.unreadable, "searches")
		self.assertIsNone(state.searches_earned)
		self.assertIsNone(state.searches_max)
		self.assertEqual((state.cards_open, state.open_points), (1, 20))

	def test_partial_daily_set_is_not_reported_as_three_open_items(self):
		state = self.read(StateDriver(daily_text="Daily Set Streak\nActivity: 3/3\nLoading..."))
		self.assertEqual((state.daily_set_open, state.unreadable), (0, "daily_set"))
		self.assertIsNone(worth_running(state, 15))

	def test_partial_daily_links_are_unreadable_even_with_a_progress_row(self):
		state = self.read(StateDriver(daily_done=1, daily_links=1))
		self.assertEqual(state.unreadable, "daily_set")

	def test_absent_today_total_is_informational_not_a_search_failure(self):
		class NoTodayDriver(StateDriver):
			def _open_panel(self, text, activities=()):
				super()._open_panel(text.replace("Today's points\n2,345\n", ""), activities)

		state = self.read(NoTodayDriver(searches="0/60"))
		self.assertIsNone(state.earned_today)
		self.assertIsNone(state.unreadable)
		self.assertTrue(worth_running(state, 15))

	def test_one_bad_card_keeps_the_other_card_observations(self):
		class StaleCard(FakeElement):
			def find_element(self, by, selector):
				raise RuntimeError("card went stale")

		state = self.read(StateDriver(cards=[StaleCard(), misc_card(points=20)]))
		self.assertEqual((state.cards_open, state.open_points, state.unreadable), (1, 20, "cards"))

	def test_a_present_card_without_content_is_unreadable(self):
		state = self.read(StateDriver(cards=[FakeElement(), misc_card(points=20)]))
		self.assertEqual((state.cards_open, state.open_points, state.unreadable), (1, 20, "cards"))

	def test_unreadable_sections_have_a_fixed_order(self):
		state = self.read(StateDriver(balance="", searches="Loading...", daily_text="Loading...", cards=[], partial_quests=True))
		self.assertEqual(state.unreadable, "searches,daily_set,cards,quests,balance")
		self.assertIsNone(worth_running(state, 15))

	def test_only_informational_controls_are_clicked(self):
		driver = StateDriver(daily_done=0, cards=[misc_card()], quests={
			"/earn/quest/weekly": [link("https://www.bing.com/search?q=do-not-follow")],
		})
		self.read(driver)
		self.assertEqual(driver.clicks, [driver.breakdown, driver.close, driver.daily_opener, driver.close])
		self.assertFalse(any("bing.com/search" in url for url in driver.loads))

	def test_close_button_fallback_never_clicks_an_unknown_action(self):
		driver = StateDriver(close_label="Claim")
		self.assertIsNone(self.read(driver).unreadable)
		self.assertEqual(driver.clicks, [driver.breakdown, driver.daily_opener])

	def test_keyboard_interrupt_is_not_hidden_as_a_page_failure(self):
		class CancelledDriver(StateDriver):
			def get(self, url):
				raise KeyboardInterrupt()

		with self.assertRaises(KeyboardInterrupt):
			self.read(CancelledDriver())


class ThresholdDecision(unittest.TestCase):
	def test_at_the_threshold_is_worth_running(self):
		self.assertTrue(worth_running(RewardsState(open_points=15), 15))

	def test_below_the_threshold_is_not_worth_running(self):
		self.assertFalse(worth_running(RewardsState(open_points=14), 15))

	def test_zero_threshold_accepts_a_readable_done_day(self):
		self.assertTrue(worth_running(RewardsState(), 0))

	def test_an_unlocked_quest_overrides_the_point_threshold(self):
		self.assertTrue(worth_running(RewardsState(quests_open=1), 100))

	def test_whole_page_failure_overrides_even_known_points_or_a_quest(self):
		for reason in ("page_blank", "signed_out", "terms_of_use"):
			with self.subTest(reason=reason):
				self.assertIsNone(worth_running(RewardsState(open_points=60, quests_open=1, unreadable=reason), 15))

	def test_partial_quests_with_twenty_known_points_is_worth_running(self):
		self.assertTrue(worth_running(RewardsState(open_points=20, unreadable="quests"), 15))

	def test_partial_quests_below_threshold_stays_unknown(self):
		self.assertIsNone(worth_running(RewardsState(open_points=10, unreadable="quests"), 15))

	def test_partial_searches_with_thirty_known_points_is_worth_running(self):
		self.assertTrue(worth_running(RewardsState(open_points=30, unreadable="searches"), 15))

	def test_partial_with_a_known_unlocked_quest_is_worth_running(self):
		self.assertTrue(worth_running(RewardsState(quests_open=1, unreadable="searches"), 15))

	def test_combined_partial_sections_preserve_known_work(self):
		self.assertTrue(worth_running(RewardsState(open_points=20, unreadable="quests,cards"), 15))

	def test_partial_at_threshold_runs_but_zero_open_work_stays_unknown(self):
		self.assertTrue(worth_running(RewardsState(open_points=15, unreadable="quests"), 15))
		self.assertIsNone(worth_running(RewardsState(open_points=0, unreadable="quests,cards"), 15))

	def test_whole_page_code_in_combined_unreadable_stays_unknown(self):
		for code in ("signed_out", "terms_of_use", "page_blank"):
			with self.subTest(code=code):
				self.assertIsNone(worth_running(RewardsState(open_points=30, quests_open=1, unreadable="quests," + code), 15))

	def test_negative_threshold_is_rejected(self):
		with self.assertRaises(ValueError):
			worth_running(RewardsState(), -1)


class ReadCurrentBalance(unittest.TestCase):
	def test_does_not_navigate_wait_or_quit_the_driver(self):
		driver = StateDriver()
		driver.get(rewards_state.EARN_URL)
		with mock.patch.object(rewards_state.time, "sleep", side_effect=AssertionError("must not wait")):
			self.assertEqual(read_balance(driver, ElementSelectionUtils(driver)), 12345)
		self.assertEqual(driver.loads, [rewards_state.EARN_URL])
		self.assertEqual((driver.clicks, driver.quit_calls), ([], 0))

	def test_zero_is_a_valid_balance(self):
		driver = StateDriver(balance="0")
		driver.get(rewards_state.EARN_URL)
		self.assertEqual(read_balance(driver, ElementSelectionUtils(driver)), 0)

	def test_absent_balance_is_none(self):
		driver = StateDriver()
		self.assertIsNone(read_balance(driver, ElementSelectionUtils(driver)))

	def test_ambiguous_hidden_or_bad_balance_is_unknown(self):
		for text in ("-1", "Loading...", "1/60", "", "1,23"):
			with self.subTest(text=text):
				driver = StateDriver(balance=text)
				driver.get(rewards_state.EARN_URL)
				self.assertIsNone(read_balance(driver, ElementSelectionUtils(driver)))
		driver = StateDriver()
		driver.get(rewards_state.EARN_URL)
		header = driver.find_element(By.TAG_NAME, "header")
		header.children[(By.TAG_NAME, "button")].append(FakeElement(text="9,999", attributes={"aria-label": "View profile"}))
		self.assertIsNone(read_balance(driver, ElementSelectionUtils(driver)))
		header.children[(By.TAG_NAME, "button")][-1].displayed = False
		self.assertEqual(read_balance(driver, ElementSelectionUtils(driver)), 12345)


class WaitForRendering(unittest.TestCase):
	def test_retries_until_a_getter_stops_raising(self):
		getter = mock.Mock(side_effect=[ElementNotReady("loading"), 0])
		with mock.patch.object(rewards_state.time, "monotonic", side_effect=[0, 1, 2]), \
				mock.patch.object(rewards_state.time, "sleep") as sleep:
			self.assertEqual(rewards_state._wait_for(getter, 30), 0)
		self.assertEqual(getter.call_count, 2)
		sleep.assert_called_once_with(2)

	def test_final_getter_failure_is_preserved(self):
		with self.assertRaises(ElementNotReady):
			rewards_state._wait_for(mock.Mock(side_effect=ElementNotReady("loading")), 0)


if __name__ == "__main__":
	unittest.main()
