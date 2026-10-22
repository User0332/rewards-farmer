"""Sign-in owns its lock and browsers until the session is saved."""

import json
import io
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from file_replace import read_file, replace_file
from rfapp import paths, settings
from rfapp.envfile import EnvFile


_logger = logging.getLogger("rfapp")


def _status_path(account_id: str) -> Path:
	return paths.runs_dir() / f"signin-{account_id.replace('@', '')}.json"


def _cancel_path(account_id: str) -> Path:
	return paths.runs_dir() / f"signin-{account_id.replace('@', '')}.cancel"


def _write_status(account_id: str, state: str, error_code: str | None = None, detail: str | None = None, pid: int | None = None) -> dict:
	message = f"errors.browser.{error_code}" if state == "failed" and error_code not in (None, "crashed") else f"accounts.signin.{state}"
	if error_code == "terms_of_use":
		message = "errors.state.terms_of_use"
	value = {"account_id": account_id, "state": state, "error_code": error_code, "message_key": message, "detail": detail, "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"), "pid": os.getpid() if pid is None else pid}
	path = _status_path(account_id)
	name = None
	try:
		with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".signin-", delete=False) as stream:
			name = stream.name
			json.dump(value, stream, ensure_ascii=False)
			stream.flush()
			os.fsync(stream.fileno())
		replace_file(name, path)
	finally:
		if name is not None:
			Path(name).unlink(missing_ok=True)
	return value


def _quit(driver) -> None:
	if driver is not None:
		try:
			driver.quit()
		except Exception:
			_logger.exception("Could not close the sign-in browser")


def run(account_id, new) -> int:
	import dotenv
	try:
		text = read_file(paths.env_path())
	except FileNotFoundError:
		text = ""
	dotenv.load_dotenv(stream=io.StringIO(text))
	os.environ["REWARDS_HEADLESS"] = "0"
	os.environ["USE_VIRTUAL_DESKTOP"] = "0"
	# browser fixes HEADLESS at import, so it belongs after these overrides.
	import accounts
	import browser
	import element_selectors
	import rewards_state
	import run_lock
	from selenium import webdriver
	from selenium.common.exceptions import WebDriverException
	from rfapp import accounts_admin, schedule

	if not isinstance(account_id, str) or new and account_id == "@root" or account_id != "@root" and not accounts.directory_name_is_valid(account_id):
		raise ValueError("Invalid profile id")
	root = settings.user_data_dir()
	if account_id != "@root":
		profile = root / account_id
		if not profile.resolve().is_relative_to(root.resolve()) or profile.resolve() == root.resolve():
			raise ValueError("Profile resolves outside the data directory")
	else:
		profile = root
	lock = run_lock.acquire("signin", account=account_id, directory=str(root))
	if lock is None:
		_write_status(account_id, "busy")
		return 3
	visible = None
	verification = None
	state = "failed"
	session_saved = False
	error_code = None
	detail = None
	try:
		_write_status(account_id, "starting")
		if new and (root / "Local State").is_file() and "@root" in settings.active_accounts():
			accounts_admin.migrate_root_profile(account_id)
		account = accounts.get_default_account() if account_id == "@root" else accounts.Account(account_id, str(profile), "Default")
		try:
			visible = webdriver.Edge(options=browser.build_options(account), service=browser.build_service())
			visible.set_page_load_timeout(60)
			visible.get("https://rewards.bing.com/")
		except WebDriverException as exc:
			error_code, detail = browser.start_failure_code(exc), str(exc)
		else:
			_write_status(account_id, "waiting")
			deadline = time.monotonic() + 15 * 60
			closed = False
			verify = True
			while time.monotonic() < deadline:
				if _cancel_path(account_id).exists():
					state, verify = "cancelled", False
					break
				try:
					url = visible.current_url
				except Exception:
					closed = True
					break
				if urlsplit(url).hostname == "rewards.bing.com":
					balance = rewards_state.read_balance(visible, element_selectors.ElementSelectionUtils(visible))
					if balance is not None:
						time.sleep(3)
						state, verify = "signed_in", False
						break
				time.sleep(2)
			_quit(visible)
			visible = None
			if verify:
				_write_status(account_id, "verifying")
				try:
					options = browser.build_options(account)
					options.add_argument("--headless=new")
					verification = webdriver.Edge(options=options, service=browser.build_service())
					verification.set_page_load_timeout(60)
					snapshot = rewards_state.read_state(verification, element_selectors.ElementSelectionUtils(verification))
					if snapshot.unreadable == "signed_out":
						state = "cancelled" if closed else "timed_out"
					else:
						state = "signed_in"
						error_code = "terms_of_use" if snapshot.unreadable == "terms_of_use" else None
				except WebDriverException as exc:
					state, error_code, detail = "failed", browser.start_failure_code(exc), str(exc)
				finally:
					_quit(verification)
					verification = None
		if new and state == "signed_in":
			session_saved = True
			editor = EnvFile.load(paths.env_path())
			active = [item for item in settings.active_accounts() if item != "@root"]
			if account_id.casefold() not in [item.casefold() for item in active]:
				active.append(account_id)
			editor.set("REWARDS_ACCOUNTS", ",".join(active))
			editor.save()
			schedule.ensure()
	except Exception as exc:
		_logger.exception("Sign-in failed for profile %s", account_id)
		state = "failed"
		error_code = getattr(exc, "code", "unknown")
		detail = str(exc)
	finally:
		_quit(visible)
		_quit(verification)
		try:
			if new and not session_saved and state in ("failed", "cancelled", "timed_out") and profile.exists():
				if not accounts_admin._recycle([profile]):
					_logger.warning("Sign-in profile could not be recycled: %s", profile)
					detail = (detail + "; " if detail else "") + f"Profile data could not be recycled: {profile}"
			_write_status(account_id, state, error_code, detail)
			_cancel_path(account_id).unlink(missing_ok=True)
		finally:
			lock.release()
	return 0 if state == "signed_in" else 1
