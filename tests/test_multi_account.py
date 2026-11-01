"""Tests for running more than one account in a single run.

Names become directory names, so most of these are about refusing one that
would resolve somewhere other than where it reads: `..`, an absolute path, and
the trailing dot Win32 strips but Python's normalisation does not. The rest
cover the run loop, where one account failing used to end the batch and take
the remaining accounts with it.

None of these need a browser. The last case does, and starts Edge twice to show
that two profiles hold two independent, persistent identities, so it is opt in:

	python -m unittest discover -s tests
	REWARDS_BROWSER_TESTS=1 python -m unittest discover -s tests
"""

import logging
from contextlib import ExitStack
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from selenium.common.exceptions import (
	NoSuchDriverException,
	SessionNotCreatedException,
	WebDriverException,
)

import accounts
import browser
import main
import constants

# Names that have to be refused, with the reason each one is not simply a
# directory sitting under data-dir.
REFUSED_NAMES = [
	# relative traversal
	"..", ".", "../escape", "..\\escape", "a/b", "a\\b",
	# absolute, drive relative and UNC
	"/etc", "\\", "/", "C:", "C:\\Windows", "\\\\server\\share",
	# expanded by a shell somewhere else, not here
	"~", "%TEMP%", "$HOME",
	# Win32 strips a trailing dot from a path component and Python does not, so
	# "personal." is the "personal" directory and "..." is data-dir itself
	"...", "....", "personal.", "personal..",
	# shell and filesystem metacharacters
	"a b", "a:b", "a;b", "a|b", "a*b", "a?b", "a<b",
]


def accounts_for(value):
	"""configured() under a given REWARDS_ACCOUNTS, or ValueError."""
	if value is None:
		os.environ.pop(accounts.ACCOUNTS_ENV_VAR, None)
	else:
		os.environ[accounts.ACCOUNTS_ENV_VAR] = value

	return accounts.configured()


class EnvironmentTestCase(unittest.TestCase):
	"""Restores everything these tests reach into, so ordering cannot matter."""

	def setUp(self):
		self.addCleanup(os.environ.pop, accounts.ACCOUNTS_ENV_VAR, None)


class TestAccountConfiguration(EnvironmentTestCase):
	def test_unset_is_the_existing_single_profile(self):
		configured = accounts_for(None)

		self.assertEqual([a.name for a in configured], ["default"])
		self.assertEqual(configured[0].user_data_dir, os.environ.get(accounts.DATA_DIR_ENV_VAR, accounts.DEFAULT_ROOT_DATA_DIR))
		self.assertTrue(configured[0].is_default)

	def test_blank_falls_back_to_the_single_profile(self):
		self.assertEqual([a.name for a in accounts_for("   ")], ["default"])

	def test_names_are_taken_in_order(self):
		self.assertEqual([a.name for a in accounts_for("personal,spare")], ["personal", "spare"])

	def test_surrounding_whitespace_and_empty_entries_are_ignored(self):
		self.assertEqual([a.name for a in accounts_for(" personal , spare ")], ["personal", "spare"])
		self.assertEqual([a.name for a in accounts_for("personal,,spare,")], ["personal", "spare"])

	def test_duplicates_collapse_case_insensitively(self):
		# Running the same profile twice earns nothing the second time and
		# doubles the length of the run.
		self.assertEqual(
			[a.name for a in accounts_for("personal,PERSONAL,spare")], ["personal", "spare"]
		)

	def test_unusable_directory_names_are_refused(self):
		for name in REFUSED_NAMES:
			with self.subTest(name=name):
				with self.assertRaises(ValueError):
					accounts_for(f"good,{name}")

	def test_a_leading_dot_is_still_usable(self):
		# Only the trailing dot moves the directory.
		self.assertEqual([a.name for a in accounts_for(".hidden")], [".hidden"])

	def test_each_account_gets_its_own_directory(self):
		named = accounts_for("personal,spare")

		self.assertEqual(len({a.user_data_dir for a in named}), 2)
		# Distinct strings are not enough: two names can spell one directory,
		# which is what the trailing dot did, so compare where they land.
		self.assertEqual(len({os.path.realpath(a.user_data_dir) for a in named}), 2)

	def test_every_directory_sits_under_the_profile_directory(self):
		root = os.path.realpath(os.environ.get(accounts.DATA_DIR_ENV_VAR, accounts.DEFAULT_ROOT_DATA_DIR))

		for account in accounts_for("personal,spare"):
			with self.subTest(account=account.name):
				resolved = os.path.realpath(account.user_data_dir)

				self.assertEqual(os.path.commonpath([root, resolved]), root)
				self.assertNotEqual(resolved, root)
				self.assertFalse(account.is_default)


