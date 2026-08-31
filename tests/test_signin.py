"""Tests for signing a profile in from inside the container.

The sign-in exists because Chromium takes its cookie key from the operating
system: a profile signed in on a Windows or macOS host cannot be read by the
container, so the container's own Edge has to write it. That browser needs a
display, and the display needs to reach the user without anything installed on
the host.

Most of what can go wrong here is not the browser. It is picking the wrong
profile directory, publishing the screen somewhere it should not be published,
and leaving a SingletonLock behind when the browser is killed rather than
closed, which makes every later run look like the profile is open elsewhere.

	python -m unittest discover -s tests
	REWARDS_BROWSER_TESTS=1 python -m unittest discover -s tests
"""

import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import accounts
import signin
from constants import USER_DATA_DIR


SIGNIN_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "src", "signin.py")


def container_address() -> str:
	"""This container's address on the docker network, not loopback."""
	return socket.gethostbyname(socket.gethostname())


def wait_for(predicate, timeout=60):
	"""Poll until true, or give up. Returns whether it came true."""
	deadline = time.monotonic() + timeout

	while time.monotonic() < deadline:
		if predicate():
			return True

		time.sleep(0.2)

	return False


class EnvironmentTestCase(unittest.TestCase):
	"""Restores everything these tests reach into, so ordering cannot matter."""

	def setUp(self):
		self.addCleanup(os.environ.pop, accounts.ENV_VAR, None)


class TestAccountSelection(EnvironmentTestCase):
	"""One browser window, so one account.

	Resolution goes through accounts.configured() rather than reimplementing the
	name rules, so a name this refuses is exactly a name a run refuses.
	"""

	def test_unset_is_the_default_profile(self):
		os.environ.pop(accounts.ENV_VAR, None)

		self.assertEqual(signin.account_to_sign_in().user_data_dir, USER_DATA_DIR)

	def test_one_name_is_that_profile(self):
		os.environ[accounts.ENV_VAR] = "personal"

		account = signin.account_to_sign_in()

		self.assertEqual(account.name, "personal")
		self.assertEqual(account.user_data_dir, os.path.join(USER_DATA_DIR, "personal"))

	def test_several_names_are_refused(self):
		os.environ[accounts.ENV_VAR] = "personal,spare"

		with self.assertRaises(ValueError) as caught:
			signin.account_to_sign_in()

		# Naming both is the whole value of the message: the reader has to be
		# able to see what to run instead without going to the source.
		message = str(caught.exception)

		self.assertIn("personal", message)
		self.assertIn("spare", message)

	def test_a_name_a_run_would_refuse_is_refused_here_too(self):
		# "..." is data-dir itself once Win32 strips the trailing dot. If this
		# resolved, a sign-in would write the default profile under a name that
		# reads as a separate account.
		os.environ[accounts.ENV_VAR] = "..."

		with self.assertRaises(ValueError):
			signin.account_to_sign_in()


class TestBrowserCommand(EnvironmentTestCase):
	"""What the browser is told to do decides what the user first sees.

	Cheap to assert here and expensive to notice otherwise: the failure is a
	browser that came up perfectly well on the wrong thing, which reads as the
	sign-in working right up until nobody can find where to type.
	"""

	def test_the_first_run_dialog_is_skipped(self):
		"""Every sign-in is a new profile, and a new profile opens on the terms
		dialog rather than the page it was handed. Without these switches the
		first thing on the noVNC screen is a modal about terms of service with
		no Rewards page behind it. A run never meets this, because msedgedriver
		passes the same two switches itself, so only this path needs them.
		"""
		command = signin.browser_command(signin.account_to_sign_in())

		self.assertIn("--no-first-run", command)
		self.assertIn("--no-default-browser-check", command)

	def test_it_opens_the_sign_in_page(self):
		command = signin.browser_command(signin.account_to_sign_in())

		self.assertEqual(command[-1], signin.SIGNIN_URL)


