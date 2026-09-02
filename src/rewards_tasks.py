import os
import random
import time
from pathlib import Path
from typing import Callable, Dict, List, Tuple
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import (
	StaleElementReferenceException,
	TimeoutException,
	NoSuchElementException,
	WebDriverException,
)
import tab_utils
import llm_utils
import mouse_trajectory
import mimic_typing
import element_selectors
import app_rewards
from constants import (
	REWARDS_EARN_URL,
	REWARDS_DASHBOARD_URL,
	BING_BASE_URL,
	IDLE_DELAY_MIN,
	IDLE_DELAY_MAX,
	TASK_PAUSE_MIN,
	TASK_PAUSE_MAX,
	VISUAL_SEARCH_IMAGE_PATH,
	COOLDOWN_BATCH_SIZE,
	COOLDOWN_SEARCH_DELAY_MIN,
	COOLDOWN_SEARCH_DELAY_MAX,
	COOLDOWN_SLEEP_MIN,
	COOLDOWN_SLEEP_MAX,
	MAX_COOLDOWN_ROUNDS,
	MAX_TOTAL_RUNTIME_SECONDS,
)


def ensure_visual_search_image() -> str:
	"""Ensure a valid JPEG image file exists for visual search, generating one if missing."""
	path = Path(VISUAL_SEARCH_IMAGE_PATH)
	if not path.exists():
		try:
			from PIL import Image
			img = Image.new("RGB", (200, 200), color=(64, 128, 192))
			img.save(str(path), format="JPEG")
			print(f"[INFO] Auto-generated visual search image at: {path.name}")
		except Exception:
			try:
				# Minimal valid 1x1 JPEG bytes fallback
				minimal_jpg = bytes([
					0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00, 0x01,
					0x01, 0x01, 0x00, 0x48, 0x00, 0x48, 0x00, 0x00, 0xFF, 0xDB, 0x00, 0x43,
					0x00, 0x08, 0x06, 0x06, 0x07, 0x06, 0x05, 0x08, 0x07, 0x07, 0x07, 0x09,
					0x09, 0x08, 0x0A, 0x0C, 0x14, 0x0D, 0x0C, 0x0B, 0x0B, 0x0C, 0x19, 0x12,
					0x13, 0x0F, 0x14, 0x1D, 0x1A, 0x1F, 0x1E, 0x1D, 0x1A, 0x1C, 0x1C, 0x20,
					0x24, 0x2E, 0x27, 0x20, 0x22, 0x2C, 0x23, 0x1C, 0x1C, 0x28, 0x37, 0x29,
					0x2C, 0x30, 0x31, 0x34, 0x34, 0x34, 0x1F, 0x27, 0x39, 0x3D, 0x38, 0x32,
					0x3C, 0x2E, 0x33, 0x34, 0x32, 0xFF, 0xC0, 0x00, 0x0B, 0x08, 0x00, 0x01,
					0x00, 0x01, 0x01, 0x01, 0x11, 0x00, 0xFF, 0xC4, 0x00, 0x1F, 0x00, 0x00,
					0x01, 0x05, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00,
					0x00, 0x00, 0x00, 0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08,
					0x09, 0x0A, 0x0B, 0xFF, 0xDA, 0x00, 0x08, 0x01, 0x01, 0x00, 0x00, 0x3F,
					0x00, 0xBF, 0x80, 0xFF, 0xD9
				])
				path.write_bytes(minimal_jpg)
				print(f"[INFO] Created fallback 1x1 JPEG at: {path.name}")
			except Exception as exc2:
				print(f"[WARNING] Could not create fallback visual search image: {exc2}")
	return str(path.resolve())


def human_idle_delay(min_s: float = IDLE_DELAY_MIN, max_s: float = IDLE_DELAY_MAX, reason: str = ""):
	"""Simulate human reading/idle delay with slight randomized variance."""
	delay = random.uniform(min_s, max_s)
	if reason:
		print(f"[INFO] Human pause ({delay:.1f}s) - {reason}")
	time.sleep(delay)