class TestEdgeOptions(EnvironmentTestCase):
	def test_each_account_is_handed_its_own_profile(self):
		seen = []

		for account in accounts_for("personal,spare"):
			arguments = browser.build_options(account).arguments
			user_data = [a for a in arguments if a.startswith("--user-data-dir=")]
			profile = [a for a in arguments if a.startswith("--profile-directory=")]

			with self.subTest(account=account.name):
				self.assertEqual(len(user_data), 1)
				self.assertEqual(len(profile), 1)

			seen.append(user_data[0])

		self.assertEqual(len(set(seen)), 2)


class RunLoopTestCase(EnvironmentTestCase):
	"""main() drives real browsers, so both entry points are replaced here."""

	def setUp(self):
		super().setUp()

		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.stack = ExitStack()
		self.addCleanup(self.stack.close)
		self.stack.enter_context(mock.patch.dict(os.environ, {"USER_DATA_DIR": self.temp.name}))
		self.stack.enter_context(mock.patch.object(constants, "RUNS_DIR", os.path.join(self.temp.name, "runs")))

		# main() waits on input() when it is not headless, which would hang.
		headless = main.HEADLESS
		main.HEADLESS = True
		self.addCleanup(setattr, main, "HEADLESS", headless)

		# main() reports per account at info, and the failure paths at error, by
		# design. The assertions are what reports the outcome here, so keep the
		# suite's own output to what unittest prints.
		logging.disable(logging.CRITICAL)
		self.addCleanup(logging.disable, logging.NOTSET)


class TestRunLoop(RunLoopTestCase):
	def setUp(self):
		super().setUp()

		self.calls = []
		real = main.run_account
		self.addCleanup(setattr, main, "run_account", real)

	def _record(self, started):
		def run_account(account):
			self.calls.append(account.name)

			return started(account.name)

		main.run_account = run_account

	def test_a_profile_that_fails_to_start_does_not_end_the_run(self):
		accounts_for("personal,spare")
		self._record(lambda name: name != "personal")

		self.assertEqual(main.main(), 0)
		self.assertEqual(self.calls, ["personal", "spare"])

	def test_exit_code_is_one_when_no_account_ran(self):
		accounts_for("personal,spare")
		self._record(lambda name: False)

		self.assertEqual(main.main(), 1)
		self.assertEqual(self.calls, ["personal", "spare"])

	def test_unset_still_runs_the_single_profile(self):
		accounts_for(None)
		self._record(lambda name: True)

		self.assertEqual(main.main(), 0)
		self.assertEqual(self.calls, ["default"])


