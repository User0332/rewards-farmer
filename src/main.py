import sys

import rewards_tasks
import mouse_trajectory
import mimic_typing
from selenium import webdriver
from selenium.common.exceptions import SessionNotCreatedException
from constants import USER_DATA_DIR, PROFILE_NAME

options = webdriver.EdgeOptions()

options.add_experimental_option("excludeSwitches", ["enable-automation"])
options.add_experimental_option('useAutomationExtension', False)
options.add_argument("--disable-blink-features=AutomationControlled")
options.add_argument(f"--user-data-dir={USER_DATA_DIR}")
options.add_argument(f"--profile-directory={PROFILE_NAME}")

try:
	driver = webdriver.Edge(options=options)
except SessionNotCreatedException as exc:
	# Chromium allows one process per user data directory. When the profile is
	# already open, the driver's copy exits on startup and selenium reports it
	# as the browser crashing, with a message that mentions neither the profile
	# nor another window. Observed wording varies between "Chrome instance
	# exited" and "Microsoft Edge failed to start: crashed" for the same cause,
	# so this keys on the exception rather than on the text.
	print("Could not start Edge with the automation profile.")
	print()
	print(f"  profile directory: {USER_DATA_DIR}")
	print(f"  profile name:      {PROFILE_NAME}")
	print()
	print("The usual cause is that this profile is already open in another Edge")
	print("window, including one left over from a previous run. Close every window")
	print("of that profile and run this again.")
	print()
	print("The driver's own message is repeated for reference. It names Chrome and")
	print("points at a verbose log, neither of which applies here:")
	print(f"  {str(exc).strip().splitlines()[0]}")

	sys.exit(1)

mouse = mouse_trajectory.MouseUtils(driver)
keyboard = mimic_typing.KeyboardUtils(driver)

rewards = rewards_tasks.RewardsTaskUtils(driver)

rewards.complete_all_tasks()

input("Press Enter to exit...")

driver.quit()