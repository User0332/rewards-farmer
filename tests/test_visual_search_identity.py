"""Visual search belongs to its tile and panel, not their positions (#138)."""
import logging
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait as RealWait

import element_selectors
import rewards_tasks
from fakes import FakeDriver, FakeElement

TITLE = "p.text-globalBody2Strong"
METADATA = "div.line-clamp-2.text-metadata"
HEADINGS = 'h1, h2, h3, h4, h5, h6, [role="heading"], p.text-globalBody2Strong'
DENNIS = ("Bing Search Streak", "Daily Set Streak", "Bing App Streak", "Visual Search Streak")
SLIMDEBUG = ("Bing Search Streak", "Daily Set Streak", "Edge Browsing Streak", "Bing App Streak")


def tile(title, metadata="Activity: 0/1", *, displayed=True):
	return FakeElement(text=title + "\nDay 0 of 7 streak completed.\n" + metadata, displayed=displayed, children={
		(By.CSS_SELECTOR, TITLE): [FakeElement(text=title)],
		(By.CSS_SELECTOR, METADATA): [FakeElement(text=metadata)],
	})


def panel(title="Visual Search Streak", links=(), controls=(), displayed=True):
	return FakeElement(attributes={"id": "react-aria-synthetic"}, displayed=displayed, children={
		(By.CSS_SELECTOR, HEADINGS): [FakeElement(text=title)] if title is not None else [],
		(By.TAG_NAME, "a"): list(links),
		(By.CSS_SELECTOR, '[role="link"]'): list(controls),
	})


def driver_for(titles=DENNIS, *, metadata="Activity: 0/1", sidebar=None):
	tiles = [FakeElement(text="1,000 BONUS")] + [tile(title, metadata if title.strip().casefold() == "visual search streak" else "Search: 1/1") for title in titles]
	section = FakeElement(children={(By.TAG_NAME, "button"): tiles})
	driver = FakeDriver(children={(By.ID, "streaks"): [section], (By.TAG_NAME, "section"): [sidebar] if sidebar else []})
	driver.tiles, driver.clicked = tiles, []
	driver.current_url = "https://rewards.bing.com/earn"
	return driver


class TileIdentity(unittest.TestCase):
	def test_dennis_layout_finds_actual_title_not_position(self):
		driver = driver_for()
		self.assertIs(element_selectors.ElementSelectionUtils(driver).get_open_visual_search_sidebar(), driver.tiles[4])
		# Moving it before all other tiles keeps identity, not the index.
		driver.children[(By.ID, "streaks")][0].children[(By.TAG_NAME, "button")] = list(reversed(driver.tiles))
		self.assertIs(element_selectors.ElementSelectionUtils(driver).get_open_visual_search_sidebar(), driver.tiles[4])

	def test_slimdebug_layout_reports_titles_and_never_returns_bing_app(self):
		driver = driver_for(SLIMDEBUG)
		with self.assertRaises(NoSuchElementException) as caught:
			element_selectors.ElementSelectionUtils(driver).get_open_visual_search_sidebar()
		for title in SLIMDEBUG:
			self.assertIn(title, caught.exception.msg)
		self.assertNotIn("Day 0", caught.exception.msg)
		self.assertNotIn("Search: 1/1", caught.exception.msg)

	def test_exact_normalized_title_inside_streaks_only(self):
		driver = driver_for(("  vIsUaL\n SEARCH\tStReAk  ",))
		self.assertIs(element_selectors.ElementSelectionUtils(driver).get_open_visual_search_sidebar(), driver.tiles[1])
		for title in ("Not Visual Search Streak", "Visual Search Streak bonus", "Visual Search"):
			with self.subTest(title=title):
				driver = driver_for((title,))
				driver.children[(By.TAG_NAME, "button")] = [tile("Visual Search Streak")]
				with self.assertRaises(element_selectors.VisualSearchUnavailable):
					element_selectors.ElementSelectionUtils(driver).get_open_visual_search_sidebar()

	def test_missing_empty_hidden_or_partly_untitled_row_is_not_ready_not_absent(self):
		for sections in ([], [FakeElement()], [FakeElement(displayed=False)],
			[FakeElement(children={(By.TAG_NAME, "button"): [tile("Bing App Streak"), tile("")]})]):
			with self.subTest(sections=sections), self.assertRaises(element_selectors.ElementNotReady):
				element_selectors.ElementSelectionUtils(FakeDriver(children={(By.ID, "streaks"): sections})).get_open_visual_search_sidebar()

	def test_hidden_responsive_tile_does_not_shadow_visible_copy(self):
		driver = driver_for()
		driver.children[(By.ID, "streaks")].insert(0, FakeElement(displayed=False, children={(By.TAG_NAME, "button"): [tile("Visual Search Streak")]}))
		self.assertIs(element_selectors.ElementSelectionUtils(driver).get_open_visual_search_sidebar(), driver.tiles[4])

	def test_stale_or_hidden_copy_does_not_hide_a_visible_usable_tile(self):
		driver = driver_for()
		stale = FakeElement()
		stale.is_displayed = mock.Mock(side_effect=StaleElementReferenceException("render replaced"))
		driver.children[(By.ID, "streaks")].insert(0, stale)
		self.assertIs(element_selectors.ElementSelectionUtils(driver).get_open_visual_search_sidebar(), driver.tiles[4])

	def test_row_with_no_titled_tiles_is_unready(self):
		driver = FakeDriver(children={(By.ID, "streaks"): [FakeElement(children={(By.TAG_NAME, "button"): [FakeElement(text="Loading...")]})]})
		with self.assertRaises(element_selectors.ElementNotReady):
			element_selectors.ElementSelectionUtils(driver).get_open_visual_search_sidebar()

	def test_metadata_done_completed_reset_and_streak_text(self):
		for metadata, done in (("Activity: 1/1", True), ("Activity: 2/1", True), ("Activity: 0/1", False),
			("Activity: 0/0", False), ("Activity: ?/1", False), ("", False), ("Activity: 1/1\nActivity: 0/1", False)):
			with self.subTest(metadata=metadata):
				driver = driver_for(metadata=metadata)
				driver.tiles[4].text = "Visual Search Streak\nDay 7 of 7 streak completed."
				self.assertEqual(element_selectors.ElementSelectionUtils(driver).visual_search_tile_done_today(), done)


