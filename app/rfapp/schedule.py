"""Pure task XML and bounded Task Scheduler operations."""

import ctypes
import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from rfapp import paths


APP_TASK_PREFIX = "RewardsFarmerApp_"
LEGACY_TASK_NAME = "rewards-farmer"
_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_DAY_TAGS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_logger = logging.getLogger("rfapp")


class _Error(ValueError):
	def __init__(self, code: str, *, fields=None, params=None, detail=None):
		super().__init__(detail or code)
		self.code, self.fields, self.params, self.detail = code, fields, params or {}, detail


@dataclass(frozen=True)
class ScheduleSettings:
	mode: str
	window_start: time
	window_end: time
	checks: int
	fixed_time: time
	days: frozenset[str]
	random_delay: int
	catch_up: bool
	on_battery: bool
	min_points: int
	active_accounts: int


def from_settings(values: dict) -> ScheduleSettings:
	_guard_name(paths.task_name())
	from rfapp import settings
	return ScheduleSettings(
		mode=values["schedule.mode"],
		window_start=time.fromisoformat(values["schedule.window_start"]),
		window_end=time.fromisoformat(values["schedule.window_end"]),
		checks=values["schedule.checks"],
		fixed_time=time.fromisoformat(values["schedule.time"]),
		days=frozenset(values["schedule.days"]),
		random_delay=values["schedule.random_delay"],
		catch_up=values["schedule.catch_up"],
		on_battery=values["schedule.on_battery"],
		min_points=values["schedule.min_points"],
		active_accounts=len(settings.active_accounts()),
	)


