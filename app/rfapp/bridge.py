from datetime import datetime, timezone
import functools
import importlib
import io
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from urllib.parse import urlsplit

import requests
from dotenv import dotenv_values
from file_replace import read_file

from rfapp import __version__, paths

_log = logging.getLogger("rfapp.bridge")
_RUN_ID = re.compile(r"\d{8}T\d{6}Z-[a-f0-9]{4}\Z")
_TASK_IDS = ("daily_set", "explore_on_bing", "visual_search", "misc_cards", "quests", "searches", "bonus")
_ALLOWED_HOSTS = frozenset(("github.com", "rewards.bing.com", "account.microsoft.com", "ollama.com", "openrouter.ai", "discord.com", "support.discord.com"))
_SECRET_SETTINGS = {"run.openrouter_key": "OPENROUTER_API_KEY", "notify.webhook_url": "REWARDS_WEBHOOK_URL"}


class _BridgeError(ValueError):
	def __init__(self, code: str, *, detail=None, fields=None, params=None):
		super().__init__(detail or code)
		self.code = code
		self.detail = detail
		self.fields = fields
		self.params = params or {}


def _response_error(exc):
	code = getattr(exc, "code", "internal")
	if not isinstance(code, str):
		code = "internal"
	return {"ok": False, "error": {"code": code, "message_key": "errors.app." + code.replace(".", "_"), "params": getattr(exc, "params", None) or {}, "detail": getattr(exc, "detail", None) or (str(exc) if code == "internal" else None), "fields": getattr(exc, "fields", None)}}


def _method(required=(), optional=()):
	def decorate(function):
		@functools.wraps(function)
		def wrapped(self, request):
			try:
				if not isinstance(request, dict) or any(key not in request for key in required) or any(key not in (*required, *optional) for key in request):
					raise _BridgeError("bad_request")
				return self._redact_response({"ok": True, "data": function(self, request)})
			except BaseException as exc:
				if getattr(exc, "code", "internal") == "internal":
					_log.exception("Bridge call %s failed", function.__name__)
				return self._redact_response(_response_error(exc))
		return wrapped
	return decorate


def _boolean(value):
	if type(value) is not bool:
		raise _BridgeError("bad_request")
	return value


def _string(value):
	if not isinstance(value, str) or not value:
		raise _BridgeError("bad_request")
	return value


def _integer(value, minimum=0, maximum=None):
	if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
		raise _BridgeError("bad_request")
	return value


def _account(value):
	_string(value)
	if value != "@root" and (value in (".", "..") or value.endswith(".") or not re.fullmatch(r"[A-Za-z0-9_.-]+", value)):
		raise _BridgeError("bad_request")
	return value


def _run_id(value):
	if not isinstance(value, str) or not _RUN_ID.fullmatch(value):
		raise _BridgeError("bad_request")
	return value


