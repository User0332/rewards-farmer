"""Minimal stand-ins for the selenium objects the selectors read.

Only what the selectors actually call is implemented: text, DOM attributes,
visibility and find_element(s). Enough to drive the parsing and filtering
without a browser, and small enough to stay readable.
"""

from selenium.common.exceptions import NoSuchElementException


class FakeElement:
	def __init__(self, text="", attributes=None, children=None, displayed=True):
		self.text = text
		self.attributes = attributes or {}
		# {(by, selector): [FakeElement, ...]}
		self.children = children or {}
		self.displayed = displayed

	def get_dom_attribute(self, name):
		return self.attributes.get(name)

	def get_property(self, name):
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
