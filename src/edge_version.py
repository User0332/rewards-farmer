"""Read the installed Edge major without starting a browser or another program."""

import ctypes
import ntpath
import os
import re
import sys


class _FixedFileInfo(ctypes.Structure):
	_fields_ = [(name, ctypes.c_uint32) for name in (
		"signature", "struct_version", "file_version_ms", "file_version_ls",
		"product_version_ms", "product_version_ls", "flags_mask", "flags",
		"os", "type", "subtype", "date_ms", "date_ls",
	)]


def _binary_major(path: str) -> int | None:
	try:
		version = ctypes.WinDLL("version", use_last_error=True)
		version.GetFileVersionInfoSizeW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
		version.GetFileVersionInfoSizeW.restype = ctypes.c_uint32
		version.GetFileVersionInfoW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
		version.GetFileVersionInfoW.restype = ctypes.c_int
		version.VerQueryValueW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint)]
		version.VerQueryValueW.restype = ctypes.c_int
		ignored = ctypes.c_uint32()
		size = version.GetFileVersionInfoSizeW(path, ctypes.byref(ignored))
		if not size:
			return None
		data = ctypes.create_string_buffer(size)
		if not version.GetFileVersionInfoW(path, 0, size, data):
			return None
		pointer, length = ctypes.c_void_p(), ctypes.c_uint()
		if not version.VerQueryValueW(data, "\\", ctypes.byref(pointer), ctypes.byref(length)):
			return None
		if not pointer.value or length.value < ctypes.sizeof(_FixedFileInfo):
			return None
		info = ctypes.cast(pointer, ctypes.POINTER(_FixedFileInfo)).contents
		if info.signature != 0xFEEF04BD:
			return None
		return (info.file_version_ms >> 16) or None
	except (AttributeError, OSError, ValueError):
		return None


def _beacon_major() -> int | None:
	try:
		import winreg
		with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Edge\BLBeacon", 0, winreg.KEY_READ) as key:
			value, _ = winreg.QueryValueEx(key, "version")
		match = re.fullmatch(r"([1-9][0-9]*)\.\d+\.\d+\.\d+", value.strip()) if isinstance(value, str) else None
		return int(match[1]) if match else None
	except (ImportError, OSError, ValueError):
		return None


def installed_major(binary: str | None = None) -> int | None:
	"""Prefer the selected binary's file version, then the user's Edge beacon."""
	if sys.platform != "win32":
		return None
	if binary is None:
		folders = [os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), os.environ.get("PROGRAMFILES", r"C:\Program Files")]
		if os.environ.get("LOCALAPPDATA"):
			folders.append(os.environ["LOCALAPPDATA"])
		candidates = [ntpath.join(folder, "Microsoft", "Edge", "Application", "msedge.exe") for folder in folders]
		binary = next((path for path in candidates if os.path.isfile(path)), None)
	# Once a binary was selected, another installation's version would describe
	# the wrong browser. Fall back to the beacon rather than another binary.
	major = _binary_major(binary) if binary else None
	return major if major is not None else _beacon_major()
