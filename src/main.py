from constants import DOTENV_PATH
import _thread
import argparse
import dataclasses
import io
from dataclasses import dataclass
import logging
import os
import sys
import threading
import time
import dotenv
import log_utils
import accounts
import browser
import desktop_utils
import rewards_tasks
import rewards_state
import element_selectors
import run_record
import run_lock
from file_replace import read_file

HEADLESS = browser.HEADLESS

logger = logging.getLogger(__name__)

DEFAULT_MIN_POINTS = 15
MIN_POINTS_ENV = "REWARDS_MIN_POINTS"
STOP_FILE_ENV = "REWARDS_STOP_FILE"
WHOLE_PAGE_CODES = ("signed_out", "terms_of_use", "page_blank")


@dataclass(frozen=True)
class RunPlan:
	read_state: bool = False
	only_if_open: bool = False
	check_only: bool = False
	min_points: int | None = None
	run_if_unreadable: bool = False


_plan = RunPlan()
_stop_requested = False
_account_started = False
_account_ran = False


def decision_state(state: rewards_state.RewardsState) -> rewards_state.RewardsState:
	if state.unreadable is None or state.unreadable in WHOLE_PAGE_CODES:
		return state
	sections = [code for code in state.unreadable.split(",") if code != "balance"]
	return dataclasses.replace(state, unreadable=",".join(sections) or None)


def decide(state, plan) -> tuple[bool, str]:
	worth = rewards_state.worth_running(decision_state(state), plan.min_points)
	if worth is None:
		return (True, "unreadable_run_anyway") if plan.run_if_unreadable else (False, "unreadable")
	if worth:
		return True, ("enough_open" if state.open_points >= plan.min_points else "quest_unlocked")
	return False, ("below_threshold" if state.open_points > 0 else "nothing_open")


def _decision_log(account, state, reason):
	if reason == "quest_unlocked":
		logger.info("%s: a quest step is unlocked, running", account.name)
	elif reason in ("enough_open", "unreadable_run_anyway"):
		logger.info("%s: about %s points open, running", account.name, state.open_points)
	elif reason == "below_threshold":
		logger.info("%s: only %s points open, under the threshold of %s, not running", account.name, state.open_points, _plan.min_points)
	elif reason == "nothing_open":
		logger.info("%s: nothing open, not running", account.name)
	else:
		logger.info("%s: the Rewards page could not be read (%s), not running", account.name, state.unreadable)


def _watch_stop(path, done):
	global _stop_requested
	while not done.wait(1):
		if os.path.isfile(path):
			_stop_requested = True
			run_record.emit("stop_requested", by="app")
			_thread.interrupt_main()
			return


def run_account(account: accounts.Account) -> bool:
	"""Work one account. Returns whether the browser started."""
	global _account_started, _account_ran
	began = time.monotonic()
	_account_started = _account_ran = False
	run_record.emit("account_start", account=account.name, profile=account.user_data_dir)
	def ended(error_code=None):
		run_record.emit("account_end", account=account.name, started=_account_started, ran_tasks=_account_ran,
			error_code=error_code, detail=None, duration_ms=round((time.monotonic() - began) * 1000))
	try:
		if not desktop_utils.prepare_desktop_before_launch():
			ended("browser_failed")
			return False
		driver = browser.start_driver(account)
	finally:
		desktop_utils.switch_back_after_launch()

	if driver is None:
		ended("browser_failed")
		return False

	_account_started = True
	try:
		only = None
		if _plan.read_state:
			driver.set_page_load_timeout(60)
			driver.set_script_timeout(30)
			elements = element_selectors.ElementSelectionUtils(driver)
			state = rewards_state.read_state(driver, elements, skip_cards=run_record.cards_left_open_today(account.name), phase="before")
			run_record.emit("state", phase="before", **dataclasses.asdict(state))
			if _plan.check_only:
				ended()
				return True
			if _plan.only_if_open:
				should_run, reason = decide(state, _plan)
				run_record.emit("decision", should_run=should_run, reason=reason, open_points=state.open_points, min_points=_plan.min_points)
				_decision_log(account, state, reason)
				if not should_run:
					ended()
					return True
				if reason == "quest_unlocked":
					only = {"quests"}
					logger.info("%s: running quests only because a quest step is unlocked", account.name)
		rewards = rewards_tasks.RewardsTaskUtils(driver)
		if _plan.read_state:
			rewards.balance_reader = lambda: rewards_state.read_balance(driver, rewards.elements)
		_account_ran = True
		if only is None:
			rewards.complete_all_tasks()
		else:
			rewards.complete_all_tasks(only=only)
		if _plan.read_state:
			driver.execute_cdp_cmd("Network.setExtraHTTPHeaders", {"headers": {}})
			state = rewards_state.read_state(driver, rewards.elements, skip_cards=run_record.cards_left_open_today(account.name), phase="after")
			run_record.emit("state", phase="after", **dataclasses.asdict(state))
		ended()
	except KeyboardInterrupt:
		ended()
		raise
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


