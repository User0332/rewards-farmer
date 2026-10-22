import ctypes
import getpass
import io
import importlib
import json
import os
import platform
import re
import subprocess
import zipfile
from urllib.parse import urlsplit

from dotenv.parser import parse_stream
from file_replace import read_file

from rfapp import __version__, paths

_SECRETS = frozenset(("OPENROUTER_API_KEY", "LOCAL_LLM_API_KEY", "REWARDS_WEBHOOK_URL"))
_RECORD_NAME = re.compile(r"\d{8}T\d{6}Z-[a-f0-9]{4}\.jsonl\Z")
_run = subprocess.run


class DiagnosticsError(ValueError):
	def __init__(self, code: str, *, fields=None, params=None, detail=None):
		super().__init__(detail or code)
		self.code = code
		self.fields = fields
		self.params = params or {}
		self.detail = detail


def _redact(text):
	parts = []
	for binding in parse_stream(io.StringIO(text)):
		if binding.error and any(re.search(r"(?:^|\n)\s*(?:export\s+)?" + key + r"\s*=", binding.original.string) for key in _SECRETS):
			raise DiagnosticsError("diagnostics.failed", detail="Cannot safely redact a malformed secret assignment")
		if binding.key in _SECRETS:
			# Replace every duplicate and multiline binding, not only the effective value.
			ending = "\r\n" if binding.original.string.endswith("\r\n") else "\n"
			parts.append(f"{binding.key}='<redacted>'{ending}")
		else:
			parts.append(binding.original.string)
	return "".join(parts)


def _edge_version():
	import winreg
	for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
		try:
			with winreg.OpenKey(hive, r"Software\Microsoft\Edge\BLBeacon") as key:
				return winreg.QueryValueEx(key, "version")[0]
		except OSError:
			pass
	return None


def _decode_output(data):
	if data.startswith((b"\xff\xfe", b"\xfe\xff")):
		return data.decode("utf-16")
	if len(data) > 3 and data[1:64:2] and all(byte == 0 for byte in data[1:64:2]):
		return data.decode("utf-16-le")
	try:
		return data.decode("utf-8")
	except UnicodeDecodeError:
		return data.decode("cp%d" % ctypes.windll.kernel32.GetOEMCP())


def _identity():
	return os.environ.get("USERNAME") or getpass.getuser(), os.environ.get("COMPUTERNAME") or platform.node()


def _sanitizer(env_text, preferences, records):
	replacements = {}
	def add(value, placeholder):
		if isinstance(value, str) and value:
			replacements.setdefault(value.casefold(), (value, placeholder))
	bindings = list(parse_stream(io.StringIO(env_text)))
	env = {binding.key: binding.value for binding in bindings if binding.key and not binding.error}
	user, computer = _identity()
	add(user, "<user>")
	add(computer, "<pc>")
	for binding in bindings:
		if binding.key in _SECRETS:
			add(binding.value, "<redacted>")
	accounts = []
	for key in ("REWARDS_ACCOUNTS", "REWARDS_PAUSED_ACCOUNTS"):
		accounts.extend(part.strip() for part in (env.get(key) or "").split(",") if part.strip())
	accounts.extend(preferences.get("account_labels", {}))
	accounts.extend(preferences.get("goals", {}))
	for text in records:
		for line in text.splitlines():
			try:
				event = json.loads(line)
				if isinstance(event.get("accounts"), list):
					accounts.extend(value for value in event["accounts"] if isinstance(value, str))
				if isinstance(event.get("account"), str):
					accounts.append(event["account"])
			except (ValueError, TypeError, AttributeError):
				pass
	ids, count = {}, 0
	for account in accounts:
		if account.casefold() in ids:
			continue
		if re.fullmatch(r"(?:@root|account-\d+)", account):
			placeholder = account
		else:
			count += 1
			placeholder = f"<account-{count}>"
			add(account, placeholder)
		ids[account.casefold()] = placeholder
	for account, label in preferences.get("account_labels", {}).items():
		add(label, ids.get(account.casefold(), account))
	for binding in bindings:
		if binding.key in ("OPENROUTER_BASE_URL", "LOCAL_LLM_BASE_URL") and binding.value:
			try:
				host = urlsplit(binding.value).hostname
				if host and host.casefold() not in ("localhost", "127.0.0.1", "::1", "openrouter.ai"):
					add(host, "<host>")
			except ValueError:
				raise DiagnosticsError("diagnostics.failed", detail="Cannot safely redact a malformed LLM URL")
	pairs = sorted(replacements.values(), key=lambda pair: len(pair[0]), reverse=True)
	if not pairs:
		return lambda text: text
	# One substitution pass keeps placeholders stable even when original values overlap.
	lookup = {value.casefold(): placeholder for value, placeholder in pairs}
	pattern = re.compile(r"<redacted>|<user>|<pc>|<host>|<account-\d+>|(?<!\w)(?:" + "|".join(re.escape(value) for value, _ in pairs) + r")(?!\w)", re.IGNORECASE)
	def clean(text):
		return pattern.sub(lambda match: lookup.get(match.group().casefold(), match.group()), text)
	return clean


