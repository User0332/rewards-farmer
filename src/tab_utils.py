from selenium.common.exceptions import WebDriverException, JavascriptException
from selenium import webdriver


def is_ghost_tab(url: str) -> bool:
	return "ntp.msn.com" in url or "newtab" in url or "chrome://" in url or "edge://" in url


class TabUtils:
	def __init__(self, driver: webdriver.Edge):
		self.driver = driver
		self.problematic_tabs = set()

	def ensure_focus(self):
		"""Ensure window/tab has focus using CDP protocol without monkey-patching native DOM prototypes."""
		try:
			self.driver.execute_cdp_cmd("Emulation.setFocusEmulationEnabled", {"enabled": True})
			self.driver.execute_cdp_cmd("Page.bringToFront", {})
		except Exception:
			pass

	def get_rewards_tab_handle(self) -> str:
		"""Find the window handle that contains rewards.bing.com."""
		for handle in self.driver.window_handles:
			try:
				self.driver.switch_to.window(handle)
				if "rewards.bing.com" in (self.driver.current_url or ""):
					return handle
			except Exception:
				pass
		return self.driver.window_handles[0] if self.driver.window_handles else ""

	def switch_to_other_tab(self):
		"""Switch to the most recently opened tab that isn't the current or ghost tab."""
		current_window = self.driver.current_window_handle
		for handle in reversed(self.driver.window_handles):
			if handle != current_window and handle not in self.problematic_tabs:
				try:
					self.driver.switch_to.window(handle)
					if is_ghost_tab(self.driver.current_url or ""):
						continue
					self.ensure_focus()
					return
				except WebDriverException:
					continue

	def close_all_other_tabs(self, exceptions: list = None):
		"""Close all opened secondary tabs and ghost tabs, keeping only the main Rewards tab."""
		rewards_handle = self.get_rewards_tab_handle()
		
		for handle in list(self.driver.window_handles):
			if handle != rewards_handle and len(self.driver.window_handles) > 1:
				try:
					self.driver.switch_to.window(handle)
					tab_url = self.driver.current_url
					self.driver.close()
					print(f"[INFO] Closed extra tab: {tab_url}")
				except WebDriverException:
					self.problematic_tabs.add(handle)

		if rewards_handle in self.driver.window_handles:
			self.driver.switch_to.window(rewards_handle)
			self.ensure_focus()
		elif self.driver.window_handles:
			self.driver.switch_to.window(self.driver.window_handles[0])
			self.ensure_focus()