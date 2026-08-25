"""Report which selectors resolve against the Rewards UI you actually get.

The Rewards markup differs between markets and changes between deploys, so a
selector that works for one account silently finds nothing for another. This
walks every selector and prints what resolved, what is absent, and what broke.

It completes no activities and claims no points. It only reads, and opens the
points breakdown and daily set panels, which award nothing.

	poetry run python src/check_selectors.py

Paste the output into a bug report. Absent is a normal result for a task the
variant does not ship. FAILED is what needs fixing.
"""

import sys
import time

from selenium import webdriver
from selenium.webdriver.common.by import By

import element_selectors
from constants import USER_DATA_DIR, PROFILE_NAME

RENDER_TIMEOUT = 60


def build_driver():
	options = webdriver.EdgeOptions()

	options.add_experimental_option("excludeSwitches", ["enable-automation"])
	options.add_experimental_option("useAutomationExtension", False)
	options.add_argument("--disable-blink-features=AutomationControlled")
	options.add_argument(f"--user-data-dir={USER_DATA_DIR}")
	options.add_argument(f"--profile-directory={PROFILE_NAME}")

	return webdriver.Edge(options=options)


def wait_until(predicate, timeout=RENDER_TIMEOUT):
	deadline = time.time() + timeout

	while time.time() < deadline:
		try:
			if predicate():
				return True
		except Exception:
			pass

		time.sleep(2)

	return False


class Report:
	def __init__(self):
		self.rows = []

	def record(self, name, status, detail=""):
		self.rows.append((name, status, detail))
		print(f"  {status:<8} {name:<44} {detail}")

	def check(self, name, fn, optional=False):
		try:
			value = fn()
		except Exception as exc:
			self.record(name, "ABSENT" if optional else "FAILED", type(exc).__name__)
			return None

		if isinstance(value, list):
			detail = f"{len(value)} element(s)"

			if not value and optional:
				self.record(name, "ABSENT", "0 elements")
				return value
		elif isinstance(value, tuple):
			detail = str(value)
		else:
			try:
				detail = repr((value.text or "").replace("\n", " | ")[:44])
			except Exception:
				detail = "<element>"

		self.record(name, "OK", detail)
		return value

	def summary(self):
		counts = {"OK": 0, "ABSENT": 0, "FAILED": 0}

		for _, status, _ in self.rows:
			counts[status] = counts.get(status, 0) + 1

		print(f"\nOK={counts['OK']}  ABSENT={counts['ABSENT']}  FAILED={counts['FAILED']}")

		return counts["FAILED"]


def describe_environment(driver, report):
	print("\n## environment")

	caps = driver.capabilities

	print(f"  browser        {caps.get('browserVersion')}")
	print(f"  msedgedriver   {caps.get('msedge', {}).get('msedgedriverVersion', '?').split(' ')[0]}")
	print(f"  selenium       {getattr(__import__('selenium'), '__version__', '?')}")
	print(f"  python         {sys.version.split()[0]}")
	print(f"  page lang      {driver.find_element(By.TAG_NAME, 'html').get_attribute('lang')!r}")
	print(f"  viewport       {driver.execute_script('return [window.innerWidth, window.innerHeight];')}")

	sections = [
		s.get_dom_attribute("id")
		for s in driver.find_elements(By.XPATH, "/html/body/div[2]/div[2]/div/main/section")
	]
	print(f"  earn sections  {sections}")

	duplicates = [i for i in set(sections) if i and sections.count(i) > 1]
	if duplicates:
		print(f"  duplicated ids {duplicates}")


def main():
	driver = build_driver()
	elements = element_selectors.ElementSelectionUtils(driver)
	report = Report()

	try:
		driver.get("https://rewards.bing.com/earn")

		rendered = wait_until(lambda: elements.get_points_breakdown_button() is not None)

		if not rendered:
			print("The earn page never finished rendering.")
			print("In the EU the cookie consent banner blocks it until answered, and it")
			print("cannot be dismissed reliably from selenium. Open the profile in a")
			print("normal Edge window, answer the banner once, then run this again.")
			return 2

		describe_environment(driver, report)

		print("\n## navigation")
		report.check("get_earn_tab", elements.get_earn_tab)
		report.check("get_dashboard_tab", elements.get_dashboard_tab)
		report.check("get_points_breakdown_button", elements.get_points_breakdown_button)

		print("\n## daily set")
		opener = report.check("get_open_daily_set_button", elements.get_open_daily_set_button)

		if opener is not None:
			driver.execute_script("arguments[0].scrollIntoView({block:'center'});", opener)
			time.sleep(1)
			driver.execute_script("arguments[0].click();", opener)
			wait_until(lambda: elements.get_sidebar_section() is not None, 30)
			report.check("get_daily_set_elements", elements.get_daily_set_elements)

			try:
				driver.execute_script(
					"arguments[0].click();", elements.get_generic_sidebar_close_button()
				)
				time.sleep(2)
			except Exception:
				driver.get("https://rewards.bing.com/earn")
				wait_until(lambda: elements.get_points_breakdown_button() is not None)

		print("\n## optional tasks")
		report.check("get_explore_on_bing_elements", elements.get_explore_on_bing_elements, optional=True)
		report.check("get_open_visual_search_sidebar", elements.get_open_visual_search_sidebar, optional=True)

		print("\n## cards")
		cards = report.check("get_all_misc_cards", elements.get_all_misc_cards)

		if cards:
			for index, card in enumerate(cards, start=1):
				try:
					points = elements.get_card_point_value(card)
					done = elements.card_is_complete(card)
					description = elements.extract_card_descriptions(card)[:38]
					print(f"    card[{index}] points={points:<4} completed={done!s:<5} {description!r}")
				except Exception as exc:
					print(f"    card[{index}] unreadable: {type(exc).__name__}")

		print("\n## points breakdown")
		driver.get("https://rewards.bing.com/earn")
		wait_until(lambda: elements.get_points_breakdown_button() is not None)
		driver.execute_script("arguments[0].click();", elements.get_points_breakdown_button())
		wait_until(lambda: elements.get_sidebar_section() is not None, 30)

		report.check("get_sidebar_section", elements.get_sidebar_section)
		report.check(
			"get_points_earned_from_searches_on_points_breakdown",
			elements.get_points_earned_from_searches_on_points_breakdown,
		)
		report.check("get_close_button_on_points_breakdown", elements.get_close_button_on_points_breakdown)

		print("\n## bonus")
		driver.get("https://rewards.bing.com/dashboard")
		wait_until(lambda: elements.get_bonus_button_on_dashboard() is not None, 30)
		report.check("get_bonus_button_on_dashboard", elements.get_bonus_button_on_dashboard, optional=True)

		print("\n## bing")
		driver.get("https://www.bing.com/")
		wait_until(lambda: bool(driver.find_elements(By.TAG_NAME, "textarea")), 30)
		report.check("get_bing_search_bar", elements.get_bing_search_bar)

		failures = report.summary()

		if failures:
			print("\nFAILED entries are selectors that should have resolved on this page.")
		else:
			print("\nEvery selector that this variant ships resolved.")

		return 1 if failures else 0
	finally:
		driver.quit()


if __name__ == "__main__":
	sys.exit(main())
