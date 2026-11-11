"""Optional, UI-measured Edge browsing. Never submits or invents client reports."""
import logging
import contextlib
import math
import os
import re
import time
from urllib.parse import urlsplit

from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException
import run_record

TITLE = "Edge Browsing Streak"
EARN = "https://rewards.bing.com/earn"
PAGES = ("https://www.bing.com/", "https://www.msn.com/")
POLL_SECONDS = 180
SCROLL_SECONDS = 30
logger = logging.getLogger(__name__)


class EdgeStreakUnavailable(NoSuchElementException):
	pass


class EdgeStreakIncomplete(Exception):
	pass


@contextlib.contextmanager
def command_limits(driver):
	"""Bound page/transport stalls and restore the caller's driver settings."""
	page_load = driver.timeouts.page_load
	config = getattr(getattr(driver, "command_executor", None), "_client_config", None)
	transport = config.timeout if config is not None else None
	try:
		if config is not None:
			config.timeout = 15
		driver.set_page_load_timeout(15)
		yield
	finally:
		try:
			driver.set_page_load_timeout(page_load)
		finally:
			if config is not None:
				config.timeout = transport


def enabled():
	return os.environ.get("EDGE_BROWSING_STREAK", "").strip().lower() in ("1", "true", "yes")


def maximum_minutes():
	try:
		value = float(os.environ.get("EDGE_BROWSING_STREAK_MAX_MINUTES", "35"))
	except ValueError:
		raise ValueError("EDGE_BROWSING_STREAK_MAX_MINUTES must be a finite positive number") from None
	if not math.isfinite(value) or value <= 0:
		raise ValueError("EDGE_BROWSING_STREAK_MAX_MINUTES must be greater than0")
	return value


def normalized(value):
	return " ".join((value or "").split())


def find_tile(driver):
	# Deliberately local to this task: no dependency on the visual-search PR.
	from element_selectors import ElementNotReady
	titles, incomplete = [], False
	for section in driver.find_elements(By.ID, "streaks"):
		try:
			if not section.is_displayed():
				continue
			for button in section.find_elements(By.TAG_NAME, "button"):
				if not button.is_displayed():
					continue
				nodes = button.find_elements(By.CSS_SELECTOR, "p.text-globalBody2Strong")
				labels = [normalized(node.text) for node in nodes if node.is_displayed() and normalized(node.text)]
				incomplete |= bool(nodes) and not labels
				titles.extend(labels)
				if any(label.casefold() == TITLE.casefold() for label in labels):
					return button
		except StaleElementReferenceException:
			incomplete = True
	if not titles or incomplete:
		raise ElementNotReady("streaks row is not ready for Edge browsing")
	raise EdgeStreakUnavailable("Edge Browsing Streak is not available; shown tiles: " + ", ".join(dict.fromkeys(titles)))


def read_minutes(driver):
	from element_selectors import ElementNotReady
	tile = find_tile(driver)
	values = []
	for metadata in tile.find_elements(By.CSS_SELECTOR, "div.line-clamp-2.text-metadata"):
		if metadata.is_displayed():
			values.extend((int(done), int(total)) for done, total in re.findall(r"\bMinutes\s*:?\s*(\d+)\s*/\s*(\d+)", metadata.text or "", re.I))
	if len(set(values)) != 1 or values[0][1] <= 0:
		raise ElementNotReady("Edge browsing minute counter is not ready")
	return values[0]


def activation(driver, *, panel_opened=False):
	# A tile may contain activation without a panel. Otherwise inspect only
	# a panel identified by its title; unknown app/visual panels cannot act.
	tile = find_tile(driver)
	for scope in [tile, *(driver.find_elements(By.TAG_NAME, "section") if panel_opened else [])]:
		if scope is not tile:
			if not (scope.get_dom_attribute("id") or "").startswith("react-aria") or not scope.is_displayed():
				continue
			titles = scope.find_elements(By.CSS_SELECTOR, 'h1, h2, h3, h4, h5, h6, [role="heading"], p.text-globalBody2Strong')
			labels = [normalized(title.text).casefold() for title in titles if title.is_displayed() and normalized(title.text)]
			if labels and not any(TITLE.casefold() in label for label in labels):
				raise EdgeStreakIncomplete("Edge browsing opener showed another streak panel; stopped")
		for control in scope.find_elements(By.CSS_SELECTOR, 'a, button, [role="link"], [role="button"]'):
			if (control.is_displayed() and normalized(control.text).casefold() in ("activate streak", "get started")
				and control.get_dom_attribute("aria-disabled") != "true" and control.get_dom_attribute("disabled") is None):
				return control
	return None


