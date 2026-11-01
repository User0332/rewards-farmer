"""Diagnostic classifications on fixed pages, without starting a browser."""

from contextlib import ExitStack, redirect_stdout
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from selenium.webdriver.common.by import By
import accounts
import check_selectors
import element_selectors
from fakes import FakeDriver, FakeElement

SECTIONS = "/html/body/div[2]/div[2]/div/main/section"


class Html(FakeElement):
	def get_attribute(self, name):
		return self.get_dom_attribute(name)


class DiagnosticDriver(FakeDriver):
	def __init__(self, *, rendered=True, streaks=None, opener=False, dashboard=True, section_ids=None):
		super().__init__()
		self.capabilities = {"browserVersion": "synthetic", "msedge": {"msedgedriverVersion": "synthetic"}}
		self.rendered = rendered
		self.streaks = streaks
		self.dashboard = dashboard
		self.section_ids = section_ids
		self.opener = FakeElement(text="Daily set streak") if opener else None
		self.points = FakeElement(text="Points breakdown")
		self.close = FakeElement()
		self.panel = None
		self.clicks = []
		self.quit_calls = 0

	def get(self, url):
		self.panel = None
		ids = self.section_ids
		if ids is None:
			ids = ["exploreonbing", "levelup", "quests", "moreactivities", "microsoft"]
			if self.streaks is not None:
				ids.append("streaks")
		sections = [FakeElement(attributes={"id": name}) for name in ids]
		self.children = {
			(By.TAG_NAME, "html"): [Html(attributes={"lang": "en"})],
			(By.XPATH, SECTIONS): sections,
			(By.TAG_NAME, "button"): [self.points] if self.rendered else [],
			(By.CSS_SELECTOR, '[id$="-tab-/earn"]'): [FakeElement(text="Earn")],
			(By.ID, "moreactivities"): [FakeElement(children={(By.TAG_NAME, "a"): [FakeElement(text="A card")]})],
		}
		if self.dashboard:
			self.children[(By.CSS_SELECTOR, '[id$="-tab-/dashboard"]')] = [FakeElement(text="Dashboard")]
		if self.streaks is not None:
			self.children[(By.ID, "streaks")] = [self.streaks]
		if self.opener is not None:
			self.children[(By.TAG_NAME, "button")].append(self.opener)
		if url == "https://rewards.bing.com/dashboard":
			self.children[(By.TAG_NAME, "button")] = [FakeElement(text="Ready to claim")]
		if url == "https://www.bing.com/":
			self.children[(By.TAG_NAME, "textarea")] = [FakeElement()]

	def execute_script(self, script, *args):
		if "window.innerWidth" in script:
			return [1280, 720]
		if "arguments[0].click()" not in script:
			return
		element = args[0]
		self.clicks.append(element)
		if element is self.close:
			self.panel = None
		else:
			links = [FakeElement(attributes={"href": f"https://www.bing.com/search?q={i}"}) for i in range(3)] if element is self.opener else []
			self.panel = FakeElement(text="Points breakdown\nBing search\n0/60", attributes={"id": "react-aria-synthetic"}, children={
				(By.TAG_NAME, "a"): links,
				(By.TAG_NAME, "button"): [self.close],
				(By.CSS_SELECTOR, "button[aria-label*='lose']"): [self.close],
			})
		self.children[(By.TAG_NAME, "section")] = [self.panel] if self.panel else []

	def quit(self):
		self.quit_calls += 1


