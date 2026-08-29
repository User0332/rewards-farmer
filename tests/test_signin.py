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
import socket
import sys
import unittest
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import accounts
import signin
from constants import USER_DATA_DIR


def container_address() -> str:
	"""This container's address on the docker network, not loopback."""
	return socket.gethostbyname(socket.gethostname())


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


if __name__ == "__main__":
	unittest.main()