def complete(tasks):
	if not enabled():
		return
	limit = maximum_minutes()
	with command_limits(tasks.driver):
		return _complete(tasks, limit)


def _complete(tasks, limit):
	clock, sleep = time.monotonic, time.sleep
	began = clock()
	deadline = began + limit * 60
	before = after = None
	activated = False
	def budget():
		if clock() >= deadline:
			raise EdgeStreakIncomplete("Edge browsing reached its maximum minutes before completion")
	def measure():
		budget()
		tasks.driver.get(EARN)
		tasks.tab_utils.ensure_focus()
		url = urlsplit(tasks.driver.current_url)
		if url.hostname != "rewards.bing.com" or url.path.rstrip("/") != "/earn":
			raise EdgeStreakIncomplete("Edge browsing could not return to the earn page; stopped")
		# Rendered absence is definitive; an empty row may still hydrate.
		try:
			find_tile(tasks.driver)
		except __import__("element_selectors").ElementNotReady:
			tasks.wait_for_element(lambda: find_tile(tasks.driver), timeout=min(10, max(0, deadline-clock())))
		return tasks.wait_for_element(lambda: read_minutes(tasks.driver), timeout=min(10, max(0, deadline-clock())))
	# This constructor previously enabled Rewards-only emulation headers. The
	# new task must use the ordinary browser identity on both offer and pages.
	tasks.driver.execute_cdp_cmd("Network.setExtraHTTPHeaders", {"headers": {}})
	try:
		before, target = measure()
		after = before
		logger.info("Edge browsing minutes before: %s/%s", before, target)
		if before >= target:
			return
		control = activation(tasks.driver)
		if control is None:
			tasks.move_to_and_click(lambda: find_tile(tasks.driver))
			def panel_ready():
				return next((section for section in tasks.driver.find_elements(By.TAG_NAME, "section")
					if (section.get_dom_attribute("id") or "").startswith("react-aria") and section.is_displayed()), None)
			tasks.wait_for_element(panel_ready, timeout=min(10, max(0, deadline-clock())))
			control = activation(tasks.driver, panel_opened=True)
		if control is not None:
			# No pointer retry after an ambiguous activation response.
			tasks.move_to_and_click(control, retries=1)
			activated = True
		tasks.progress = f"Edge browsing minutes {before}/{target}"
		page = 0
		while after < target:
			budget()
			tasks.driver.get(PAGES[page % len(PAGES)])
			tasks.tab_utils.ensure_focus()
			next_read = min(deadline, clock() + POLL_SECONDS)
			while clock() < next_read:
				url = urlsplit(tasks.driver.current_url)
				if url.scheme != "https" or url.hostname not in ("bing.com", "www.bing.com", "msn.com", "www.msn.com"):
					raise EdgeStreakIncomplete("Edge browsing page changed to an unapproved host; stopped")
				ActionChains(tasks.driver).scroll_by_amount(0, 180 if page % 2 == 0 else -180).perform()
				sleep(max(0, min(SCROLL_SECONDS, next_read-clock())))
			previous = after
			if clock() >= deadline:
				# One last reading, not extra browsing, confirms the actual result.
				tasks.driver.get(EARN)
				url = urlsplit(tasks.driver.current_url)
				if url.hostname != "rewards.bing.com" or url.path.rstrip("/") != "/earn":
					raise EdgeStreakIncomplete("Edge browsing final counter page changed; stopped")
				after, current_target = read_minutes(tasks.driver)
			else:
				after, current_target = measure()
			if current_target != target or after < previous:
				raise EdgeStreakIncomplete("Edge browsing counter changed its daily target; stopped")
			tasks.progress = f"Edge browsing minutes {before}/{target} -> {after}/{target}"
			page += 1
			if clock() >= deadline and after < target:
				raise EdgeStreakIncomplete(f"Edge browsing maximum {limit:g} minutes reached; counter {before}/{target} -> {after}/{target}")
	finally:
		if before is not None:
			logger.info("Edge browsing minutes after: %s (before %s)", after, before)
			run_record.emit("edge_browsing", minutes_before=before, minutes_after=after, minutes_target=target,
				activated=activated, maximum_minutes=limit, elapsed_seconds=round(clock()-began, 3),
				maximum_reached=clock() >= deadline)
