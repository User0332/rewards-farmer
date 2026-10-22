"""Tests for starting Edge and explaining why it did not start.

Every startup failure used to be reported as "the profile is already open",
whatever the driver actually said, and check_selectors.py had no handling at
all. These drive the shared start_driver with the messages msedgedriver and
selenium really produce, and check that paths no longer depend on where the
script was launched from. None of them start a browser.

	python -m unittest discover -s tests
"""

import logging
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
sys.path.insert(0, SRC)

from selenium.common.exceptions import (
	NoSuchDriverException,
	SessionNotCreatedException,
)

import accounts
import browser
import check_selectors
import log_utils
import main

REPO_ROOT = os.path.realpath(os.path.join(SRC, ".."))

ACCOUNT = accounts.get_default_account()

ENV_VARS = ("MSEDGEDRIVER_PATH", "EDGE_BINARY", "REWARDS_DRIVER_LOG")

# (driver message as reported in #65, #70, #79 and friends, a phrase the log must contain)
FAILURES = [
	(
		SessionNotCreatedException(
			"session not created: Chrome instance exited. Examine ChromeDriver verbose log to determine the cause."
		),
		"Edge exited during startup",
	),
	(
		SessionNotCreatedException(
			"session not created: cannot create default profile directory"
		),
		"could not create the profile directory",
	),
	(
		SessionNotCreatedException(
			"session not created\nfrom unknown error: Could not remove old devtools port file. Perhaps the "
			"given user-data-dir at /x/data-dir is still attached to a running Microsoft Edge or Chromium process"
		),
		"already open in another Edge window",
	),
	(
		SessionNotCreatedException(
			"session not created: This version of Microsoft Edge WebDriver only supports Microsoft Edge "
			"version 150\nCurrent browser version is 152.0.4191.62"
		),
		"msedgedriver and Edge versions do not match",
	),
	(
		NoSuchDriverException("Unable to obtain driver for MicrosoftEdge"),
		"could not find msedgedriver or Edge",
	),
	(
		SessionNotCreatedException("session not created: something nobody has seen yet"),
		"something nobody has seen yet",
	),
]


class EnvTestCase(unittest.TestCase):
	def setUp(self):
		saved = {name: os.environ.pop(name, None) for name in ENV_VARS}

		def restore():
			for name, value in saved.items():
				os.environ.pop(name, None)

				if value is not None:
					os.environ[name] = value

		self.addCleanup(restore)

		edge = browser.webdriver.Edge
		self.addCleanup(setattr, browser.webdriver, "Edge", edge)


class TestStartupFailures(EnvTestCase):
	def _raise(self, exc):
		def edge(*args, **kwargs):
			raise exc

		browser.webdriver.Edge = edge

	def test_each_driver_message_gets_its_own_explanation(self):
		for exc, phrase in FAILURES:
			with self.subTest(phrase=phrase):
				self._raise(exc)

				with self.assertLogs("browser", logging.ERROR) as logs:
					self.assertIsNone(browser.start_driver(ACCOUNT))

				output = "\n".join(logs.output)
				self.assertIn(phrase, output)
				self.assertIn(os.environ.get(accounts.DATA_DIR_ENV_VAR, accounts.DEFAULT_ROOT_DATA_DIR), output)

	def test_only_the_lock_message_blames_an_open_window(self):
		for exc, phrase in FAILURES:
			if "still attached" in str(exc):
				continue

			with self.subTest(phrase=phrase):
				self._raise(exc)

				with self.assertLogs("browser", logging.ERROR) as logs:
					browser.start_driver(ACCOUNT)

				self.assertNotIn("already open in another Edge window", "\n".join(logs.output))

	def test_run_account_reports_a_failed_start(self):
		self._raise(FAILURES[0][0])

		with self.assertLogs("browser", logging.ERROR):
			self.assertFalse(main.run_account(ACCOUNT))

	def test_check_selectors_explains_instead_of_a_traceback(self):
		# #70: the diagnostic tool crashed before it could diagnose anything.
		self._raise(FAILURES[2][0])

		with mock.patch.object(log_utils, "setup_logging"):
			with self.assertLogs("browser", logging.ERROR) as logs:
				self.assertEqual(check_selectors.main(), 2)

		self.assertIn("already open in another Edge window", "\n".join(logs.output))