class CheckSelectors(unittest.TestCase):
	def run_diagnostic(self, driver):
		elements = element_selectors.ElementSelectionUtils(driver)
		report = check_selectors.Report()
		output = io.StringIO()
		def wait(predicate, timeout=60):
			try:
				return bool(predicate())
			except Exception:
				return False
		with ExitStack() as stack:
			stack.enter_context(mock.patch.object(check_selectors.log_utils, "setup_logging"))
			stack.enter_context(mock.patch.object(check_selectors.accounts, "get_default_account", return_value=accounts.Account("sample", "synthetic-profile", "Default")))
			stack.enter_context(mock.patch.object(check_selectors.browser, "start_driver", return_value=driver))
			stack.enter_context(mock.patch.object(check_selectors.element_selectors, "ElementSelectionUtils", return_value=elements))
			stack.enter_context(mock.patch.object(check_selectors, "Report", return_value=report))
			stack.enter_context(mock.patch.object(check_selectors, "wait_until", side_effect=wait))
			stack.enter_context(mock.patch.object(check_selectors.time, "sleep"))
			opener = stack.enter_context(mock.patch.object(elements, "get_open_daily_set_button", wraps=elements.get_open_daily_set_button))
			activities = stack.enter_context(mock.patch.object(elements, "get_daily_set_elements", wraps=elements.get_daily_set_elements))
			stack.enter_context(redirect_stdout(output))
			code = check_selectors.main()
		self.assertEqual(driver.quit_calls, 1)
		return code, report.rows, output.getvalue(), opener, activities

	def test_render_timeout_suggests_signin_without_blame_for_consent(self):
		driver = DiagnosticDriver(rendered=False)
		code, rows, output, opener, activities = self.run_diagnostic(driver)
		self.assertEqual(code, 2)
		self.assertIn("The earn page never finished rendering.", output)
		self.assertIn("One known cause is a profile that is not signed in.", output)
		self.assertNotIn("most common", output.lower())
		self.assertIn("normal Edge window", output)
		self.assertIn("sign in on rewards.bing.com and on\nbing.com, then run this again.", output)
		self.assertNotIn("consent", output.lower())
		self.assertNotIn("banner", output.lower())
		self.assertEqual((rows, driver.clicks), ([], []))
		opener.assert_not_called()
		activities.assert_not_called()

	def test_no_streaks_reports_absent_without_daily_set_subchecks(self):
		for section_ids in (None, [], ["streaks"]):
			with self.subTest(section_ids=section_ids):
				code, rows, output, opener, activities = self.run_diagnostic(DiagnosticDriver(section_ids=section_ids))
				self.assertEqual(code, 0)
				row = next(row for row in rows if row[0] == "get_open_daily_set_button")
				self.assertEqual(row[1], "ABSENT")
				self.assertIn("no Daily Set section", row[2])
				self.assertIn("FAILED=0", output)
				self.assertNotIn("get_daily_set_elements", [row[0] for row in rows])
				opener.assert_called_once_with()
				activities.assert_not_called()

	def test_absent_daily_set_does_not_hide_an_unrelated_failure(self):
		code, rows, output, opener, activities = self.run_diagnostic(DiagnosticDriver(dashboard=False))
		self.assertEqual(code, 1)
		self.assertEqual([row[0] for row in rows if row[1] == "FAILED"], ["get_dashboard_tab"])
		self.assertIn("FAILED=1", output)
		activities.assert_not_called()

	def test_streaks_without_opener_remains_failed(self):
		streaks = FakeElement(attributes={"id": "streaks"})
		code, rows, output, opener, activities = self.run_diagnostic(DiagnosticDriver(streaks=streaks))
		self.assertEqual(code, 1)
		self.assertIn(("get_open_daily_set_button", "FAILED", "NoSuchElementException"), rows)
		self.assertIn("FAILED=1", output)
		opener.assert_called_once_with()
		activities.assert_not_called()

	def test_hidden_or_empty_streaks_is_not_reported_absent(self):
		for displayed in (True, False):
			with self.subTest(displayed=displayed):
				streaks = FakeElement(attributes={"id": "streaks"}, displayed=displayed)
				code, rows, output, opener, activities = self.run_diagnostic(DiagnosticDriver(streaks=streaks))
				self.assertEqual(code, 1)
				self.assertEqual(next(row[1] for row in rows if row[0] == "get_open_daily_set_button"), "FAILED")
				activities.assert_not_called()

	def test_resolved_daily_set_keeps_panel_subchecks(self):
		streaks = FakeElement(attributes={"id": "streaks"})
		driver = DiagnosticDriver(streaks=streaks, opener=True)
		code, rows, output, opener, activities = self.run_diagnostic(driver)
		self.assertEqual(code, 0)
		self.assertIn(("get_daily_set_elements", "OK", "3 element(s)"), rows)
		self.assertEqual(activities.call_count, 2)
		self.assertIn(driver.opener, driver.clicks)
		self.assertIn(driver.close, driver.clicks)

	def test_opener_outside_streaks_resolves_when_section_inventory_misses_it(self):
		for section_ids in ([], ["moreactivities"]):
			with self.subTest(section_ids=section_ids):
				driver = DiagnosticDriver(opener=True, section_ids=section_ids)
				code, rows, output, opener, activities = self.run_diagnostic(driver)
				self.assertEqual(code, 0)
				self.assertIn(("get_open_daily_set_button", "OK", "'Daily set streak'"), rows)
				self.assertIn(("get_daily_set_elements", "OK", "3 element(s)"), rows)
				opener.assert_called_once_with()
				self.assertEqual(activities.call_count, 2)
				self.assertIn(driver.opener, driver.clicks)

	def test_unlisted_streaks_without_opener_remains_failed(self):
		streaks = FakeElement(attributes={"id": "streaks"})
		code, rows, output, opener, activities = self.run_diagnostic(DiagnosticDriver(streaks=streaks, section_ids=[]))
		self.assertEqual(code, 1)
		self.assertIn(("get_open_daily_set_button", "FAILED", "NoSuchElementException"), rows)
		self.assertIn("FAILED=1", output)
		opener.assert_called_once_with()
		activities.assert_not_called()


if __name__ == "__main__":
	unittest.main()
