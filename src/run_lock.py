"""One OS-owned lock for every profile under a USER_DATA_DIR."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import tempfile
import time

import constants
from file_replace import read_file, replace_file

LOCK_FILE_NAME = ".rewards-farmer.lock"
INFO_FILE_NAME = ".rewards-farmer.lock.json"


@dataclass(frozen=True)
class LockInfo:
	owner: str
	pid: int
	pid_created: int | None
	run_id: str | None
	account: str | None
	since: str


def lock_dir() -> str:
	return os.environ.get("USER_DATA_DIR") or constants.DEFAULT_ROOT_DATA_DIR


def _process_created(pid: int) -> int | None:
	if os.name != "nt":
		try:
			os.kill(pid, 0)
			return 0
		except PermissionError:
			return 0
		except (OSError, ValueError):
			return None
	kernel = ctypes.WinDLL("kernel32", use_last_error=True)
	kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
	kernel.OpenProcess.restype = wintypes.HANDLE
	kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
	kernel.GetProcessTimes.restype = wintypes.BOOL
	kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
	kernel.GetExitCodeProcess.restype = wintypes.BOOL
	kernel.CloseHandle.argtypes = [wintypes.HANDLE]
	handle = kernel.OpenProcess(0x1000, False, pid)
	if not handle:
		return None
	try:
		code = wintypes.DWORD()
		if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
			return None
		creation, exit_at, system, user = (wintypes.FILETIME() for _ in range(4))
		if not kernel.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_at), ctypes.byref(system), ctypes.byref(user)):
			return None
		return (creation.dwHighDateTime << 32) | creation.dwLowDateTime
	finally:
		kernel.CloseHandle(handle)


def _unlock(fd):
	if os.name == "nt":
		import msvcrt
		os.lseek(fd, 0, os.SEEK_SET)
		msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
	else:
		import fcntl
		fcntl.flock(fd, fcntl.LOCK_UN)


class RunLock:
	def __init__(self, fd, directory, info):
		self._fd = fd
		self._directory = directory
		self.info = info

	def __enter__(self):
		return self

	def __exit__(self, *args):
		self.release()

	def release(self) -> None:
		if self._fd is None:
			return
		fd, self._fd = self._fd, None
		try:
			Path(self._directory, INFO_FILE_NAME).unlink(missing_ok=True)
		finally:
			try:
				_unlock(fd)
			finally:
				os.close(fd)


def acquire(owner: str, run_id: str | None = None, account: str | None = None, directory: str | None = None) -> RunLock | None:
	directory = directory or lock_dir()
	Path(directory).mkdir(parents=True, exist_ok=True)
	fd = os.open(str(Path(directory, LOCK_FILE_NAME)), os.O_RDWR | os.O_CREAT)
	try:
		deadline = time.monotonic() + 5
		while True:
			try:
				if os.name == "nt":
					import msvcrt
					msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
				else:
					import fcntl
					fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
			except OSError:
				# Windows can retain a dead holder's lock after its process has exited.
				remaining = deadline - time.monotonic()
				if status(directory) is not None or remaining <= 0:
					os.close(fd)
					return None
				time.sleep(min(0.05, remaining))
			else:
				break
	except BaseException:
		os.close(fd)
		raise
	try:
		info = LockInfo(owner, os.getpid(), _process_created(os.getpid()) if os.name == "nt" else None,
			run_id, account, datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"))
		with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as stream:
			temp = stream.name
			json.dump(asdict(info), stream, ensure_ascii=False, separators=(",", ":"))
			stream.flush()
			os.fsync(stream.fileno())
		try:
			replace_file(temp, str(Path(directory, INFO_FILE_NAME)))
		finally:
			Path(temp).unlink(missing_ok=True)
		return RunLock(fd, directory, info)
	except BaseException:
		_unlock(fd)
		os.close(fd)
		raise


def status(directory: str | None = None) -> LockInfo | None:
	try:
		info = LockInfo(**json.loads(read_file(Path(directory or lock_dir(), INFO_FILE_NAME))))
		if type(info.pid) is not int or info.pid <= 0:
			return None
		created = _process_created(info.pid)
		if created is None or (os.name == "nt" and created != info.pid_created):
			return None
		return info
	except (OSError, ValueError, TypeError):
		return None