@unittest.skipUnless(
	os.environ.get("REWARDS_BROWSER_TESTS"),
	"needs Xvfb, x11vnc and websockify; run in the container with "
	"REWARDS_BROWSER_TESTS=1",
)
class TestDisplayStack(unittest.TestCase):
	"""Where the screen is published, which is the part with a blast radius.

	While a sign-in is open this is a live Microsoft page, so the reachable
	surface is worth asserting rather than assuming.
	"""

	def test_the_vnc_server_never_leaves_the_container(self):
		with signin.display_stack():
			self.assertTrue(signin.is_listening("127.0.0.1", signin.VNC_PORT))
			# x11vnc is bound with -localhost, so only websockify in this same
			# container can reach it. Nothing publishes 5900 and nothing should.
			self.assertFalse(
				signin.is_listening(container_address(), signin.VNC_PORT)
			)

	def test_the_bridge_serves_novnc(self):
		with signin.display_stack():
			# The bridge does bind every interface, because docker publishes a
			# port by forwarding to the container's address: bound to loopback
			# here it would be unreachable through the published port. The
			# boundary is the 127.0.0.1 prefix on the compose publish, so the
			# host only exposes it on its own loopback.
			self.assertTrue(
				signin.is_listening(container_address(), signin.BRIDGE_PORT)
			)

			with urllib.request.urlopen(
				f"http://127.0.0.1:{signin.BRIDGE_PORT}/", timeout=15
			) as response:
				body = response.read()

			# index.html is a symlink the image adds; Debian ships only
			# vnc.html, and a bare URL 404ing reads as a broken feature.
			self.assertEqual(response.status, 200)
			self.assertIn(b"noVNC", body)


@unittest.skipUnless(
	os.environ.get("REWARDS_BROWSER_TESTS"),
	"starts Edge on a virtual display; run in the container with "
	"REWARDS_BROWSER_TESTS=1",
)
class TestCleanShutdown(EnvironmentTestCase):
	"""Stopping the container must not poison the profile.

	Chromium writes SingletonLock as a symlink naming the machine and process
	that hold the profile, and removes it on a clean exit. A browser that is
	killed leaves it behind, and every later run - container or not - reads that
	as the profile being open somewhere else and refuses to start, with an error
	that says nothing about a stale lock.

	So this is the regression test for a failure this feature is in a position
	to cause, on the path a user takes every time they press Ctrl-C.
	"""

	def setUp(self):
		super().setUp()

		os.environ[accounts.ENV_VAR] = "shutdown_probe"
		self.account = signin.account_to_sign_in()
		self.addCleanup(shutil.rmtree, self.account.user_data_dir, ignore_errors=True)

	def lock_path(self) -> str:
		return os.path.join(self.account.user_data_dir, "SingletonLock")

	def lock_exists(self) -> bool:
		# lexists, not exists: the lock is a symlink to "<host>-<pid>", which
		# does not resolve to anything. exists() follows it and reports False
		# for a lock that is very much present, which would make this test pass
		# without testing anything.
		return os.path.lexists(self.lock_path())

	def test_sigterm_closes_the_browser_and_leaves_no_lock(self):
		process = subprocess.Popen(
			[sys.executable, SIGNIN_SCRIPT],
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL,
			env=dict(os.environ),
		)
		self.addCleanup(self._make_sure_it_is_gone, process)

		self.assertTrue(
			wait_for(self.lock_exists), "the browser never took the profile lock"
		)

		process.send_signal(signal.SIGTERM)

		# Exiting 0 is half the claim: a process that died on the signal would
		# report -15 and would not have run its shutdown at all.
		self.assertEqual(process.wait(timeout=60), 0)
		self.assertFalse(
			self.lock_exists(),
			"SingletonLock survived shutdown; every later run will read this "
			"profile as open elsewhere",
		)

	def test_being_stopped_says_the_sign_in_was_probably_lost(self):
		"""Silence here cost a working sign-in and four probes chasing it.

		Chromium writes the session on its own shutdown and skips that on the
		fast exit a signal produces, so a sign-in finished just before a Ctrl-C
		is gone. The profile looks fine and fails at the next run, which is the
		worst shape a failure can take.
		"""
		process = subprocess.Popen(
			[sys.executable, SIGNIN_SCRIPT],
			stdout=subprocess.PIPE,
			stderr=subprocess.STDOUT,
			text=True,
			env=dict(os.environ),
		)
		self.addCleanup(self._make_sure_it_is_gone, process)

		self.assertTrue(wait_for(self.lock_exists), "the browser never started")

		process.send_signal(signal.SIGTERM)
		output, _ = process.communicate(timeout=60)

		self.assertIn("NOT saved", output)
		self.assertNotIn("sign-in saved to the profile", output)

	def test_a_browser_that_exits_on_its_own_does_not_warn(self):
		"""The warning has to distinguish, or it is noise on every normal run.

		The browser ending by itself is what closing the window looks like from
		here, so ending it externally exercises that branch.
		"""
		process = subprocess.Popen(
			[sys.executable, SIGNIN_SCRIPT],
			stdout=subprocess.PIPE,
			stderr=subprocess.STDOUT,
			text=True,
			env=dict(os.environ),
		)
		self.addCleanup(self._make_sure_it_is_gone, process)

		self.assertTrue(wait_for(self.lock_exists), "the browser never started")

		# Past the fast-exit window first. A browser that ends inside it has
		# refused the profile rather than been closed, which is a different
		# branch with a different message, and killing it too early tests that
		# one instead of this one.
		time.sleep(signin.FAST_EXIT_SECONDS + 1)
		subprocess.run(["pkill", "-f", "msedge"], check=False)

		output, _ = process.communicate(timeout=60)

		self.assertIn("sign-in saved to the profile", output)
		self.assertNotIn("NOT saved", output)

	def test_the_profile_can_be_opened_again_afterwards(self):
		"""The guarantee is a usable profile, not an absent file.

		Asserting only that the lock is gone would still pass if shutdown left
		the profile unopenable some other way, which is the thing a user
		actually runs into.
		"""
		for attempt in ("first", "second"):
			process = subprocess.Popen(
				[sys.executable, SIGNIN_SCRIPT],
				stdout=subprocess.DEVNULL,
				stderr=subprocess.DEVNULL,
				env=dict(os.environ),
			)
			self.addCleanup(self._make_sure_it_is_gone, process)

			self.assertTrue(
				wait_for(self.lock_exists),
				f"the {attempt} run never opened the profile",
			)

			process.send_signal(signal.SIGTERM)

			# 1 is the exit code for a browser that refused the profile, which
			# is exactly what a stale lock from the first run would produce.
			self.assertEqual(process.wait(timeout=60), 0, f"{attempt} run")

	def _make_sure_it_is_gone(self, process):
		if process.poll() is None:
			process.kill()
			process.wait(timeout=30)


