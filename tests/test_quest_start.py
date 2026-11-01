"""Quest activation from #121's saved element, without a browser or network."""

from html.parser import HTMLParser
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from selenium.webdriver.common.by import By
from fakes import FakeDriver, FakeElement
import element_selectors
import rewards_tasks

ACTIVATE = Path(__file__).with_name("quest_activate_outerhtml.html").read_text(encoding="utf-8").strip()
CARD = "https://rewards.bing.com/earn/quest/WW_pcparent_HalloweenQuestROWOct26_punchcard"
NAV = """<a href="https://rewards.bing.com/dashboard" target="_blank" rel="noopener noreferrer" data-rac data-react-aria-pressable="true" aria-label="Dashboard">Dashboard</a>
<a href="https://rewards.bing.com/dashboard">Back to dashboard</a>
<a href="https://rewards.bing.com/earn/quest/another" target="_blank" rel="noopener noreferrer" data-rac data-react-aria-pressable="true" aria-label="Activate quest, Another card">Activate quest</a>"""


class QuestMarkup(HTMLParser):
	"""Read the saved anchor's nested text and attributes into upstream's fakes."""
	def __init__(self, fragment):
		super().__init__()
		self.anchors, self.mains = [], []
		self.main = self.anchor = None
		self.feed(fragment)

	def handle_starttag(self, tag, attributes):
		if tag == "main":
			self.main = FakeElement(children={(By.TAG_NAME, "a"): []})
			self.mains.append(self.main)
		elif tag == "a":
			attributes = {name: "" if value is None else value for name, value in attributes}
			self.anchor = FakeElement(attributes=attributes, displayed="hidden" not in attributes)
			self.anchors.append(self.anchor)
			if self.main is not None:
				self.main.children[(By.TAG_NAME, "a")].append(self.anchor)

	def handle_data(self, data):
		if self.anchor is not None:
			self.anchor.text += data

	def handle_endtag(self, tag):
		if tag == "a":
			self.anchor = None
		elif tag == "main":
			self.main = None


class QuestPage(FakeDriver):
	def __init__(self, fragment, url=CARD):
		super().__init__()
		self.current_url, self.loads = url, []
		self.set_markup(fragment)

	def set_markup(self, fragment):
		markup = QuestMarkup(fragment)
		self.children = {(By.TAG_NAME, "main"): markup.mains, (By.TAG_NAME, "a"): markup.anchors}

	def get(self, url):
		self.current_url = url
		self.loads.append(url)


def anchors(fragment, url=CARD):
	return element_selectors.ElementSelectionUtils(QuestPage(fragment, url)).get_quest_activity_links()