def trigger_times(s: ScheduleSettings) -> list[time]:
	_guard_name(paths.task_name())
	if s.mode == "manual":
		return []
	if s.mode == "fixed":
		return [s.fixed_time]
	if s.mode != "automatic" or not 1 <= s.checks <= 12:
		raise ValueError("Invalid schedule")
	start = s.window_start.hour * 60 + s.window_start.minute
	end = s.window_end.hour * 60 + s.window_end.minute
	if end <= start or end - start < 30 * (s.checks - 1):
		raise ValueError("Invalid schedule window")
	minutes = [start] if s.checks == 1 else [start + index * (end - start) // (s.checks - 1) for index in range(s.checks)]
	return [time(minute // 60, minute % 60) for minute in minutes]


def config_hash(s: ScheduleSettings, command: list[str]) -> str:
	_guard_name(paths.task_name())
	values = asdict(s)
	for key in ("window_start", "window_end", "fixed_time"):
		values[key] = values[key].isoformat()
	values["days"] = sorted(s.days)
	canonical = json.dumps({"settings": values, "command": command}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
	return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def build_task_xml(s: ScheduleSettings, command: list[str], working_dir: str, user_id: str, now: datetime) -> str:
	_guard_name(paths.task_name())
	if not command or not s.days or not s.days.issubset(_DAYS):
		raise ValueError("Invalid task configuration")
	boolean = lambda value: "true" if value else "false"
	lines = ['<?xml version="1.0" encoding="UTF-16"?>', f'<Task version="1.2" xmlns="{_NS}">', "  <RegistrationInfo>", "    <Author>Rewards Farmer</Author>"]
	description = "Rewards Farmer app schedule. The app manages this task and overwrites changes made here. config=" + config_hash(s, command)
	lines += [f"    <Description>{escape(description)}</Description>", "  </RegistrationInfo>", "  <Triggers>"]
	for clock in trigger_times(s):
		boundary = datetime.combine(now.date(), clock, tzinfo=now.tzinfo)
		if boundary <= now:
			boundary += timedelta(days=1)
		lines += ["    <CalendarTrigger>", f"      <StartBoundary>{boundary.isoformat(timespec='seconds')}</StartBoundary>", "      <Enabled>true</Enabled>"]
		if s.random_delay:
			lines.append(f"      <RandomDelay>PT{s.random_delay}M</RandomDelay>")
		if s.days == frozenset(_DAYS):
			lines += ["      <ScheduleByDay>", "        <DaysInterval>1</DaysInterval>", "      </ScheduleByDay>"]
		else:
			lines += ["      <ScheduleByWeek>", "        <WeeksInterval>1</WeeksInterval>", "        <DaysOfWeek>"]
			lines += [f"          <{tag}/>" for day, tag in zip(_DAYS, _DAY_TAGS) if day in s.days]
			lines += ["        </DaysOfWeek>", "      </ScheduleByWeek>"]
		lines.append("    </CalendarTrigger>")
	lines += ["  </Triggers>", "  <Principals>", '    <Principal id="Author">', f"      <UserId>{escape(user_id)}</UserId>", "      <LogonType>InteractiveToken</LogonType>", "      <RunLevel>LeastPrivilege</RunLevel>", "    </Principal>", "  </Principals>", "  <Settings>"]
	values = (
		("MultipleInstancesPolicy", "IgnoreNew"),
		("DisallowStartIfOnBatteries", boolean(not s.on_battery)),
		("StopIfGoingOnBatteries", boolean(not s.on_battery)),
		("AllowHardTerminate", "true"),
		("StartWhenAvailable", boolean(s.catch_up)),
		("RunOnlyIfNetworkAvailable", "true"),
	)
	lines += [f"    <{key}>{value}</{key}>" for key, value in values]
	lines += ["    <IdleSettings>", "      <StopOnIdleEnd>false</StopOnIdleEnd>", "      <RestartOnIdle>false</RestartOnIdle>", "    </IdleSettings>"]
	values = (("AllowStartOnDemand", "true"), ("Enabled", "true"), ("Hidden", "false"), ("RunOnlyIfIdle", "false"), ("WakeToRun", "false"), ("ExecutionTimeLimit", f"PT{min(4, max(1, s.active_accounts))}H"), ("Priority", "5"))
	lines += [f"    <{key}>{value}</{key}>" for key, value in values]
	lines += ["  </Settings>", '  <Actions Context="Author">', "    <Exec>", f"      <Command>{escape(command[0])}</Command>", f"      <Arguments>{escape(subprocess.list2cmdline(command[1:]))}</Arguments>", f"      <WorkingDirectory>{escape(working_dir)}</WorkingDirectory>", "    </Exec>", "  </Actions>", "</Task>"]
	return "\n".join(lines) + "\n"


def next_check(s: ScheduleSettings, now: datetime) -> datetime | None:
	_guard_name(paths.task_name())
	times = trigger_times(s)
	for offset in range(8):
		date = now.date() + timedelta(days=offset)
		if _DAYS[date.weekday()] not in s.days:
			continue
		for clock in times:
			candidate = datetime.combine(date, clock, tzinfo=now.tzinfo)
			if candidate > now:
				return candidate
	return None


def current_user_id() -> str:
	_guard_name(paths.task_name())
	function = ctypes.windll.secur32.GetUserNameExW
	function.argtypes = [ctypes.c_int, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
	function.restype = ctypes.c_ubyte
	size = ctypes.c_ulong(0)
	function(2, None, ctypes.byref(size))
	buffer = ctypes.create_unicode_buffer(size.value)
	if not function(2, buffer, ctypes.byref(size)):
		raise OSError("Could not read the Windows user identity")
	return buffer.value


def _guard_name(name: str, legacy: bool = False) -> None:
	if not isinstance(name, str):
		raise ValueError("Only app task names are allowed")
	if legacy and name.casefold() == LEGACY_TASK_NAME.casefold():
		return
	if not name.casefold().startswith(APP_TASK_PREFIX.casefold()) or any(char in name for char in "\\/\x00\r\n"):
		raise ValueError("Only app task names are allowed")


def _schtasks(args: list[str], name: str, legacy: bool = False):
	_guard_name(name, legacy)
	return subprocess.run(["schtasks", *args], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), capture_output=True, timeout=30, stdin=subprocess.DEVNULL)


def _decode_output(raw: bytes) -> str:
	if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
		return raw.decode("utf-16")
	sample = raw[:64]
	if len(sample) >= 4 and all(byte == 0 for byte in sample[1::2]):
		return raw.decode("utf-16-le")
	try:
		return raw.decode("utf-8")
	except UnicodeDecodeError:
		return raw.decode("cp%d" % ctypes.windll.kernel32.GetOEMCP())


def _read_task(name: str, legacy: bool = False) -> ET.Element | None:
	_guard_name(name, legacy)
	result = _schtasks(["/Query", "/TN", name, "/XML"], name, legacy)
	if result.returncode:
		return None
	text = re.sub(r"^\s*<\?xml[^>]*\?>", "", _decode_output(result.stdout), count=1)
	return ET.fromstring(text)


def _create_task(name: str, xml: str) -> None:
	_guard_name(name)
	filename = None
	try:
		with tempfile.NamedTemporaryFile(mode="w", encoding="utf-16", dir=paths.data_home(), suffix=".xml", delete=False) as stream:
			filename = stream.name
			stream.write(xml)
		result = _schtasks(["/Create", "/TN", name, "/XML", filename, "/F"], name)
		if result.returncode:
			raise OSError(_decode_output(result.stderr or result.stdout))
	finally:
		if filename:
			Path(filename).unlink(missing_ok=True)


def _delete_task(name: str, legacy: bool = False) -> None:
	_guard_name(name, legacy)
	result = _schtasks(["/Delete", "/TN", name, "/F"], name, legacy)
	if result.returncode:
		raise OSError(_decode_output(result.stderr or result.stdout))


def _text(root: ET.Element, path: str) -> str | None:
	return root.findtext("/".join(f"{{{_NS}}}{part}" for part in path.split("/")))


def _in_sync(root, s, command) -> bool:
	return root is not None and f"config={config_hash(s, command)}" in (_text(root, "RegistrationInfo/Description") or "") and _text(root, "Actions/Exec/Command") == command[0]


def _settings():
	from rfapp import settings
	return from_settings(settings.load())


def _status(s, name, root, error_code=None) -> dict:
	command = paths.launcher_command(["--scheduled"])
	wanted = s.mode != "manual" and s.active_accounts > 0
	next_at = next_check(s, datetime.now().astimezone()) if wanted else None
	try:
		legacy = find_legacy_task()
	except (OSError, subprocess.SubprocessError, ET.ParseError, UnicodeError):
		_logger.exception("Could not read the legacy task")
		legacy, error_code = None, "schedule.failed"
	return {"mode": s.mode, "task_name": name, "task_exists": root is not None, "in_sync": _in_sync(root, s, command) if wanted else root is None, "next_at": next_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if next_at else None, "next_is_check": s.mode == "automatic", "legacy_task": legacy, "error_code": error_code}


def status() -> dict:
	name = paths.task_name()
	_guard_name(name)
	s = _settings()
	try:
		return _status(s, name, _read_task(name))
	except (OSError, subprocess.SubprocessError, ET.ParseError, UnicodeError):
		_logger.exception("Could not read the app task")
		return _status(s, name, None, "schedule.failed")


def _record_task_name(name: str | None) -> None:
	if not paths.is_frozen():
		return
	import winreg
	subkey = r"Software\RewardsFarmer"
	access = winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE
	if name is None:
		try:
			with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey, 0, access) as key:
				try:
					winreg.DeleteValue(key, "TaskName")
				except FileNotFoundError:
					pass
				subkeys, values, _ = winreg.QueryInfoKey(key)
		except FileNotFoundError:
			return
		if subkeys == 0 and values == 0:
			try:
				winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)
			except FileNotFoundError:
				pass
		return
	try:
		key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey, 0, access)
	except FileNotFoundError:
		key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, subkey)
	with key:
		try:
			current, kind = winreg.QueryValueEx(key, "TaskName")
		except FileNotFoundError:
			current, kind = None, None
		if current != name or kind != winreg.REG_SZ:
			winreg.SetValueEx(key, "TaskName", 0, winreg.REG_SZ, name)


