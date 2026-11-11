"""Eligible tile and real UI measurements; synthetic clock, no browser/API."""
import os
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from selenium.webdriver.common.by import By
from selenium.common.exceptions import StaleElementReferenceException
import edge_browsing_streak as edge
import rewards_tasks
import run_record
from fakes import FakeDriver, FakeElement


class Clock:
	def __init__(self):
		self.now = 0
	def __call__(self):
		return self.now
	def sleep(self, seconds):
		self.now += seconds


class Harness:
	def __init__(self, readings=((0, 30), (30, 30)), *, absent=False, activate=None, panel_activation=False):
		self.clock = Clock()
		self.readings, self.read_index = readings, -1
		self.loads, self.scrolls, self.clicks, self.headers = [], [], [], []
		self.page_changed = False
		self.driver = FakeDriver()
		self.driver.current_url = edge.EARN
		self.driver.timeouts = types.SimpleNamespace(page_load=60)
		self.driver.command_executor = types.SimpleNamespace(_client_config=types.SimpleNamespace(timeout=120))
		self.driver.set_page_load_timeout = lambda value: setattr(self.driver.timeouts, "page_load", value)
		self.activation = FakeElement(text=activate) if activate else None
		self.title = "Bing App Streak" if absent else "  EDGE  Browsing Streak  "
		self.tile = FakeElement(text=self.title, attributes={"class": "restyled-tile"})
		self.section = FakeElement(children={(By.TAG_NAME, "button"): [self.tile]})
		self.driver.children[(By.ID, "streaks")] = [self.section]
		self.driver.children[(By.TAG_NAME, "section")] = [FakeElement(attributes={"id": "react-aria-edge"}, children={
			(By.CSS_SELECTOR, 'h1, h2, h3, h4, h5, h6, [role="heading"]'): [FakeElement(text="Edge Browsing Streak")]})]
		if self.activation and not panel_activation:
			self.tile.children[(By.CSS_SELECTOR, 'a, button, [role="link"], [role="button"]')] = [self.activation]
		if self.activation and panel_activation:
			self.driver.children[(By.TAG_NAME, "section")] = [FakeElement(attributes={"id": "react-aria-edge"}, children={
				(By.CSS_SELECTOR, 'h1, h2, h3, h4, h5, h6, [role="heading"]'): [FakeElement(text="Edge Browsing Streak")],
				(By.CSS_SELECTOR, 'a, button, [role="link"], [role="button"]'): [self.activation]})]
		def get(url):
			self.loads.append(url)
			self.driver.current_url = url
			if url == edge.EARN:
				self.read_index += 1
				done, total = self.readings[min(self.read_index, len(self.readings)-1)]
				self.tile.text = self.title + f"\nMinutes: {done}/{total}"
		self.driver.get = get
		self.driver.execute_cdp_cmd = lambda name, fields: self.headers.append((name, fields))
		self.tasks = rewards_tasks.RewardsTaskUtils.__new__(rewards_tasks.RewardsTaskUtils)
		self.tasks.driver = self.driver
		self.tasks.progress = None
		self.tasks.tab_utils = types.SimpleNamespace(ensure_focus=lambda: None)
		self.tasks.wait_for_element = lambda getter, timeout=10: getter()
		def click(target, retries=3):
			self.clicks.append((target() if callable(target) else target, retries))
		self.tasks.move_to_and_click = click
		self.tasks.restore_main_tab = lambda: None
		self.tasks.return_to_rewards_home = lambda: None

	def run(self, *, maximum="35"):
		harness = self
		class Actions:
			def __init__(self, driver):
				self.driver = driver
			def scroll_by_amount(self, x, y):
				harness.scrolls.append((self.driver.current_url, x, y))
				if harness.page_changed:
					self.driver.current_url = "https://login.live.com/"
				return self
			def perform(self):
				return self
		with mock.patch.dict(os.environ, {"EDGE_BROWSING_STREAK": "true", "EDGE_BROWSING_STREAK_MAX_MINUTES": maximum}), mock.patch.object(edge, "time", types.SimpleNamespace(monotonic=self.clock, sleep=self.clock.sleep)), mock.patch.object(edge, "ActionChains", Actions), mock.patch.object(run_record, "emit") as emit:
			try:
				edge.complete(self.tasks)
			finally:
				self.records = [call.kwargs for call in emit.call_args_list if call.args == ("edge_browsing",)]


