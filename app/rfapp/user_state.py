"""Read Windows notification availability without changing the user's session."""

import ctypes
import sys


def notification_state() -> int | None:
	try:
		if sys.platform != "win32":
			return None
		query = ctypes.WinDLL("shell32", use_last_error=True).SHQueryUserNotificationState
		query.argtypes = [ctypes.POINTER(ctypes.c_int)]
		query.restype = ctypes.c_int32
		state = ctypes.c_int()
		if ctypes.c_int32(query(ctypes.byref(state))).value < 0:
			return None
		return int(state.value)
	except Exception:
		return None
