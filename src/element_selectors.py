from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.common.exceptions import NoSuchElementException
from selenium import webdriver


class ElementSelectionUtils:
	def __init__(self, driver: webdriver.Edge):
		self.driver = driver

	def resolve(self, xpath: str):
		return self.driver.find_element(By.XPATH, xpath)

	def get_earn_tab(self):
		return self.resolve('//*[@id="react-aria-_R_18mbslbH1_-tab-/earn"]')

	def get_dashboard_tab(self):
		return self.resolve('//*[@id="react-aria-_R_18mbslbH1_-tab-/dashboard"]')

	def get_open_daily_set_button(self):
		return self.resolve("/html/body/div[2]/div[2]/div/main/section[1]/div/div[2]/div/div/button[3]")

	def get_open_visual_search_sidebar(self):
		return self.resolve("/html/body/div[2]/div[2]/div/main/section[1]/div/div[2]/div/div/button[6]")

	def get_sidebar_section(self):
		sections = self.driver.find_elements(By.TAG_NAME, "section")

		for section in sections:
			if section.get_dom_attribute("id").startswith("react-aria"):
				return section

		raise Exception("Sidebar section not found")

	def get_daily_set_elements(self):
		daily_set_sidebar = self.get_sidebar_section()

		daily_set_elems = daily_set_sidebar.find_elements(By.TAG_NAME, "a")[1:]

		return daily_set_elems

	def get_explore_on_bing_elements(self):
		sections = self.driver.find_elements(By.XPATH, "/html/body/div[2]/div[2]/div/main/section")

		if len(sections) >= 2:
			explore_section = sections[1]  # second section
		else:
			for sec in sections:
				try:
					heading = sec.find_element(By.XPATH, ".//h2")
					if "exploreonbing" in heading.text.lower():
						explore_section = sec
						break
				except:
					continue
			else:
				raise Exception("Could not find the 'Explore on Bing' section.")

		all_links = explore_section.find_elements(By.XPATH, 
		".//a[@href and contains(@href, 'bing.com/?form=')]")
		valid_cards = [link for link in all_links if link.is_displayed() and link.text.strip()]

		if not valid_cards:
			raise Exception("No Explore on Bing task cards found.")

		return valid_cards

	def get_search_now_link_from_visual_search_sidebar(self):
		visual_search_sidebar = self.get_sidebar_section()

		return visual_search_sidebar.find_elements(By.TAG_NAME, "a")[1]

	def extract_card_descriptions(self, card: WebElement):
		return card.find_element(By.CSS_SELECTOR, "p:nth-child(2)").text

	def card_is_complete(self, card: WebElement):
		return "completed" in card.find_element(By.CSS_SELECTOR, "div.flex.w-full.items-center.gap-2").text.lower()

	def get_bing_search_bar(self):
		return self.driver.find_element(By.TAG_NAME, "textarea")

	def get_clear_bing_search_query_button(self):
		return self.driver.find_element(By.ID, "sw_clx")

	def get_visual_search_button(self):
		return self.driver.find_element(By.CSS_SELECTOR, "#sb_form > div.camera.icon")

	def get_visual_search_file_input(self):
		return self.driver.find_element(By.CSS_SELECTOR, "#sb_fileinput")

	def get_all_misc_cards(self):
		misc_cards_container = self.driver.find_element(By.ID, "moreactivities")

		return misc_cards_container.find_elements(By.TAG_NAME, "a")

	def get_card_point_value(self, card: WebElement):
		# querySelector("div.flex.w-full.items-center.gap-2").querySelector('p')

		try: elem = card.find_element(By.CSS_SELECTOR, "div.flex.w-full.items-center.gap-2").find_element(By.TAG_NAME, "p")
		except NoSuchElementException:
			return 0

		return int(elem.text)

	def element_is_fully_in_viewport(self, elem: WebElement) -> bool:
		js_viewport_check = """
var elem = arguments[0];
var box = elem.getBoundingClientRect();

// Check if the element is at least partially in the viewport
return (
	  box.top >= 0 &&
  box.left >= 0 &&
  box.bottom <= (window.innerHeight || document.documentElement.clientHeight) &&
  box.right <= (window.innerWidth || document.documentElement.clientWidth)
);
"""

		return self.driver.execute_script(js_viewport_check, elem)

	def get_points_breakdown_button(self):
		elem = self.driver.find_element(By.XPATH, "//button[.//img[contains(@src, 'Icons.Coins.')]]")

		return elem

	def get_close_button_on_points_breakdown(self):
		breakdown_sidebar = self.get_sidebar_section()

		return breakdown_sidebar.find_elements(By.TAG_NAME, "button")[2]

	def get_points_earned_from_searches_on_points_breakdown(self) -> int:
		breakdown_sidebar = self.get_sidebar_section()

		fraction = breakdown_sidebar.find_element(By.CSS_SELECTOR, "div.py-3.wrap-anywhere.justify-self-end").text

		earned_str, max_str = fraction.split('/')

		return int(earned_str.strip()), int(max_str.strip())

	def get_bonus_button_on_dashboard(self):
		button = self.driver.find_element(By.XPATH, "//button[.//img[contains(@src, 'Icons.CoinsTransparent')]]")

		return button

	def get_claim_bonus_points_button(self):
		bonus_sidebar = self.driver.find_element(By.XPATH, "//button[.//img[contains(@width, '40')]]")

		return bonus_sidebar

	def get_generic_sidebar_close_button(self):
		sidebar = self.get_sidebar_section()

		return sidebar.find_elements(By.TAG_NAME, "button")[0]