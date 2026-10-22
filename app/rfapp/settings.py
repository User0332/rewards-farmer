"""The settings the farmer reads, with app-only state kept separate."""

import json
import io
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values
from file_replace import read_file

from rfapp import autostart, paths
from rfapp.envfile import EnvFile


SETTING_IDS = (
	"schedule.mode", "schedule.window_start", "schedule.window_end", "schedule.checks", "schedule.time", "schedule.days", "schedule.random_delay", "schedule.catch_up", "schedule.on_battery", "schedule.min_points",
	"run.browser_mode", "run.tasks_off", "run.search_source", "run.ollama_url", "run.ollama_model", "run.openrouter_key", "run.openrouter_model",
	"notify.policy", "notify.webhook_url", "app.autostart", "app.close_to_tray", "app.language", "app.update_check",
	"advanced.edge_path", "advanced.driver_path", "advanced.log_level", "advanced.data_folder",
)
_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_TASK_IDS = ("daily_set", "explore_on_bing", "visual_search", "misc_cards", "quests", "searches", "bonus")
_KEYS = {
	"schedule.mode": "REWARDS_SCHEDULE_MODE", "schedule.checks": "REWARDS_SCHEDULE_CHECKS", "schedule.time": "REWARDS_SCHEDULE_TIME", "schedule.days": "REWARDS_SCHEDULE_DAYS", "schedule.random_delay": "REWARDS_SCHEDULE_RANDOM_DELAY", "schedule.catch_up": "REWARDS_SCHEDULE_CATCH_UP", "schedule.on_battery": "REWARDS_SCHEDULE_ON_BATTERY", "schedule.min_points": "REWARDS_MIN_POINTS",
	"run.tasks_off": "REWARDS_SKIP_TASKS", "run.ollama_url": "LOCAL_LLM_BASE_URL", "run.ollama_model": "LOCAL_LLM_MODEL", "run.openrouter_key": "OPENROUTER_API_KEY", "run.openrouter_model": "OPENROUTER_MODEL",
	"notify.policy": "REWARDS_NOTIFY", "notify.webhook_url": "REWARDS_WEBHOOK_URL", "advanced.edge_path": "EDGE_BINARY", "advanced.driver_path": "MSEDGEDRIVER_PATH", "advanced.log_level": "REWARDS_FARMER_LOG_LEVEL", "advanced.data_folder": "USER_DATA_DIR",
}
_PREF_KEYS = {"app.close_to_tray": "close_to_tray", "app.language": "language", "app.update_check": "update_check"}
_DEFAULTS = {
	"schedule.mode": "automatic", "schedule.window_start": "09:45", "schedule.window_end": "21:45", "schedule.checks": 4, "schedule.time": "09:45", "schedule.days": list(_DAYS), "schedule.random_delay": 15, "schedule.catch_up": True, "schedule.on_battery": True, "schedule.min_points": 15,
	"run.browser_mode": "visible", "run.tasks_off": [], "run.search_source": "ollama", "run.ollama_url": "http://localhost:11434/v1", "run.ollama_model": "gemma3:4b", "run.openrouter_key": "", "run.openrouter_model": "openrouter/free",
	"notify.policy": "always", "notify.webhook_url": "", "app.autostart": False, "app.close_to_tray": True, "app.language": "system", "app.update_check": True,
	"advanced.edge_path": "", "advanced.driver_path": "", "advanced.log_level": "INFO", "advanced.data_folder": "",
}
_TIME = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")


class _Error(ValueError):
	def __init__(self, code: str, *, fields=None, params=None, detail=None):
		super().__init__(detail or code)
		self.code, self.fields, self.params, self.detail = code, fields, params or {}, detail


def _env() -> dict:
	try:
		text = read_file(paths.env_path())
	except FileNotFoundError:
		return {}
	return dict(dotenv_values(stream=io.StringIO(text)))


def _truthy(value) -> bool:
	return str(value or "").strip().lower() in ("1", "true", "yes")


def _entries(value) -> list[str]:
	seen = set()
	result = []
	for entry in (value or "").split(","):
		entry = entry.strip()
		if entry and entry.casefold() not in seen:
			seen.add(entry.casefold())
			result.append(entry)
	return result


def user_data_dir() -> Path:
	value = _env().get("USER_DATA_DIR")
	return Path(value) if value else paths.data_home() / "data-dir"


def active_accounts() -> list[str]:
	entries = _entries(_env().get("REWARDS_ACCOUNTS"))
	if entries:
		return entries
	return ["@root"] if (user_data_dir() / "Local State").is_file() else []


def paused_accounts() -> list[str]:
	return _entries(_env().get("REWARDS_PAUSED_ACCOUNTS"))


