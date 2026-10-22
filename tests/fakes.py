"""Minimal stand-ins for the selenium objects the selectors read.

Only what the selectors actually call is implemented: text, DOM attributes,
visibility and find_element(s). Enough to drive the parsing and filtering
without a browser, and small enough to stay readable.
"""

import re
from urllib.parse import urlsplit

from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException
from selenium.webdriver.common.by import By


class FakeElement:
	def __init__(self, text="", attributes=None, children=None, displayed=True):
		self.text = text
		self.attributes = attributes or {}
		# {(by, selector): [FakeElement, ...]}
		self.children = children or {}
		self.displayed = displayed

	def get_dom_attribute(self, name):
		return self.attributes.get(name)

	def is_displayed(self):
		return self.displayed

	def find_elements(self, by, selector):
		return list(self.children.get((by, selector), []))

	def find_element(self, by, selector):
		found = self.find_elements(by, selector)

		if not found:
			raise NoSuchElementException(f"no element for {by} {selector!r}")

		return found[0]


class FakeDriver(FakeElement):
	"""A driver behaves like an element for the lookups used here."""

	def __init__(self, children=None):
		super().__init__(children=children)

	def execute_script(self, script, *args):
		from element_selectors import _BALANCE_SNAPSHOT
		assert script == _BALANCE_SNAPSHOT
		return balance_snapshot(self)


def balance_snapshot(driver):
	"""The DOM packet a single script returns, with no Selenium element calls."""
	headers = driver.find_elements(By.TAG_NAME, "header")
	buttons = []
	for header in headers:
		for button in header.find_elements(By.TAG_NAME, "button"):
			try:
				if (button.get_dom_attribute("aria-label") or "").strip().lower() == "view profile":
					buttons.append({"text": button.text, "content": button.get_dom_attribute("textContent") or button.text,
						"displayed": button.is_displayed(), "parts": [child.text for child in button.find_elements(By.CSS_SELECTOR, "span")]})
			except StaleElementReferenceException:
				continue
	return safe_balance_packet(buttons, [header.text for header in headers], len(headers), host=urlsplit(getattr(driver, "current_url", "https://rewards.bing.com/")).hostname)


def safe_balance_packet(buttons, header_texts=(), headers=1, size=(1131, 643), host="rewards.bing.com"):
	"""Fake only the browser's DOM snapshot; native JS parity runs separately."""
	from element_selectors import _BALANCE_NUMBER, _BALANCE_PATTERNS, ElementSelectionUtils
	def parse(text, excluded=()):
		values = set()
		for line in text.splitlines():
			for pattern in _BALANCE_PATTERNS:
				match = re.fullmatch(pattern, line, re.IGNORECASE)
				if match:
					value = int(re.sub(r"[,\. \u00a0\u202f]", "", match[1]))
					if value not in excluded:
						values.add(value)
		return values.pop() if len(values) == 1 else None
	labelled = []
	for text in header_texts:
		for match in re.finditer(rf"(?im)^\s*(?:available points|points balance)\s*:?\s*({_BALANCE_NUMBER})(?:\s+points)?\s*$", text):
			labelled.append(parse(match[1]))
	masked = []
	for button in buttons:
		excluded = {int(value) for value in re.findall(r"\blevel\s*(\d+)\b", button["text"] + "\n" + button["content"], re.IGNORECASE)}
		masked.append({"displayed": button["displayed"],
			"text_shape": ElementSelectionUtils._balance_text_shape(button["text"]),
			"content_shape": ElementSelectionUtils._balance_text_shape(button["content"]),
			"text_value": parse(button["text"], excluded), "content_value": parse(button["content"], excluded),
			"part_values": [parse(part, excluded) for part in button["parts"]]})
	return {"headers": headers, "buttons": masked,
		"header_values": labelled, "inner_size": list(size), "host": host if host in ("rewards.bing.com", "www.bing.com", "bing.com", "login.live.com", "account.microsoft.com") else "other"}
