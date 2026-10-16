from constants import DOTENV_PATH
import logging
import os
import sys
import dotenv
import log_utils
import accounts
import browser
import desktop_utils
import rewards_tasks

HEADLESS = browser.HEADLESS

logger = logging.getLogger(__name__)


def run_account(account: accounts.Account) -> bool:
	"""Work one account. Returns whether the browser started."""
	try:
		if not desktop_utils.prepare_desktop_before_launch():
			return False
		driver = browser.start_driver(account)
	finally:
		desktop_utils.switch_back_after_launch()

	if driver is None:
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


def _windows_console_attached() -> bool:
	"""Whether stdin is a real Windows console, by asking the console itself.

	isatty() cannot answer this: the null device the GUI hands main.py is a
	character device, so isatty() reports True while every read ends at once.
	GetConsoleMode succeeds only for an actual console.
	"""

	try:
		import ctypes

		kernel32 = ctypes.windll.kernel32
		handle = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
		mode = ctypes.c_uint()

		return bool(kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
	except Exception:
		return False


def stdin_is_interactive() -> bool:
	"""Whether pressing Enter here could actually be read."""

	if not sys.stdin:
		return False

	if os.name == "nt":
		return _windows_console_attached()

	return sys.stdin.isatty()


def pause_for_enter() -> None:
	"""Wait for Enter when someone is watching, and never any longer.

	Nothing is watching a container, and stdin is not a terminal there. The
	same is true when the GUI runs this: it wants the process to end, not to
	sit on a prompt. The check above tells the two apart on Windows, and the
	EOFError catch is there in case a console goes away between the two.
	"""

	if HEADLESS or not stdin_is_interactive():
		return

	try:
		input("Press Enter to exit...")
	except EOFError:
		pass


def main() -> int:
	log_utils.setup_logging()
	desktop_utils.reset_virtual_desktop_state()

	if desktop_utils.is_virtual_desktop_enabled() and not desktop_utils.is_windows():
		logger.warning("USE_VIRTUAL_DESKTOP is enabled, but virtual desktops are only supported on Windows.")

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

	# Nothing is watching a container, and stdin is not a terminal there. The
	# same is true when the GUI runs this.
	pause_for_enter()

	desktop_utils.cleanup_virtual_desktop()

	return 0 if started else 1


if __name__ == "__main__":
	if os.path.isfile(DOTENV_PATH): dotenv.load_dotenv(DOTENV_PATH)
	sys.exit(main())
