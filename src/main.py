import log_utils
import rewards_tasks
import mouse_trajectory
import mimic_typing
from selenium import webdriver
from constants import USER_DATA_DIR, PROFILE_NAME

log_utils.setup_logging()

options = webdriver.EdgeOptions()

options.add_experimental_option("excludeSwitches", ["enable-automation"])
options.add_experimental_option('useAutomationExtension', False)
options.add_argument("--disable-blink-features=AutomationControlled")
options.add_argument(f"--user-data-dir={USER_DATA_DIR}")
options.add_argument(f"--profile-directory={PROFILE_NAME}")

driver = webdriver.Edge(options=options)

mouse = mouse_trajectory.MouseUtils(driver)
keyboard = mimic_typing.KeyboardUtils(driver)

rewards = rewards_tasks.RewardsTaskUtils(driver)

rewards.complete_all_tasks()

input("Press Enter to exit...")

driver.quit()