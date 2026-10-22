"""Windows regional formatting without changing the process-wide locale."""

import ctypes
import logging
import sys
from ctypes import wintypes
from datetime import datetime


LOCALE_STHOUSAND = 0x0000000F
LOCALE_SGROUPING = 0x00000010
LOCALE_SNEGATIVESIGN = 0x00000051
TIME_NOSECONDS = 0x00000002
_logger = logging.getLogger("rfapp")


class _SYSTEMTIME(ctypes.Structure):
	_fields_ = [(name, wintypes.WORD) for name in (
		"wYear", "wMonth", "wDayOfWeek", "wDay", "wHour", "wMinute", "wSecond", "wMilliseconds",
	)]


def _kernel32():
	return ctypes.WinDLL("kernel32", use_last_error=True)


def _locale_info(locale_name: str, field: int) -> str:
	function = _kernel32().GetLocaleInfoEx
	function.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPWSTR, ctypes.c_int]
	function.restype = ctypes.c_int
	size = function(locale_name, field, None, 0)
	if not size:
		raise ctypes.WinError(ctypes.get_last_error())
	buffer = ctypes.create_unicode_buffer(size)
	if not function(locale_name, field, buffer, size):
		raise ctypes.WinError(ctypes.get_last_error())
	return buffer.value


def _group_digits(digits: str, separator: str, grouping: str) -> str:
	sizes = [int(size) for size in grouping.split(";")]
	if not sizes or sizes[0] == 0 or not separator:
		return digits
	chunks = []
	index = 0
	while digits:
		size = sizes[index]
		if size == 0:
			chunks.append(digits)
			break
		chunks.append(digits[-size:])
		digits = digits[:-size]
		if index + 1 < len(sizes):
			if sizes[index + 1] != 0:
				index += 1
			elif index + 2 < len(sizes):
				index += 1
			# A terminal zero repeats the previous group, as in 3;2;0 for India.
		elif digits:
			chunks.append(digits)
			break
	return separator.join(reversed(chunks))


def format_int(n: int, locale_name: str) -> str:
	if sys.platform != "win32":
		return f"{n:,}"
	try:
		separator = _locale_info(locale_name, LOCALE_STHOUSAND)
		grouping = _locale_info(locale_name, LOCALE_SGROUPING)
		sign = _locale_info(locale_name, LOCALE_SNEGATIVESIGN)
		result = _group_digits(str(abs(n)), separator, grouping)
		return (sign if n < 0 else "") + result
	except (OSError, ValueError):
		_logger.warning("Windows number formatting failed for %s", locale_name, exc_info=True)
		return f"{n:,}"


def format_time(dt: datetime, locale_name: str) -> str:
	if sys.platform != "win32":
		return dt.strftime("%H:%M")
	try:
		local = dt.astimezone() if dt.tzinfo is not None else dt
		value = _SYSTEMTIME(local.year, local.month, (local.weekday() + 1) % 7, local.day, local.hour, local.minute, local.second, local.microsecond // 1000)
		function = _kernel32().GetTimeFormatEx
		function.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(_SYSTEMTIME), wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_int]
		function.restype = ctypes.c_int
		size = function(locale_name, TIME_NOSECONDS, ctypes.byref(value), None, None, 0)
		if not size:
			raise ctypes.WinError(ctypes.get_last_error())
		buffer = ctypes.create_unicode_buffer(size)
		if not function(locale_name, TIME_NOSECONDS, ctypes.byref(value), None, buffer, size):
			raise ctypes.WinError(ctypes.get_last_error())
		return buffer.value
	except OSError:
		_logger.warning("Windows time formatting failed for %s", locale_name, exc_info=True)
		return dt.strftime("%H:%M")