class TestDriverEnvironment(EnvTestCase):
	def setUp(self):
		super().setUp()

		self.seen = {}

		def edge(options=None, service=None):
			self.seen.update(options=options, service=service)

			return object()

		browser.webdriver.Edge = edge

	def test_unset_leaves_selenium_to_find_both(self):
		browser.start_driver(ACCOUNT)

		self.assertFalse(self.seen["service"].path)
		self.assertFalse(self.seen["options"].binary_location)
		self.assertNotIn("--verbose", self.seen["service"].service_args)

	def test_env_vars_reach_the_driver(self):
		os.environ["MSEDGEDRIVER_PATH"] = "/opt/msedgedriver"
		os.environ["EDGE_BINARY"] = "/opt/edge/msedge"
		os.environ["REWARDS_DRIVER_LOG"] = "/tmp/msedgedriver.log"

		browser.start_driver(ACCOUNT)

		self.assertEqual(self.seen["service"].path, "/opt/msedgedriver")
		self.assertEqual(self.seen["options"].binary_location, "/opt/edge/msedge")
		self.assertIn("--verbose", self.seen["service"].service_args)
		self.assertIn("--log-path=/tmp/msedgedriver.log", self.seen["service"].service_args)

	def test_the_account_profile_is_passed(self):
		browser.start_driver(ACCOUNT)

		self.assertIn(f"--user-data-dir={os.environ.get(accounts.DATA_DIR_ENV_VAR, accounts.DEFAULT_ROOT_DATA_DIR)}", self.seen["options"].arguments)

	def test_local_page_loads_are_bounded_and_rendered_on_the_cpu(self):
		# Ollama and the renderer must not compete for the same GPU during searches.
		browser.start_driver(ACCOUNT)
		options = self.seen["options"]
		self.assertEqual(options.to_capabilities().get("timeouts"), {"pageLoad": 60_000, "script": 30_000})
		self.assertIn("--disable-gpu", options.arguments)

	def test_headless_sessions_get_the_same_startup_limits_and_cpu_rendering(self):
		with mock.patch.object(browser, "HEADLESS", True):
			browser.start_driver(ACCOUNT)
		options = self.seen["options"]
		self.assertEqual(options.to_capabilities().get("timeouts"), {"pageLoad": 60_000, "script": 30_000})
		self.assertIn("--disable-gpu", options.arguments)

	def test_pages_are_asked_for_in_english(self):
		# #91: a browser set to Spanish got Rewards in Spanish, and none of the
		# English labels the selectors look for were on the page.
		browser.start_driver(ACCOUNT)

		self.assertIn("--accept-lang=en-US", self.seen["options"].arguments)

	def test_headless_pages_are_asked_for_in_english_too(self):
		with mock.patch.object(browser, "HEADLESS", True):
			browser.start_driver(ACCOUNT)

		self.assertIn("--accept-lang=en-US", self.seen["options"].arguments)


class TestPathsIgnoreTheWorkingDirectory(unittest.TestCase):
	"""#71: launched from C:\\WINDOWS\\system32, the profile was created there."""

	PROBE = (
		"import constants, rewards_tasks, llm_utils, query_sources, random_image_for_visual_search as r\n"
		"print(rewards_tasks.VISUAL_SEARCH_IMAGE_PATH)\n"
		"print(r.OUTPUT_FILE)\n"
		"print(r.METADATA_FILE)\n"
		"print(len(llm_utils.NOUNS) > 0)\n"
		"print(len(query_sources.wordlist_queries(3)))\n"
	)

	def test_paths_resolve_to_the_repo_from_any_cwd(self):
		with tempfile.TemporaryDirectory() as cwd:
			result = subprocess.run(
				[sys.executable, "-c", self.PROBE],
				cwd=cwd,
				env={**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, (os.environ.get("PYTHONPATH"), os.path.abspath(SRC))))},
				capture_output=True,
				text=True,
			)

		self.assertEqual(result.returncode, 0, result.stderr)

		image, output, metadata, nouns, queries = result.stdout.splitlines()

		self.assertEqual(os.path.realpath(image), os.path.join(REPO_ROOT, "visual_search.jpg"))
		self.assertEqual(os.path.realpath(output), os.path.join(REPO_ROOT, "visual_search.jpg"))
		self.assertEqual(os.path.realpath(metadata), os.path.join(REPO_ROOT, "visual_search.json"))
		self.assertEqual(nouns, "True")
		self.assertEqual(queries, "3")


if __name__ == "__main__":
	unittest.main()