def main(unattended: bool = False, plan: RunPlan | None = None) -> int:
	global _plan, _stop_requested, _account_started, _account_ran
	_plan = plan or RunPlan()
	_plan = dataclasses.replace(_plan, read_state=_plan.read_state or _plan.only_if_open or _plan.check_only,
		min_points=(_plan.min_points if _plan.min_points is not None else int(os.environ.get(MIN_POINTS_ENV, DEFAULT_MIN_POINTS))) if _plan.only_if_open else _plan.min_points)
	_stop_requested = False
	log_utils.setup_logging()
	desktop_utils.reset_virtual_desktop_state()

	if desktop_utils.is_virtual_desktop_enabled() and not desktop_utils.is_windows():
		logger.warning("USE_VIRTUAL_DESKTOP is enabled, but virtual desktops are only supported on Windows.")

	try:
		configured = accounts.configured()
	except ValueError as exc:
		logger.error("[FAIL] %s", exc)
		run_record.open_run(os.environ.get(run_record.TRIGGER_ENV, "cli"), [], dataclasses.asdict(_plan), sorted(rewards_tasks.skipped_tasks()))
		run_record.close_run("config_error", 2, str(exc))

		return 2

	recorder = run_record.open_run(os.environ.get(run_record.TRIGGER_ENV, "cli"), [account.name for account in configured], dataclasses.asdict(_plan), sorted(rewards_tasks.skipped_tasks()))
	lock = run_lock.acquire("farmer", recorder.run_id)
	if lock is None:
		info = run_lock.status()
		logger.error("[FAIL] another run is using the browser profiles (started %s by %s)", info.since if info else "unknown", info.owner if info else "unknown")
		run_record.close_run("busy", 3)
		return 3
	run_record.prune()
	done = threading.Event()
	watcher = None
	if os.environ.get(STOP_FILE_ENV):
		watcher = threading.Thread(target=_watch_stop, args=(os.environ[STOP_FILE_ENV], done), daemon=True)
		watcher.start()
	started = 0
	result, code = "failed", 1
	try:
		for account in configured:
			if len(configured) > 1:
				logger.info("=== account: %s ===", account.name)
			_account_started = _account_ran = False
			began = time.monotonic()
			with run_record.account_context(account.name):
				try:
					if run_account(account):
						started += 1
				except Exception as exc:
					logger.error("[FAIL] %s: %s: %s", account.name, type(exc).__name__, log_utils.exception_summary(exc), exc_info=logger.isEnabledFor(logging.DEBUG))
					run_record.emit("account_end", started=_account_started, ran_tasks=_account_ran, error_code="error",
						detail=f"{type(exc).__name__}: {log_utils.exception_summary(exc)}", duration_ms=round((time.monotonic() - began) * 1000))
			if _stop_requested:
				raise KeyboardInterrupt
		if len(configured) > 1:
			logger.info("%s/%s accounts ran", started, len(configured))
		# Preserve the terminal prompt, but never hold a scheduled process open.
		if not HEADLESS and not unattended:
			input("Press Enter to exit...")
		code = 0 if started else 1
		stats = recorder.stats
		if _plan.check_only:
			result = "checked"
		elif stats["accounts_started"] == 0 or (not started and stats["account_error"]):
			result = "failed"
		elif stats["accounts_decided_no"] and not stats["accounts_ran"] and not any(stats[key] for key in ("task_fail", "browser_failed", "account_error")):
			result = "skipped"
		elif any(stats[key] for key in ("task_fail", "browser_failed", "account_error")):
			result = "partial"
		else:
			result = "ok"
		return code
	except KeyboardInterrupt:
		if _stop_requested:
			logger.info("Stopped on request")
		result, code = "stopped", 4 if _stop_requested else 130
		if not _stop_requested:
			raise
		return 4
	finally:
		done.set()
		if watcher is not None:
			watcher.join(timeout=2)
		try:
			desktop_utils.cleanup_virtual_desktop()
		finally:
			try:
				run_record.close_run(result, code)
			finally:
				lock.release()


def cli(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description="Work through the Rewards tasks for every configured account.")
	parser.add_argument(
		"--unattended",
		action="store_true",
		help="exit when the run ends instead of waiting for Enter, for scheduled runs",
	)
	parser.add_argument("--read-state", action="store_true")
	parser.add_argument("--only-if-open", action="store_true")
	parser.add_argument("--min-points", type=int)
	parser.add_argument("--run-if-unreadable", action="store_true")
	parser.add_argument("--check-only", action="store_true")
	arguments = parser.parse_args(argv)

	if os.path.isfile(DOTENV_PATH):
		try:
			text = read_file(DOTENV_PATH)
		except FileNotFoundError:
			text = ""
		dotenv.load_dotenv(stream=io.StringIO(text))
	minimum = arguments.min_points
	if minimum is None and arguments.only_if_open:
		try:
			minimum = int(os.environ.get(MIN_POINTS_ENV, DEFAULT_MIN_POINTS))
		except ValueError:
			parser.error("REWARDS_MIN_POINTS must be an integer")
	if minimum is not None and minimum < 0:
		parser.error("--min-points must not be negative")
	return main(unattended=arguments.unattended, plan=RunPlan(arguments.read_state or arguments.only_if_open or arguments.check_only,
		arguments.only_if_open, arguments.check_only, minimum, arguments.run_if_unreadable))


if __name__ == "__main__":
	sys.exit(cli())
