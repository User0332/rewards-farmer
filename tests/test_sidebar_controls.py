"""Never substitute a sidebar position for its action label."""
import os
import sys
import unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException
from element_selectors import ElementSelectionUtils
from fakes import FakeDriver, FakeElement


def selectors(buttons):
	panel = FakeElement(attributes={"id": "react-aria-panel"}, children={(By.TAG_NAME, "button"): buttons})
	return ElementSelectionUtils(FakeDriver(children={(By.TAG_NAME, "section"): [panel]}))


class SidebarControls(unittest.TestCase):
	def test_no_claim_never_returns_third_button_or_claim_heading(self):
		for labels in (("Close", "How it works", "Get app"), ("Ready to claim", "Not now", "Other")):
			with self.subTest(labels=labels):
				with self.assertRaises(NoSuchElementException) as caught:
					selectors([FakeElement(text=label) for label in labels]).get_claim_bonus_points_button()
				self.assertIn(labels[2], caught.exception.msg)

	def test_claim_by_exact_normalized_text_or_accessible_label_at_any_position(self):
		for target in (FakeElement(text="  CLAIM\n Bonus Points "), FakeElement(attributes={"aria-label": "Claim"})):
			for order in ([target, FakeElement(text="Ready to claim")], [FakeElement(text="Close"), target]):
				self.assertIs(selectors(order).get_claim_bonus_points_button(), target)

	def test_no_close_never_returns_first_activity(self):
		with self.assertRaises(NoSuchElementException) as caught:
			selectors([FakeElement(text="Claim"), FakeElement(text="Get started")]).get_generic_sidebar_close_button()
		self.assertIn("Claim", caught.exception.msg)

	def test_claim_substring_fallback_after_all_exact_labels(self):
		for attribute in ("text", "aria-label", "title"):
			with self.subTest(attribute=attribute):
				fallback = FakeElement(text="Claim 50 points" if attribute == "text" else "", attributes={} if attribute == "text" else {attribute: "Claim 50 points"})
				heading = FakeElement(text="Ready to claim 50 points")
				self.assertIs(selectors([heading, fallback]).get_claim_bonus_points_button(), fallback)
				exact = FakeElement(text="CLAIM")
				self.assertIs(selectors([fallback, exact]).get_claim_bonus_points_button(), exact)

	def test_ready_to_claim_is_excluded_even_with_a_claim_alias(self):
		for target in (FakeElement(text="Ready to claim"), FakeElement(text="Ready to CLAIM 50 points"),
			FakeElement(text="Claim", attributes={"aria-label": "Ready to claim"})):
			with self.subTest(text=target.text), self.assertRaises(NoSuchElementException):
				selectors([target]).get_claim_bonus_points_button()

	def test_close_by_visible_text_aria_or_title(self):
		for label in ("Close", "Close panel", "Dismiss", "Close rewards details", "Dismiss this pane"):
			for attribute in ("text", "aria-label", "title"):
				with self.subTest(label=label, attribute=attribute):
					target = FakeElement(text=label if attribute == "text" else "", attributes={} if attribute == "text" else {attribute: label})
					self.assertIs(selectors([FakeElement(text="Claim"), target]).get_generic_sidebar_close_button(), target)

	def test_hidden_disabled_and_lookalike_actions_are_not_matches(self):
		for button in (FakeElement(text="Claim", displayed=False), FakeElement(text="Claim", attributes={"aria-disabled": "true"}),
			FakeElement(text="Claim", attributes={"disabled": ""}), FakeElement(text="Ready to claim")):
			with self.subTest(button=button.text), self.assertRaises(NoSuchElementException):
				selectors([button]).get_claim_bonus_points_button()

	def test_close_account_is_not_a_sidebar_dismiss_action(self):
		for label in ("Unknown", "Enclosed", "Dismissal", "Close account", "Close ACCOUNT settings"):
			with self.subTest(label=label), self.assertRaises(NoSuchElementException):
				selectors([FakeElement(text=label)]).get_generic_sidebar_close_button()
		with self.assertRaises(NoSuchElementException):
			selectors([FakeElement(text="Close", attributes={"aria-label": "Close account"})]).get_generic_sidebar_close_button()


if __name__ == "__main__":
	unittest.main()
