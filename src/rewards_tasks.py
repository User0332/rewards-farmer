from contextlib import contextmanager
import logging
import log_utils
import os
import random
import time
from typing import Callable
from urllib.parse import urlsplit
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webelement import WebElement
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException, NoSuchElementException
from urllib3.util import Timeout
import tab_utils
import queries
import mouse_trajectory
import mimic_typing
import element_selectors
import quest_steps
import run_record

from constants import DATA_HOME

VISUAL_SEARCH_IMAGE_PATH = os.path.join(DATA_HOME, "visual_search.jpg")

TASKS = (
	("daily_set", "Bing daily set", "complete_bing_daily_set"),
	("explore_on_bing", "Explore on Bing", "complete_explore_on_bing_tasks"),
	("visual_search", "Visual search", "complete_visual_search"),
	("misc_cards", "Misc cards", "complete_misc_cards"),
	("quests", "Quests", "complete_quests"),
	("searches", "Required searches", "complete_required_searches"),
	("bonus", "Bonus points", "claim_bonus_points"),
)
TASK_IDS = tuple(task_id for task_id, _, _ in TASKS)
SKIP_TASKS_ENV = "REWARDS_SKIP_TASKS"


def skipped_tasks() -> frozenset[str]:
	return frozenset(part.strip().lower() for part in os.environ.get(SKIP_TASKS_ENV, "").split(",")) & frozenset(TASK_IDS)

REWARDS_HOME_URL = "https://rewards.bing.com/"
# Each account owns one measured budget, including WebDriver command time.
BALANCE_RUN_BUDGET = 60.0
BALANCE_READ_TIMEOUT = BALANCE_RUN_BUDGET / (len(TASKS) + 1)
BALANCE_POLL_INTERVAL = 0.25
# Leave time for the transport to return its timeout and unwind locally.
BALANCE_COMMAND_MARGIN = 0.05
BALANCE_RESTORE_RESERVE = 0.5

logger = logging.getLogger(__name__)


@contextmanager
def _balance_command_deadline(driver, deadline, clock, *, cleanup=False):
	"""Bound serial Selenium calls, without leaving a reader thread behind.

	The instance-local executor hook is restored before the next task. Selenium
	uses this config for its HTTP read timeout; urllib3 retries must also be off
	or a read timeout could silently spend the same remaining budget twice.
	"""
	executor = getattr(driver, "command_executor", None)
	config = getattr(executor, "_client_config", None)
	if config is None:
		yield
		return
	execute = executor.execute
	previous_timeout = config.timeout
	previous_deadline = getattr(executor, "_balance_deadline", None)
	executor._balance_deadline = min(deadline, previous_deadline) if previous_deadline is not None and not cleanup else deadline
	connection = getattr(executor, "_conn", None)
	request = getattr(connection, "request", None)
	def bounded_execute(*args, **kwargs):
		remaining = executor._balance_deadline - clock() - BALANCE_COMMAND_MARGIN
		if remaining <= 0:
			raise TimeoutException("balance command budget exhausted")
		config.timeout = Timeout(total=remaining, connect=remaining, read=remaining)
		return execute(*args, **kwargs)
	def bounded_request(*args, **kwargs):
		kwargs["retries"] = False
		return request(*args, **kwargs)
	executor.execute = bounded_execute
	if request is not None:
		connection.request = bounded_request
	try:
		yield
	finally:
		executor.execute = execute
		config.timeout = previous_timeout
		if previous_deadline is None:
			del executor._balance_deadline
		else:
			executor._balance_deadline = previous_deadline
		if request is not None:
			connection.request = request


class ElementNeverAppeared(TimeoutException):
	"""A wait expired without the element ever being in the page.

	WebDriverWait reports only that the wait ran out, so a section this market
	does not ship and a section that was on screen and slow arrived as the same
	TimeoutException. Reporting both as "not available in this UI variant" was
	wrong for the second one, which is what #52 describes.

	Subclassed from TimeoutException so the handlers that already wait on a
	control being absent, claim_bonus_points and complete_bing_daily_set, keep
	working unchanged.
	"""


class SearchQuotaShort(Exception):
	"""A readable quota stayed short; retrying a Selenium lookup cannot fix it."""

	def __init__(self, earned: int, maximum: int):
		self.earned, self.maximum = earned, maximum
		super().__init__(f"the search points stayed at {earned}/{maximum}")


