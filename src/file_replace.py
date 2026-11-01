"""Bounded reads and atomic replacements across short-lived Windows sharing races."""

import os
import builtins
import hashlib
from pathlib import Path
import secrets
import time
from time import monotonic as _monotonic, sleep as _sleep


def _process_stamp(pid):
	if os.name != "nt":
		return None
	import ctypes
	from ctypes import wintypes
	kernel = ctypes.WinDLL("kernel32", use_last_error=True)
	kernel.OpenProcess.argtypes, kernel.OpenProcess.restype = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE
	kernel.GetProcessTimes.argtypes, kernel.GetProcessTimes.restype = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)], wintypes.BOOL
	kernel.GetExitCodeProcess.argtypes, kernel.GetExitCodeProcess.restype = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL
	kernel.CloseHandle.argtypes = [wintypes.HANDLE]
	handle = kernel.OpenProcess(0x1000, False, pid)
	if not handle:
		return None
	try:
		created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
		code = wintypes.DWORD()
		if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259 or not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel_time), ctypes.byref(user_time)):
			return None
		if exited.dwHighDateTime or exited.dwLowDateTime:
			return None
		return created.dwHighDateTime << 32 | created.dwLowDateTime
	finally:
		kernel.CloseHandle(handle)


def _source_prefix(target):
	identity = os.path.normcase(os.path.abspath(os.fspath(target)))
	return ".rfr-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16] + "-"


def _source_name(target, pid, stamp, nonce):
	return f"{_source_prefix(target)}{pid:08x}-{stamp:016x}-{nonce}.tmp"


def _source_owner(target, name):
	prefix = _source_prefix(target)
	if not name.startswith(prefix) or not name.endswith(".tmp"):
		return None
	parts = name[len(prefix):-4].split("-")
	if len(parts) != 3 or tuple(map(len, parts)) != (8, 16, 16) or any(char not in "0123456789abcdef" for part in parts for char in part):
		return None
	return int(parts[0], 16), int(parts[1], 16)


def _replacement_pending(target):
	if os.name != "nt":
		return False
	try:
		with os.scandir(Path(target).parent) as entries:
			for entry in entries:
				owner = _source_owner(target, entry.name)
				if owner is None or not entry.is_file(follow_symlinks=False):
					continue
				try:
					pid, stamp = owner
					if _process_stamp(pid) == stamp:
						return True
				except (ValueError, OverflowError, OSError):
					continue
	except OSError:
		pass
	return False


def replace_file(source: str | os.PathLike, target: str | os.PathLike, *, replace=None, clock=None, sleep=None) -> None:
	replace = os.replace if replace is None else replace
	clock = time.monotonic if clock is None else clock
	sleep = time.sleep if sleep is None else sleep
	deadline = clock() + 2
	pending = source
	try:
		if os.name == "nt":
			stamp = _process_stamp(os.getpid())
			if stamp is None:
				raise PermissionError("Could not identify replacement owner")
			if not Path(source).is_file():
				raise FileNotFoundError(os.fspath(source))
			# Rename the actual replacement bytes, not a separate marker. Their
			# name stays visible while Windows temporarily removes the target.
			pending = Path(target).absolute().parent / _source_name(target, os.getpid(), stamp, secrets.token_hex(8))
			while True:
				try:
					os.rename(os.path.abspath(os.fspath(source)), pending)
					break
				except PermissionError:
					remaining = deadline - clock()
					if remaining <= 0:
						raise
					sleep(min(0.05, remaining))
		while True:
			try:
				replace(pending, target)
				return
			except PermissionError:
				# Python readers do not share delete on Windows, so an atomic
				# replace must wait for them without rewriting the destination.
				remaining = deadline - clock()
				if remaining <= 0:
					raise
				sleep(min(0.05, remaining))
	except BaseException:
		try:
			Path(pending).unlink(missing_ok=True)
			if pending != source:
				Path(source).unlink(missing_ok=True)
		except OSError:
			# Cleanup must not hide the error that prevented the replacement.
			pass
		raise


def read_file(path: str | os.PathLike, *, encoding="utf-8", errors="strict", newline=None, open_file=None, clock=None, sleep=None) -> str:
	open_file = builtins.open if open_file is None else open_file
	# Keep this bounded I/O clock independent of callers' simulated clocks;
	# focused tests inject clock/sleep directly when they need virtual time.
	clock = _monotonic if clock is None else clock
	sleep = _sleep if sleep is None else sleep
	deadline = clock() + 2
	missing_deadline = None
	missing_error = None
	replacement_seen = False
	while True:
		try:
			with open_file(path, "r", encoding=encoding, errors=errors, newline=newline) as stream:
				return stream.read()
		except PermissionError:
			remaining = deadline - clock()
			if remaining <= 0:
				raise
			sleep(min(0.05, remaining))
		except FileNotFoundError as exc:
			missing_error = exc
			now = clock()
			if missing_deadline is None:
				missing_deadline = min(deadline, now + 0.1)
			# The source disappears exactly when replacement completes. Once
			# observed, do not mistake that completion for a genuinely absent
			# target between the separate existence and directory lookups.
			replacement_seen = replacement_seen or _replacement_pending(path)
			try:
				present = os.path.lexists(path)
			except PermissionError:
				present = True
			# A replacement may briefly hide its destination. Confirm absence
			# instead of treating a single negative lookup as missing settings.
			remaining = (deadline if present or replacement_seen else missing_deadline) - now
			if remaining <= 0:
				# A replacement can finish between the independent name lookups.
				# Give that newly published target one final open before reporting
				# confirmed absence; the established deadline never restarts.
				if now < deadline:
					try:
						with open_file(path, "r", encoding=encoding, errors=errors, newline=newline) as stream:
							return stream.read()
					except FileNotFoundError as final_missing:
						missing_error = final_missing
					except PermissionError:
						sleep(min(0.05, deadline - now))
						continue
				raise missing_error
			sleep(min(0.01, remaining))
