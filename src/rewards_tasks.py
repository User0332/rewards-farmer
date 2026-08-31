import os
import math
import random
import time
from typing import Callable
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.remote.webelement import WebElement
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException, NoSuchElementException
from constants import POINTS_PER_SEARCH, CUSTOM_SEARCH_COUNT, VISUAL_SEARCH_IMAGES_DIR
import tab_utils
import llm_utils
import mouse_trajectory
import mimic_typing
import element_selectors

def get_random_visual_search_image(directory: str = VISUAL_SEARCH_IMAGES_DIR) -> str | None:
	valid_extensions = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
	if not os.path.exists(directory):
		return None
	images = [
		os.path.join(directory, f)
		for f in os.listdir(directory)
		if os.path.splitext(f)[1].lower() in valid_extensions and os.path.isfile(os.path.join(directory, f))
	]
	return os.path.abspath(random.choice(images)) if images else None

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
		dashboard_tab = self.elements.get_dashboard_tab()
		self.move_to_and_click(dashboard_tab)
		time.sleep(random.uniform(0.15, 0.25))
		self.mouse.human_like_click()
		time.sleep(random.uniform(0.5, 1.0))

	def move_to_and_click(self, elem: WebElement):
		self.mouse.move_to_element(elem)
		self.mouse.human_like_click()

	def wait_for_then_click(self, element_getter: Callable[[], WebElement], timeout: int = 10):
		elem = self.wait_for_element(element_getter, timeout)
		self.move_to_and_click(elem)

	def complete_bing_daily_set(self, expected_activities: int = 3):
		self.switch_to_earn_page()

		self.wait_for_then_click(self.elements.get_open_daily_set_button)

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

			print(f"[WARNING] Daily set panel only shows {len(daily_set_links)} of {expected_activities} activities")

		# Re-read the panel per index: clicking an activity can re-render it and
		# stale the captured references.
		for index in range(len(daily_set_links)):
			activities = self.elements.get_daily_set_elements()

			if index >= len(activities):
				break

			self.move_to_and_click(activities[index])
			time.sleep(random.uniform(2, 3))
			self.driver.switch_to.window(self.driver.current_window_handle) # refocus on the main tab

		self.tab_utils.close_all_other_tabs()

	def complete_explore_on_bing_tasks(self):
		self.switch_to_earn_page()

		explore_on_bing_links = self.elements.get_explore_on_bing_elements()

		if not explore_on_bing_links:
			# Raise rather than return, so complete_all_tasks reports this as
			# [SKIP]. Returning quietly made it print [OK] for a task that never
			# ran, which is exactly the kind of false success a scheduled run
			# must not produce.
			raise NoSuchElementException("no Explore on Bing section in this UI variant")

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

		image_path = get_random_visual_search_image()
		if not image_path:
			print(f"[INFO] No valid visual search images (.png/.jpg/.webp) found in '{VISUAL_SEARCH_IMAGES_DIR}'. Skipping visual search...")
			return

		print(f"[INFO] Selected random image for visual search: {os.path.basename(image_path)}")

		try:
			self.wait_for_then_click(self.elements.get_open_visual_search_sidebar, timeout=5)
			self.wait_for_then_click(self.elements.get_search_now_link_from_visual_search_sidebar, timeout=5)

			self.tab_utils.switch_to_other_tab()

			self.wait_for_then_click(self.elements.get_visual_search_button, timeout=5)

			file_input = self.wait_for_element(self.elements.get_visual_search_file_input, timeout=5)

			file_input.send_keys(image_path)

			time.sleep(random.uniform(3, 5))

			self.tab_utils.switch_to_other_tab()
			self.tab_utils.close_all_other_tabs()
		except (TimeoutException, Exception) as e:
			print(f"[INFO] Visual search task skipped: {e}")
			self.tab_utils.close_all_other_tabs()

	def complete_misc_cards(self):
		self.switch_to_earn_page()

		misc_cards: list[WebElement] = self.wait_for_element(self.elements.get_all_misc_cards)

		for card in misc_cards:
			self.mouse.wheel_scroll_element_into_view(card)

			if not self.elements.card_is_complete(card) and self.elements.get_card_point_value(card) > 0:
				self.move_to_and_click(card)
				time.sleep(random.uniform(1, 2))
				self.driver.switch_to.window(self.driver.current_window_handle)

		for card in misc_cards:
			if not self.elements.card_is_complete(card) and self.elements.get_card_point_value(card) > 0:
				print(f"[WARNING] Misc Card [desc={self.elements.extract_card_descriptions(card)!r}] is not complete after clicking. Please check manually.")

		self.tab_utils.close_all_other_tabs()

		self.mouse.wheel_scroll_to_top()

	def complete_required_searches(self, max_rounds: int = 6):
		if CUSTOM_SEARCH_COUNT is not None:
			print(f"[INFO] Performing custom search count: {CUSTOM_SEARCH_COUNT} search(es)...")
			self.run_search_batch(CUSTOM_SEARCH_COUNT)
			return

		# Points per search are not fixed. Some markets award 3 rather than 5,
		# the daily maximum itself changes (observed 15, 30 and 60 on the same
		# account within one day, with the counter resetting), and daily set and
		# card searches count towards the same quota. A single up front division
		# therefore leaves points on the table and still reports success.
		# Measure, search, measure again.
		points_earned, max_pts = self.read_search_points()

		print(f"[INFO] Search points before: {points_earned}/{max_pts}")

		for round_number in range(1, max_rounds + 1):
			if points_earned >= max_pts:
				break

			# Assume the lower known rate so a round never overshoots by much.
			rate = POINTS_PER_SEARCH if POINTS_PER_SEARCH else 3
			searches = max(1, (max_pts - points_earned) // rate)

			self.run_search_batch(searches)

			previous = points_earned
			points_earned, max_pts = self.read_search_points()

			print(f"[INFO] Round {round_number}: {searches} searches -> {points_earned}/{max_pts}")

			if points_earned <= previous:
				print("[WARNING] Round produced no points, stopping instead of searching pointlessly.")
				break

		if points_earned < max_pts:
			print(f"[WARNING] Search quota not filled: {points_earned}/{max_pts}")
		else:
			print(f"Search quota complete: {points_earned}/{max_pts}")

	def read_search_points(self):
		"""Open the points breakdown, read the Bing search row, close it again."""
		self.switch_to_earn_page()

		# 30s rather than the default 10s: this runs after the earlier tasks have
		# navigated away, so the earn page re-renders from scratch first and the
		# breakdown button regularly needs longer than 10s to appear. Timing out
		# here skipped the entire search task while points were still available.
		self.wait_for_then_click(self.elements.get_points_breakdown_button, timeout=30)

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

	def run_search_batch(self, count: int):
		self.driver.get("https://www.bing.com/")
		self.tab_utils.ensure_focus()

		self.wait_for_element(self.elements.get_bing_search_bar)

		# search bar should be auto-focused

		for i, query in enumerate(
			llm_utils.get_related_search_queries(
				llm_utils.get_random_noun(), num_queries=count
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

	def claim_bonus_points(self):
		try:
			self.switch_to_dashboard()
			self.wait_for_then_click(self.elements.get_bonus_button_on_dashboard, timeout=5)
			self.wait_for_then_click(self.elements.get_claim_bonus_points_button, timeout=5)
			print("[INFO] Successfully claimed bonus points!")
		except (TimeoutException, Exception):
			print("[INFO] No bonus points ready to claim at this time.")

	def complete_all_tasks(self):
		# Each task is run independently. The Rewards UI differs by market and
		# changes between deploys, so a task the current variant does not ship
		# must not take the remaining ones down with it.
		steps = (
			("Bing daily set", self.complete_bing_daily_set),
			("Explore on Bing", self.complete_explore_on_bing_tasks),
			("Visual search", self.complete_visual_search),
			("Misc cards", self.complete_misc_cards),
			("Required searches", self.complete_required_searches),
			("Bonus points", self.claim_bonus_points),
		)

		for name, step in steps:
			try:
				step()
				print(f"[OK] {name}")
			except (NoSuchElementException, TimeoutException) as exc:
				print(f"[SKIP] {name}: not available in this UI variant ({type(exc).__name__})")
			except Exception as exc:
				print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")

			# Leave a clean tab state behind for the next task.
			try:
				self.tab_utils.close_all_other_tabs()
			except Exception:
				pass