class QuestStartSelectors(unittest.TestCase):
	def test_saved_outerhtml_is_an_activation_step_on_a_quest_page(self):
		links = anchors("<main>" + ACTIVATE + "</main>")
		self.assertEqual(len(links), 1)
		self.assertEqual(links[0].text.strip(), "Activate quest")
		self.assertEqual(links[0].get_dom_attribute("aria-label"), "Activate quest, Your Halloween adventure starts here")
		self.assertEqual(links[0].get_dom_attribute("href"), "https://rewards.bing.com/dashboard?form=ML2YBE&OCID=ML2YBE&PUBL=RewardsDO&CREA=ML2YBE")

	def test_dashboard_back_and_other_card_links_stay_excluded(self):
		self.assertEqual(anchors("<main>" + NAV + "</main>"), [])
		self.assertEqual(len(anchors("<main>" + NAV + ACTIVATE + "</main>")), 1)

	def test_activation_outside_main_or_off_a_quest_page_stays_excluded(self):
		for fragment, url in (("<main></main><footer>" + ACTIVATE + "</footer>", CARD), (ACTIVATE, CARD),
			("<main>" + ACTIVATE + "</main>", "https://rewards.bing.com/dashboard"),
			("<main>" + ACTIVATE + "</main>", "https://rewards.bing.com/earn"),
			("<main>" + ACTIVATE + "</main>", "https://example.test/earn/quest/card")):
			with self.subTest(url=url, fragment=fragment[:30]):
				self.assertEqual(anchors(fragment, url), [])

	def test_disabled_hidden_or_changed_signature_is_not_an_activation(self):
		changes = (
			('data-rac=""', ''), ('data-react-aria-pressable="true"', ''), ('target="_blank"', 'target="_self"'),
			('rel="noopener noreferrer"', 'rel="noopener"'),
			('aria-label="Activate quest, Your Halloween adventure starts here"', 'aria-label="Activate quest"'),
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

	def test_already_activated_card_keeps_bing_activities_and_disabled_filter(self):
		fragment = '<main>' + NAV + '<a href="https://www.bing.com/search?q=one">Start searching</a><a href="https://www.bing.com/images" aria-disabled="true">Locked</a></main>'
		self.assertEqual([(link.text, link.get_dom_attribute("href")) for link in anchors(fragment)], [("Start searching", "https://www.bing.com/search?q=one")])


class QuestStartLoop(unittest.TestCase):
	def scenario(self, *, unlock=True, tracking_changes=False, activated=False, max_steps=8, navigation_only=False):
		class Page(QuestPage):
			stage = 1 if activated else 0
			def get(self, url):
				super().get(url)
				activation = ACTIVATE.replace("form=ML2YBE", f"form=load{len(self.loads)}") if tracking_changes else ACTIVATE
				search = f'<a href="https://www.bing.com/search?q=quest" aria-disabled="{str(self.stage < 1).lower()}">Start searching</a>'
				images = f'<a href="https://www.bing.com/images?q=quest" aria-disabled="{str(self.stage < 2).lower()}">Search costumes</a>'
				content = "" if navigation_only else ("" if activated else activation) + search + images
				self.set_markup("<main>" + NAV + content + "</main>")
		tasks = rewards_tasks.RewardsTaskUtils.__new__(rewards_tasks.RewardsTaskUtils)
		tasks.driver = Page("<main></main>")
		tasks.elements = element_selectors.ElementSelectionUtils(tasks.driver)
		steps, lifecycle, handles = [], [], ["quest"]
		def click():
			label = tasks.driver.target.text.strip()
			steps.append(label)
			lifecycle.append("click:" + label)
			handles.append("activity")
			if unlock:
				tasks.driver.stage = 1 if label == "Activate quest" else 2
		def close():
			self.assertEqual(handles, ["quest", "activity"])
			handles.remove("activity")
			lifecycle.append("close")
		tasks.mouse = types.SimpleNamespace(move_to_element=lambda element: setattr(tasks.driver, "target", element), human_like_click=click)
		tasks.tab_utils = types.SimpleNamespace(ensure_focus=mock.Mock(side_effect=lambda: lifecycle.append("focus")), close_all_other_tabs=mock.Mock(side_effect=close))
		wait = tasks.wait_for_element
		tasks.wait_for_element = lambda getter: wait(getter, timeout=0)
		with mock.patch.object(rewards_tasks.time, "sleep") as sleep, self.assertLogs(rewards_tasks.logger, level="INFO") as output:
			completed = tasks.complete_single_quest(CARD, max_steps)
		self.assertEqual(handles, ["quest"])
		self.assertEqual(tasks.tab_utils.close_all_other_tabs.call_count, completed)
		self.assertEqual(tasks.tab_utils.ensure_focus.call_count, len(tasks.driver.loads))
		# WebDriverWait also sleeps while an empty card times out.
		self.assertEqual(sum(call.args != (0.5,) for call in sleep.call_args_list), completed * 2)
		return tasks, completed, steps, lifecycle, [record.getMessage() for record in output.records]

	def test_start_then_unlocked_steps_use_existing_click_wait_close_reload(self):
		tasks, completed, steps, lifecycle, messages = self.scenario()
		self.assertEqual((completed, steps, len(tasks.driver.loads)), (3, ["Activate quest", "Start searching", "Search costumes"], 4))
		self.assertEqual(lifecycle, ["focus", "click:Activate quest", "close", "focus", "click:Start searching", "close", "focus", "click:Search costumes", "close", "focus"])
		self.assertEqual(messages, ["Quest activity done: 'Activate quest'", "Quest activity done: 'Start searching'", "Quest activity done: 'Search costumes'", "3 activity/activities completed on WW_pcparent_HalloweenQuestROWOct26_punchcard"])

	def test_start_unlocking_nothing_is_clicked_only_once(self):
		tasks, completed, steps, _, messages = self.scenario(unlock=False)
		self.assertEqual((completed, steps, len(tasks.driver.loads)), (1, ["Activate quest"], 2))
		self.assertEqual(messages, ["Quest activity done: 'Activate quest'", "1 activity/activities completed on WW_pcparent_HalloweenQuestROWOct26_punchcard"])

	def test_changed_tracking_query_cannot_repeat_activation(self):
		tasks, completed, steps, _, _ = self.scenario(unlock=False, tracking_changes=True)
		self.assertEqual((completed, steps, len(tasks.driver.loads)), (1, ["Activate quest"], 2))

	def test_start_counts_toward_existing_step_limit(self):
		tasks, completed, steps, _, _ = self.scenario(max_steps=2)
		self.assertEqual((completed, steps, len(tasks.driver.loads)), (2, ["Activate quest", "Start searching"], 2))

	def test_already_activated_card_clicks_only_its_bing_steps(self):
		tasks, completed, steps, _, messages = self.scenario(activated=True)
		self.assertEqual((completed, steps, len(tasks.driver.loads)), (2, ["Start searching", "Search costumes"], 3))
		self.assertEqual(messages, ["Quest activity done: 'Start searching'", "Quest activity done: 'Search costumes'", "2 activity/activities completed on WW_pcparent_HalloweenQuestROWOct26_punchcard"])

	def test_navigation_only_card_retains_the_existing_no_click_report(self):
		tasks, completed, steps, lifecycle, messages = self.scenario(navigation_only=True)
		self.assertEqual((completed, steps, len(tasks.driver.loads)), (0, [], 1))
		self.assertEqual(lifecycle, ["focus"])
		self.assertEqual(messages, ["Nothing left to click on WW_pcparent_HalloweenQuestROWOct26_punchcard"])


if __name__ == "__main__":
	unittest.main()
