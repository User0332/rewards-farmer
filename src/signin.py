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
import socket
import subprocess
import time

import accounts

logger = logging.getLogger(__name__)

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
