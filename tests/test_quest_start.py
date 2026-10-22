"""Quest activation from #121's saved element, with no live account or browser."""

from pathlib import Path
import logging
import sys
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from element_selectors import ElementSelectionUtils
import rewards_tasks
import test_quest_diagnostics as diagnostics

ACTIVATE = (Path(__file__).with_name("fixtures") / "quest_activate_outerhtml.html").read_text(encoding="utf-8").strip()
CARD = "https://rewards.bing.com/earn/quest/WW_pcparent_HalloweenQuestROWOct26_punchcard"
NAV = """<a href="https://rewards.bing.com/dashboard" target="_blank" rel="noopener noreferrer" data-rac data-react-aria-pressable="true" aria-label="Dashboard">Dashboard</a>
<a href="https://rewards.bing.com/dashboard">Back to dashboard</a>
<a href="https://rewards.bing.com/earn/quest/another" target="_blank" rel="noopener noreferrer" data-rac data-react-aria-pressable="true" aria-label="Activate quest, Another card">Activate quest</a>
<a href="https://rewards.bing.com/dashboard" target="_blank" rel="noopener noreferrer" data-rac data-react-aria-pressable="true" aria-label="Terms">Terms</a>
<a href="https://rewards.bing.com/dashboard" target="_blank" rel="noopener noreferrer" data-rac data-react-aria-pressable="true" aria-label="Help">Help</a>"""


def anchors(fragment, url=CARD):
	page = diagnostics.Page(fragment)
	page.current_url = url
	return ElementSelectionUtils(page).get_quest_activity_links()


class QuestStartSelectors(unittest.TestCase):
	def test_exact_outerhtml_is_a_start_step_inside_main_on_quest_page(self):
		links = anchors("<main>" + ACTIVATE + "</main>")
		self.assertEqual(len(links), 1)
		self.assertEqual(links[0].text.strip(), "Activate quest")
		self.assertEqual(links[0].get_dom_attribute("aria-label"), "Activate quest, Your Halloween adventure starts here")
		self.assertEqual(links[0].get_dom_attribute("href"), "https://rewards.bing.com/dashboard?form=ML2YBE&OCID=ML2YBE&PUBL=RewardsDO&CREA=ML2YBE")

	def test_dashboard_back_other_cards_terms_and_help_inside_main_stay_excluded(self):
		self.assertEqual(anchors("<main>" + NAV + "</main>"), [])
		self.assertEqual(len(anchors("<main>" + NAV + ACTIVATE + "</main>")), 1)

	def test_activation_like_control_outside_main_or_off_quest_page_stays_excluded(self):
		for html, url in (("<main></main><footer>" + ACTIVATE + "</footer>", CARD), (ACTIVATE, CARD),
			("<main>" + ACTIVATE + "</main>", "https://rewards.bing.com/dashboard"),
			("<main>" + ACTIVATE + "</main>", "https://rewards.bing.com/earn"),
			("<main>" + ACTIVATE + "</main>", "https://example.test/earn/quest/card")):
			with self.subTest(url=url, html=html[:30]):
				self.assertEqual(anchors(html, url), [])

	def test_disabled_hidden_missing_signature_and_altered_destination_are_not_accepted(self):
		changes = (
			('data-rac=""', ''), ('data-react-aria-pressable="true"', ''), ('target="_blank"', 'target="_self"'),
			('rel="noopener noreferrer"', 'rel="noopener"'), ('aria-label="Activate quest, Your Halloween adventure starts here"', 'aria-label="Activate quest"'),
			('aria-label="Activate quest, Your Halloween adventure starts here"', 'aria-label="Dashboard"'),
			('data-rac=""', 'data-rac="" aria-disabled="true"'), ('data-rac=""', 'data-rac="" disabled'),
			('data-rac=""', 'data-rac="" data-disabled'), ('data-rac=""', 'data-rac="" hidden'),
			('https://rewards.bing.com/dashboard?', 'https://rewards.bing.com/help?'),
			('https://rewards.bing.com/dashboard?', 'https://rewards.bing.com/earn/quest/other?'),
			('https://rewards.bing.com/dashboard?', 'https://rewards.bing.com.example.test/dashboard?'),
			('https://rewards.bing.com/dashboard?', 'https://example.test/rewards.bing.com/dashboard?'),
			('https://rewards.bing.com/dashboard?', 'http://rewards.bing.com/dashboard?'),
			('https://rewards.bing.com/dashboard?', '/dashboard?'),
			('>Activate quest<svg', '>Dashboard<svg'), ('>Activate quest<svg', '>Quest aktivieren<svg'),
		)
		for old, new in changes:
			with self.subTest(old=old, new=new):
				self.assertIn(old, ACTIVATE)
				self.assertEqual(anchors("<main>" + ACTIVATE.replace(old, new) + "</main>"), [])

	def test_activated_card_keeps_bing_search_steps_and_disabled_filter(self):
		page = '<main>' + NAV + '<a href="https://www.bing.com/search?q=one">Start searching</a><a href="https://www.bing.com/images" aria-disabled="true">Locked</a></main>'
		links = anchors(page)
		self.assertEqual([(link.text, link.get_dom_attribute("href")) for link in links], [("Start searching", "https://www.bing.com/search?q=one")])


