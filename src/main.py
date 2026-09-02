import logging
import os
import sys

import log_utils
import accounts
import rewards_tasks
from selenium import webdriver
from selenium.common.exceptions import SessionNotCreatedException

HEADLESS = os.environ.get("REWARDS_HEADLESS", "").strip().lower() in (
    "1",
    "true",
    "yes",
)

logger = logging.getLogger(__name__)


def build_options(account: accounts.Account) -> webdriver.EdgeOptions:
    options = webdriver.EdgeOptions()

    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(f"--user-data-dir={account.user_data_dir}")
    options.add_argument(f"--profile-directory={account.profile_name}")

    if HEADLESS:
        # A container has no display. The window size is set explicitly because
        # the pointer code works in viewport coordinates, and the default
        # headless window is small enough to put cards out of reach.
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")

    return options


def run_account(account: accounts.Account) -> bool:
    """Work one account. Returns whether the browser started."""
    try:
        driver = webdriver.Edge(options=build_options(account))
		# Set headers to spoof the rewards app for the rewards only quests
        driver.execute_cdp_cmd("Network.enable", {})
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36 Edg/151.0.0.0 MSRewards/Desktop/1.1.0",
            "X-Rewards-Source": "msrewards-desktop",
        }

        driver.execute_cdp_cmd("Network.setExtraHTTPHeaders", {"headers": headers})
    except SessionNotCreatedException as exc:
        # Chromium allows one process per user data directory. When the profile
        # is already open the driver's copy exits during startup, and selenium
        # reports it as the browser crashing with a message that names neither
        # the profile nor the other window.
        logger.error("[FAIL] %s: could not start Edge with this profile.", account.name)
        logger.error("       profile directory: %s", account.user_data_dir)
        logger.error(
            "       The usual cause is that this profile is already open in another"
        )
        logger.error("       Edge window, including one left over from a previous run.")
        logger.error("       driver said: %s", log_utils.exception_summary(exc))

        return False

    try:
        rewards = rewards_tasks.RewardsTaskUtils(driver)
        rewards.complete_all_tasks()
    finally:
        try:
            driver.quit()
        except Exception as exc:
            # quit() raises when the browser is already gone. Letting it out
            # here would replace whatever actually went wrong with the tidy-up's
            # own error, and the process it is meant to end is dead anyway.
            logger.warning(
                "%s: the driver did not shut down cleanly: %s",
                account.name,
                log_utils.exception_summary(exc),
            )

    return True


def main() -> int:
    log_utils.setup_logging()

    try:
        configured = accounts.configured()
    except ValueError as exc:
        logger.error("[FAIL] %s", exc)

        return 2

    started = 0

    for account in configured:
        if len(configured) > 1:
            logger.info("=== account: %s ===", account.name)

        # One account must not be able to end the batch. complete_all_tasks
        # already contains a task that fails, and run_account names the profile
        # that is already open, but everything else - a driver that will not
        # start for some other reason, the browser dying mid-run, a page that
        # never loads - reached here and took the remaining accounts with it.
        # KeyboardInterrupt is deliberately not caught: Ctrl-C means stop.
        try:
            if run_account(account):
                started += 1
        except Exception as exc:
            logger.error(
                "[FAIL] %s: %s: %s",
                account.name,
                type(exc).__name__,
                log_utils.exception_summary(exc),
                exc_info=logger.isEnabledFor(logging.DEBUG),
            )

    if len(configured) > 1:
        logger.info("%s/%s accounts ran", started, len(configured))

    # Nothing is watching a container, and stdin is not a terminal there.
    if not HEADLESS:
        input("Press Enter to exit...")

    return 0 if started else 1


if __name__ == "__main__":
    sys.exit(main())