def _clean_json(value, clean):
	if isinstance(value, str):
		return clean(value)
	if isinstance(value, dict):
		return {clean(key): _clean_json(item, clean) for key, item in value.items()}
	if isinstance(value, list):
		return [_clean_json(item, clean) for item in value]
	return value


def export(path):
	try:
		i18n = importlib.import_module("rfapp.i18n")
		prefs = importlib.import_module("rfapp.prefs")
		p = prefs.load()
		index = json.loads(paths.resource("ui", "i18n", "index.json").read_text(encoding="utf-8"))
		lang, _, locale = i18n.resolve(p.get("language", "system"), [entry["code"] for entry in index["locales"]])
		query = _run(["schtasks", "/Query", "/TN", paths.task_name(), "/XML"], capture_output=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
		xml = _decode_output(query.stdout) if query.returncode == 0 else ""
		system = {"version": __version__, "frozen": paths.is_frozen(), "windows": platform.platform(), "edge": _edge_version(), "lang": lang, "format_locale": locale}
		env_source = read_file(paths.env_path(), newline="") if paths.env_path().is_file() and not paths.env_path().is_symlink() else None
		env_text = _redact(env_source) if env_source is not None else None
		directory = paths.runs_dir()
		records = sorted((item for item in directory.glob("*.jsonl") if _RECORD_NAME.fullmatch(item.name) and item.is_file() and not item.is_symlink()), key=lambda item: item.name, reverse=True)[:10]
		members = {"runs/" + record.name: record.read_text(encoding="utf-8", errors="replace") for record in records}
		clean = _sanitizer(env_source or "", p, members.values())
		for record in records:
			log = record.with_suffix(".log")
			if log.is_file() and not log.is_symlink():
				members["runs/" + log.name] = log.read_text(encoding="utf-8", errors="replace")
		for log in paths.data_home().glob("app.log*"):
			if log.is_file() and not log.is_symlink():
				members[log.name] = log.read_text(encoding="utf-8", errors="replace")
		if paths.prefs_path().is_file() and not paths.prefs_path().is_symlink():
			members["app-prefs.json"] = read_file(paths.prefs_path())
		if env_text is not None:
			members[".env"] = env_text
		members["schedule.xml"] = xml
		members["system.json"] = json.dumps(system, ensure_ascii=False, indent=2)
		with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
			for name, text in members.items():
				if name == ".env":
					parts = []
					for binding in parse_stream(io.StringIO(text)):
						assignment = re.match(r"(\s*(?:export\s+)?" + re.escape(binding.key) + r"\s*=)(.*)", binding.original.string, re.DOTALL) if binding.key else None
						parts.append(assignment[1] + clean(assignment[2]) if assignment else clean(binding.original.string))
					text = "".join(parts)
				elif name.endswith(".json"):
					text = json.dumps(_clean_json(json.loads(text), clean), ensure_ascii=False, indent=2)
				elif name.endswith(".jsonl"):
					lines = []
					for line in text.splitlines():
						try:
							lines.append(json.dumps(_clean_json(json.loads(line), clean), ensure_ascii=False))
						except ValueError:
							lines.append(clean(line))
					text = "\n".join(lines) + "\n"
				else:
					text = clean(text)
				archive.writestr(name, text)
	except Exception as exc:
		raise DiagnosticsError("diagnostics.failed", detail=str(exc)) from exc
