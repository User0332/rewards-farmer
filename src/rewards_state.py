"""Read Rewards without searching, starting activities or claiming anything.

The caller owns the driver and must serialize access to its profile. Only the
points breakdown and daily set information panels are clicked. Quest pages are
read by navigation; their activity links are never followed. The final page can
be a quest page, so a caller reusing this driver must navigate before farming.

timeout bounds each DOM wait, not the whole snapshot. Navigation uses the
driver's own page-load timeout, which the caller should also bound.
"""

from dataclasses import dataclass
import logging
import re
import time
from urllib.parse import urljoin, urlsplit

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By

from element_selectors import ElementNotReady
import quest_steps

EARN_URL = "https://rewards.bing.com/earn"
DAILY_SET_POINTS = 10
DAILY_SET_ITEMS = 3
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RewardsState:
	"""One read, with partial observations retained when a section is unreadable.

	open_points estimates remaining search points plus 10 per daily set item
	and the displayed value of every open, positive-point misc card. Quest
	activities award punches rather than a reliable immediate point value, so
	they add zero. quests_open counts unlocked activities, not punch cards.
	A known unlocked quest still makes a partial state worth running because its
	next punch can be time limited.

	A zero count with unreadable set is not proof that a section is complete.
	The estimate excludes bonuses, app check-ins, visual search streak bonuses
	and future locked quest rewards. Search and activity rewards can overlap,
	so it is a scheduling estimate, not a promise of that many new points.
	"""

	balance: int | None = None
	searches_earned: int | None = None
	searches_max: int | None = None
	daily_set_open: int = 0
	cards_open: int = 0
	quests_open: int = 0
	unreadable: str | None = None
	open_points: int = 0
	earned_today: int | None = None


def worth_running(state: RewardsState, threshold: int) -> bool | None:
	"""Known open work can justify a run even when some sections are unreadable.

	Whole-page failures stay unknown. A partial read below the threshold, with
	no known unlocked quest, also returns None rather than claiming it is done.
	"""
	if threshold < 0:
		raise ValueError("threshold must not be negative")
	if state.unreadable is not None and any(code in ("signed_out", "terms_of_use", "page_blank") for code in state.unreadable.split(",")):
		return None
	if state.open_points >= threshold or state.quests_open > 0:
		return True
	if state.unreadable is not None:
		return None
	return False


def read_balance(driver, elements) -> int | None:
	"""Read the current document once, without navigating or waiting."""
	try:
		values = set()
		for header in driver.find_elements(By.TAG_NAME, "header"):
			for button in header.find_elements(By.TAG_NAME, "button"):
				if button.is_displayed() and (button.get_dom_attribute("aria-label") or "").lower() == "view profile":
					text = (button.text or "").strip()
					if re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)", text):
						values.add(int(text.replace(",", "")))
		return values.pop() if len(values) == 1 else None
	except Exception:
		return None


def _wait_for(getter, timeout):
	deadline = time.monotonic() + max(0, timeout)
	last_error = None

	while True:
		try:
			value = getter()
			# Zero points and an empty list are valid reads, not a reason to wait.
			if value is not None and value is not False:
				return value
			last_error = None
		except Exception as exc:
			last_error = exc

		remaining = deadline - time.monotonic()
		if remaining <= 0:
			if last_error is not None:
				raise last_error
			raise TimeoutException("section did not finish rendering")

		time.sleep(min(2, remaining))


def _location(driver):
	try:
		url = urlsplit(driver.current_url or "")
		return url.hostname or "", url.path
	except Exception:
		return "", ""


class _PageStayedBlank(Exception):
	"""Neither full navigation produced a usable earn page."""

	def __init__(self, code):
		super().__init__(code)
		self.code = code


