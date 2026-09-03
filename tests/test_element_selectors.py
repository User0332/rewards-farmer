"""Tests for the parts of element_selectors that parse or choose.

Every case here is a bug that reached a real run: a point value the parser could
not read, the wrong row of the breakdown panel, a container that looks right but
is empty, and a label that matches two different buttons. They need no browser,
so they run anywhere.

	python -m unittest discover -s tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By

import element_selectors
from fakes import FakeDriver, FakeElement

STATUS_SELECTOR = "div.flex.w-full.items-center.gap-2"


def card(status_text=None, points_text=None):
	"""A misc card, optionally with a status block and a point value in it."""
	if status_text is None and points_text is None:
		return FakeElement()

	status_children = {}

	if points_text is not None:
		status_children[(By.TAG_NAME, "p")] = [FakeElement(text=points_text)]

	status = FakeElement(text=status_text or "", children=status_children)

	return FakeElement(children={(By.CSS_SELECTOR, STATUS_SELECTOR): [status]})


def sidebar_driver(panel_text):
	"""A driver whose only section is the react-aria sidebar panel."""
	panel = FakeElement(text=panel_text, attributes={"id": "react-aria-42"})

	return FakeDriver(children={(By.TAG_NAME, "section"): [panel]})


def selectors_for(driver):
	return element_selectors.ElementSelectionUtils(driver)


class CardPointValue(unittest.TestCase):
	def test_reads_the_rendered_plus_prefix(self):
		# The page renders "+10". int() happens to accept that, which hid the
		# fragility until a variant added a unit.
		self.assertEqual(selectors_for(FakeDriver()).get_card_point_value(card(points_text="+10")), 10)

	def test_reads_a_value_with_a_unit(self):
		self.assertEqual(selectors_for(FakeDriver()).get_card_point_value(card(points_text="10 points")), 10)

	def test_is_zero_when_the_card_has_no_point_value(self):
		# Promo cards carry a status block without a value.
		self.assertEqual(selectors_for(FakeDriver()).get_card_point_value(card(status_text="")), 0)

	def test_is_zero_when_the_card_has_no_status_block(self):
		self.assertEqual(selectors_for(FakeDriver()).get_card_point_value(card()), 0)


class CardCompletion(unittest.TestCase):
	def test_completed_card(self):
		self.assertTrue(selectors_for(FakeDriver()).card_is_complete(card(status_text="Completed")))

	def test_open_card_showing_its_reward(self):
		self.assertFalse(selectors_for(FakeDriver()).card_is_complete(card(status_text="+10")))

	def test_card_without_a_status_block_is_not_complete(self):
		self.assertFalse(selectors_for(FakeDriver()).card_is_complete(card()))


class SearchPointsRow(unittest.TestCase):
	PANEL = "\n".join([
		"Points breakdown",
		"Today's points",
		"41",
		"Bing search",
		"6/15",
		"Offers",
		"20",
		"This month",
		"3,037",
		"Lifetime",
		"12,742",
	])

	def test_reads_the_bing_search_row(self):
		earned, maximum = selectors_for(
			sidebar_driver(self.PANEL)
		).get_points_earned_from_searches_on_points_breakdown()

		self.assertEqual((earned, maximum), (6, 15))

	def test_ignores_rows_that_are_not_the_search_row(self):
		# Several rows share the same value class in the real panel, so a
		# position based read returns whichever row happens to come first.
		reordered = "\n".join([
			"Points breakdown",
			"This month",
			"3,037",
			"Bing search",
			"6/15",
		])

		earned, maximum = selectors_for(
			sidebar_driver(reordered)
		).get_points_earned_from_searches_on_points_breakdown()

		self.assertEqual((earned, maximum), (6, 15))

	def test_handles_a_thousands_separator_in_the_fraction(self):
		panel = "Bing search\n1,020/1,500"

		earned, maximum = selectors_for(
			sidebar_driver(panel)
		).get_points_earned_from_searches_on_points_breakdown()

		self.assertEqual((earned, maximum), (1020, 1500))

	def test_raises_when_the_panel_has_no_fraction(self):
		with self.assertRaises(NoSuchElementException):
			selectors_for(
				sidebar_driver("Points breakdown\nLoading...")
			).get_points_earned_from_searches_on_points_breakdown()


class DuplicatedContainer(unittest.TestCase):
	"""Some sections are emitted twice for responsive layout."""

	def _driver(self, visible_links, hidden_links):
		def container(displayed, count):
			links = [FakeElement(text=f"card {i}") for i in range(count)]

			return FakeElement(
				displayed=displayed,
				children={(By.TAG_NAME, "a"): links},
			)

		return FakeDriver(children={
			(By.ID, "moreactivities"): [
				container(True, visible_links),
				container(False, hidden_links),
			]
		})

	def test_refuses_the_hidden_copy_even_though_it_has_the_links(self):
		# The visible copy is the empty one here. Handing back the hidden copy
		# would produce links that cannot be clicked and whose text is empty, so
		# this has to fail loudly rather than return them.
		with self.assertRaises(NoSuchElementException):
			selectors_for(self._driver(visible_links=0, hidden_links=7)).get_all_misc_cards()

	def test_uses_the_visible_copy_when_it_has_the_content(self):
		cards = selectors_for(self._driver(visible_links=7, hidden_links=0)).get_all_misc_cards()

		self.assertEqual(len(cards), 7)

	def test_a_container_that_is_there_but_empty_is_not_reported_as_missing(self):
		# Both failures used to raise NoSuchElementException, so a section that
		# was on the page and still rendering got reported as one this market
		# does not ship. Waiting is the answer to this one.
		with self.assertRaises(element_selectors.ElementNotReady):
			selectors_for(self._driver(visible_links=0, hidden_links=7)).get_all_misc_cards()

	def test_a_container_that_is_absent_is_reported_as_missing(self):
		with self.assertRaises(NoSuchElementException) as caught:
			selectors_for(FakeDriver()).get_all_misc_cards()

		self.assertNotIsInstance(caught.exception, element_selectors.ElementNotReady)


class DailySetOpener(unittest.TestCase):
	"""The opener label has to be distinguished from the level up entry."""

	def _driver(self, labels):
		buttons = [FakeElement(text=text) for text in labels]

		return FakeDriver(children={(By.TAG_NAME, "button"): buttons})

	def test_matches_the_streak_button(self):
		driver = self._driver([
			"Complete the Daily Set for 7 days in a row",
			"Daily Set Streak\nDay 2 of 7 streak completed.",
		])

		button = selectors_for(driver).get_open_daily_set_button()

		self.assertIn("Daily Set Streak", button.text)

	def test_does_not_match_the_level_up_entry_alone(self):
		driver = self._driver(["Complete the Daily Set for 7 days in a row"])

		# No streak button and no streaks section to fall back to.
		with self.assertRaises(NoSuchElementException):
			selectors_for(driver).get_open_daily_set_button()


class DailySetActivityUrls(unittest.TestCase):
	"""Which hrefs in the panel count as activities and which are promos."""

	def _links(self, *hrefs):
		links = [FakeElement(text="activity", attributes={"href": h}) for h in hrefs]
		panel = FakeElement(
			attributes={"id": "react-aria-42"},
			children={(By.TAG_NAME, "a"): links},
		)

		return FakeDriver(children={(By.TAG_NAME, "section"): [panel]})

	def test_matches_a_plain_search_activity(self):
		found = selectors_for(
			self._links("https://www.bing.com/search?q=weather")
		).get_daily_set_elements()

		self.assertEqual(len(found), 1)

	def test_matches_a_rewards_path_activity(self):
		# "Turn referrals into rewards" awards points and is not a search.
		found = selectors_for(
			self._links("https://www.bing.com/rewards/panelflyout")
		).get_daily_set_elements()

		self.assertEqual(len(found), 1)

	def test_matches_an_activity_on_the_rewards_host(self):
		found = selectors_for(
			self._links("https://rewards.bing.com/redeem/12345")
		).get_daily_set_elements()

		self.assertEqual(len(found), 1)

	def test_skips_the_bing_app_promo(self):
		# The link behind #45. Clicking it leaves the rewards host and every
		# element captured beforehand goes stale.
		found = selectors_for(
			self._links("https://bingapp.microsoft.com/bing?adjust=14u4j3kz")
		).get_daily_set_elements()

		self.assertEqual(found, [])

	def test_keeps_activities_and_drops_promos_from_the_same_panel(self):
		found = selectors_for(self._links(
			"https://bingapp.microsoft.com/bing?adjust=14u4j3kz",
			"https://www.bing.com/search?q=news",
			"https://www.bing.com/rewards/panelflyout",
			"https://play.google.com/store/apps/details?id=com.microsoft.bing",
		)).get_daily_set_elements()

		self.assertEqual(len(found), 2)


if __name__ == "__main__":
	unittest.main()