class QuestStartLoop(unittest.TestCase):
	def scenario(self, *, unlock=True, max_steps=8, tracking_changes=False, activated=False):
		class Page(diagnostics.Page):
			stage = 1 if activated else 0
			def get(self, url):
				super().get(url)
				activation = ACTIVATE.replace("form=ML2YBE", f"form=load{len(self.loads)}") if tracking_changes else ACTIVATE
				search = f'<a href="https://www.bing.com/search?q=quest" aria-disabled="{str(self.stage < 1).lower()}">Start searching</a>'
				images = f'<a href="https://www.bing.com/images?q=quest" aria-disabled="{str(self.stage < 2).lower()}">Search costumes</a>'
				self.root = diagnostics.Page('<main>' + NAV + ("" if activated else activation) + search + images + '</main>').root
		tasks = rewards_tasks.RewardsTaskUtils.__new__(rewards_tasks.RewardsTaskUtils)
		tasks.driver = Page('<main></main>')
		tasks.elements = ElementSelectionUtils(tasks.driver)
		steps, lifecycle, handles = [], [], ["quest"]
		def click(element):
			label = element.text.strip()
			steps.append(label)
			lifecycle.append("click:" + label)
			handles.append("activity")
			if unlock:
				tasks.driver.stage = 1 if label == "Activate quest" else 2
		def close():
			self.assertEqual(handles, ["quest", "activity"])
			handles.remove("activity")
			lifecycle.append("close")
		tasks.move_to_and_click = mock.Mock(side_effect=click)
		tasks.tab_utils = types.SimpleNamespace(ensure_focus=mock.Mock(side_effect=lambda: lifecycle.append("focus")), close_all_other_tabs=mock.Mock(side_effect=close))
		def wait(getter):
			found = getter()
			if not found:
				raise TimeoutException()
			return found
		tasks.wait_for_element = mock.Mock(side_effect=wait)
		with mock.patch.object(rewards_tasks.time, "sleep") as sleep, self.assertLogs(rewards_tasks.logger, level=logging.INFO) as output:
			completed = tasks.complete_single_quest(CARD, max_steps)
		self.assertEqual(handles, ["quest"])
		self.assertEqual(tasks.tab_utils.close_all_other_tabs.call_count, completed)
		self.assertEqual(tasks.tab_utils.ensure_focus.call_count, len(tasks.driver.loads))
		self.assertEqual(sleep.call_count, completed * 2)
		return tasks, completed, steps, lifecycle, [record.getMessage() for record in output.records]

	def test_start_clicked_once_then_newly_unlocked_steps_use_existing_tab_wait_close_reload(self):
		tasks, completed, steps, lifecycle, messages = self.scenario()
		self.assertEqual(completed, 3)
		self.assertEqual(steps, ["Activate quest", "Start searching", "Search costumes"])
		self.assertEqual(len(tasks.driver.loads), 4)
		self.assertEqual(lifecycle, ["focus", "click:Activate quest", "close", "focus", "click:Start searching", "close", "focus", "click:Search costumes", "close", "focus"])
		self.assertEqual(messages, ["Quest activity done: 'Activate quest'", "Quest activity done: 'Start searching'", "Quest activity done: 'Search costumes'", "3 activity/activities completed on WW_pcparent_HalloweenQuestROWOct26_punchcard"])

	def test_start_unlocking_nothing_is_clicked_once_only(self):
		tasks, completed, steps, _, messages = self.scenario(unlock=False)
		self.assertEqual((completed, steps, len(tasks.driver.loads)), (1, ["Activate quest"], 2))
		self.assertEqual(messages, ["Quest activity done: 'Activate quest'", "1 activity/activities completed on WW_pcparent_HalloweenQuestROWOct26_punchcard"])

	def test_tracking_query_changes_do_not_make_the_same_activation_a_new_step(self):
		tasks, completed, steps, _, _ = self.scenario(unlock=False, tracking_changes=True)
		self.assertEqual((completed, steps, len(tasks.driver.loads)), (1, ["Activate quest"], 2))

	def test_start_counts_toward_existing_step_limit(self):
		tasks, completed, steps, _, _ = self.scenario(max_steps=2)
		self.assertEqual((completed, steps, len(tasks.driver.loads)), (2, ["Activate quest", "Start searching"], 2))

	def test_already_activated_card_runs_only_its_existing_bing_steps(self):
		tasks, completed, steps, _, messages = self.scenario(activated=True)
		self.assertEqual((completed, steps, len(tasks.driver.loads)), (2, ["Start searching", "Search costumes"], 3))
		self.assertEqual(messages, ["Quest activity done: 'Start searching'", "Quest activity done: 'Search costumes'", "2 activity/activities completed on WW_pcparent_HalloweenQuestROWOct26_punchcard"])

	def test_only_rewards_navigation_retains_existing_no_click_and_diagnostic_lines(self):
		tasks = rewards_tasks.RewardsTaskUtils.__new__(rewards_tasks.RewardsTaskUtils)
		tasks.driver = diagnostics.Page('<main><a href="https://rewards.bing.com/dashboard">Back to dashboard</a></main>')
		tasks.elements = ElementSelectionUtils(tasks.driver)
		def wait(getter):
			found = getter()
			if not found:
				raise TimeoutException()
			return found
		tasks.wait_for_element = mock.Mock(side_effect=wait)
		tasks.tab_utils = types.SimpleNamespace(ensure_focus=mock.Mock())
		tasks.move_to_and_click = mock.Mock()
		with self.assertLogs(rewards_tasks.logger, level=logging.INFO) as output:
			self.assertEqual(tasks.complete_single_quest(CARD, 8), 0)
		self.assertEqual([record.getMessage() for record in output.records], [
			"Nothing left to click on WW_pcparent_HalloweenQuestROWOct26_punchcard",
			"Quest controls in main: a role=- text='Back to dashboard' href=rewards.bing.com/dashboard aria-disabled=- disabled=- rule=rewards host"])
		tasks.move_to_and_click.assert_not_called()