class TestFailureIsolation(RunLoopTestCase):
	"""One account failing, every way it can, without ending the batch.

	Only selenium is replaced: run_account and main() are the shipped ones, so
	what is measured is where their exception handling actually reaches. Before
	this, only "profile already open" was handled by name and everything else
	took the remaining accounts with it.
	"""

	# (label, where it fails, the exception raised)
	CASES = [
		("the profile is already open", "start", SessionNotCreatedException("profile in use")),
		("the driver will not start", "start", WebDriverException("driver version mismatch")),
		("there is no driver installed", "start", NoSuchDriverException("msedgedriver not found")),
		("the profile directory is unwritable", "start", PermissionError(13, "Permission denied")),
		("the first page never loads", "connect", WebDriverException("net::ERR_NAME_NOT_RESOLVED")),
		("the browser dies mid-run", "tasks", WebDriverException("chrome not reachable")),
		("the browser will not shut down", "quit", WebDriverException("browser already gone")),
	]

	def setUp(self):
		super().setUp()

		edge, tasks = browser.webdriver.Edge, main.rewards_tasks.RewardsTaskUtils
		self.addCleanup(setattr, browser.webdriver, "Edge", edge)
		self.addCleanup(setattr, main.rewards_tasks, "RewardsTaskUtils", tasks)

	def _install(self, fail_at, exc, started, quit_cleanly):
		def account_of(options):
			flag = next(a for a in options.arguments if a.startswith("--user-data-dir="))

			return os.path.basename(flag.split("=", 1)[1])

		class Driver:
			def __init__(self, options, service=None):
				self.name = account_of(options)
				started.append(self.name)

				if self.name == "two" and fail_at == "start":
					raise exc

			def quit(self):
				if self.name == "two" and fail_at == "quit":
					raise exc

				quit_cleanly.append(self.name)

		class Tasks:
			def __init__(self, driver):
				self.driver = driver

				if driver.name == "two" and fail_at == "connect":
					raise exc

			def complete_all_tasks(self):
				if self.driver.name == "two" and fail_at == "tasks":
					raise exc

		browser.webdriver.Edge = Driver
		main.rewards_tasks.RewardsTaskUtils = Tasks

	def test_the_remaining_accounts_still_run(self):
		for label, fail_at, exc in self.CASES:
			with self.subTest(case=label):
				started, quit_cleanly = [], []
				self._install(fail_at, exc, started, quit_cleanly)
				accounts_for("one,two,three")

				self.assertEqual(main.main(), 0)
				self.assertEqual(started, ["one", "two", "three"])

				# Whatever went wrong, a browser that started has to be shut
				# down or its profile stays locked against the next run.
				expected = ["one", "three"] if fail_at in ("start", "quit") else ["one", "two", "three"]
				self.assertEqual(quit_cleanly, expected)

	def test_a_lone_accounts_failure_is_still_a_failing_exit_code(self):
		# The unset, unchanged path. Its failure has nothing left to protect, so
		# it must not be swallowed into a success.
		accounts_for(None)
		real = main.run_account
		self.addCleanup(setattr, main, "run_account", real)

		def boom(_):
			raise WebDriverException("chrome not reachable")

		main.run_account = boom

		self.assertEqual(main.main(), 1)


@unittest.skipUnless(
	os.environ.get("REWARDS_BROWSER_TESTS"),
	"starts Edge twice; set REWARDS_BROWSER_TESTS=1 to run",
)
class TestTwoRealProfiles(EnvironmentTestCase):
	"""Two profiles, two independent identities, each surviving a restart.

	Keyed on bing.com's own MUID rather than an injected cookie: one added
	through webdriver is not written to the profile the way a Set-Cookie is, so
	it would prove nothing about a sign-in outliving the browser.
	"""

	def setUp(self):
		super().setUp()

		self.first, self.second = accounts_for("mat_a,mat_b")

		for account in (self.first, self.second):
			self.addCleanup(shutil.rmtree, account.user_data_dir, ignore_errors=True)

	def identity_of(self, account):
		from selenium import webdriver

		driver = webdriver.Edge(options=browser.build_options(account))

		try:
			driver.get("https://www.bing.com")
			cookie = driver.get_cookie("MUID")

			return cookie["value"] if cookie else None
		finally:
			driver.quit()

	def test_two_profiles_hold_two_persistent_identities(self):
		first_id = self.identity_of(self.first)
		second_id = self.identity_of(self.second)

		self.assertIsNotNone(first_id)
		self.assertIsNotNone(second_id)
		self.assertNotEqual(first_id, second_id)

		# The point of a profile per account: the identity has to outlive the
		# browser, or a sign-in would not either.
		self.assertEqual(self.identity_of(self.first), first_id)
		self.assertEqual(self.identity_of(self.second), second_id)

		for account in (self.first, self.second):
			self.assertTrue(os.path.isdir(account.user_data_dir))

		self.assertEqual(
			len({os.path.realpath(a.user_data_dir) for a in (self.first, self.second)}), 2
		)


if __name__ == "__main__":
	unittest.main()
