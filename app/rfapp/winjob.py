"""Own the farmer tree through a kill-on-close Windows job handle."""

import ctypes
from ctypes import wintypes
import os


class _BasicLimits(ctypes.Structure):
	_fields_ = [
		("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
		("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
		("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
		("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD),
	]


class _IOCounters(ctypes.Structure):
	_fields_ = [(name, ctypes.c_ulonglong) for name in (
		"ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
	)]


class _ExtendedLimits(ctypes.Structure):
	_fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", _IOCounters)] + [
		(name, ctypes.c_size_t) for name in ("ProcessMemoryLimit", "JobMemoryLimit", "PeakProcessMemoryUsed", "PeakJobMemoryUsed")
	]


class Job:
	def __init__(self) -> None:
		if os.name != "nt":
			raise OSError("Windows job objects are only available on Windows")
		self._handle = None
		self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
		for name, arguments, result in (
			("CreateJobObjectW", [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
			("SetInformationJobObject", [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
			("OpenProcess", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
			("AssignProcessToJobObject", [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
			("TerminateJobObject", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
			("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
		):
			function = getattr(self._kernel, name)
			function.argtypes, function.restype = arguments, result
		self._handle = self._kernel.CreateJobObjectW(None, None)
		if not self._handle:
			raise ctypes.WinError(ctypes.get_last_error())
		limits = _ExtendedLimits()
		limits.BasicLimitInformation.LimitFlags = 0x2000
		if not self._kernel.SetInformationJobObject(self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
			error = ctypes.WinError(ctypes.get_last_error())
			self.close()
			raise error

	def assign(self, pid: int) -> None:
		handle = self._kernel.OpenProcess(0x100 | 0x1, False, pid)
		if not handle:
			raise ctypes.WinError(ctypes.get_last_error())
		try:
			if not self._kernel.AssignProcessToJobObject(self._handle, handle):
				raise ctypes.WinError(ctypes.get_last_error())
		finally:
			self._kernel.CloseHandle(handle)

	def terminate(self, exit_code: int = 1) -> None:
		if self._handle and not self._kernel.TerminateJobObject(self._handle, exit_code):
			raise ctypes.WinError(ctypes.get_last_error())

	def close(self) -> None:
		if self._handle:
			handle, self._handle = self._handle, None
			if not self._kernel.CloseHandle(handle):
				raise ctypes.WinError(ctypes.get_last_error())