def _failure_text(exc: BaseException, progress: str | None = None) -> tuple[str, str]:
	"""The tag and the reason a failed task is reported with.

	Absence and an expired wait need different next steps. A section this market
	does not ship is nothing to act on, so it stays a [SKIP]. A section that was
	on the page and never became usable may have left points behind, so it is
	reported as a failure instead of being folded into the same sentence.

	progress is what the task had already done when it stopped. A task that got
	that far was on the page, so an element missing after that point is not
	absence either. It is a failure with work left over, and the reason starts
	with how far it got.

	Ordered from the most specific case outwards, not by exception hierarchy:
	ElementNeverAppeared is a TimeoutException and ElementNotReady is a
	NoSuchElementException, so each has to be tested before the class it
	refines.
	"""
	name = type(exc).__name__

	# No cause means nothing matched at all, which is only absence when the
	# task had not got anywhere yet.
	if isinstance(exc, SearchQuotaShort):
		cause = f"the search points stayed at {exc.earned}/{exc.maximum}"
	elif isinstance(exc, ElementNeverAppeared):
		cause = None
	elif isinstance(exc, (element_selectors.ElementNotReady, TimeoutException)):
		cause = f"on the page but not ready in time ({name})"
	elif isinstance(exc, NoSuchElementException):
		cause = None
	else:
		cause = f"{name}: {log_utils.exception_summary(exc)}"

	if progress is None:
		if cause is None:
			return "SKIP", f"not available in this UI variant ({name})"

		return "FAIL", cause

	if cause is None:
		cause = f"the next element never appeared ({name})"

	return "FAIL", f"{progress}, then {cause}"


def classify_task_failure(exc: BaseException, progress: str | None = None) -> tuple[str, str, str]:
	tag, reason = _failure_text(exc, progress)
	if tag == "SKIP":
		key = "not_available"
	elif isinstance(exc, SearchQuotaShort):
		key = "quota_short"
	elif isinstance(exc, ElementNeverAppeared) or (isinstance(exc, NoSuchElementException) and not isinstance(exc, element_selectors.ElementNotReady)):
		key = "element_missing"
	elif isinstance(exc, (element_selectors.ElementNotReady, TimeoutException)):
		key = "not_ready"
	else:
		message = str(exc).lower()
		if any(text in message for text in ("chrome not reachable", "no such window", "invalid session id", "target window already closed", "disconnected")):
			key = "browser_closed"
		elif any(text in message for text in ("err_internet_disconnected", "err_name_not_resolved", "err_connection_", "err_network_changed", "err_timed_out")):
			key = "offline"
		else:
			key = "error"
	return tag, reason, key


def task_failure_report(exc: BaseException, progress: str | None = None) -> tuple[str, str]:
	return classify_task_failure(exc, progress)[:2]


