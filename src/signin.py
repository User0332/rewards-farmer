"""Sign a profile in from inside the container.

Chromium encrypts cookie values with a key it takes from the operating system.
On Windows that key is wrapped with DPAPI and tied to the Windows account; on
macOS it is wrapped with the login Keychain. The container can unwrap neither,
so a profile signed in on such a host arrives looking healthy and behaving as
though it were logged out.

The way out is to sign in with the browser that will read the profile. This
gives that browser a virtual display and puts the display on the user's screen
over noVNC, so the whole flow needs nothing on the host but a web browser:

    docker compose run --rm --service-ports signin

Sign-in itself stays manual. Nothing here reads, stores or types a credential.
"""

import contextlib
import logging
import os
import signal
import socket
import subprocess
import sys
import time

import accounts
import log_utils

logger = logging.getLogger(__name__)

SIGNIN_URL = "https://rewards.bing.com"

# Edge exits about this fast when it refuses to open the profile at all, which
# is a different failure from a user closing the window and deserves a
# different message.
FAST_EXIT_SECONDS = 3

DISPLAY = ":99"

# The same geometry main.build_options gives the headless window. The pointer
# code works in viewport coordinates, so a profile signed in on a smaller screen
# would carry metrics a run then disagrees with.
SCREEN = "1920x1080x24"

# Never published. websockify reaches it over loopback inside this container,
# and x11vnc is bound so that nothing else can.
VNC_PORT = 5900

# Published, but only onto the host's loopback by the compose file.
BRIDGE_PORT = 6080

NOVNC_ROOT = "/usr/share/novnc"

STARTUP_TIMEOUT = 20
SHUTDOWN_TIMEOUT = 10


def account_to_sign_in() -> accounts.Account:
	"""The single profile this invocation signs in.

	One browser window, so one account. Several names is a user error rather
	than a reason to guess at which was meant, and the message names them so the
	reader can see what to run instead.

	Resolution goes through accounts.configured() rather than reimplementing the
	name rules here. Two implementations drift, and the one that drifts is the
	one nobody is reading when a name resolves onto the wrong directory.
	"""
	configured = accounts.configured()

	if len(configured) > 1:
		names = ", ".join(account.name for account in configured)

		raise ValueError(
			f"sign in one account at a time; {accounts.ENV_VAR} names {names}. "
			f"Run this once per name, starting with "
			f"{accounts.ENV_VAR}={configured[0].name}"
		)

	return configured[0]


def is_listening(host: str, port: int, timeout: float = 1.0) -> bool:
	"""Whether something accepts a connection there right now."""
	try:
		with socket.create_connection((host, port), timeout=timeout):
			return True
	except OSError:
		return False


def _wait_until(predicate, description: str, timeout: int = STARTUP_TIMEOUT) -> None:
	"""Poll rather than sleep a guessed interval.

	A fixed sleep is either too short on a loaded machine, where it produces a
	failure that looks like the feature being broken, or too long on every
	other machine.
	"""
	deadline = time.monotonic() + timeout

	while time.monotonic() < deadline:
		if predicate():
			return

		time.sleep(0.1)

	raise TimeoutError(f"{description} did not come up within {timeout}s")


def _terminate(process: subprocess.Popen, name: str) -> None:
	"""Ask a process to stop, and insist only if asking fails."""
	if process.poll() is not None:
		return

	process.terminate()

	try:
		process.wait(timeout=SHUTDOWN_TIMEOUT)
	except subprocess.TimeoutExpired:
		logger.warning("%s did not stop when asked; killing it", name)
		process.kill()
		process.wait()


@contextlib.contextmanager
def display_stack():
	"""A virtual display, served to the host as a web page.

	Three processes, started in dependency order and stopped in reverse:

	    Xvfb        a screen for a browser that has no monitor
	    x11vnc      that screen as a VNC server, bound to loopback
	    websockify  that server as noVNC, which a browser can open

	Only websockify binds every interface, and it has to: docker publishes a
	port by forwarding to the container's own address, so a bridge bound to
	loopback here would be unreachable through the published port. The container
	network is isolated and the compose file publishes onto the host's loopback
	only, which is where the boundary actually sits.
	"""
	processes: list[tuple[str, subprocess.Popen]] = []

	def spawn(name: str, command: list[str]) -> subprocess.Popen:
		process = subprocess.Popen(
			command,
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL,
			env={**os.environ, "DISPLAY": DISPLAY},
		)
		processes.append((name, process))

		return process

	try:
		spawn("Xvfb", ["Xvfb", DISPLAY, "-screen", "0", SCREEN, "-nolisten", "tcp"])

		# Xvfb has no port to poll. It creates its socket when it is ready to
		# take clients, so that is the signal.
		socket_path = f"/tmp/.X11-unix/X{DISPLAY.lstrip(':')}"
		_wait_until(lambda: os.path.exists(socket_path), "Xvfb")

		spawn("x11vnc", [
			"x11vnc",
			"-display", DISPLAY,
			"-rfbport", str(VNC_PORT),
			# Not reachable from outside this container, at all.
			"-localhost",
			"-nopw",
			# The user reloading the page must not end the session.
			"-forever",
			"-shared",
			"-quiet",
		])
		_wait_until(lambda: is_listening("127.0.0.1", VNC_PORT), "x11vnc")

		spawn("websockify", [
			"websockify",
			f"--web={NOVNC_ROOT}",
			str(BRIDGE_PORT),
			f"localhost:{VNC_PORT}",
		])
		_wait_until(lambda: is_listening("127.0.0.1", BRIDGE_PORT), "websockify")

		yield
	finally:
		for name, process in reversed(processes):
			_terminate(process, name)