def load() -> dict[str, object]:
	from rfapp import prefs
	env = _env()
	values = json.loads(json.dumps(_DEFAULTS))
	for setting_id, key in _KEYS.items():
		if env.get(key) is None:
			continue
		value = env[key]
		default = _DEFAULTS[setting_id]
		if isinstance(default, bool):
			value = _truthy(value)
		elif isinstance(default, int):
			try:
				value = int(value)
			except ValueError:
				value = default
		elif isinstance(default, list):
			value = _entries(value)
		values[setting_id] = value
	window = (env.get("REWARDS_SCHEDULE_WINDOW") or "09:45-21:45").split("-")
	if len(window) == 2:
		values["schedule.window_start"], values["schedule.window_end"] = window
	values["run.browser_mode"] = "hidden" if _truthy(env.get("REWARDS_HEADLESS")) else "visible"
	source = (env.get("QUERY_SOURCE") or "llm").strip().lower()
	provider = (env.get("LLM_PROVIDER") or "local").strip().lower()
	values["run.search_source"] = "trends" if source == "trends" else ("openrouter" if provider in ("openrouter", "open-router") else "ollama")
	state = prefs.load()
	for setting_id, key in _PREF_KEYS.items():
		values[setting_id] = state[key]
	values["app.autostart"] = autostart.is_enabled()
	return {key: values[key] for key in SETTING_IDS}


def _url(value: str, schemes) -> bool:
	try:
		parts = urlsplit(value)
		return parts.scheme in schemes and bool(parts.hostname) and not any(char.isspace() for char in value) and parts.port != 0
	except ValueError:
		return False


def _creatable_folder(value: str) -> bool:
	try:
		path = Path(value)
		if not path.is_absolute() or any(char in value for char in '\x00<>"|?*'):
			return False
		ancestor = path
		while not ancestor.exists() and ancestor != ancestor.parent:
			ancestor = ancestor.parent
		return ancestor.is_dir() and os.access(ancestor, os.W_OK)
	except (OSError, ValueError):
		return False


def validate(changes: dict[str, object], current: dict[str, object]) -> dict[str, str]:
	values = {**current, **changes}
	errors = {}
	for key in changes:
		if key not in SETTING_IDS:
			errors[key] = "out_of_range"
	enums = {"schedule.mode": ("automatic", "fixed", "manual"), "run.browser_mode": ("visible", "hidden"), "run.search_source": ("trends", "ollama", "openrouter"), "notify.policy": ("always", "errors", "never"), "advanced.log_level": ("DEBUG", "INFO", "WARNING", "ERROR")}
	for key, choices in enums.items():
		if values.get(key) not in choices:
			errors[key] = "out_of_range"
	for key in ("schedule.window_start", "schedule.window_end", "schedule.time"):
		if not isinstance(values.get(key), str) or not _TIME.fullmatch(values[key]):
			errors[key] = "out_of_range"
	for key, low, high in (("schedule.checks", 1, 12), ("schedule.min_points", 1, 500)):
		if type(values.get(key)) is not int or not low <= values[key] <= high:
			errors[key] = "out_of_range"
	if type(values.get("schedule.random_delay")) is not int or values["schedule.random_delay"] not in (0, 5, 15, 30, 60):
		errors["schedule.random_delay"] = "out_of_range"
	for key in ("schedule.catch_up", "schedule.on_battery", "app.autostart", "app.close_to_tray", "app.update_check"):
		if type(values.get(key)) is not bool:
			errors[key] = "out_of_range"
	if not any(key in errors for key in ("schedule.window_start", "schedule.window_end", "schedule.checks")):
		start = sum(int(part) * multiplier for part, multiplier in zip(values["schedule.window_start"].split(":"), (60, 1)))
		end = sum(int(part) * multiplier for part, multiplier in zip(values["schedule.window_end"].split(":"), (60, 1)))
		if end <= start:
			errors["schedule.window_end"] = "window_order"
		elif end - start < 30 * (values["schedule.checks"] - 1):
			errors["schedule.window_end"] = "window_too_short"
	days = values.get("schedule.days")
	if not isinstance(days, list) or not days or any(day not in _DAYS for day in days):
		errors["schedule.days"] = "days_empty"
	tasks = values.get("run.tasks_off")
	if not isinstance(tasks, list) or any(task not in _TASK_IDS for task in tasks):
		errors["run.tasks_off"] = "out_of_range"
	elif set(tasks) == set(_TASK_IDS):
		errors["run.tasks_off"] = "tasks_all_off"
	if not isinstance(values.get("run.ollama_url"), str) or not _url(values["run.ollama_url"], ("http", "https")):
		errors["run.ollama_url"] = "url_invalid"
	webhook = values.get("notify.webhook_url")
	if not isinstance(webhook, str) or webhook and not _url(webhook, ("https",)):
		errors["notify.webhook_url"] = "https_required"
	for key in ("run.ollama_model", "run.openrouter_model"):
		value = values.get(key)
		if not isinstance(value, str) or not value.strip() or key == "run.ollama_model" and any(char.isspace() for char in value):
			errors[key] = "model_required"
	if not isinstance(values.get("run.openrouter_key"), str) or values.get("run.search_source") == "openrouter" and not values["run.openrouter_key"].strip():
		errors["run.openrouter_key"] = "key_required"
	for key, filename in (("advanced.edge_path", "msedge.exe"), ("advanced.driver_path", "msedgedriver.exe")):
		value = values.get(key)
		if not isinstance(value, str):
			errors[key] = "file_missing"
		elif value:
			if not Path(value).is_file():
				errors[key] = "file_missing"
			elif Path(value).name.lower() != filename:
				errors[key] = "file_wrong_name"
	folder = values.get("advanced.data_folder")
	if not isinstance(folder, str) or folder and not _creatable_folder(folder):
		errors["advanced.data_folder"] = "folder_invalid"
	if "advanced.data_folder" in changes and folder != current.get("advanced.data_folder"):
		import run_lock
		from rfapp import accounts_admin
		if run_lock.status(directory=str(user_data_dir())) is not None or accounts_admin._signin_active() is not None:
			errors["advanced.data_folder"] = "run_active"
	locale = values.get("app.language")
	if locale != "system":
		try:
			index = json.loads((paths.ui_dir() / "i18n" / "index.json").read_text(encoding="utf-8"))
			locales = index["locales"] if isinstance(index, dict) else index
			if not isinstance(locale, str) or locale not in [item["code"] for item in locales]:
				errors["app.language"] = "out_of_range"
		except (OSError, ValueError, KeyError, TypeError):
			errors["app.language"] = "out_of_range"
	for key, value in values.items():
		if isinstance(value, str) and "${" in value:
			errors[key] = "dollar_brace"
	return errors