class PanelIdentity(unittest.TestCase):
	def test_wrong_panel_with_two_links_cannot_return_app_store_link(self):
		links = [FakeElement(text="Check-in now"), FakeElement(text="Search now", attributes={"href": "https://synthetic.invalid/app-store"})]
		selectors = element_selectors.ElementSelectionUtils(driver_for(sidebar=panel("Bing App Streak", links)))
		for getter in (selectors.get_search_now_link_from_visual_search_sidebar, selectors.visual_search_done_today):
			with self.assertRaises(element_selectors.UnexpectedVisualSearchPanel) as caught:
				getter()
			self.assertIn("Bing App Streak", str(caught.exception))

	def test_right_panel_only_returns_exact_visible_enabled_actions(self):
		for label in ("Search now", "Activate streak", "  SEARCH\n NOW "):
			with self.subTest(label=label):
				link = FakeElement(text=label)
				selectors = element_selectors.ElementSelectionUtils(driver_for(sidebar=panel(links=[link])))
				self.assertIs(selectors.get_search_now_link_from_visual_search_sidebar(), link)
		for links in ([FakeElement(text="Activity: 0/1"), FakeElement(text="Get Bing app")],
			[FakeElement(text="Search now", displayed=False)], [FakeElement(text="Search now", attributes={"aria-disabled": "true"})],
			[FakeElement(text="Do not Search now")]):
			with self.subTest(links=links), self.assertRaises(NoSuchElementException):
				element_selectors.ElementSelectionUtils(driver_for(sidebar=panel(links=links))).get_search_now_link_from_visual_search_sidebar()

	def test_untitled_or_hidden_panel_is_unready(self):
		for sidebar in (panel(title=None), panel(displayed=False)):
			with self.assertRaises(element_selectors.ElementNotReady):
				element_selectors.ElementSelectionUtils(driver_for(sidebar=sidebar)).get_visual_search_sidebar()

	def test_visible_visual_panel_wins_over_stale_hidden_or_other_panel_copy(self):
		correct = panel("  VISUAL\n Search Streak  ", [FakeElement(text="Search now")])
		driver = driver_for(sidebar=correct)
		driver.children[(By.TAG_NAME, "section")] = [panel("Bing App Streak"), panel(displayed=False), correct]
		self.assertIs(element_selectors.ElementSelectionUtils(driver).get_visual_search_sidebar(), correct)


