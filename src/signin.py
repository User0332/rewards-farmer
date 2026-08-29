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

import logging

import accounts

logger = logging.getLogger(__name__)


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