class TestPublishedPort(unittest.TestCase):
	"""The compose file is where the exposure is actually decided.

	websockify binds every interface inside the container because docker
	forwards a published port to the container's address, so nothing in the
	Python constrains this. Dropping the 127.0.0.1 prefix would put a live
	Microsoft sign-in on every interface of the host, and would look like
	tidying up.
	"""

	def test_the_bridge_is_published_to_loopback_only(self):
		compose = os.path.join(os.path.dirname(__file__), "..", "docker-compose.yml")

		if not os.path.exists(compose):
			# The image carries src/ and nouns.txt and no compose file, so this
			# runs from a checkout. Skipping rather than passing: a test that
			# quietly finds nothing to check is worse than one that says so.
			self.skipTest("no docker-compose.yml here; run this from a checkout")

		with open(compose, encoding="utf-8") as handle:
			entries = [
				line.strip().lstrip("- ").strip('"')
				for line in handle
				if line.strip().startswith("-") and f":{signin.BRIDGE_PORT}" in line
			]

		self.assertTrue(entries, "nothing publishes the noVNC port at all")

		for entry in entries:
			self.assertTrue(
				entry.startswith("127.0.0.1:"),
				f"{entry!r} publishes the sign-in screen beyond loopback",
			)


class TestLockOwnership(unittest.TestCase):
	"""A lock this run did not take is not this run's to remove.

	The whole reason the lock is honoured is that a profile open on another
	machine must not be opened twice. Clearing it indiscriminately on shutdown
	would trade one failure for a worse one.
	"""

	def setUp(self):
		self.directory = tempfile.mkdtemp()
		self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)

		self.account = accounts.Account(
			name="probe", user_data_dir=self.directory, profile_name="Default"
		)
		self.lock = os.path.join(self.directory, "SingletonLock")

	def write_lock(self, owner):
		try:
			os.symlink(owner, self.lock)
		except (OSError, NotImplementedError) as exc:
			# Windows refuses symlinks without developer mode or elevation.
			self.skipTest(f"cannot create a symlink here: {exc}")

	def test_a_lock_from_another_machine_is_left_alone(self):
		self.write_lock("some-other-host-4321")

		# Says so as well as doing so: a lock left in place without a word looks
		# identical to one that was never noticed.
		with self.assertLogs(signin.logger, level="WARNING") as logged:
			signin._release_profile_lock(self.account, browser_pid=4321)

		self.assertTrue(os.path.lexists(self.lock))
		self.assertIn("some-other-host-4321", "\n".join(logged.output))

	def test_a_lock_from_another_process_here_is_left_alone(self):
		self.write_lock(f"{socket.gethostname()}-999999")

		with self.assertLogs(signin.logger, level="WARNING"):
			signin._release_profile_lock(self.account, browser_pid=4321)

		self.assertTrue(os.path.lexists(self.lock))

	def test_our_own_lock_is_removed(self):
		self.write_lock(f"{socket.gethostname()}-4321")

		signin._release_profile_lock(self.account, browser_pid=4321)

		self.assertFalse(os.path.lexists(self.lock))

	def test_no_lock_at_all_is_not_an_error(self):
		signin._release_profile_lock(self.account, browser_pid=4321)


if __name__ == "__main__":
	unittest.main()