def _require_earn_page(driver, elements, timeout):
	# A blank SPA once lasted beyond 60s. A second navigation, not a longer
	# wait on the same document, is the recovery the scheduled reader uses.
	for _ in range(2):
		try:
			driver.get(EARN_URL)
		except TimeoutException:
			# A page-load timeout can leave a usable DOM behind. Inspect it before
			# discarding the attempt, and still retry if it never becomes usable.
			pass
		except Exception as exc:
			# Other navigation failures can leave yesterday's document on screen.
			# Its counters are not evidence that this attempted read succeeded.
			logger.debug("Earn page navigation failed (%s)", type(exc).__name__)
			continue

		def ready():
			host, path = _location(driver)
			if host != "rewards.bing.com" or path.rstrip("/") != "/earn":
				return None
			return elements.get_points_breakdown_button()

		try:
			_wait_for(ready, timeout)
			return
		except Exception:
			continue

	host, path = _location(driver)
	if any(marker in path.lower() for marker in ("terms", "tou", "consent")):
		code = "terms_of_use"
	elif host and host != "rewards.bing.com":
		code = "signed_out"
	else:
		code = "page_blank"

	# Never accept terms or navigate to an activity to get around a redirect.
	raise _PageStayedBlank(code)


def _close_sidebar(driver, elements):
	try:
		button = elements.get_generic_sidebar_close_button()
		# The upstream getter has a first-button fallback. Do not click that
		# fallback if the accessible label does not identify a close control.
		if "close" in (button.get_dom_attribute("aria-label") or "").lower():
			driver.execute_script("arguments[0].click();", button)
		else:
			logger.debug("Sidebar close skipped because the fallback is not a close control")
	except Exception as exc:
		# Every following section gets its own navigation, so a missing close
		# button cannot hide otherwise readable points.
		logger.debug("Sidebar close failed (%s)", type(exc).__name__)


def _search_points(elements):
	lines = [line.strip() for line in (elements.get_sidebar_section().text or "").splitlines()]
	for index, line in enumerate(lines):
		if "bing search" in line.lower() and any(
			re.fullmatch(r"[\d,]+\s*/\s*[\d,]+", candidate)
			for candidate in lines[index + 1:index + 3]
		):
			earned, maximum = elements.get_points_earned_from_searches_on_points_breakdown()
			if isinstance(earned, int) and isinstance(maximum, int) and min(earned, maximum) >= 0:
				return earned, maximum

	# The upstream parser falls back to any fraction. During hydration that
	# can be a different row, so wait for the labelled search value first.
	raise ElementNotReady("Bing search row is not readable yet")


def _earned_today(elements):
	lines = [line.strip() for line in (elements.get_sidebar_section().text or "").splitlines()]
	for index, line in enumerate(lines[:-1]):
		if line.lower() == "today's points":
			value = lines[index + 1]
			if re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)", value):
				return int(value.replace(",", ""))

	# Today's earnings are informational and may be absent in a UI variant.
	return None


def _daily_set_open(elements):
	text = elements.get_sidebar_section().text or ""
	progress = re.search(r"(?im)^\s*activity:\s*(\d+)\s*/\s*(\d+)\s*$", text)
	if not progress:
		raise ElementNotReady("daily set activity progress is not readable yet")

	earned, maximum = map(int, progress.groups())
	# A substring count also matches "Day 2 of 7 streak completed." and used
	# to report four completions out of three activities.
	done = sum(line.strip().lower() == "completed" for line in text.splitlines())
	if maximum != DAILY_SET_ITEMS or done != earned or not 0 <= done <= maximum:
		raise ElementNotReady("daily set completion labels are incomplete")
	if done < maximum and len(elements.get_daily_set_elements()) < maximum:
		raise ElementNotReady("daily set activities are still rendering")

	return maximum - done


def _quest_urls(driver, elements):
	# get_quest_links currently swallows ElementNotReady as [] because it is
	# a NoSuchElementException. A present but empty section is not zero quests.
	sections = driver.find_elements(By.ID, "quests")
	if sections and not any(
		section.is_displayed() and section.find_elements(By.TAG_NAME, "a")
		for section in sections
	):
		raise ElementNotReady("quests section is still rendering")

	urls = []
	for link in elements.get_quest_links():
		url = urljoin(EARN_URL, link.get_dom_attribute("href") or "")
		parsed = urlsplit(url)
		if (
			parsed.scheme == "https" and parsed.netloc.lower() == "rewards.bing.com"
			and parsed.path.startswith("/earn/quest/") and url not in urls
		):
			urls.append(url)

	return urls


