import os
import random
import time
from typing import Callable
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
import tab_utils
import llm_utils
import mouse_trajectory
import mimic_typing
import element_selectors

VISUAL_SEARCH_IMAGE_PATH = os.path.abspath("keypress_times.png")

class RewardsTaskUtils:
	def __init__(self, driver: webdriver.Edge):
		self.driver = driver

		self.driver.get("https://rewards.bing.com/")

		self.tab_utils = tab_utils.TabUtils(driver)
		self.tab_utils.ensure_focus()

		self.mouse = mouse_trajectory.MouseUtils(driver)
		self.keyboard = mimic_typing.KeyboardUtils(driver)
		self.elements = element_selectors.ElementSelectionUtils(driver)

	def find_element(self, xpath: str):
		return self.driver.find_element(By.XPATH, xpath)

	def wait_for_element(self, element_getter: Callable[[], WebElement | list[WebElement]], timeout: int = 10) -> WebElement | list[WebElement]:
		def condition(_: webdriver.Edge):
			try:
				element_or_elements = element_getter()

				return element_or_elements
			except:
				return False

		return WebDriverWait(self.driver, timeout).until(condition)

	def switch_to_earn_page(self):
		self.move_to_and_click(self.elements.get_earn_tab())

	def switch_to_dashboard(self):
		self.move_to_and_click(self.elements.get_dashboard_tab())

	def move_to_and_click(self, elem: WebElement):
		self.mouse.move_to_element(elem)
		self.mouse.human_like_click()

	def wait_for_then_click(self, element_getter: Callable[[], WebElement], timeout: int = 10):
		elem = self.wait_for_element(element_getter, timeout)
		self.move_to_and_click(elem)

	def complete_bing_daily_set(self):
		self.switch_to_earn_page()

		self.wait_for_then_click(self.elements.get_open_daily_set_button)

		daily_set_links = self.wait_for_element(self.elements.get_daily_set_elements)

		self.move_to_and_click(daily_set_links[0])
		time.sleep(random.uniform(2, 3))
		self.driver.switch_to.window(self.driver.current_window_handle) # refocus on the main tab

		self.move_to_and_click(daily_set_links[1])
		time.sleep(random.uniform(2, 3))
		self.driver.switch_to.window(self.driver.current_window_handle)

		self.move_to_and_click(daily_set_links[2])
		time.sleep(random.uniform(2, 3))
		self.driver.switch_to.window(self.driver.current_window_handle)

		self.tab_utils.close_all_other_tabs()

	def complete_explore_on_bing_tasks(self):
		self.switch_to_earn_page()

		explore_on_bing_links = self.wait_for_element(self.elements.get_explore_on_bing_elements)

		for card in explore_on_bing_links:
			desc = self.elements.extract_card_descriptions(card)
			query = llm_utils.get_search_query_from_task_description(desc)

			self.move_to_and_click(card)
			self.tab_utils.switch_to_other_tab()

			self.wait_for_element(self.elements.get_bing_search_bar)

			# search bar should be auto-focused

			self.keyboard.send_keys(f"{query} -noai{Keys.ENTER}")

			time.sleep(random.uniform(2, 3))

			self.tab_utils.switch_to_other_tab()
			self.tab_utils.close_all_other_tabs()

		time.sleep(random.uniform(1, 2)) # allow card statuses to update

		for card in explore_on_bing_links:
			if not self.elements.card_is_complete(card):
				print(f"[WARNING] Explore on Bing Card [desc={self.elements.extract_card_descriptions(card)!r}] is not complete after searching. Please check manually.")

	def complete_visual_search(self):
		self.switch_to_earn_page()

		self.wait_for_then_click(self.elements.get_open_visual_search_sidebar)

		self.wait_for_then_click(self.elements.get_search_now_link_from_visual_search_sidebar)

		self.tab_utils.switch_to_other_tab()

		self.wait_for_then_click(self.elements.get_visual_search_button)

		file_input = self.wait_for_element(self.elements.get_visual_search_file_input)

		file_input.send_keys(VISUAL_SEARCH_IMAGE_PATH)

		time.sleep(random.uniform(3, 5))

		self.tab_utils.switch_to_other_tab()
		self.tab_utils.close_all_other_tabs()

	def complete_misc_cards(self):
		self.switch_to_earn_page()

		misc_cards: list[WebElement] = self.wait_for_element(self.elements.get_all_misc_cards)

		scroll_times = 0

		for card in misc_cards:
			while not self.elements.element_is_fully_in_viewport(card): # this should work for top-down iteration
				ActionChains(self.driver).scroll_by_amount(0, 100).perform()
				scroll_times+=1

			if not self.elements.card_is_complete(card) and self.elements.get_card_point_value(card) > 0:
				self.move_to_and_click(card)
				time.sleep(random.uniform(1, 2))
				self.driver.switch_to.window(self.driver.current_window_handle)

		for card in misc_cards:
			if not self.elements.card_is_complete(card) and self.elements.get_card_point_value(card) > 0:
				print(f"[WARNING] Misc Card [desc={self.elements.extract_card_descriptions(card)!r}] is not complete after clicking. Please check manually.")

		self.tab_utils.close_all_other_tabs()

		for i in range(scroll_times):
			ActionChains(self.driver).scroll_by_amount(0, -100).perform() # scroll back to top of page

	def complete_required_searches(self):
		self.switch_to_earn_page()
		self.wait_for_then_click(self.elements.get_points_breakdown_button)
		self.wait_for_element(self.elements.get_close_button_on_points_breakdown) # make sure sidebar loads

		points_earned, max_pts = self.elements.get_points_earned_from_searches_on_points_breakdown()
		searches_needed = (max_pts - points_earned) // 5

		self.driver.get("https://www.bing.com/")
		self.tab_utils.ensure_focus()

		self.wait_for_element(self.elements.get_bing_search_bar)

		# search bar should be auto-focused

		for i, query in enumerate(
			llm_utils.get_related_search_queries(
				llm_utils.get_random_noun(), num_queries=searches_needed
			)
		):
			self.keyboard.send_keys(f"{query} -noai{Keys.ENTER}")

			time.sleep(random.uniform(0.5, 1))

			try: self.wait_for_then_click(self.elements.get_clear_bing_search_query_button)
			except StaleElementReferenceException:
				print(f"[WARNING] StaleElementReferenceException when trying to click the clear button for query {i+1}. Trying again...")
				self.wait_for_then_click(self.elements.get_clear_bing_search_query_button)

		self.driver.get("https://rewards.bing.com/")
		self.tab_utils.ensure_focus()

		self.switch_to_earn_page()

		self.wait_for_then_click(self.elements.get_points_breakdown_button)

		close_btn = self.wait_for_element(self.elements.get_close_button_on_points_breakdown)

		points_earned, max_pts = self.elements.get_points_earned_from_searches_on_points_breakdown()

		self.move_to_and_click(close_btn)

		print(f"Points earned from {searches_needed} searches: {points_earned}/{max_pts}")

	def claim_bonus_points(self):
		self.switch_to_dashboard()

		self.wait_for_then_click(self.elements.get_bonus_button_on_dashboard)

		try:
			self.wait_for_then_click(self.elements.get_claim_bonus_points_button)
		except TimeoutException:
			print("[WARNING] Could not find the 'Claim Bonus Points' button. There are likely no bonus points to claim at this time.")

	def complete_all_tasks(self):
		self.complete_bing_daily_set()
		self.complete_explore_on_bing_tasks()
		self.complete_visual_search()
		self.complete_misc_cards()
		self.complete_required_searches()
		self.claim_bonus_points()