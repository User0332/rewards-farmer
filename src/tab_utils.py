import logging
from selenium.common.exceptions import WebDriverException, JavascriptException, NoSuchWindowException
from selenium import webdriver

logger = logging.getLogger(__name__)

GHOST_TAB_URLS = (
	"https://ntp.msn.com/edge/ntp?locale=en-US&title=New%20tab&fre=1&dsp=1&sp=Bing&feed_dis=always&en_widget_reg=false&prerender=1&PC=U531", # has fre
	"https://ntp.msn.com/edge/ntp?locale=en-US&title=New%20tab&dsp=1&sp=Bing&feed_dis=always&en_widget_reg=false&prerender=1&PC=U531" # no fre
)

class TabUtils:
	def __init__(self, driver: webdriver.Edge):
		self.driver = driver
		self.problematic_tabs = set()

	def ensure_focus(self):
		try:
			self.driver.execute_script("""
Object.defineProperty(document, 'hidden', { get: () => false });
Object.defineProperty(document, 'visibilityState', { get: () => 'visible' });
Document.prototype.hasFocus = function() { return true; };
window.hasFocus = function() { return true; };
document.dispatchEvent(new Event('visibilitychange'));
""")
		except JavascriptException: pass # it's probably a property redef exc

	def switch_to_other_tab(self):
		current_window = self.driver.current_window_handle

		for handle in self.driver.window_handles:
			if handle != current_window and handle not in self.problematic_tabs:
				self.driver.switch_to.window(handle)

				if self.driver.current_url in GHOST_TAB_URLS:
					logger.debug("Found ghost tab with handle %s and URL %s.", handle, self.driver.current_url)
					continue

				self.ensure_focus()
				return

	def close_all_other_tabs(self, exceptions: list[str] = None):
		if exceptions is None:
			exceptions = [self.driver.current_window_handle]

		switch_back_to = exceptions[0]

		for handle in self.driver.window_handles:
			if handle not in exceptions and handle not in self.problematic_tabs:
				self.driver.switch_to.window(handle)

				if self.driver.current_url in GHOST_TAB_URLS:
					logger.debug("Found ghost tab with handle %s and URL %s, not closing.", handle, self.driver.current_url)
					continue

				tab_url = self.driver.current_url

				try:
					self.driver.close()
					# Routine bookkeeping, one line per tab. At info it drowned
					# the task summary: 19 of the 33 records in a full run were
					# these. The warning below stays at warning, a tab that will
					# not close is a real problem.
					logger.debug("Closed tab with handle %s and URL %s.", handle, tab_url)

				except (WebDriverException, NoSuchWindowException):
					logger.warning("Could not close tab with handle %s and URL %s.", handle, tab_url)
					self.problematic_tabs.add(handle)
					pass

		self.driver.switch_to.window(switch_back_to)