class TaskIdentity(unittest.TestCase):
	def run_task(self, driver, *, missing_image=False):
		tasks = rewards_tasks.RewardsTaskUtils.__new__(rewards_tasks.RewardsTaskUtils)
		tasks.driver, tasks.elements = driver, element_selectors.ElementSelectionUtils(driver)
		tasks.switch_to_earn_page = lambda: None
		tasks.restore_main_tab = lambda: None
		tasks.return_to_rewards_home = lambda: None
		tasks.dismiss_cookie_consent = lambda: None
		tasks.tab_utils = types.SimpleNamespace(switch_to_other_tab=lambda: None, close_all_other_tabs=lambda: None)
		camera = FakeElement()
		driver.children[(By.CSS_SELECTOR, "#sb_form > div.camera.icon")] = [camera]
		class Upload(FakeElement):
			def send_keys(self, path):
				driver.current_url = "https://www.bing.com/images/search?synthetic=uploaded"
		driver.children[(By.CSS_SELECTOR, "#sb_fileinput")] = [Upload()]
		def click(target):
			element = target() if callable(target) else target
			driver.clicked.append(element)
			if element not in driver.tiles and element is not camera:
				driver.current_url = "https://www.bing.com/visualsearch"
		tasks.move_to_and_click = click
		image = "synthetic-image-must-not-be-requested-f152.jpg" if missing_image else __file__
		with mock.patch.dict(sys.modules, {"random_image_for_visual_search": types.SimpleNamespace(get_random_image=lambda: self.fail("unavailable/done task must not generate an image"))}), mock.patch.object(rewards_tasks, "WebDriverWait", lambda driver, timeout: RealWait(driver, 0)), mock.patch.object(rewards_tasks.time, "sleep", return_value=None), mock.patch.object(rewards_tasks, "VISUAL_SEARCH_IMAGE_PATH", image), self.assertLogs(rewards_tasks.logger, level=logging.INFO) as captured:
			tasks.complete_all_tasks(only={"visual_search"})
		return "\n".join(captured.output)

	def test_absent_tile_is_skip_with_titles_no_panel_or_image_side_effect(self):
		driver = driver_for(SLIMDEBUG)
		output = self.run_task(driver, missing_image=True)
		self.assertIn("[SKIP] Visual search: not available", output)
		for title in SLIMDEBUG:
			self.assertIn(title, output)
		self.assertEqual(driver.clicked, [])
		self.assertNotIn("Generating", output)

	def test_done_today_and_day_seven_do_not_open_panel(self):
		for day in (1, 7):
			with self.subTest(day=day):
				driver = driver_for(metadata="Activity: 1/1")
				driver.tiles[4].text = f"Visual Search Streak\nDay {day} of 7 streak completed.\nActivity: 1/1"
				output = self.run_task(driver, missing_image=True)
				self.assertIn("[OK] Visual search", output)
				self.assertIn("already done for today", output)
				self.assertEqual(driver.clicked, [])

	def test_reset_and_activate_path_click_only_identified_tile_and_action(self):
		for label in ("Search now", "Activate streak"):
			with self.subTest(label=label):
				link = FakeElement(text=label)
				driver = driver_for(metadata="Activity: 0/1", sidebar=panel(links=[link]))
				output = self.run_task(driver)
				self.assertIn("[OK] Visual search", output)
				self.assertEqual(driver.clicked[:2], [driver.tiles[4], link])
				self.assertEqual(len(driver.clicked), 3)  # camera is the only other click.

	def test_panel_done_marker_still_returns_ok_without_action_or_image(self):
		control = FakeElement(text="Search now", attributes={"role": "link", "aria-disabled": "true"})
		driver = driver_for(sidebar=panel(controls=[control]))
		output = self.run_task(driver, missing_image=True)
		self.assertIn("[OK] Visual search", output)
		self.assertEqual(driver.clicked, [driver.tiles[4]])

	def test_row_that_hydrates_during_wait_is_found_by_title_before_click(self):
		driver = driver_for(metadata="Activity: 1/1")
		section = driver.children[(By.ID, "streaks")][0]
		original = section.find_elements
		polls = []
		def delayed(by, selector):
			polls.append(None)
			return [] if len(polls) == 1 else original(by, selector)
		section.find_elements = delayed
		output = self.run_task(driver, missing_image=True)
		self.assertIn("[OK] Visual search", output)
		self.assertEqual(driver.clicked, [])
		self.assertGreater(len(polls), 1)

	def test_wrong_panel_fails_immediately_and_never_clicks_its_links(self):
		links = [FakeElement(text="Check-in now"), FakeElement(text="Search now")]
		driver = driver_for(sidebar=panel("Bing App Streak", links))
		output = self.run_task(driver)
		self.assertIn("[FAIL] Visual search", output)
		self.assertIn("opened panel: Bing App Streak", output)
		self.assertEqual(driver.clicked, [driver.tiles[4]])

	def test_right_panel_without_action_or_done_keeps_timeout_diagnostics_and_fail(self):
		driver = driver_for(sidebar=panel(links=[FakeElement(text="Unrelated"), FakeElement(text="App store")]))
		output = self.run_task(driver)
		self.assertIn("[FAIL] Visual search: opened the sidebar", output)
		self.assertIn("no Search now/Activate streak or done marker matched", output)
		self.assertEqual(driver.clicked, [driver.tiles[4]])

	def test_unrendered_streak_row_is_fail_not_skip(self):
		driver = driver_for()
		driver.children[(By.ID, "streaks")] = []
		output = self.run_task(driver)
		self.assertIn("[FAIL] Visual search: on the page but not ready in time", output)
		self.assertNotIn("[SKIP]", output)
		self.assertEqual(driver.clicked, [])


if __name__ == "__main__":
	unittest.main()