class EdgeBrowsingStreakTests(unittest.TestCase):
	def test_off_by_default_and_invalid_limit_cannot_start(self):
		with mock.patch.dict(os.environ, {}, clear=True):
			self.assertFalse(edge.enabled())
			edge.complete(types.SimpleNamespace())
			self.assertEqual(edge.maximum_minutes(), 35)
		for value in ("0", "-1", "nan", "inf", "bad"):
			with self.subTest(value=value), mock.patch.dict(os.environ, {"EDGE_BROWSING_STREAK_MAX_MINUTES": value}), self.assertRaises(ValueError):
				edge.maximum_minutes()

	def test_absent_tile_skip_has_titles_and_never_browses(self):
		h = Harness(absent=True)
		with self.assertRaises(edge.EdgeStreakUnavailable) as caught:
			h.run()
		self.assertIn("Bing App Streak", caught.exception.msg)
		self.assertEqual(h.loads, [edge.EARN])
		self.assertEqual(h.clicks, [])

	def test_already_complete_is_ok_without_browsing_or_opening_panel(self):
		h = Harness(((30, 30),))
		h.run()
		self.assertEqual(h.loads, [edge.EARN])
		self.assertEqual(h.clicks, [])
		self.assertEqual(h.scrolls, [])
		self.assertEqual((h.records[0]["minutes_before"], h.records[0]["minutes_after"]), (30, 30))

	def test_activation_once_from_tile_or_titled_panel_then_counter_rises(self):
		for panel in (False, True):
			for label in ("Get started", "Activate streak"):
				with self.subTest(panel=panel, label=label):
					h = Harness(activate=label, panel_activation=panel)
					h.run()
					self.assertEqual([item for item in h.clicks if item[0] is h.activation], [(h.activation, 1)])
					self.assertEqual(h.records[0]["minutes_after"], 30)
					self.assertTrue(h.records[0]["activated"])
					self.assertEqual(h.clock.now, 180)

	def test_counter_rises_and_same_driver_uses_only_ordinary_pages(self):
		h = Harness(((0, 30), (15, 30), (30, 30)))
		h.run()
		self.assertEqual(h.loads, [edge.EARN, edge.PAGES[0], edge.EARN, edge.PAGES[1], edge.EARN])
		self.assertTrue(all(host in edge.PAGES for host, _, _ in h.scrolls))
		self.assertEqual(h.headers, [("Network.setExtraHTTPHeaders", {"headers": {}})])
		self.assertEqual(h.records[0]["minutes_before"], 0)
		self.assertEqual(h.records[0]["minutes_after"], 30)
		self.assertEqual(h.driver.timeouts.page_load, 60)
		self.assertEqual(h.driver.command_executor._client_config.timeout, 120)

	def test_unchanged_counter_stops_at_maximum_with_clear_result(self):
		h = Harness(((0, 30),))
		with self.assertRaises(edge.EdgeStreakIncomplete) as caught:
			h.run(maximum="5")
		self.assertIn("counter 0/30 -> 0/30", str(caught.exception))
		self.assertEqual(h.clock.now, 300)
		self.assertEqual(h.records[0]["minutes_after"], 0)
		self.assertTrue(h.records[0]["maximum_reached"])
		self.assertEqual(h.records[0]["elapsed_seconds"], 300)
		self.assertEqual(h.driver.timeouts.page_load, 60)

	def test_default_35_minute_limit_without_credit_is_bounded(self):
		h = Harness(((0, 30),))
		with self.assertRaises(edge.EdgeStreakIncomplete):
			h.run()
		self.assertEqual(h.clock.now, 35*60)
		self.assertEqual(h.records[0]["minutes_target"], 30)
		self.assertTrue(h.records[0]["maximum_reached"])

	def test_daily_counter_reset_or_changed_target_stops(self):
		for readings in (((10, 30), (5, 30)), ((10, 30), (15, 60))):
			with self.subTest(readings=readings):
				h = Harness(readings)
				with self.assertRaises(edge.EdgeStreakIncomplete) as caught:
					h.run()
				self.assertIn("changed its daily target", str(caught.exception))
				self.assertEqual(h.clock.now, 180)

	def test_page_redirect_midway_stops_without_more_scrolling_or_reports(self):
		h = Harness()
		h.page_changed = True
		with self.assertRaises(edge.EdgeStreakIncomplete) as caught:
			h.run()
		self.assertIn("unapproved host", str(caught.exception))
		self.assertEqual(len(h.scrolls), 1)
		self.assertEqual(h.records[0]["minutes_after"], 0)

	def test_ambiguous_activation_response_is_not_retried(self):
		h = Harness(activate="Get started")
		def click(control, retries=3):
			h.clicks.append((control, retries))
			raise StaleElementReferenceException("lost activation response")
		h.tasks.move_to_and_click = click
		with self.assertRaises(StaleElementReferenceException):
			h.run()
		self.assertEqual(h.clicks, [(h.activation, 1)])
		self.assertEqual(h.scrolls, [])

	def test_missing_or_conflicting_counter_is_not_guessed(self):
		h = Harness()
		h.driver.get(edge.EARN)
		for text in ("Email: 1/1", "Minutes: 0/0", "Minutes: 0/30 Minutes: 5/30"):
			with self.subTest(text=text):
				h.tile.text = h.title + "\n" + text
				with self.assertRaises(__import__("element_selectors").ElementNotReady):
					edge.read_minutes(h.driver)

	def test_wrong_panel_activation_is_not_clicked(self):
		h = Harness(activate="Get started", panel_activation=True)
		h.driver.children[(By.TAG_NAME, "section")][0].children[(By.CSS_SELECTOR, 'h1, h2, h3, h4, h5, h6, [role="heading"]')] = [FakeElement(text="Bing App Streak")]
		with self.assertRaises(edge.EdgeStreakIncomplete):
			h.run()
		self.assertEqual([target for target, _ in h.clicks], [h.tile])

	def test_preexisting_unrelated_panel_activation_is_not_a_tile_activation(self):
		h = Harness(activate="Get started", panel_activation=True)
		self.assertIsNone(edge.activation(h.driver))
		self.assertIs(edge.activation(h.driver, panel_opened=True), h.activation)
		self.assertEqual(h.clicks, [])

	def test_task_loop_registers_edge_only_when_enabled_and_preserves_skip(self):
		h = Harness(absent=True)
		with mock.patch.dict(os.environ, {"EDGE_BROWSING_STREAK": "true"}), mock.patch.object(rewards_tasks.logger, "log") as log, mock.patch.object(run_record, "emit") as emit:
			h.tasks.complete_all_tasks(only={"edge_browsing"})
		end = next(call.kwargs for call in emit.call_args_list if call.args == ("task_end",))
		self.assertEqual(end["outcome"], "skip")
		self.assertIn("shown tiles: Bing App Streak", end["detail"])
		with mock.patch.dict(os.environ, {"EDGE_BROWSING_STREAK": "false"}), mock.patch.object(run_record, "emit") as emit:
			h.tasks.complete_all_tasks(only={"edge_browsing"})
		emit.assert_not_called()


if __name__ == "__main__":
	unittest.main()