def _quest_ready(driver, url):
	host, path = _location(driver)
	if host != "rewards.bing.com" or path != urlsplit(url).path:
		return False
	# Keep the local reader's body-size gate, but also require main content:
	# a rendered footer alone must not turn a blank quest into zero activities.
	body = driver.find_element(By.TAG_NAME, "body")
	main = driver.find_element(By.TAG_NAME, "main")
	return len(body.text or "") > 400 and bool((main.text or "").strip())


def read_state(driver, elements, timeout: float = 30) -> RewardsState:
	"""Read every section independently; page failures become unreadable reasons.

	unreadable is a whole-page code (signed_out, terms_of_use, page_blank),
	or section codes in the order searches,daily_set,cards,quests,balance.
	Every open card counts, including cards a previous run could not complete.
	"""
	try:
		_require_earn_page(driver, elements, timeout)
	except _PageStayedBlank as exc:
		return RewardsState(unreadable=exc.code)
	except Exception:
		return RewardsState(unreadable="page_blank")

	balance = searches_earned = searches_max = earned_today = None
	daily_set_open = cards_open = quests_open = open_points = 0
	unreadable = set()

	try:
		balance = _wait_for(lambda: read_balance(driver, elements), timeout)
	except Exception:
		unreadable.add("balance")

	try:
		driver.execute_script("arguments[0].click();", elements.get_points_breakdown_button())
		searches_earned, searches_max = _wait_for(lambda: _search_points(elements), timeout)
		open_points += max(0, searches_max - searches_earned)
		earned_today = _earned_today(elements)
	except Exception:
		unreadable.add("searches")
	finally:
		_close_sidebar(driver, elements)

	try:
		_require_earn_page(driver, elements, timeout)
		opener = _wait_for(elements.get_open_daily_set_button, timeout)
		driver.execute_script("arguments[0].scrollIntoView({block:'center'});", opener)
		time.sleep(1)
		driver.execute_script("arguments[0].click();", opener)
		daily_set_open = _wait_for(lambda: _daily_set_open(elements), timeout)
		open_points += daily_set_open * DAILY_SET_POINTS
	except _PageStayedBlank as exc:
		unreadable.add(exc.code)
	except Exception:
		unreadable.add("daily_set")
	finally:
		_close_sidebar(driver, elements)

	try:
		_require_earn_page(driver, elements, timeout)
		cards = _wait_for(elements.get_all_misc_cards, timeout)
		for card in cards:
			try:
				if not (card.text or "").strip():
					raise ElementNotReady("card content is still rendering")
				points = elements.get_card_point_value(card)
				if points > 0 and not elements.card_is_complete(card):
					cards_open += 1
					open_points += points
			except Exception:
				unreadable.add("cards")
	except _PageStayedBlank as exc:
		unreadable.add(exc.code)
	except Exception:
		unreadable.add("cards")

	try:
		_require_earn_page(driver, elements, timeout)
		urls = _wait_for(lambda: _quest_urls(driver, elements), timeout)
		clicked = quest_steps.clicked_today()
		not_counted = 0
		for url in urls:
			try:
				try:
					driver.get(url)
				except TimeoutException:
					pass
				_wait_for(lambda: _quest_ready(driver, url), timeout)
				activities = elements.get_quest_activity_links()
				identities = {
					(link.get_dom_attribute("href") or "", (link.text or "").strip())
					for link in activities if link.is_displayed()
				}
				omitted = {(href, text) for href, text in identities if (url, href, text) in clicked}
				quests_open += len(identities - omitted)
				not_counted += len(omitted)
			except Exception:
				unreadable.add("quests")
		if not_counted:
			logger.info("quest steps already clicked today and unchanged: %s (not counted)", not_counted)
	except _PageStayedBlank as exc:
		unreadable.add(exc.code)
	except Exception:
		unreadable.add("quests")

	whole_page = next((code for code in ("terms_of_use", "signed_out", "page_blank") if code in unreadable), None)
	section_codes = ",".join(code for code in ("searches", "daily_set", "cards", "quests", "balance") if code in unreadable)

	return RewardsState(
		balance=balance, searches_earned=searches_earned, searches_max=searches_max,
		daily_set_open=daily_set_open, cards_open=cards_open, quests_open=quests_open,
		unreadable=whole_page or section_codes or None, open_points=open_points,
		earned_today=earned_today,
	)