def ensure() -> dict:
	name = paths.task_name()
	_guard_name(name)
	s = _settings()
	command = paths.launcher_command(["--scheduled"])
	root = None
	try:
		root = _read_task(name)
		if s.mode == "manual" or s.active_accounts == 0:
			if root is not None:
				_delete_task(name)
			_record_task_name(None)
			root = None
		elif not _in_sync(root, s, command):
			paths.data_home().mkdir(parents=True, exist_ok=True)
			xml = build_task_xml(s, command, str(paths.data_home()), current_user_id(), datetime.now())
			_create_task(name, xml)
			_record_task_name(name)
			root = ET.fromstring(re.sub(r"^<\?xml[^>]*\?>", "", xml))
		else:
			_record_task_name(name)
		return _status(s, name, root)
	except (OSError, subprocess.SubprocessError, ET.ParseError, UnicodeError):
		_logger.exception("Could not update the app task")
		return _status(s, name, root, "schedule.failed")


def remove() -> None:
	name = paths.task_name()
	_guard_name(name)
	try:
		if _read_task(name) is not None:
			_delete_task(name)
		_record_task_name(None)
	except (OSError, subprocess.SubprocessError, ET.ParseError, UnicodeError) as exc:
		raise _Error("schedule.failed", detail=str(exc)) from exc


def find_legacy_task() -> dict | None:
	_guard_name(paths.task_name())
	root = _read_task(LEGACY_TASK_NAME, legacy=True)
	if root is None:
		return None
	arguments = _text(root, "Actions/Exec/Arguments") or ""
	match = re.search(r'"([^"\r\n]*src[\\/]main\.py)"|([^\s"]*src[\\/]main\.py)', arguments, re.IGNORECASE)
	if match is None:
		return None
	install = Path(match.group(1) or match.group(2)).parent.parent
	from rfapp import settings
	if os.path.normcase(str(install)) != os.path.normcase(str(paths.data_home())) and os.path.normcase(str(install / "data-dir")) != os.path.normcase(str(settings.user_data_dir())):
		return None
	boundary = _text(root, "Triggers/CalendarTrigger/StartBoundary")
	try:
		clock = datetime.fromisoformat(boundary).strftime("%H:%M") if boundary else None
	except ValueError:
		clock = None
	return {"name": LEGACY_TASK_NAME, "time": clock, "command": _text(root, "Actions/Exec/Command") or ""}


def replace_legacy_task() -> dict:
	_guard_name(paths.task_name())
	try:
		if find_legacy_task() is None:
			raise _Error("schedule.legacy_not_found")
		_delete_task(LEGACY_TASK_NAME, legacy=True)
	except (OSError, subprocess.SubprocessError, ET.ParseError, UnicodeError) as exc:
		raise _Error("schedule.failed", detail=str(exc)) from exc
	result = ensure()
	if result.get("error_code"):
		raise _Error("schedule.failed")
	return result
