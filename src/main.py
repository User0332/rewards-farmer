import time
import rewards_tasks
import mouse_trajectory
import mimic_typing
import desktop_utils
from selenium import webdriver
from constants import (
	USER_DATA_DIR,
	PROFILE_NAME,
	USE_VIRTUAL_DESKTOP,
	SWITCH_BACK_TO_MAIN_DESKTOP,
)

if USE_VIRTUAL_DESKTOP:
	print("[INFO] Creating and switching to a new Windows Virtual Desktop...")
	desktop_utils.create_virtual_desktop()

options = webdriver.EdgeOptions()

options.add_experimental_option("excludeSwitches", ["enable-automation"])
options.add_experimental_option('useAutomationExtension', False)
options.add_argument("--disable-blink-features=AutomationControlled")
options.add_argument(f"--user-data-dir={USER_DATA_DIR}")
options.add_argument(f"--profile-directory={PROFILE_NAME}")

driver = webdriver.Edge(options=options)

if USE_VIRTUAL_DESKTOP and SWITCH_BACK_TO_MAIN_DESKTOP:
	time.sleep(1.5) # Give the Edge window a moment to attach to the new desktop
	print("[INFO] Switching back to your main desktop. Script is running in the background...")
	desktop_utils.switch_to_left_desktop()

mouse = mouse_trajectory.MouseUtils(driver)
keyboard = mimic_typing.KeyboardUtils(driver)

rewards = rewards_tasks.RewardsTaskUtils(driver)

rewards.complete_all_tasks()

driver.quit()