class RewardsTaskUtils:
	def __init__(
		self,
		driver: webdriver.Edge,
		debug_cursor: bool = False,
		enable_cooldown: bool = True,
		enable_mobile: bool = True
	):
		self.driver = driver
		self.debug_cursor = debug_cursor
		self.enable_cooldown = enable_cooldown
		self.enable_mobile = enable_mobile

		self.driver.get(REWARDS_EARN_URL)
		element_selectors.dismiss_cookie_and_consent_banners(self.driver)

		self.tab_utils = tab_utils.TabUtils(driver)
		self.tab_utils.ensure_focus()

		self.mouse = mouse_trajectory.MouseUtils(driver, debug_cursor=debug_cursor)
		self.keyboard = mimic_typing.KeyboardUtils(driver)
		self.elements = element_selectors.ElementSelectionUtils(driver)

	def find_element(self, xpath: str) -> WebElement:
		return self.driver.find_element(By.XPATH, xpath)

	def wait_for_element(self, element_getter: Callable[[], WebElement | list[WebElement]], timeout: int = 15) -> WebElement | list[WebElement]:
		def condition(_: webdriver.Edge):
			try:
				element_or_elements = element_getter()
				return element_or_elements
			except (NoSuchElementException, StaleElementReferenceException):
				return False
			except Exception:
				return False

		return WebDriverWait(self.driver, timeout).until(condition)

	def switch_to_earn_page(self, refresh_wait: float = 0.0):
		element_selectors.dismiss_cookie_and_consent_banners(self.driver)
		curr = self.driver.current_url or ""
		if "rewards.bing.com/earn" not in curr:
			self.driver.get(REWARDS_EARN_URL)
			self.tab_utils.ensure_focus()
		else:
			try:
				earn_tab = self.elements.get_earn_tab()
				self.move_to_and_click(earn_tab)
			except Exception:
				self.driver.get(REWARDS_EARN_URL)
				self.tab_utils.ensure_focus()

		if refresh_wait > 0:
			time.sleep(refresh_wait)
			try:
				self.driver.refresh()
				time.sleep(2.5)
			except Exception:
				pass

		element_selectors.dismiss_cookie_and_consent_banners(self.driver)

	def switch_to_dashboard(self):
		element_selectors.dismiss_cookie_and_consent_banners(self.driver)
		try:
			dash_tab = self.elements.get_dashboard_tab()
			self.move_to_and_click(dash_tab)
		except Exception:
			self.driver.get(REWARDS_DASHBOARD_URL)
			self.tab_utils.ensure_focus()
		element_selectors.dismiss_cookie_and_consent_banners(self.driver)

	def move_to_and_click(self, elem: WebElement):
		self.mouse.move_to_element(elem)
		time.sleep(random.uniform(0.1, 0.3))
		self.mouse.human_like_click()

	def wait_for_then_click(self, element_getter: Callable[[], WebElement], timeout: int = 15):
		elem = self.wait_for_element(element_getter, timeout)
		self.move_to_and_click(elem)

	# ------------------------------------------------------------------
	# Interactive Quiz / Poll Answering
	# ------------------------------------------------------------------

	def attempt_solve_quiz_or_poll(self, max_attempts: int = 6):
		"""Detect and complete interactive Daily Set activities (Supersonic, This or That, Daily Poll)."""
		quiz_selectors = [
			# Poll choices
			".btOption",
			"[id*='btoption' i]",
			"[role='radio']",
			# Supersonic / Lightspeed quiz choices
			".rqOption",
			"[role='button'][id*='choice' i]",
			"[class*='rqOption' i]",
			"input[type='button'][value*='choice' i]",
			"div[id*='QuestionPane' i] [role='button']",
			# This or That cards
			"div[id*='choice' i] img",
			"div[id*='choice' i]",
		]

		for attempt in range(max_attempts):
			element_selectors.dismiss_cookie_and_consent_banners(self.driver)
			options = []
			for selector in quiz_selectors:
				try:
					found = self.driver.find_elements(By.CSS_SELECTOR, selector)
					visible = [el for el in found if el.is_displayed()]
					if visible:
						options = visible
						break
				except Exception:
					continue

			if not options:
				break

			target = random.choice(options)
			try:
				self.move_to_and_click(target)
				print(f"[INFO] Daily set interactive click ({attempt + 1}/{max_attempts})")
			except Exception:
				try:
					self.driver.execute_script("arguments[0].click();", target)
				except Exception:
					break

			time.sleep(random.uniform(2.0, 3.5))

			# Check if there is a 'Next Question' or 'Continue' button
			next_btns = self.driver.find_elements(
				By.CSS_SELECTOR,
				"input[type='submit'][value*='Next' i], button[id*='next' i], .rqNext, input[value*='Fortsätt' i], input[value*='Nästa' i]"
			)
			for nb in next_btns:
				if nb.is_displayed():
					try:
						self.move_to_and_click(nb)
						time.sleep(random.uniform(1.5, 2.5))
					except Exception:
						pass
					break

	# ------------------------------------------------------------------
	# Task: Bing Daily Set
	# ------------------------------------------------------------------

	def complete_bing_daily_set(self, expected_activities: int = 3):
		self.switch_to_earn_page()
		self.wait_for_then_click(self.elements.get_open_daily_set_button)

		def full_activity_list():
			activities = self.elements.get_daily_set_elements()
			return activities if len(activities) >= expected_activities else False

		try:
			daily_set_links = self.wait_for_element(full_activity_list, timeout=25)
		except TimeoutException:
			daily_set_links = self.elements.get_daily_set_elements()
			print(f"[WARNING] Daily set panel only shows {len(daily_set_links)} of {expected_activities} activities")

		for index in range(len(daily_set_links)):
			activities = self.elements.get_daily_set_elements()
			if index >= len(activities):
				break

			self.move_to_and_click(activities[index])
			time.sleep(random.uniform(2.0, 3.0))

			# Switch to opened activity tab and dismiss cookies
			self.tab_utils.switch_to_other_tab()
			element_selectors.dismiss_cookie_and_consent_banners(self.driver)

			# Attempt quiz or poll completion if applicable
			self.attempt_solve_quiz_or_poll()

			time.sleep(random.uniform(1.5, 2.5))
			self.tab_utils.close_all_other_tabs()

		self.tab_utils.close_all_other_tabs()

	# ------------------------------------------------------------------
	# Task: Explore on Bing
	# ------------------------------------------------------------------

	def complete_explore_on_bing_tasks(self):
		self.switch_to_earn_page()
		explore_on_bing_links = self.elements.get_explore_on_bing_elements()

		if not explore_on_bing_links:
			raise NoSuchElementException("no Explore on Bing section in this UI variant")

		for card in explore_on_bing_links:
			desc = self.elements.extract_card_descriptions(card)
			query = llm_utils.get_search_query_from_task_description(desc)

			self.move_to_and_click(card)
			self.tab_utils.switch_to_other_tab()
			element_selectors.dismiss_cookie_and_consent_banners(self.driver)

			self.wait_for_element(self.elements.get_bing_search_bar)
			# Natural human search query WITHOUT static -noai suffix
			self.keyboard.send_keys(f"{query}{Keys.ENTER}")

			# Simulate reading and browsing result
			time.sleep(random.uniform(2.0, 3.5))
			self.mouse.realistic_browse_scroll(min_scrolls=1, max_scrolls=2)

			self.tab_utils.switch_to_other_tab()
			self.tab_utils.close_all_other_tabs()

		time.sleep(random.uniform(1.5, 2.5))

		for card in explore_on_bing_links:
			if not self.elements.card_is_complete(card):
				print(f"[WARNING] Explore on Bing Card [desc={self.elements.extract_card_descriptions(card)!r}] not complete after searching.")

	# ------------------------------------------------------------------
	# Task: Visual Search
	# ------------------------------------------------------------------

	def complete_visual_search(self):
		self.switch_to_earn_page()
		try:
			btn = self.elements.get_open_visual_search_sidebar()
		except NoSuchElementException:
			raise NoSuchElementException("Visual Search streak is not in today's active streak set")

		self.move_to_and_click(btn)
		self.wait_for_then_click(self.elements.get_search_now_link_from_visual_search_sidebar)

		self.tab_utils.switch_to_other_tab()
		element_selectors.dismiss_cookie_and_consent_banners(self.driver)

		self.wait_for_then_click(self.elements.get_visual_search_button)
		file_input = self.wait_for_element(self.elements.get_visual_search_file_input)
		
		# Ensure image exists before sending keys
		valid_img_path = ensure_visual_search_image()
		file_input.send_keys(valid_img_path)

		time.sleep(random.uniform(3.5, 5.5))
		self.tab_utils.switch_to_other_tab()
		self.tab_utils.close_all_other_tabs()

	# ------------------------------------------------------------------
	# Task: Misc Cards & Activities
	# ------------------------------------------------------------------

	def complete_misc_cards(self):
		self.switch_to_earn_page()
		try:
			misc_cards = self.elements.get_all_misc_cards()
		except Exception:
			misc_cards = []

		if not misc_cards:
			print("[INFO] No extra activity cards found in this UI variant.")
			return

		uncompleted = [
			card for card in misc_cards
			if not self.elements.card_is_complete(card) and self.elements.get_card_point_value(card) > 0
		]

		if not uncompleted:
			print("[INFO] All misc activity cards are already completed.")
			return

		for card in uncompleted:
			self.mouse.wheel_scroll_element_into_view(card)

			if not self.elements.card_is_complete(card) and self.elements.get_card_point_value(card) > 0:
				self.move_to_and_click(card)
				time.sleep(random.uniform(1.8, 3.2))
				self.driver.switch_to.window(self.driver.current_window_handle)

		for card in uncompleted:
			if not self.elements.card_is_complete(card) and self.elements.get_card_point_value(card) > 0:
				print(f"[WARNING] Misc Card [desc={self.elements.extract_card_descriptions(card)!r}] not complete after clicking.")

		self.tab_utils.close_all_other_tabs()
		self.mouse.wheel_scroll_to_top()


	# ------------------------------------------------------------------
	# Task: Required Daily Searches with Adaptive 15-Minute Cooldown Loop
	# ------------------------------------------------------------------

	def complete_required_searches(self, max_rounds: int = MAX_COOLDOWN_ROUNDS):
		start_timestamp = time.time()
		points_earned, max_pts = self.read_search_points()
		print(f"[INFO] Search points before: {points_earned}/{max_pts}")

		if points_earned >= max_pts:
			print(f"[OK] Search quota complete: {points_earned}/{max_pts}")
			return

		round_number = 0
		while points_earned < max_pts and round_number < max_rounds:
			round_number += 1
			elapsed_s = time.time() - start_timestamp
			if elapsed_s > MAX_TOTAL_RUNTIME_SECONDS:
				print(f"[WARNING] Safety limit reached ({elapsed_s / 60:.1f}m runtime). Ending search loop.")
				break

			remaining = max_pts - points_earned
			needed_searches = max(1, remaining // 3)
			batch_count = min(COOLDOWN_BATCH_SIZE, needed_searches)

			print(f"\n[INFO] --- Search Batch {round_number}/{max_rounds} (Target: {batch_count} searches) ---")
			self.run_search_batch(batch_count)

			# Give Bing's points telemetry a moment to settle
			time.sleep(random.uniform(4.0, 6.0))

			previous = points_earned
			points_earned, max_pts = self.read_search_points()
			diff = points_earned - previous
			print(f"[INFO] Batch {round_number} complete -> {points_earned}/{max_pts} (gained +{diff} pts)")

			if points_earned >= max_pts:
				break

			# If more points are needed, execute cooldown pause to bypass 15-minute search throttling
			if self.enable_cooldown and round_number < max_rounds:
				cooldown_sec = random.uniform(COOLDOWN_SLEEP_MIN, COOLDOWN_SLEEP_MAX)
				cooldown_min = cooldown_sec / 60.0
				print(f"[INFO] Cooldown pause ({cooldown_min:.1f}m) until next search batch ({points_earned}/{max_pts} pts)...")
				time.sleep(cooldown_sec)
			else:
				time.sleep(random.uniform(6.0, 12.0))

		if points_earned < max_pts:
			print(f"[WARNING] Search quota incomplete: {points_earned}/{max_pts}")
		else:
			print(f"[OK] Search quota complete: {points_earned}/{max_pts}")

	def read_search_points(self, search_type: str = "pc") -> Tuple[int, int]:
		"""Open points breakdown sidebar, read Bing search fraction, close panel."""
		self.switch_to_earn_page()
		self.wait_for_then_click(self.elements.get_points_breakdown_button, timeout=30)

		close_btn = self.wait_for_element(self.elements.get_close_button_on_points_breakdown, timeout=15)
		points_earned, max_pts = self.elements.get_points_earned_from_searches_on_points_breakdown(search_type=search_type)

		try:
			self.move_to_and_click(close_btn)
		except Exception:
			pass

		return points_earned, max_pts

	def enable_mobile_emulation(self):
		"""Dynamically switch browser session to Edge Mobile view and UA via CDP."""
		from constants import MOBILE_UA
		try:
			self.driver.execute_cdp_cmd("Network.setUserAgentOverride", {
				"userAgent": MOBILE_UA,
				"platform": "iPhone"
			})
			self.driver.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {
				"width": 393,
				"height": 852,
				"deviceScaleFactor": 3,
				"mobile": True
			})
			self.driver.execute_cdp_cmd("Emulation.setTouchEmulationEnabled", {
				"enabled": True,
				"maxTouchPoints": 5
			})
			print("[INFO] Enabled Edge Mobile device emulation via CDP.")
		except Exception as exc:
			print(f"[WARNING] Could not enable mobile CDP emulation: {exc}")

	def disable_mobile_emulation(self):
		"""Restore desktop browser session and UA via CDP."""
		from constants import WINDOWS_DESKTOP_UA
		try:
			self.driver.execute_cdp_cmd("Network.setUserAgentOverride", {
				"userAgent": WINDOWS_DESKTOP_UA,
				"platform": "Win32"
			})
			self.driver.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {
				"width": 1366,
				"height": 768,
				"deviceScaleFactor": 1,
				"mobile": False
			})
			self.driver.execute_cdp_cmd("Emulation.setTouchEmulationEnabled", {
				"enabled": False
			})
			print("[INFO] Restored Desktop emulation via CDP.")
		except Exception as exc:
			print(f"[WARNING] Could not restore desktop CDP emulation: {exc}")

	def complete_mobile_searches(self, max_rounds: int = MAX_COOLDOWN_ROUNDS):
		"""Farm mobile search points using Edge Mobile device emulation."""
		print("\n[INFO] --- Checking Mobile Rewards Quota ---")
		try:
			points_earned, max_pts = self.read_search_points(search_type="mobile")
		except NoSuchElementException:
			print("[SKIP] Mobile searches: mobile search quota not unlocked on this account level (Level 2 required).")
			return
		except Exception as exc:
			print(f"[SKIP] Mobile searches: could not read mobile quota ({exc})")
			return

		print(f"[INFO] Mobile search points before: {points_earned}/{max_pts}")

		if points_earned >= max_pts:
			print(f"[OK] Mobile search quota complete: {points_earned}/{max_pts}")
			return

		self.enable_mobile_emulation()
		try:
			time.sleep(1.5)

			start_timestamp = time.time()
			round_number = 0
			while points_earned < max_pts and round_number < max_rounds:
				round_number += 1
				elapsed_s = time.time() - start_timestamp
				if elapsed_s > MAX_TOTAL_RUNTIME_SECONDS:
					print(f"[WARNING] Safety limit reached during mobile searches ({elapsed_s / 60:.1f}m).")
					break

				remaining = max_pts - points_earned
				needed_searches = max(1, remaining // 3)
				batch_count = min(COOLDOWN_BATCH_SIZE, needed_searches)

				print(f"\n[INFO] --- Mobile Search Batch {round_number}/{max_rounds} (Target: {batch_count} searches) ---")
				self.run_search_batch(batch_count)

				time.sleep(random.uniform(4.0, 6.0))
				previous = points_earned
				points_earned, max_pts = self.read_search_points(search_type="mobile")
				diff = points_earned - previous
				print(f"[INFO] Mobile Batch {round_number} complete -> {points_earned}/{max_pts} (gained +{diff} pts)")

				if points_earned >= max_pts:
					break

				if self.enable_cooldown and round_number < max_rounds:
					cooldown_sec = random.uniform(COOLDOWN_SLEEP_MIN, COOLDOWN_SLEEP_MAX)
					cooldown_min = cooldown_sec / 60.0
					print(f"[INFO] Cooldown pause ({cooldown_min:.1f}m) until next mobile search batch ({points_earned}/{max_pts} pts)...")
					time.sleep(cooldown_sec)
				else:
					time.sleep(random.uniform(6.0, 12.0))

			if points_earned < max_pts:
				print(f"[WARNING] Mobile search quota incomplete: {points_earned}/{max_pts}")
			else:
				print(f"[OK] Mobile search quota complete: {points_earned}/{max_pts}")
		finally:
			self.disable_mobile_emulation()

	def run_search_batch(self, count: int):
		self.driver.get(BING_BASE_URL)
		self.tab_utils.ensure_focus()
		element_selectors.dismiss_cookie_and_consent_banners(self.driver)

		self.wait_for_element(self.elements.get_bing_search_bar)

		seed_word = llm_utils.get_random_noun()
		queries = list(llm_utils.get_related_search_queries(seed_word, num_queries=count))

		for i, query in enumerate(queries, start=1):
			print(f"[INFO] Search [{i}/{len(queries)}]: '{query}'")
			# Natural human search query WITHOUT static -noai suffix
			self.keyboard.send_keys(f"{query}{Keys.ENTER}")

			# Natural pause and realistic viewport browsing scroll
			time.sleep(random.uniform(2.5, 4.0))
			self.mouse.realistic_browse_scroll(min_scrolls=1, max_scrolls=2)

			# 20% chance of clicking an organic search result (CTR simulation)
			if random.random() < 0.20:
				try:
					results = self.driver.find_elements(By.CSS_SELECTOR, "li.b_algo h2 a, #b_results h2 a")
					visible_results = [r for r in results if r.is_displayed()]
					if visible_results:
						target_link = random.choice(visible_results[:3])
						self.mouse.move_to_element(target_link, visualize=self.debug_cursor)
						time.sleep(random.uniform(0.2, 0.4))
						target_link.click()
						# Read / browse page for 4 to 6.5 seconds
						time.sleep(random.uniform(4.0, 6.5))
						self.mouse.realistic_browse_scroll(min_scrolls=1, max_scrolls=2)
						self.driver.back()
						self.tab_utils.ensure_focus()
						time.sleep(random.uniform(1.5, 2.5))
				except Exception:
					pass

			# Clear search input for next query
			try:
				self.wait_for_then_click(self.elements.get_clear_bing_search_query_button)
			except StaleElementReferenceException:
				self.wait_for_then_click(self.elements.get_clear_bing_search_query_button)
			except (NoSuchElementException, TimeoutException):
				# Fallback: re-select search bar and clear
				bar = self.elements.get_bing_search_bar()
				bar.clear()

			# Human delay between individual searches
			delay = random.uniform(COOLDOWN_SEARCH_DELAY_MIN, COOLDOWN_SEARCH_DELAY_MAX)
			time.sleep(delay)

		self.driver.get(REWARDS_EARN_URL)
		self.tab_utils.ensure_focus()

	# ------------------------------------------------------------------
	# Task: Bonus Points
	# ------------------------------------------------------------------

	def claim_bonus_points(self):
		self.switch_to_dashboard()
		self.wait_for_then_click(self.elements.get_bonus_button_on_dashboard)

		try:
			self.wait_for_then_click(self.elements.get_claim_bonus_points_button)
		except TimeoutException:
			print("[INFO] No bonus points button to claim at this time.")

	# ------------------------------------------------------------------
	# Task: Read to Earn (Bing / Start App News Articles)
	# ------------------------------------------------------------------

	def complete_read_to_earn(self):
		"""Complete Read to Earn news articles (+30 points) via Bing App API or MSN browsing."""
		print("\n[INFO] --- Starting Read to Earn News Task ---")
		token = app_rewards.get_mobile_app_access_token(self.driver)
		if token:
			count = app_rewards.execute_read_to_earn_api(token, max_articles=10)
			print(f"[OK] Read to Earn ({count}/10 articles read)")
			# Also attempt Mobile App Daily Check-In while authenticated
			app_rewards.execute_daily_checkin_api(token)
		else:
			self.enable_mobile_emulation()
			try:
				app_rewards.browse_msn_news_fallback(self.driver, count=6)
				print("[OK] Read to Earn (MSN browser fallback)")
			finally:
				self.disable_mobile_emulation()

	# ------------------------------------------------------------------
	# Main Orchestration with Randomized Task Execution Order
	# ------------------------------------------------------------------

	def complete_all_tasks(self) -> Dict[str, str]:
		"""Execute daily rewards tasks in a randomized order with human reading pauses.

		Returns per-task status: 'ok', 'skipped' (element not in this UI
		variant) or 'failed: <ExcType>: <msg>' — consumed by
		critical_tasks_succeeded() in main.py for the process exit code.
		"""
		available_tasks: List[Tuple[str, Callable[[], None]]] = [
			("Required searches", self.complete_required_searches),
			("Bing daily set", self.complete_bing_daily_set),
			("Explore on Bing", self.complete_explore_on_bing_tasks),
			("Visual search", self.complete_visual_search),
			("Misc cards", self.complete_misc_cards),
			("Bonus points", self.claim_bonus_points),
		]

		if self.enable_mobile:
			available_tasks.append(("Mobile searches", self.complete_mobile_searches))
			available_tasks.append(("Read to Earn", self.complete_read_to_earn))

		# Required searches always run first (critical daily earner); shuffle
		# only the remaining tasks to emulate human unpredictability.
		rest = available_tasks[1:]
		random.shuffle(rest)
		available_tasks = [available_tasks[0]] + rest
		order_names = " -> ".join(name for name, _ in available_tasks)
		print(f"\n[INFO] Randomized task execution plan:\n       {order_names}\n")

		task_status: Dict[str, str] = {}
		for index, (name, task_fn) in enumerate(available_tasks, start=1):
			print(f"--- Task [{index}/{len(available_tasks)}]: {name} ---")
			try:
				task_fn()
				print(f"[OK] {name}")
				task_status[name] = "ok"
			except (NoSuchElementException, TimeoutException) as exc:
				print(f"[SKIP] {name}: not available in this UI variant ({type(exc).__name__})")
				task_status[name] = "skipped"
			except Exception as exc:
				print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")
				task_status[name] = f"failed: {type(exc).__name__}: {exc}"

			# Clean tabs state and insert realistic human pause between tasks
			try:
				self.tab_utils.close_all_other_tabs()
			except Exception:
				pass

			if index < len(available_tasks):
				human_idle_delay(TASK_PAUSE_MIN, TASK_PAUSE_MAX, reason=f"between tasks ({name})")

		return task_status