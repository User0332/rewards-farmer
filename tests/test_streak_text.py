"""Streak identity comes from rendered text, not CSS or button position."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException
from element_selectors import ElementSelectionUtils, ElementNotReady, VisualSearchUnavailable, UnexpectedVisualSearchPanel
from fakes import FakeDriver, FakeElement


def selectors(buttons):
	row = FakeElement(children={(By.TAG_NAME, "button"): buttons})
	return ElementSelectionUtils(FakeDriver(children={(By.ID, "streaks"): [row]}))


class StreakText(unittest.TestCase):
	def test_class_free_and_restyled_tiles_use_exact_normalized_title_lines(self):
		for classes in ("", "new-title-design new-counter-design"):
			with self.subTest(classes=classes):
				tile = FakeElement(text="  vISual  SEARCH\tStreak  \nDay 7 of 7\nActivity: 1/1", attributes={"class": classes},
					children={(By.TAG_NAME, "span"): [FakeElement(text="Visual Search Streak"), FakeElement(text="Activity: 1/1")]})
				reader = selectors([FakeElement(text="Bing App Streak\nCheck-in: 0/1"), tile])
				self.assertIs(reader.get_open_visual_search_sidebar(), tile)
				self.assertTrue(reader.visual_search_tile_done_today())

	def test_daily_set_uses_same_row_identity_not_global_substring_or_third_button(self):
		tile = FakeElement(text="Daily Set Streak\nActivity: 0/3")
		reader = selectors([tile, FakeElement(text="Bing App Streak"), FakeElement(text="Other")])
		reader.driver.children[(By.TAG_NAME, "button")] = [FakeElement(text="Daily Set Streak bonus")]
		self.assertIs(reader.get_open_daily_set_button(), tile)
		reader = selectors([FakeElement(text="Bing App Streak")])
		reader.driver.children[(By.TAG_NAME, "button")] = [tile]
		with self.assertRaises(NoSuchElementException):
			reader.get_open_daily_set_button()

	def test_empty_button_text_is_retryable_but_rendered_absence_lists_titles(self):
		with self.assertRaises(ElementNotReady):
			selectors([FakeElement(text=" \n ")]).get_open_visual_search_sidebar()
		with self.assertRaises(VisualSearchUnavailable) as caught:
			selectors([FakeElement(text="1,000 BONUS"), FakeElement(text="Day 1\nBing App Streak\nCheck-in: 0/1"), FakeElement()]).get_open_visual_search_sidebar()
		self.assertIn("1,000 BONUS, Bing App Streak", caught.exception.msg)
		self.assertNotIn("Check-in", caught.exception.msg)

	def test_shared_counter_reads_only_labelled_fraction_lines(self):
		for text, expected in (("Day 7 of 7\nActivity: 1/1", (1, 1)), ("Minutes: 12/30", (12, 30)),
			("Day 7 of 7\n1/1", None), ("Activity: 0/0", None), ("Activity: 1/1\nActivity: 0/1", None)):
			with self.subTest(text=text):
				self.assertEqual(ElementSelectionUtils.streak_tile_counter(FakeElement(text=text)), expected)

	def test_panel_without_heading_uses_its_streak_title_line(self):
		panel = FakeElement(text="Bing App Streak\nCheck-in now", attributes={"id": "react-aria-panel"},
			children={(By.TAG_NAME, "p"): [FakeElement(text="Bing App Streak")]})
		reader = ElementSelectionUtils(FakeDriver(children={(By.TAG_NAME, "section"): [panel]}))
		with self.assertRaises(UnexpectedVisualSearchPanel):
			reader.get_visual_search_sidebar()


if __name__ == "__main__":
	unittest.main()