def browser_command(account: accounts.Account) -> list[str]:
	"""Edge, headful, on the virtual display, opened at the sign-in page."""
	return [
		"microsoft-edge",
		f"--user-data-dir={account.user_data_dir}",
		f"--profile-directory={account.profile_name}",
		# A container runs as root on a filesystem the sandbox cannot use, and
		# Chromium wants more shared memory than the default 64MB.
		"--no-sandbox",
		"--disable-dev-shm-usage",
		"--window-size=1920,1080",
		SIGNIN_URL,
	]


def _report_profile_refused(account: accounts.Account) -> None:
	"""The message for a browser that exited instead of opening.

	Without this the user is left watching an empty screen in the browser tab,
	with the reason on a log line they have no cause to read.
	"""
	logger.error("[FAIL] %s: Edge exited instead of opening the profile.", account.name)
	logger.error("       profile directory: %s", account.user_data_dir)
	logger.error("       The usual cause is that this profile is already open,")
	logger.error("       including in a run left over from earlier. Chromium allows")
	logger.error("       one process per user data directory.")


def _release_profile_lock(account: accounts.Account, browser_pid: int) -> None:
	"""Remove the profile lock our own browser left behind.

	Chromium removes SingletonLock when it is quit from inside the browser and
	leaves it when it exits on a signal, which is the path docker stop and
	Ctrl-C both take. Measured, not assumed: Edge takes the SIGTERM and exits in
	under a second, and the lock is still there afterwards.

	Left behind, it names this container, so every later run reads the profile
	as open on another machine and refuses to start - the failure the README
	warns about, except caused by the tool meant to make signing in easy.

	The lock is a symlink to "<host>-<pid>". Removing it only when it names this
	host and the process we started means a lock genuinely held by another
	machine, which is what the check exists to respect, is never touched.
	"""
	lock = os.path.join(account.user_data_dir, "SingletonLock")

	try:
		owner = os.readlink(lock)
	except OSError:
		# Gone already, which is what a window closed from inside looks like.
		return

	ours = f"{socket.gethostname()}-{browser_pid}"

	if owner != ours:
		logger.warning(
			"leaving the profile lock in place: it names %r, not this run (%r)",
			owner, ours
		)

		return

	os.unlink(lock)
	logger.debug("released the profile lock left behind by pid %s", browser_pid)


class StopRequest:
	"""Set when the container is asked to stop.

	The default disposition for SIGTERM ends the process where it stands, so no
	shutdown runs and the browser is orphaned holding the profile lock. Catching
	it turns "docker stop" and Ctrl-C into the same orderly close a user gets by
	shutting the window.
	"""

	def __init__(self):
		self.requested = False

	def install(self) -> "StopRequest":
		for received in (signal.SIGTERM, signal.SIGINT):
			signal.signal(received, self._request)

		return self

	def _request(self, signum, frame) -> None:
		self.requested = True


def main() -> int:
	log_utils.setup_logging()

	try:
		account = account_to_sign_in()
	except ValueError as exc:
		logger.error("[FAIL] %s", exc)

		return 2

	with display_stack():
		browser = subprocess.Popen(
			browser_command(account),
			# There is no dbus in a container, so Edge writes about twenty
			# ERROR lines about failing to reach it before it has drawn
			# anything. None of them mean the browser is unwell, and left in
			# they bury the one line the user has to act on.
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL,
			env={**os.environ, "DISPLAY": DISPLAY},
		)
		started = time.monotonic()

		logger.info("Signing in to the %s profile at %s", account.name, account.user_data_dir)
		logger.info("")
		logger.info("    Open http://localhost:%s in a browser on this machine.", BRIDGE_PORT)
		logger.info("")
		logger.info("Sign in there, then close the Edge window on that screen.")
		logger.info("Closing the window ends this container; nothing else needs doing.")

		stop = StopRequest().install()

		try:
			while browser.poll() is None and not stop.requested:
				time.sleep(0.5)

			if browser.poll() is not None and time.monotonic() - started < FAST_EXIT_SECONDS:
				_report_profile_refused(account)

				return 1
		finally:
			# Stop the browser first, then clear the lock it leaves behind on
			# any exit that is not a window close. Order matters: reading the
			# lock's owner is only meaningful once the process naming it is
			# known to be gone.
			_terminate(browser, "Edge")
			_release_profile_lock(account, browser.pid)

	logger.info("%s: browser closed, sign-in saved to the profile.", account.name)

	return 0


if __name__ == "__main__":
	sys.exit(main())
