"""Current-user startup registration and Task Manager's disabled flag."""

import subprocess

from rfapp import paths


_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"


class _Error(ValueError):
	def __init__(self, code: str, *, fields=None, params=None, detail=None):
		super().__init__(detail or code)
		self.code, self.fields, self.params, self.detail = code, fields, params or {}, detail


def is_enabled() -> bool:
	import winreg
	name = paths.task_name()
	try:
		with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
			winreg.QueryValueEx(key, name)
	except FileNotFoundError:
		return False
	try:
		with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _APPROVED_KEY) as key:
			value, kind = winreg.QueryValueEx(key, name)
			if kind == winreg.REG_BINARY and value and value[0] & 1:
				return False
	except FileNotFoundError:
		pass
	return True


def set_enabled(enabled: bool) -> None:
	import winreg
	name = paths.task_name()
	try:
		if enabled:
			with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
				winreg.SetValueEx(key, name, 0, winreg.REG_SZ, subprocess.list2cmdline(paths.launcher_command(["--tray"])))
			# Remove only our approval entry so re-enabling also works after a
			# user disabled this installation in Task Manager.
			try:
				with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _APPROVED_KEY, 0, winreg.KEY_SET_VALUE) as key:
					winreg.DeleteValue(key, name)
			except FileNotFoundError:
				pass
		else:
			try:
				with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
					winreg.DeleteValue(key, name)
			except FileNotFoundError:
				pass
	except OSError as exc:
		raise _Error("autostart.failed", detail=str(exc)) from exc
