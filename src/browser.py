"""Starting Edge for an account, and saying why it did not start.

Shared by main.py and check_selectors.py so both launch the same way and both
explain a failure instead of dumping a traceback.
"""

import logging
import os

from selenium import webdriver
from selenium.common.exceptions import NoSuchDriverException, WebDriverException

import accounts
import log_utils

logger = logging.getLogger(__name__)

# Matched in order against the driver's message, lowercased. selenium reports
# nearly every one of these as SessionNotCreatedException, so the class alone
# says nothing about which it was.
EXPLANATIONS = [
	("chrome instance exited", [
		"Edge exited during startup, before the driver could connect to it.",
		"Common causes: this profile is open in another Edge window, a lock left",
		"behind by a browser that was killed, or a profile directory Edge cannot",
		"write to. Set REWARDS_DRIVER_LOG=msedgedriver.log and run again; that",
		"log has Edge's own reason.",
	]),
	("cannot create default profile directory", [
		"Edge could not create the profile directory. Check that the current user",
		"can write to it without administrator rights.",
	]),
	("still attached to a running", [
		"This profile is already open in another Edge window, including one left",
		"over from a previous run or held by a container. Close it and try again.",
	]),
	("only supports microsoft edge version", [
		"msedgedriver and Edge versions do not match. Update msedgedriver to your",
		"Edge version, or remove the old one from PATH / MSEDGEDRIVER_PATH.",
	]),
]


def headless_enabled() -> bool:
	return os.environ.get("REWARDS_HEADLESS", "").strip().lower() in ("1", "true", "yes")


def build_options(account: accounts.Account, *, headless: bool | None = None) -> webdriver.EdgeOptions:
	headless = headless_enabled() if headless is None else headless
	options = webdriver.EdgeOptions()

	options.add_experimental_option("excludeSwitches", ["enable-automation"])
	options.add_experimental_option("useAutomationExtension", False)
	options.add_argument("--disable-blink-features=AutomationControlled")
	options.add_argument(f"--user-data-dir={account.user_data_dir}")
	options.add_argument(f"--profile-directory={account.profile_name}")

	# The selectors find buttons by their English labels, and Rewards renders
	# in the browser's accept language, so on a browser set to another
	# language every task came back as not available (#91). Edge saves this
	# in the profile, which is what the README tells users to expect.
	options.add_argument("--accept-lang=en-US")

	if os.environ.get("EDGE_BINARY"):
		options.binary_location = os.environ["EDGE_BINARY"]

	if headless:
		# A container has no display. The window size is set explicitly because
		# the pointer code works in viewport coordinates, and the default
		# headless window is small enough to put cards out of reach.
		options.add_argument("--headless=new")
		options.add_argument("--window-size=1920,1080")
		options.add_argument("--no-sandbox")
		options.add_argument("--disable-dev-shm-usage")

	return options


def build_service() -> webdriver.EdgeService:
	driver_log = os.environ.get("REWARDS_DRIVER_LOG")

	# An explicit driver path skips Selenium Manager entirely, which is also
	# what fails in #79 when it cannot locate the Edge install.
	return webdriver.EdgeService(
		executable_path=os.environ.get("MSEDGEDRIVER_PATH") or None,
		service_args=["--verbose"] if driver_log else None,
		log_output=driver_log or None,
	)


def explain(exc: Exception) -> list[str]:
	if isinstance(exc, NoSuchDriverException):
		return [
			"selenium could not find msedgedriver or Edge on this machine.",
			"Set MSEDGEDRIVER_PATH to the full path of msedgedriver, and EDGE_BINARY",
			"to msedge if Edge is installed somewhere non-standard.",
		]

	message = str(exc).lower()

	for needle, lines in EXPLANATIONS:
		if needle in message:
			return lines

	return ["The driver's message is below; it did not match a known cause."]


def start_driver(account: accounts.Account):
	"""An Edge driver for the account, or None after logging why it failed."""
	try:
		return webdriver.Edge(options=build_options(account), service=build_service())
	except WebDriverException as exc:
		logger.error("[FAIL] %s: could not start Edge with this profile.", account.name)
		logger.error("       profile directory: %s", account.user_data_dir)

		for line in explain(exc):
			logger.error("       %s", line)

		logger.error("       driver said: %s", log_utils.exception_summary(exc))

		return None
