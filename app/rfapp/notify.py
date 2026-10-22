import importlib
import logging
import subprocess
import threading
from urllib.parse import urlsplit

import requests

from rfapp import APP_NAME, paths, user_state

_log = logging.getLogger("rfapp.notify")
_lock = threading.RLock()
_tray = None
_PROBLEMS = frozenset(("failed", "partial", "crashed", "config_error"))


def _set_tray(tray):
	global _tray
	with _lock:
		_tray = tray


def toast(title: str, body: str) -> bool:
	with _lock:
		tray = _tray
	if tray is not None:
		return tray.notify(title, body)
	icon = None
	try:
		from PIL import Image
		from rfapp.tray import _icon
		ready = threading.Event()
		with Image.open(paths.resource("ui", "img", "app-icon-256.png")) as source:
			image = source.convert("RGBA").resize((32, 32))
		def clicked():
			try:
				flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
				subprocess.Popen(paths.launcher_command([]), cwd=paths.data_home(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
			except Exception:
				_log.exception("Notification could not open the window app")
		icon = _icon("RewardsFarmerApp_" + paths.install_identity() + "_notify", image, APP_NAME, clicked=clicked)
		def setup(item):
			item.visible = True
			ready.set()
		icon.run_detached(setup=setup)
		if not ready.wait(5):
			return False
		icon.notify(body, title)
		# The shell owns displaying the balloon after API acceptance.
		threading.Event().wait(8)
		return True
	except Exception:
		_log.exception("Toast API rejected the notification")
		return False
	finally:
		if icon is not None:
			try:
				icon.stop()
			except Exception:
				_log.exception("Temporary tray icon did not stop cleanly")


def send_webhook(url: str, text: str) -> tuple[bool, str]:
	try:
		parsed = urlsplit(url)
		if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
			return False, "Webhook requires an HTTPS URL without credentials"
		response = requests.post(url, json={"username": APP_NAME, "content": text.replace("\r", " ").replace("\n", " ")}, timeout=10, allow_redirects=False)
		if 300 <= response.status_code < 400:
			_log.warning("Webhook redirect refused: HTTP %d", response.status_code)
			return False, "HTTP %d redirect refused" % response.status_code
		response.raise_for_status()
		return True, "HTTP %d" % response.status_code
	except Exception as exc:
		# Webhook tokens can appear in exception URLs, so persist only the error class.
		_log.warning("Webhook failed: %s", type(exc).__name__)
		return False, type(exc).__name__


def _state_problem(account):
	state = account.get("state_after") or account.get("state_before") or account.get("state") or {}
	return state.get("unreadable") if state.get("unreadable") in ("signed_out", "terms_of_use") else None


def summary_text(summary: dict, translator) -> tuple[str, str]:
	for account in summary.get("accounts", []):
		problem = _state_problem(account)
		if problem:
			title = translator.t("tray.notify.problem.title")
			if problem == "signed_out":
				return title, translator.t("tray.notify.signed_out", account=account["label"])
			return title, translator.t("errors.state.terms_of_use", account=account["label"])
	if summary.get("result") in _PROBLEMS:
		return translator.t("tray.notify.problem.title"), translator.t("errors.run." + summary["result"])
	points = summary.get("points_earned")
	accounts = summary.get("accounts", [])
	balances = [account.get("points_now") for account in accounts]
	balance = sum(balances) if balances and all(value is not None for value in balances) else None
	title = translator.t("tray.notify.done.title")
	if points is None:
		return title, translator.t("tray.notify.done.body_no_points")
	if balance is None:
		return title, translator.t("tray.notify.done.body_no_balance", points=points)
	return title, translator.t("tray.notify.done.body", points=points, balance=translator.number(balance))


def _eligible(summary, settings, events):
	if settings.get("notify.policy", "always") == "never" or summary.get("result") in ("checked", "busy", "stopped"):
		return False
	if any(event.get("type") == "run_start" and event.get("plan", {}).get("check_only") for event in events):
		return False
	problem = summary.get("result") in _PROBLEMS or any(_state_problem(account) for account in summary.get("accounts", []))
	ran = any(event.get("type") == "account_end" and event.get("ran_tasks") for event in events)
	return bool(problem or (settings.get("notify.policy", "always") == "always" and ran))


def _deliver(run_id, settings, window=False, state_reader=None):
	i18n = importlib.import_module("rfapp.i18n")
	prefs = importlib.import_module("rfapp.prefs")
	runs = importlib.import_module("rfapp.runs")
	single_instance = importlib.import_module("rfapp.single_instance")
	run_record = importlib.import_module("run_record")
	user_data_dir = importlib.import_module("rfapp.settings").user_data_dir
	path = paths.runs_dir() / (run_id + ".jsonl")
	events = run_record.read_events(str(path))
	summary = runs.load_run(run_id, prefs.load().get("account_labels", {}), str(user_data_dir()))
	if not _eligible(summary, settings, events):
		return
	title, body = summary_text(summary, i18n.current())
	previous = {event.get("channel") for event in events if event.get("type") == "notified"}
	if "toast" not in previous and (window or not single_instance.is_running()):
		state = (user_state.notification_state if state_reader is None else state_reader)()
		if state is None or state == 5:
			ok = toast(title, body)
			detail = None if ok else "Toast API rejected notification"
		else:
			ok, detail = False, f"held_busy_{state}"
		run_record.append_event(str(path), "notified", run_id, channel="toast", ok=ok, detail=detail)
	url = settings.get("notify.webhook_url", "")
	if not window and url and "webhook" not in previous:
		ok, detail = send_webhook(url, title + " " + body)
		run_record.append_event(str(path), "notified", run_id, channel="webhook", ok=ok, detail=detail)


def after_run(run_id: str, settings: dict) -> None:
	try:
		_deliver(run_id, settings)
	except Exception:
		_log.exception("Could not prepare notifications for run %s", run_id)