class RewardsTaskUtils:
	def __init__(self, driver: webdriver.Edge):
		self.driver = driver

		# Set headers to spoof the rewards app for the rewards only quests
		self.driver.execute_cdp_cmd("Network.enable", {})

		headers = {
			"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36 Edg/151.0.0.0 MSRewards/Desktop/1.1.0",
			"X-Rewards-Source": "msrewards-desktop",
		}

		self.driver.execute_cdp_cmd("Network.setExtraHTTPHeaders", {"headers": headers})

		self.driver.get(REWARDS_HOME_URL)

		self.tab_utils = tab_utils.TabUtils(driver)
		self.tab_utils.ensure_focus()

		# The tab the tasks work in. Recorded rather than looked up later,
		# because "the current tab" stops meaning this one the moment a task
		# opens a card in a new one.
		self.main_window = driver.current_window_handle

		# How far the running task got, for the report if it stops part way.
		# complete_all_tasks clears it before each task.
		self.progress: str | None = None

		self.mouse = mouse_trajectory.MouseUtils(driver)
		self.keyboard = mimic_typing.KeyboardUtils(driver)
		self.elements = element_selectors.ElementSelectionUtils(driver)
		self.verify_signed_in_state()

	def verify_signed_in_state(self):
		try:
			time.sleep(2)
			url = self.driver.current_url.lower()
			if "login.live.com" in url or "account.microsoft.com" in url or "signup" in url:
				logger.warning("Microsoft Rewards is NOT signed in on rewards.bing.com for this profile!")
				logger.warning("Please sign in once on rewards.bing.com in this Edge profile window.")
				run_record.emit("warning", code="signed_out", detail="Microsoft Rewards is NOT signed in on rewards.bing.com for this profile!")
		except Exception:
			pass

	def find_element(self, xpath: str):
		return self.driver.find_element(By.XPATH, xpath)

	def wait_for_element(self, element_getter: Callable[[], WebElement | list[WebElement]], timeout: int = 10) -> WebElement | list[WebElement]:
		# Keep the last reason the getter gave. Without it a wait that expires
		# cannot say whether the element was missing the whole time or was on
		# the page and not ready, and those are reported differently.
		last_error: BaseException | None = None

		def condition(_: webdriver.Edge):
			nonlocal last_error

			try:
				element_or_elements = element_getter()
			except Exception as exc:
				# Exception rather than a bare except, so Ctrl+C during a
				# getter ends the run instead of being retried away.
				last_error = exc

				return False

			last_error = None

			return element_or_elements

		try:
			return WebDriverWait(self.driver, timeout).until(condition)
		except TimeoutException:
			# A falsy return means the getter found something and rejected it,
			# and ElementNotReady means it was there but still rendering. Only
			# a plain NoSuchElementException every time means it was never
			# there at all.
			never_there = (
				isinstance(last_error, NoSuchElementException)
				and not isinstance(last_error, element_selectors.ElementNotReady)
			)

			if not never_there:
				raise

			raise ElementNeverAppeared(
				f"nothing matched during the {timeout}s wait: {log_utils.exception_summary(last_error)}"
			) from last_error

	def switch_to_earn_page(self):
		self.move_to_and_click(self.elements.get_earn_tab())

	def switch_to_dashboard(self):
		self.move_to_and_click(self.elements.get_dashboard_tab())

	def move_to_and_click(self, elem_or_getter: WebElement | Callable[[], WebElement], retries: int = 3):
		if callable(elem_or_getter):
			for attempt in range(retries):
				try:
					target_elem = elem_or_getter()
					self.mouse.move_to_element(target_elem)
					self.mouse.human_like_click()
					return
				except StaleElementReferenceException as exc:
					if attempt == retries - 1:
						raise exc
					logger.warning("StaleElementReferenceException during click attempt %d/%d, retrying...", attempt + 1, retries)
					time.sleep(0.5)
		else:
			self.mouse.move_to_element(elem_or_getter)
			self.mouse.human_like_click()

	def wait_for_then_click(self, element_getter: Callable[[], WebElement], timeout: int = 10):
		self.wait_for_element(element_getter, timeout)
		self.move_to_and_click(element_getter)

	def complete_bing_daily_set(self, expected_activities: int = 3):
		self.switch_to_earn_page()

		self.wait_for_then_click(self.elements.get_open_daily_set_button)
		self.progress = "opened the panel"

		# The panel hydrates progressively, so the first non-empty snapshot can
		# hold fewer than 3 activities. wait_for_element returns on the first
		# truthy result, so a 1-element list satisfied it and indexing [1] and
		# [2] then raised IndexError, taking the whole task down. Wait for the
		# full set instead, and if it never fills, work with what is there.
		def full_activity_list():
			activities = self.elements.get_daily_set_elements()

			return activities if len(activities) >= expected_activities else False

		try:
			daily_set_links = self.wait_for_element(full_activity_list, timeout=30)
		except TimeoutException:
			daily_set_links = self.elements.get_daily_set_elements()

			logger.warning(
				"Daily set panel only shows %s of %s activities",
				len(daily_set_links), expected_activities
			)
			run_record.emit("warning", code="daily_set_partial", detail=f"Daily set panel only shows {len(daily_set_links)} of {expected_activities} activities")

		main_tab = self.driver.current_window_handle
		opened = 0

		# Re-read the panel per index immediately before interaction: clicking an activity can re-render it and
		# stale the captured references.
		for index in range(len(daily_set_links)):
			def get_activity_elem(idx=index):
				return self.elements.get_daily_set_element_by_index(idx)

			try:
				self.move_to_and_click(get_activity_elem)
			except Exception as exc:
				logger.warning("Failed to click daily set activity %d: %s", index + 1, exc)
				continue

			opened += 1
			self.progress = f"opened {opened} of {len(daily_set_links)} activities"

			time.sleep(random.uniform(2, 3))
			self.tab_utils.close_all_other_tabs(exceptions=[main_tab])

		self.tab_utils.close_all_other_tabs(exceptions=[main_tab])

	def _explore_on_bing_cards(self):
		# Asked once first, so an account without the section moves on at once
		# instead of sitting out a wait. Only a section that is there and still
		# rendering gets the wait.
		try:
			return self.elements.get_explore_on_bing_elements()
		except element_selectors.ElementNotReady:
			return self.wait_for_element(self.elements.get_explore_on_bing_elements)

	def complete_explore_on_bing_tasks(self):
		self.switch_to_earn_page()

		explore_on_bing_links = self._explore_on_bing_cards()

		if not explore_on_bing_links:
			# Raise rather than return, so complete_all_tasks reports this as
			# [SKIP]. Returning quietly made it print [OK] for a task that never
			# ran, which is exactly the kind of false success a scheduled run
			# must not produce.
			raise NoSuchElementException("no Explore on Bing section in this UI variant")

		total = len(explore_on_bing_links)
		self.progress = f"searched 0 of {total} cards"

		for number in range(1, total + 1):
			# Read again before every card. Coming back from the search tab
			# re-renders the section, so a reference taken before the first
			# search went stale: "searched 2 of 3 cards, then
			# StaleElementReferenceException" (#102).
			cards = self._explore_on_bing_cards()

			if number > len(cards):
				break

			card = cards[number - 1]
			desc = self.elements.extract_card_descriptions(card)
			query = queries.search_query_for_task(desc)

			self.move_to_and_click(card)
			self.tab_utils.switch_to_other_tab()

			self.wait_for_element(self.elements.get_bing_search_bar)

			# search bar should be auto-focused

			self.keyboard.send_keys(f"{query} -noai{Keys.ENTER}")

			time.sleep(random.uniform(2, 3))

			self.tab_utils.switch_to_other_tab()
			self.tab_utils.close_all_other_tabs()

			self.progress = f"searched {number} of {total} cards"

		time.sleep(random.uniform(1, 2)) # allow card statuses to update

		for card in self._explore_on_bing_cards():
			if not self.elements.card_is_complete(card):
				logger.warning(
					"Explore on Bing Card [desc=%r] is not complete after searching. Please check manually.",
					self.elements.extract_card_descriptions(card)
				)

	def complete_visual_search(self):
		self.switch_to_earn_page()

		if not os.path.exists(VISUAL_SEARCH_IMAGE_PATH):
			logger.info("visual_search.jpg not found. Generating visual search image...")
			import random_image_for_visual_search
			random_image_for_visual_search.get_random_image()

		self.wait_for_then_click(self.elements.get_open_visual_search_sidebar)
		self.progress = "opened the sidebar"

		# Once today's visual search is done the panel has no link left to
		# wait for. Without this every later run that day waited it out and
		# reported a task with nothing left to do as failed.
		done = object()

		def link_or_done():
			try:
				return self.elements.get_search_now_link_from_visual_search_sidebar()
			except NoSuchElementException:
				if self.elements.visual_search_done_today():
					return done

				raise

		if self.wait_for_element(link_or_done) is done:
			logger.info("Visual search is already done for today")

			return

		self.move_to_and_click(self.elements.get_search_now_link_from_visual_search_sidebar)

		self.tab_utils.switch_to_other_tab()
		self.progress = "opened the visual search page"

		self.wait_for_then_click(self.elements.get_visual_search_button)

		file_input = self.wait_for_element(self.elements.get_visual_search_file_input)

		file_input.send_keys(VISUAL_SEARCH_IMAGE_PATH)
		self.progress = "uploaded the image"

		time.sleep(random.uniform(3, 5))

		self.tab_utils.switch_to_other_tab()
		self.tab_utils.close_all_other_tabs()

	def complete_misc_cards(self):
		self.switch_to_earn_page()
		main_tab = self.driver.current_window_handle

		misc_cards: list[WebElement] = self.wait_for_element(self.elements.get_all_misc_cards)

		opened = 0
		self.progress = "opened 0 cards"

		for index in range(len(misc_cards)):
			cards = self.elements.get_all_misc_cards()
			if index >= len(cards):
				break
			card = cards[index]

			try:
				self.mouse.wheel_scroll_element_into_view(card)

				if not self.elements.card_is_complete(card) and self.elements.get_card_point_value(card) > 0:
					self.move_to_and_click(card)

					opened += 1
					self.progress = f"opened {opened} {'card' if opened == 1 else 'cards'}"

					time.sleep(random.uniform(1, 2))
					self.tab_utils.close_all_other_tabs(exceptions=[main_tab])
			except Exception as exc:
				logger.warning("Misc Card [%d] interaction failed: %s", index, exc)
				continue

		for card in self.elements.get_all_misc_cards():
			if not self.elements.card_is_complete(card) and self.elements.get_card_point_value(card) > 0:
				logger.warning(
					"Misc Card [desc=%r] is not complete after clicking. Please check manually.",
					self.elements.extract_card_descriptions(card)
				)
				run_record.emit("card_left_open", description=self.elements.extract_card_descriptions(card))

		self.tab_utils.close_all_other_tabs(exceptions=[main_tab])

		self.mouse.wheel_scroll_to_top()

	def complete_required_searches(self, max_rounds: int = 6):
		# Points per search are not fixed. Some markets award 3 rather than 5,
		# the daily maximum itself changes (observed 15, 30 and 60 on the same
		# account within one day, with the counter resetting), and daily set and
		# card searches count towards the same quota. A single up front division
		# therefore leaves points on the table and still reports success.
		# Measure, search, measure again.
		sent = 0
		self._searches_sent = 0
		self.progress = "sent 0 searches"
		points_earned, max_pts = self.read_search_points()

		logger.info("Search points before: %s/%s", points_earned, max_pts)
		run_record.emit("search_points", earned=points_earned, max=max_pts, round=0)

		for round_number in range(1, max_rounds + 1):
			if points_earned >= max_pts:
				break

			# Assume the lower known rate so a round never overshoots by much.
			searches = max(1, (max_pts - points_earned) // 3)

			batch_error = None
			try:
				batch = self.run_search_batch(searches, already_sent=sent)
			except (NoSuchElementException, TimeoutException) as exc:
				if self._searches_sent == 0:
					raise
				batch_error = exc
				batch = self._searches_sent - sent
			sent += batch

			previous = points_earned
			try:
				if batch_error is not None:
					raise batch_error
				points_earned, max_pts = self.read_search_points()
			except (NoSuchElementException, TimeoutException) as exc:
				# The last query can fill the quota before its clear button or the
				# returning earn page is ready. Only a fresh reading can prove that.
				logger.warning("Search result could not be verified after %s (%s), reloading once", self.progress, type(exc).__name__)
				try:
					self.driver.get("https://rewards.bing.com/earn")
					self.tab_utils.ensure_focus()
					points_earned, max_pts = self.read_search_points()
				except Exception:
					if batch_error is not None:
						raise batch_error
					raise
				if points_earned < max_pts:
					raise exc

			logger.info(
				"Round %s: %s searches -> %s/%s",
				round_number, batch, points_earned, max_pts
			)
			run_record.emit("search_points", earned=points_earned, max=max_pts, round=round_number)

			if points_earned <= previous:
				logger.warning("Round produced no points, stopping instead of searching pointlessly.")
				break

		if points_earned < max_pts:
			logger.warning("Search quota not filled: %s/%s", points_earned, max_pts)
			raise SearchQuotaShort(points_earned, max_pts)
		else:
			logger.info("Search quota complete: %s/%s", points_earned, max_pts)

	def complete_quests(self, max_steps: int = 8):
		"""Work through the punch cards in the quests section.

		Cards can start with activation before their Bing activities unlock.
		They unlock one step at a time, which is why each card's page is loaded
		again after every activity instead of collecting its links up front.
		"""
		try:
			self.switch_to_earn_page()
		except NoSuchElementException:
			# A quest-only run reaches the freshly loaded home page before the
			# earlier tasks in a full run have given its navigation time to render.
			self.wait_for_element(self.elements.get_earn_tab)
			self.switch_to_earn_page()

		try:
			quest_links = self.wait_for_element(self.elements.get_quest_links)
		except TimeoutException:
			raise NoSuchElementException("no quests in this UI variant") from None

		quest_urls = []

		for link in quest_links:
			href = link.get_dom_attribute("href") or ""

			if href.startswith("/"):
				href = "https://rewards.bing.com" + href

			if href and href not in quest_urls:
				quest_urls.append(href)

		if not quest_urls:
			raise NoSuchElementException("no quests in this UI variant")

		logger.info("%s quest(s) found", len(quest_urls))

		self.progress = f"worked through 0 of {len(quest_urls)} quests"

		for number, url in enumerate(quest_urls, start=1):
			self.complete_single_quest(url, max_steps)
			self.progress = f"worked through {number} of {len(quest_urls)} quests"

	def complete_single_quest(self, url: str, max_steps: int) -> int:
		"""Click the unlocked activities of one punch card, returning how many."""
		completed = 0
		clicked = set()
		def activity_key(activity):
			href = activity.get_dom_attribute("href") or ""
			# Only the admitted activation control can be on the Rewards host.
			# New tracking parameters must not make that same start step new.
			return ("quest start",) if "rewards.bing.com" in href else ((activity.text or "").strip(), href)

		for _ in range(max_steps):
			self.driver.get(url)
			self.tab_utils.ensure_focus()

			try:
				self.wait_for_element(self.elements.get_quest_activity_links)
			except TimeoutException:
				break

			# Some cards keep an activity clickable after the click, for example
			# one whose punches come from searching on separate days rather than
			# from the link. Without this the loop clicked the same link once
			# per round until max_steps ran out.
			activities = [
				activity for activity in self.elements.get_quest_activity_links()
				if activity_key(activity) not in clicked
			]

			if not activities:
				break

			activity = activities[0]
			label = (activity.text or "").strip()
			clicked.add(activity_key(activity))
			href = activity.get_dom_attribute("href") or ""

			self.move_to_and_click(activity)
			quest_steps.record_click(url, href, label)
			time.sleep(random.uniform(2, 4))
			self.tab_utils.close_all_other_tabs()

			completed += 1
			logger.info("Quest activity done: %r", label[:40])

			time.sleep(random.uniform(1, 2))

		card = url.rsplit("/", 1)[-1]

		if completed:
			logger.info("%s activity/activities completed on %s", completed, card)
		else:
			logger.info("Nothing left to click on %s", card)
			if max_steps > 0:
				try:
					logger.info("%s", self.elements.describe_quest_controls())
				except Exception as exc:
					logger.info("Quest controls in main: unreadable (%s)", type(exc).__name__)

		return completed

	def reach_earn_page(self, marker, timeout: int = 20):
		"""Wait for earn content, with one full navigation if the SPA stays empty.

		After a tab click the page sometimes kept only 4 text buttons instead of
		17. Waiting did not hydrate it, but navigating directly to /earn did.
		"""
		self.switch_to_earn_page()

		try:
			return self.wait_for_element(marker, timeout=timeout)
		except TimeoutException:
			logger.warning("Earn page did not render after the tab click, reloading")
			self.driver.get("https://rewards.bing.com/earn")
			self.tab_utils.ensure_focus()
			return self.wait_for_element(marker, timeout=timeout)

	def read_search_points(self):
		"""Open the points breakdown, read the Bing search row, close it again."""
		# 30s rather than the default 10s: this runs after the earlier tasks have
		# navigated away, so the earn page re-renders from scratch first and the
		# breakdown button regularly needs longer than 10s to appear. Timing out
		# here skipped the entire search task while points were still available.
		breakdown = self.reach_earn_page(self.elements.get_points_breakdown_button, timeout=30)
		self.move_to_and_click(breakdown)

		# Wait for the search row, not for the panel's close button. The close
		# button is incidental to reading the number, and waiting on it first
		# meant a panel that rendered its content but not its button killed the
		# whole search task while the number was already on screen.
		points_earned, max_pts = self.wait_for_element(
			self.elements.get_points_earned_from_searches_on_points_breakdown,
			timeout=30
		)

		# Closing is best effort, the panel does not block the next navigation.
		try:
			self.move_to_and_click(self.elements.get_generic_sidebar_close_button())
		except Exception:
			pass

		return points_earned, max_pts

	def run_search_batch(self, count: int, already_sent: int = 0) -> int:
		"""Search up to count queries and return how many actually went out.

		The trends source can come back with fewer queries than asked for, so
		the caller cannot assume count.
		"""
		self._searches_sent = already_sent
		self.driver.get("https://www.bing.com/")
		self.tab_utils.ensure_focus()

		self.wait_for_element(self.elements.get_bing_search_bar)

		# search bar should be auto-focused

		sent_here = 0

		for i, query in enumerate(
			queries.related_queries(count)
		):
			self.keyboard.send_keys(f"{query} -noai{Keys.ENTER}")
			sent_here = i + 1
			sent = already_sent + sent_here
			self._searches_sent = sent
			self.progress = f"sent {sent} {'search' if sent == 1 else 'searches'}"

			time.sleep(random.uniform(5.5, 7.5))

			try: self.wait_for_then_click(self.elements.get_clear_bing_search_query_button)
			except StaleElementReferenceException:
				logger.warning(
					"StaleElementReferenceException when trying to click the clear button for query %s. Trying again...",
					i + 1
				)
				self.wait_for_then_click(self.elements.get_clear_bing_search_query_button)

		self.driver.get(REWARDS_HOME_URL)
		self.tab_utils.ensure_focus()

		return sent_here

	def restore_main_tab(self):
		"""Close the stray tabs, keeping the one the tasks work in.

		close_all_other_tabs with no arguments keeps whatever tab is focused
		right now. After a task that died on a Bing tab that is the Bing tab, so
		the cleanup closed the Rewards tab and kept the search results. Naming
		the tab to keep is the difference between tidying up and destroying the
		only tab the next task can use.

		If the main tab is gone, whatever is left is better than nothing: the
		page fix below still has to run either way.
		"""
		try:
			handles = self.driver.window_handles

			if not handles:
				return

			keep = self.main_window if self.main_window in handles else handles[0]

			self.tab_utils.close_all_other_tabs(exceptions=[keep])
		except Exception as exc:
			logger.warning(
				"Could not tidy the open tabs: %s", log_utils.exception_summary(exc)
			)

	def return_to_rewards_home(self):
		"""Put the browser back on the Rewards home page.

		Only called when a task did not finish. Navigating after every task
		would reload the page six times a run for no reason, and the tasks that
		succeed already leave the browser somewhere their successor can work
		from.
		"""
		try:
			if self.driver.current_url.startswith(REWARDS_HOME_URL):
				return

			self.driver.get(REWARDS_HOME_URL)
			self.tab_utils.ensure_focus()
		except Exception as exc:
			# Recovery is best effort. If even this fails the next task will
			# report its own [SKIP], which is no worse than before.
			logger.warning(
				"Could not return to the Rewards home page: %s",
				log_utils.exception_summary(exc)
			)

	def claim_bonus_points(self):
		self.switch_to_dashboard()

		# The dashboard can stay unhydrated after its tab click too.
		try:
			self.wait_for_then_click(self.elements.get_bonus_button_on_dashboard, timeout=20)
		except TimeoutException:
			logger.warning("Dashboard did not render after the tab click, reloading")
			self.driver.get("https://rewards.bing.com/dashboard")
			self.tab_utils.ensure_focus()
			self.wait_for_then_click(self.elements.get_bonus_button_on_dashboard, timeout=20)
		self.progress = "opened the bonus panel"

		try:
			self.wait_for_then_click(self.elements.get_claim_bonus_points_button)
		except TimeoutException:
			logger.warning("Could not find the 'Claim Bonus Points' button. There are likely no bonus points to claim at this time.")

	def _balance_host(self):
		try:
			host = urlsplit(self.driver.current_url).hostname
			# Unknown hosts can contain tenant or account names. Keep only the
			# fixed public hosts needed to tell Rewards from a search or sign-in.
			self._balance_last_host = host if host in ("rewards.bing.com", "www.bing.com", "bing.com", "login.live.com", "account.microsoft.com") else "other"
			return self._balance_last_host
		except Exception:
			self._balance_last_host = None
			return None

	def _prepare_balance_page(self, deadline, clock, cleanup_deadline=None):
		if self._balance_host() in (None, "rewards.bing.com"):
			return "not_needed"
		# Each task selects its own earn/dashboard section at entry. Returning
		# an off-site main tab home cannot discard input its successor needs.
		# Charge this navigation to the reading, not another 60s page-load wait.
		previous_timeout = None
		try:
			previous_timeout = self.driver.timeouts.page_load
			# The last slice still needs to restore the driver's page timeout.
			navigation_deadline = min(deadline, cleanup_deadline - BALANCE_RESTORE_RESERVE) if cleanup_deadline is not None else deadline
			remaining = navigation_deadline - clock()
			if remaining <= 0:
				return "timed_out"
			self.driver.set_page_load_timeout(min(previous_timeout, remaining))
			try:
				self.driver.get(REWARDS_HOME_URL)
				return "returned"
			except TimeoutException:
				# A page-load timeout can still leave a readable header.
				return "timed_out"
			finally:
				# Navigation discards the kept document's visibility overrides.
				try:
					with _balance_command_deadline(self.driver, cleanup_deadline or deadline, clock, cleanup=True):
						self.tab_utils.ensure_focus()
				except Exception:
					pass
		except Exception:
			return "failed"
		finally:
			if previous_timeout is not None:
				try:
					with _balance_command_deadline(self.driver, cleanup_deadline or deadline, clock, cleanup=True):
						self.driver.set_page_load_timeout(previous_timeout)
				except Exception:
					pass

	def _read_balance(self, *, timeout=BALANCE_READ_TIMEOUT, prepare=False, clock=time.monotonic, sleep=time.sleep):
		"""Wait for a readable header, without clicking or guessing earned points."""
		self._balance_read_diagnostic = None
		reader = getattr(self, "balance_reader", None)
		if reader is None:
			return None
		began = clock()
		budget = max(0, getattr(self, "_balance_remaining", BALANCE_RUN_BUDGET))
		deadline = began + min(max(0, timeout), budget)
		command_deadline = deadline if timeout > 0 else began + min(BALANCE_READ_TIMEOUT, budget)
		transport_margin = BALANCE_COMMAND_MARGIN if getattr(getattr(self.driver, "command_executor", None), "_client_config", None) is not None else 0
		immediate = getattr(self, "_balance_timed_out", False) or timeout <= 0
		initial_host = getattr(self, "_balance_last_host", None)
		navigation = "not_attempted"
		attempts, reader_error = 0, False
		elements = getattr(self, "elements", None)
		if elements is not None:
			elements.balance_observation = None
			elements.balance_host = None
		try:
			# A cumulative deadline, not eight independent polling deadlines, also
			# bounds URL queries, navigation, focus, observation and timeout restore.
			with _balance_command_deadline(self.driver, command_deadline, clock):
				if budget > 0:
					initial_host = self._balance_host()
					if prepare and not immediate:
						navigation = self._prepare_balance_page(deadline, clock, began + budget)
					while True:
						attempts += 1
						try:
							# The single attempt after a timeout may take command time,
							# but never another polling window or the remaining run budget.
							balance = reader()
							if getattr(elements, "balance_host", None) is not None:
								self._balance_last_host = elements.balance_host
							reader_error = False
							if type(balance) is int and balance >= 0 and clock() <= command_deadline:
								self._balance_timed_out = False
								return balance
						except Exception:
							reader_error = True
						remaining = deadline - clock() - transport_margin
						if immediate or remaining <= 0:
							break
						sleep(min(BALANCE_POLL_INTERVAL, remaining))
		except Exception:
			reader_error = True
		finally:
			self._balance_remaining = max(0, budget - (clock() - began))
		self._balance_timed_out = True
		# The record survives the run; exception text and URLs do not belong in
		# it. Reusing this missing boundary keeps both adjacent tasks honest.
		self._balance_read_diagnostic = {"status": "unreadable", "attempts": attempts,
			"elapsed_ms": round((clock() - began) * 1000), "initial_host": initial_host,
			"host": getattr(self, "_balance_last_host", None), "navigation": navigation, "reader_error": reader_error,
			"wait_mode": "immediate" if immediate else "poll", "budget_remaining_ms": round(self._balance_remaining * 1000),
			"observation": getattr(elements, "balance_observation", None)}
		return None

	def complete_all_tasks(self, *, only=None, clock=time.monotonic, sleep=time.sleep):
		# Each task is run independently. The Rewards UI differs by market and
		# changes between deploys, so a task the current variant does not ship
		# must not take the remaining ones down with it.
		self._balance_remaining = BALANCE_RUN_BUDGET
		self._balance_timed_out = False
		have_before = False
		before, before_diagnostic = None, None
		for task_id, name, method in TASKS:
			# Missing task events already mean not_run to the record readers.
			if only is not None and task_id not in only:
				continue
			if task_id in skipped_tasks():
				logger.info("[SKIP] %s: turned off in the settings", name)
				run_record.emit("task_end", task=task_id, name=name, outcome="off", reason_key="turned_off", detail="turned off in the settings", progress=None, points_before=None, points_after=None, duration_ms=0)
				continue
			if not have_before:
				before = self._read_balance(clock=clock, sleep=sleep)
				before_diagnostic = self._balance_read_diagnostic
			began = clock()
			run_record.emit("task_start", task=task_id, name=name, points_before=before)
			outcome, reason, reason_key = "ok", None, None
			# The tags stay in the message rather than being folded into the
			# level, they are the per-task outcome summary and reading a run
			# means scanning for them.
			completed = False

			# Cleared per task, so how far one task got cannot turn the next
			# one's absence into a failure.
			self.progress = None

			try:
				getattr(self, method)()
				logger.info("[OK] %s", name)
				completed = True
			except Exception as exc:
				tag, reason, reason_key = classify_task_failure(exc, self.progress)
				outcome = "skip" if tag == "SKIP" else "fail"

				logger.log(
					logging.WARNING if tag == "SKIP" else logging.ERROR,
					"[%s] %s: %s", tag, name, reason,
					exc_info=logger.isEnabledFor(logging.DEBUG)
				)
			# Leave a clean tab state behind for the next task. Both halves of
			# this matter, and they are separate failures: the right tab has to
			# survive, and it has to be showing the right page.
			self.restore_main_tab()

			if not completed:
				self.return_to_rewards_home()
			after = self._read_balance(prepare=True, clock=clock, sleep=sleep)
			after_diagnostic = self._balance_read_diagnostic
			diagnostic = {phase: value for phase, value in (("before", before_diagnostic), ("after", after_diagnostic)) if value is not None}
			run_record.emit("task_end", task=task_id, name=name, outcome=outcome, reason_key=reason_key, detail=reason,
				progress=self.progress, points_before=before, points_after=after, duration_ms=round((clock() - began) * 1000),
				**({"balance_read": diagnostic} if diagnostic else {}))
			# Only logs and record writes separate these boundaries, including
			# when a turned-off task lies between them. None is shared too.
			before, before_diagnostic, have_before = after, after_diagnostic, True