def save(changes: dict[str, object]) -> dict:
	from rfapp import prefs, schedule
	if not prefs.load()["setup_done"] and EnvFile.load(paths.env_path()).get("QUERY_SOURCE") is None:
		# Step 2 can arm the schedule before Finish. Choose its safe source
		# before deriving values so an absent key cannot add an LLM default.
		changes = {"run.search_source": "trends", **changes}
	current = load()
	errors = validate(changes, current)
	if errors:
		raise _Error("settings.invalid", fields=errors)
	values = {**current, **changes}
	before_accounts = len(active_accounts())
	editor = EnvFile.load(paths.env_path())
	for key in changes:
		if key in _KEYS:
			value = values[key]
			serialized = ",".join(value) if isinstance(value, list) else str(value).lower() if isinstance(value, bool) else str(value)
			editor.set(_KEYS[key], serialized)
	if "schedule.window_start" in changes or "schedule.window_end" in changes:
		editor.set("REWARDS_SCHEDULE_WINDOW", values["schedule.window_start"] + "-" + values["schedule.window_end"])
	if "run.browser_mode" in changes:
		editor.set("REWARDS_HEADLESS", str(values["run.browser_mode"] == "hidden").lower())
	if "run.browser_mode" in changes or _truthy(editor.get("USE_VIRTUAL_DESKTOP")):
		# Creating or switching a virtual desktop can interrupt fullscreen apps.
		editor.set("USE_VIRTUAL_DESKTOP", "false")
	if "run.search_source" in changes:
		source = values["run.search_source"]
		editor.set("QUERY_SOURCE", "trends" if source == "trends" else "llm")
		if source != "trends":
			editor.set("LLM_PROVIDER", "local" if source == "ollama" else "openrouter")
	# Write the local model explicitly so the engine's cloud default cannot
	# silently replace the local model displayed in a new app setup.
	if values["run.search_source"] == "ollama" and editor.get("LOCAL_LLM_MODEL") is None:
		editor.set("LOCAL_LLM_MODEL", str(values["run.ollama_model"]))
	editor.save()
	prefs.save({pref: values[key] for key, pref in _PREF_KEYS.items() if key in changes})
	if "app.autostart" in changes:
		autostart.set_enabled(values["app.autostart"])
	if any(key.startswith("schedule.") for key in changes) or len(active_accounts()) != before_accounts:
		schedule_status = schedule.ensure()
	else:
		schedule_status = schedule.status()
	if schedule_status.get("error_code") is not None:
		raise _Error("schedule.failed")
	return {"values": load(), "warnings": [], "schedule": schedule_status}
