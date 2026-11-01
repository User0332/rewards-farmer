"""Pin the no-flag console against upstream main, without starting a browser."""

import contextlib
import io
import logging
import os
from pathlib import Path
import re
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
import browser
import constants
import log_utils
import main
import rewards_tasks


def capture_console():
	root = logging.getLogger()
	old_handlers, old_level, old_disabled = root.handlers[:], root.level, root.manager.disable
	output = io.StringIO()
	task_class = rewards_tasks.RewardsTaskUtils
	root.handlers = []
	logging.disable(logging.NOTSET)
	try:
		with tempfile.TemporaryDirectory() as folder:
			class Driver:
				def __init__(self, options, service=None):
					profile = next(arg for arg in options.arguments if arg.startswith("--user-data-dir="))
					self.name = os.path.basename(profile.split("=", 1)[1])
					self.window_handles = ["main"]
					self.current_url = rewards_tasks.REWARDS_HOME_URL

				def quit(self):
					if self.name == "three":
						raise WebDriverException("browser already gone")

			def tasks_for(driver):
				if driver.name == "two":
					raise ValueError("first page did not load")
				tasks = task_class.__new__(task_class)
				tasks.driver = driver
				tasks.main_window = "main"
				tasks.tab_utils = types.SimpleNamespace(close_all_other_tabs=lambda **kwargs: None)
				steps = (
					"complete_bing_daily_set", "complete_explore_on_bing_tasks", "complete_visual_search",
					"complete_misc_cards", "complete_quests", "complete_required_searches", "claim_bonus_points",
				)
				for method in steps:
					def step(name=method):
						if name == "complete_explore_on_bing_tasks":
							raise NoSuchElementException("no Explore on Bing section")
						if name == "complete_visual_search":
							tasks.progress = "opened the sidebar"
							raise rewards_tasks.ElementNeverAppeared("nothing matched")
						if name == "complete_required_searches":
							raise TimeoutException("fixed timeout")
						if name == "complete_misc_cards":
							raise WebDriverException("chrome not reachable")
					setattr(tasks, method, step)
				return tasks

			with contextlib.ExitStack() as stack:
				stack.enter_context(mock.patch.dict(os.environ, {"REWARDS_ACCOUNTS": "one,two,three", "USER_DATA_DIR": folder}, clear=True))
				stack.enter_context(mock.patch.object(constants, "RUNS_DIR", os.path.join(folder, "runs"), create=True))
				stack.enter_context(mock.patch.object(constants, "REPO_ROOT", folder))
				stack.enter_context(mock.patch.object(log_utils, "_configured", False))
				stack.enter_context(mock.patch.object(browser.webdriver, "Edge", Driver))
				stack.enter_context(mock.patch.object(main.rewards_tasks, "RewardsTaskUtils", tasks_for))
				stack.enter_context(mock.patch.object(main, "HEADLESS", True))
				for method in ("reset_virtual_desktop_state", "switch_back_after_launch", "cleanup_virtual_desktop"):
					stack.enter_context(mock.patch.object(main.desktop_utils, method, lambda: None))
				stack.enter_context(mock.patch.object(main.desktop_utils, "prepare_desktop_before_launch", lambda: True))
				stack.enter_context(contextlib.redirect_stdout(output))
				code = main.main()
				if code != 0:
					raise AssertionError(f"capture exited {code}")
	finally:
		for handler in root.handlers:
			handler.close()
		root.handlers = old_handlers
		root.setLevel(old_level)
		logging.disable(old_disabled)
	return re.sub(r"(?m)^\d{2}:\d{2}:\d{2} ", "", output.getvalue())


class ConsoleGolden(unittest.TestCase):
	def test_no_new_flag_or_key_preserves_upstream_console_bytes(self):
		baseline = Path(__file__).with_name("fixtures") / "cli_output_baseline.txt"
		self.assertEqual(capture_console().encode("utf-8"), baseline.read_bytes().replace(b"\r\n", b"\n"))

	def test_golden_rejects_a_new_no_flag_log_line_then_restores(self):
		original = main.run_account
		def leaking(account):
			main.logger.info("synthetic new flag log leaked")
			return original(account)
		result = unittest.TestResult()
		with mock.patch.object(main, "run_account", side_effect=leaking):
			ConsoleGolden("test_no_new_flag_or_key_preserves_upstream_console_bytes").run(result)
		self.assertEqual((result.testsRun, len(result.failures), len(result.errors)), (1, 1, 0))
		self.assertIn("synthetic new flag log leaked", result.failures[0][1])
		self.test_no_new_flag_or_key_preserves_upstream_console_bytes()


if __name__ == "__main__":
	if "--capture" in sys.argv:
		print(capture_console(), end="")
	else:
		unittest.main()
