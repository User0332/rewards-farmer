"""Read Rewards without searching, starting activities or claiming anything.

The caller owns the driver and must serialize access to its profile. Only the
points breakdown and daily set information panels are clicked. Quest pages are
read by navigation; their activity links are never followed. The final page can
be a quest page, so a caller reusing this driver must navigate before farming.

timeout bounds each DOM wait, not the whole snapshot. Navigation uses the
driver's own page-load timeout, which the caller should also bound.
"""

from dataclasses import dataclass
import json
import logging
import re
import time
from urllib.parse import urljoin, urlsplit
from weakref import WeakKeyDictionary

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By

from element_selectors import ElementNotReady
import run_record
import quest_steps

EARN_URL = "https://rewards.bing.com/earn"
DAILY_SET_POINTS = 10
DAILY_SET_ITEMS = 3
logger = logging.getLogger(__name__)
_STREAK_BYTES = 6144
_streak_accounts = WeakKeyDictionary()
_EMAIL = re.compile(r"[^\s<>\"'@]+@[^\s<>\"'@]+")
_STREAK_SNAPSHOT = r"""
const limit = arguments[0], started = performance.now();
const clean = value => String(value || '').replace(/[^\s<>"'@]+@[^\s<>"'@]+/g, '[email]').replace(/\s+/g, ' ').trim();
const visible = element => {
	const style = getComputedStyle(element);
	return element.getClientRects().length > 0 && style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0' && !element.closest('[hidden], [aria-hidden="true"]');
};
const matches = /streak|bonus|puzzle|ready\s+to\s+claim/i;
const packet = {status: 'absent', reason: 'section_absent', sections: [], truncated: false};
const size = () => new TextEncoder().encode(JSON.stringify(packet)).length;
const clip = (value, length) => {
	const text = clean(value);
	if (text.length > length) packet.truncated = true;
	return Array.from(text).slice(0, length).join('');
};
const roots = [...document.querySelectorAll('#streaks')];
for (const section of document.querySelectorAll('section,[role="region"]')) {
	if (!roots.includes(section) && visible(section) && matches.test((section.innerText || '') + ' ' + (section.getAttribute('aria-label') || ''))) roots.push(section);
}
for (const root of roots) {
	if (!visible(root)) continue;
	if (packet.sections.length >= 4 || performance.now() - started > 100) { packet.truncated = true; break; }
	const section = {id: clip(root.id, 96), text: clip(root.innerText, 1024), nodes: []};
	packet.sections.push(section);
	for (const element of [root, ...root.querySelectorAll('*')]) {
		if (section.nodes.length >= 40 || performance.now() - started > 100) { packet.truncated = true; break; }
		if (!visible(element)) continue;
		if (element.getAttribute('role') !== 'progressbar' && element.closest('[role="progressbar"]')) continue;
		const own = [...element.childNodes].filter(node => node.nodeType === 3).map(node => node.textContent).join(' ');
		const aria = element.getAttribute('aria-label') || '';
		if (!clean(own) && !clean(aria) && element.getAttribute('role') !== 'progressbar') continue;
		const data = [...element.attributes].map(attr => attr.name).filter(name => name.startsWith('data-'));
		let depth = 0, parent = element;
		while (parent !== root && parent.parentElement) { depth++; parent = parent.parentElement; }
		if (depth > 20) { packet.truncated = true; continue; }
		const node = {tag: clip(element.tagName.toLowerCase(), 32), role: clip(element.getAttribute('role'), 96),
			aria_label: clip(aria, 240), data_attributes: data.slice(0, 12).map(name => clip(name, 64)),
			classes: [...element.classList].slice(0, 4).map(name => clip(name, 64)), text: clip(own, 240), depth};
		if (node.role === 'progressbar') {
			node.aria_valuenow = clip(element.getAttribute('aria-valuenow'), 96);
			node.aria_valuemax = clip(element.getAttribute('aria-valuemax'), 96);
			node.aria_valuetext = clip(element.getAttribute('aria-valuetext'), 240);
			node.children_count = element.children.length;
		}
		section.nodes.push(node);
		if (size() > limit) { section.nodes.pop(); packet.truncated = true; break; }
	}
	if (size() > limit) { packet.sections.pop(); packet.truncated = true; break; }
}
if (packet.sections.length) {
	packet.status = packet.sections.some(section => section.text || section.nodes.length) ? 'ok' : 'unreadable';
	packet.reason = packet.status === 'ok' ? null : 'section_empty';
}
return packet;
"""