def _utc():
	return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Bridge:
	def __init__(self, window=None, push=None, dependencies=None):
		self._window = window
		self._push_callback = push
		self._dependencies = dependencies or {}
		self._lock = threading.RLock()
		self._settings_lock = threading.RLock()
		self._ready = threading.Event()
		self._closed = threading.Event()
		self._stop_threads = {}
		self._context_callback = None
		self._schedule_callback = None
		self._secret_values = set()
		self._secret_refresh_lock = threading.Lock()
		self._secret_env_signature = None
		self._ready_callback = None

	def _remember_secrets(self, values):
		with self._lock:
			self._secret_values.update(value for key, value in values.items() if key in _SECRET_SETTINGS and isinstance(value, str) and value)

	def _refresh_secrets(self):
		with self._secret_refresh_lock:
			try:
				path = paths.env_path()
				try:
					state = path.stat()
					signature = (str(path), state.st_mtime_ns, state.st_size)
				except FileNotFoundError:
					signature = (str(path), None, None)
				if signature == self._secret_env_signature:
					return
				try:
					env = dotenv_values(stream=io.StringIO(read_file(path)), interpolate=False) if signature[1] is not None else {}
				except FileNotFoundError:
					env = {}
				self._remember_secrets({key: env.get(name) for key, name in _SECRET_SETTINGS.items()})
				self._secret_env_signature = signature
			except (OSError, ValueError):
				_log.warning("Could not read secret values for bridge response redaction")

	def _redact_response(self, response):
		self._refresh_secrets()
		with self._lock:
			secrets = tuple(sorted(self._secret_values, key=len, reverse=True))
		pattern = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(secret) for secret in secrets) + r")(?!\w)") if secrets else None
		def clean(value):
			if isinstance(value, str):
				return pattern.sub("<redacted>", value) if pattern else value
			if isinstance(value, dict):
				return {key: clean(item) for key, item in value.items()}
			if isinstance(value, (list, tuple)):
				return [clean(item) for item in value]
			return value
		return clean(response)

	def _public_settings(self, values):
		self._remember_secrets(values)
		return {"values": {key: value for key, value in values.items() if key not in _SECRET_SETTINGS}, "secrets_set": {key: bool(values.get(key)) for key in _SECRET_SETTINGS}}

	def _schedule_changed(self, status=None):
		if self._schedule_callback:
			self._schedule_callback(status)

	def _module(self, name):
		return self._dependencies.get(name) or importlib.import_module(name if name in ("run_lock", "run_record") else "rfapp." + name)

	def _push(self, topic, data):
		with self._lock:
			callback = self._push_callback
		if callback:
			callback(topic, self._redact_response(data))

	def _accounts(self):
		return self._module("accounts_admin").list_accounts()["accounts"]

	def _context_changed(self):
		if self._context_callback:
			self._context_callback()

	def _known_account(self, account_id):
		_account(account_id)
		if not any(account["id"] == account_id for account in self._accounts()):
			raise _BridgeError("accounts.unknown")
		return account_id

	def _run(self, run_id):
		_run_id(run_id)
		if not (paths.runs_dir() / (run_id + ".jsonl")).is_file():
			raise _BridgeError("run.unknown")
		try:
			return self._module("runs").load_run(run_id, self._module("prefs").load().get("account_labels", {}), str(self._module("settings").user_data_dir()))
		except FileNotFoundError as exc:
			raise _BridgeError("run.unknown") from exc

	def _overview(self):
		values = self._module("settings").load()
		self._remember_secrets(values)
		p = self._module("prefs").load()
		result = self._module("runs").overview(values, p, datetime.now(timezone.utc))
		result["schedule"] = self._module("schedule").status()
		result["generated_at"] = _utc()
		result["signin_active"] = self._signin_active()
		return result

	def _signin_active(self):
		admin = self._module("accounts_admin")
		for status_file in paths.runs_dir().glob("signin-*.json"):
			try:
				account_id = json.loads(read_file(status_file))["account_id"]
				status = admin.get_signin_status(account_id)
				if status["state"] in ("starting", "waiting", "verifying"):
					return {"account_id": account_id}
			except (OSError, ValueError, KeyError):
				_log.warning("Unreadable sign-in status: %s", status_file.name)
		return None

	def _spawn(self, args):
		flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
		options = {"cwd": paths.data_home(), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
		try:
			return subprocess.Popen(paths.launcher_command(args), creationflags=flags | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0), **options)
		except OSError:
			return subprocess.Popen(paths.launcher_command(args), creationflags=flags, **options)

	def _close(self):
		self._closed.set()
		with self._lock:
			workers = tuple(self._stop_threads.values())
		for worker in workers:
			if worker is not threading.current_thread():
				worker.join(timeout=2)

	@_method()
	def ui_ready(self, request):
		self._ready.set()
		if self._ready_callback:
			self._ready_callback()
		return {}

	@_method()
	def app_info(self, request):
		from rfapp.window import _theme, _reduced_motion
		p = self._module("prefs").load()
		index = json.loads(paths.resource("ui", "i18n", "index.json").read_text(encoding="utf-8"))
		locales = index["locales"]
		lang, direction, locale = self._module("i18n").resolve(p.get("language", "system"), [entry["code"] for entry in locales])
		return {"version": __version__, "frozen": paths.is_frozen(), "data_home": str(paths.data_home()), "platform": "win32", "lang": lang, "dir": direction, "format_locale": locale, "theme": _theme(), "reduced_motion": _reduced_motion(), "setup_done": bool(p.get("setup_done")), "has_accounts": bool(self._accounts()), "task_ids": list(_TASK_IDS), "locales": locales}

	@_method(required=("url",))
	def open_external(self, request):
		url = _string(request["url"])
		try:
			parsed = urlsplit(url)
			allowed = parsed.scheme == "https" and parsed.hostname in _ALLOWED_HOSTS and not parsed.username and not parsed.password and parsed.port in (None, 443) and not any(ord(char) < 32 for char in url)
		except ValueError:
			allowed = False
		if not allowed:
			raise _BridgeError("url_not_allowed")
		os.startfile(url)
		return {}

	@_method()
	def get_overview(self, request):
		return self._overview()

	@_method(optional=("range", "account_id", "week_start"))
	def get_stats(self, request):
		values = self._module("settings").load()
		self._remember_secrets(values)
		return self._module("stats").get_stats(request, settings=values, prefs=self._module("prefs").load(), now=datetime.now(timezone.utc))

	@_method()
	def get_live(self, request):
		run_id = self._module("runs").active_run_id()
		if not run_id:
			return {"run": None, "log": None}
		detail = self._run(run_id)
		log_path = paths.runs_dir() / (run_id + ".log")
		size = log_path.stat().st_size if log_path.exists() else 0
		log = self._read_log(run_id, max(0, size - 65536), 65536)
		return {"run": detail, "log": {"text": log["text"], "next_offset": log["next_offset"]}}

	@_method(optional=("limit", "before", "include_checks"))
	def get_history(self, request):
		limit = _integer(request.get("limit", 50), 1, 200)
		before = _run_id(request["before"]) if "before" in request else None
		include_checks = _boolean(request.get("include_checks", True))
		return self._module("runs").history(limit=limit, before=before, include_checks=include_checks, labels=self._module("prefs").load().get("account_labels", {}), user_data_dir=str(self._module("settings").user_data_dir()))

	@_method(required=("run_id",))
	def get_run(self, request):
		return self._run(request["run_id"])

	def _read_log(self, run_id, offset, max_bytes):
		path = paths.runs_dir() / (run_id + ".log")
		try:
			with path.open("rb") as source:
				source.seek(0, 2)
				size = source.tell()
				start = min(offset, size)
				source.seek(start)
				while start < size:
					byte = source.read(1)
					if not byte or byte[0] & 0xC0 != 0x80:
						break
					start += 1
				source.seek(start)
				# Three lookahead bytes cover UTF-8's longest code point without splitting a page.
				chunk = source.read(min(max_bytes + 3, size - start))
				if start + len(chunk) == size:
					for back in range(1, min(3, len(chunk)) + 1):
						lead = chunk[-back]
						if lead & 0xC0 == 0x80:
							continue
						if 0xC2 <= lead <= 0xF4:
							width = 2 if lead < 0xE0 else 3 if lead < 0xF0 else 4
							if width > back and self._module("runs").active_run_id() == run_id:
								# Wait for the active writer, but let a stopped or crashed log reach EOF.
								chunk = chunk[:-back]
						break
				length = min(max_bytes, len(chunk))
				while 0 < length < len(chunk) and chunk[length] & 0xC0 == 0x80:
					length -= 1
				if length == 0 and chunk:
					# A byte limit smaller than the first character must still advance paging.
					length = 1
					while length < len(chunk) and chunk[length] & 0xC0 == 0x80:
						length += 1
				text = chunk[:length]
				end = start + length
		except FileNotFoundError:
			size, end, text = 0, 0, b""
		return {"text": text.decode("utf-8", errors="replace"), "next_offset": end, "eof": end >= size, "size": size}

	@_method(required=("run_id",), optional=("offset", "max_bytes"))
	def get_run_log(self, request):
		run_id = _run_id(request["run_id"])
		offset = _integer(request.get("offset", 0))
		max_bytes = _integer(request.get("max_bytes", 65536), 1)
		self._run(run_id)
		return self._read_log(run_id, offset, max_bytes)

	@_method(optional=("account_ids", "check_only"))
	def start_run(self, request):
		check = _boolean(request.get("check_only", False))
		ids = request.get("account_ids")
		if ids is not None:
			if not isinstance(ids, list) or not ids or len(set(_account(value) for value in ids)) != len(ids):
				raise _BridgeError("bad_request")
			if "@root" in ids and len(ids) != 1:
				raise _BridgeError("bad_request")
		with self._settings_lock:
			if self._module("run_lock").status(directory=str(self._module("settings").user_data_dir())):
				raise _BridgeError("run.busy")
			known = {account["id"] for account in self._accounts() if not account.get("paused")}
			if not known or (ids is not None and any(value not in known for value in ids)):
				raise _BridgeError("run.no_accounts")
			args = ["--check-now" if check else "--run-now"]
			if ids is not None:
				args.extend(("--accounts", ",".join(ids)))
			self._spawn(args)
		return {"started": True}

	def _stop_fallback(self, run_id):
		if self._closed.wait(90):
			return
		try:
			lock = self._module("run_lock")
			directory = str(self._module("settings").user_data_dir())
			info = lock.status(directory=directory)
			if info is None or info.run_id != run_id or not info.supervisor_pid:
				return
			subprocess.run(["taskkill", "/PID", str(info.supervisor_pid), "/F"], capture_output=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=True)
			deadline = time.monotonic() + 30
			while lock.status(directory=directory) is not None and time.monotonic() < deadline:
				if self._closed.wait(.5):
					return
			if lock.status(directory=directory) is not None:
				_log.error("Run %s still holds the lock after hard stop", run_id)
				return
			record = self._module("run_record")
			path = paths.runs_dir() / (run_id + ".jsonl")
			if not any(event["type"] == "run_end" for event in record.read_events(str(path))):
				record.append_event(str(path), "run_end", run_id, result="stopped", exit_code=4, written_by="app", detail=None)
		except Exception:
			_log.exception("Hard stop failed for run %s", run_id)

	@_method(required=("run_id",))
	def stop_run(self, request):
		run_id = _run_id(request["run_id"])
		info = self._module("run_lock").status(directory=str(self._module("settings").user_data_dir()))
		if info is None or info.run_id != run_id:
			raise _BridgeError("run.not_active")
		if not info.supervisor_pid:
			raise _BridgeError("run.not_stoppable")
		(paths.runs_dir() / (run_id + ".stop")).write_text(json.dumps({"by": "app", "at": _utc()}), encoding="utf-8")
		with self._lock:
			if run_id not in self._stop_threads or not self._stop_threads[run_id].is_alive():
				worker = threading.Thread(target=self._stop_fallback, args=(run_id,), name="rfapp-stop", daemon=True)
				self._stop_threads[run_id] = worker
				worker.start()
		return {"stopping": True}

	@_method(required=("account_id", "target"))
	def set_goal(self, request):
		target = request["target"]
		if target is not None:
			_integer(target, 100, 1000000)
		with self._settings_lock:
			account_id = self._known_account(request["account_id"])
			p = self._module("prefs").load()
			goals = dict(p.get("goals", {}))
			if target is None:
				goals.pop(account_id, None)
			else:
				goals[account_id] = target
			self._module("prefs").save({"goals": goals})
		self._context_changed()
		self._push("overview.changed", {})
		return {"goal": next((account.get("goal") for account in self._overview()["accounts"] if account["account_id"] == account_id), None)}

	@_method()
	def get_settings(self, request):
		return {**self._public_settings(self._module("settings").load()), "schedule": self._module("schedule").status()}

	@_method(required=("changes",))
	def save_settings(self, request):
		changes = request["changes"]
		if not isinstance(changes, dict):
			raise _BridgeError("bad_request")
		settings = self._module("settings")
		self._remember_secrets(changes)
		# Serialize settings transactions separately so slow disk or scheduler work cannot block Stop.
		with self._settings_lock:
			current = settings.load()
			self._remember_secrets(current)
			fields = settings.validate(changes, current)
			if fields:
				raise _BridgeError("settings.invalid", fields=fields)
			result = settings.save(changes)
			public = self._public_settings(result.get("values", {**current, **changes}))
		if "app.language" in changes:
			self._module("i18n").reload()
		self._context_changed()
		self._schedule_changed(result.get("schedule"))
		self._push("settings.changed", {})
		self._push("overview.changed", {})
		return {**result, **public}

	@_method()
	def get_schedule_status(self, request):
		return self._module("schedule").status()

	@_method()
	def replace_legacy_task(self, request):
		with self._settings_lock:
			result = self._module("schedule").replace_legacy_task()
		self._schedule_changed(result)
		self._push("overview.changed", {})
		return result

	@_method(required=("kind",))
	def pick_path(self, request):
		kind = request["kind"]
		if not isinstance(kind, str) or kind not in ("edge", "driver", "data_folder"):
			raise _BridgeError("bad_request")
		import webview
		if kind == "data_folder":
			result = self._window.create_file_dialog(webview.FileDialog.FOLDER)
		else:
			result = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=("Edge (msedge.exe)" if kind == "edge" else "Edge driver (msedgedriver.exe)",))
		return {"path": str(result[0]) if result else None}

	@_method(required=("base_url",))
	def list_ollama_models(self, request):
		base = _string(request["base_url"]).rstrip("/")
		try:
			parsed = urlsplit(base)
		except ValueError as exc:
			raise _BridgeError("bad_request") from exc
		if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
			raise _BridgeError("bad_request")
		try:
			response = requests.get(base.removesuffix("/v1") + "/api/tags", timeout=5)
			response.raise_for_status()
			return {"models": sorted(item["name"] for item in response.json()["models"])}
		except Exception as exc:
			raise _BridgeError("ollama.unreachable", detail=type(exc).__name__) from exc

	@_method()
	def export_diagnostics(self, request):
		import webview
		result = self._window.create_file_dialog(webview.FileDialog.SAVE, save_filename="rewards-farmer-diagnostics-" + datetime.now().strftime("%Y%m%d-%H%M") + ".zip")
		if not result:
			return {"path": None}
		path = Path(result[0] if isinstance(result, (list, tuple)) else result)
		try:
			self._module("diagnostics").export(path)
		except Exception as exc:
			raise _BridgeError("diagnostics.failed", detail=type(exc).__name__) from exc
		return {"path": str(path)}

	@_method(optional=("force",))
	def check_updates(self, request):
		result = self._module("updates").check(force=_boolean(request.get("force", False)), mutation_lock=self._settings_lock)
		if result["available"]:
			self._push("update.available", result)
		return result

	@_method(required=("version",))
	def dismiss_update(self, request):
		version = _string(request["version"])
		if not re.fullmatch(r"\d+(?:\.\d+)*", version):
			raise _BridgeError("bad_request")
		with self._settings_lock:
			self._module("prefs").save({"update_dismissed_version": version})
		return {}

	@_method()
	def get_legacy_install(self, request):
		return self._module("accounts_admin").get_legacy_install()

	@_method()
	def import_legacy_install(self, request):
		with self._settings_lock:
			result = self._module("accounts_admin").import_legacy_install()
		self._context_changed()
		self._push("overview.changed", {})
		return result

	@_method()
	def complete_setup(self, request):
		from rfapp.envfile import EnvFile
		with self._settings_lock:
			env = EnvFile.load(paths.env_path())
			if env.get("QUERY_SOURCE") is None:
				env.set("QUERY_SOURCE", "trends")
				env.save()
			self._module("prefs").save({"setup_done": True})
			status = self._module("schedule").ensure()
		self._schedule_changed(status)
		return {}

	@_method()
	def list_accounts(self, request):
		return self._module("accounts_admin").list_accounts()

	@_method(required=("account_id",))
	def get_account_picture(self, request):
		account_id = self._known_account(request["account_id"])
		return self._module("accounts_admin").get_account_picture(account_id)

	@_method(optional=("confirm_migration",))
	def add_account(self, request):
		confirmed = _boolean(request.get("confirm_migration", False))
		with self._settings_lock:
			return self._module("accounts_admin").add_account(confirm_migration=confirmed)

	@_method(required=("account_id",))
	def sign_in_again(self, request):
		with self._settings_lock:
			account_id = self._known_account(request["account_id"])
			return self._module("accounts_admin").sign_in_again(account_id)

	@_method(required=("account_id",))
	def get_signin_status(self, request):
		return self._module("accounts_admin").get_signin_status(_account(request["account_id"]))

	@_method(required=("account_id",))
	def cancel_signin(self, request):
		account_id = _account(request["account_id"])
		with self._settings_lock:
			self._module("accounts_admin").cancel_signin(account_id)
		return {}

	@_method(required=("account_id", "label"))
	def rename_account(self, request):
		label = _string(request["label"]).strip()
		if not 1 <= len(label) <= 40 or any(ord(char) < 32 or 127 <= ord(char) < 160 for char in label):
			raise _BridgeError("bad_request")
		with self._settings_lock:
			account_id = self._known_account(request["account_id"])
			result = self._module("accounts_admin").rename_account(account_id, label)
		self._context_changed()
		self._push("overview.changed", {})
		return result

	@_method(required=("account_id", "paused"))
	def set_account_paused(self, request):
		paused = _boolean(request["paused"])
		with self._settings_lock:
			account_id = self._known_account(request["account_id"])
			result = self._module("accounts_admin").set_account_paused(account_id, paused)
		self._context_changed()
		self._schedule_changed()
		self._push("overview.changed", {})
		return result

	@_method(required=("account_id", "delete_data"))
	def remove_account(self, request):
		delete_data = _boolean(request["delete_data"])
		with self._settings_lock:
			account_id = self._known_account(request["account_id"])
			result = self._module("accounts_admin").remove_account(account_id, delete_data)
		self._context_changed()
		self._schedule_changed()
		self._push("overview.changed", {})
		return result