def _streak_capture_once(driver, timeout, page_error=None, *, phase="before"):
	# Diagnostic only: a distinct event cannot alter state, task or ledger math.
	try:
		recorder = run_record.current()
		account = run_record._account.get()
		if phase not in ("before", "after") or recorder is None or account is None or (account, phase) in _streak_accounts.setdefault(recorder, set()):
			return
		_streak_accounts[recorder].add((account, phase))
	except Exception:
		return
	packet = {"status": "unreadable", "reason": page_error or "driver_error", "sections": [], "truncated": False}
	try:
		if page_error is None:
			from rewards_tasks import _balance_command_deadline
			deadline = time.monotonic() + min(3, max(0, timeout))
			with _balance_command_deadline(driver, deadline, time.monotonic):
				while True:
					observed = driver.execute_script(_STREAK_SNAPSHOT, _STREAK_BYTES - 128)
					if not isinstance(observed, dict) or observed.get("status") not in ("ok", "absent", "unreadable"):
						break
					packet = observed
					remaining = deadline - time.monotonic()
					if packet["status"] == "ok" or remaining <= 0:
						break
					time.sleep(min(0.25, remaining))
					if time.monotonic() >= deadline:
						break
	except Exception:
		packet = {"status": "unreadable", "reason": "driver_error", "sections": [], "truncated": False}
	try:
		# Mask again before persistence; even a malformed driver response cannot
		# send an address or exception text into a run record.
		packet["phase"] = phase
		encoded = _EMAIL.sub("[email]", json.dumps(packet, ensure_ascii=False, separators=(",", ":")))
		packet = json.loads(encoded)
		if len(encoded.encode("utf-8")) > _STREAK_BYTES:
			packet = {"phase": phase, "status": "unreadable", "reason": "size_limit", "sections": [], "truncated": True}
		run_record.emit("streak_capture", **packet)
	except Exception:
		try:
			run_record.emit("streak_capture", phase=phase, status="unreadable", reason="capture_error", sections=[], truncated=False)
		except Exception:
			pass


@dataclass(frozen=True)
class RewardsState:
	"""One read, with partial observations retained when a section is unreadable.

	open_points estimates remaining search points plus 10 per daily set item
	and the displayed value of every open, positive-point misc card. Quest
	activities award punches rather than a reliable immediate point value, so
	they add zero. quests_open counts unlocked activities, not punch cards.
	A known unlocked quest still makes a partial state worth running because its
	next punch can be time limited, as in the local status reader.

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
		balance = elements.get_points_balance()
		return balance if type(balance) is int and balance >= 0 else None
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


def read_state(driver, elements, timeout: float = 30, skip_cards: frozenset[str] = frozenset(), *, phase: str = "before") -> RewardsState:
	"""Read every section independently; page failures become unreadable reasons.

	unreadable is a whole-page code (signed_out, terms_of_use, page_blank),
	or section codes in the order searches,daily_set,cards,quests,balance.
	No local log format or outcome file is assumed. A caller may supply today's
	card descriptions to skip from its own portable outcome store. By default
	every open card counts, including cards a previous run could not complete.
	"""
	try:
		_require_earn_page(driver, elements, timeout)
	except _PageStayedBlank as exc:
		_streak_capture_once(driver, timeout, exc.code, phase=phase)
		return RewardsState(unreadable=exc.code)
	except Exception:
		_streak_capture_once(driver, timeout, "page_blank", phase=phase)
		return RewardsState(unreadable="page_blank")
	_streak_capture_once(driver, timeout, phase=phase)

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
					if skip_cards and elements.extract_card_descriptions(card) in skip_cards:
						continue
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
