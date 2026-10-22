import ctypes
from ctypes import wintypes
import logging
import threading

from rfapp import paths

_log = logging.getLogger("rfapp.single_instance")
_ERROR_ALREADY_EXISTS = 183
_SYNCHRONIZE = 0x00100000
_EVENT_MODIFY_STATE = 0x0002
_WAIT_OBJECT_0 = 0
_INFINITE = 0xFFFFFFFF


def _kernel():
	kernel = ctypes.WinDLL("kernel32", use_last_error=True)
	for name, args, result in (
		("CreateMutexW", [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
		("OpenMutexW", [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
		("CreateEventW", [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
		("OpenEventW", [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
		("SetEvent", [wintypes.HANDLE], wintypes.BOOL),
		("WaitForSingleObject", [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
		("WaitForMultipleObjects", [wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL, wintypes.DWORD], wintypes.DWORD),
		("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
	):
		function = getattr(kernel, name)
		function.argtypes = args
		function.restype = result
	return kernel


def _name():
	return "Local\\RewardsFarmerApp_" + paths.install_identity()


def is_running() -> bool:
	kernel = _kernel()
	handle = kernel.OpenMutexW(_SYNCHRONIZE, False, _name())
	if handle:
		kernel.CloseHandle(handle)
		return True
	return False


class SingleInstance:
	def __init__(self, start_hidden=False):
		self._hidden = start_hidden
		self._kernel = _kernel()
		self._mutex = None
		self._event = None
		self._closed_event = None
		self._stop = threading.Event()
		self._thread = None

	def acquire(self) -> bool:
		if self._mutex:
			return True
		name = _name()
		# Creating the event first removes the first/second-launch signaling race.
		self._event = self._kernel.CreateEventW(None, False, False, name + "_show")
		if not self._event:
			raise ctypes.WinError(ctypes.get_last_error())
		self._mutex = self._kernel.CreateMutexW(None, False, name)
		error = ctypes.get_last_error()
		if not self._mutex:
			self.close()
			raise ctypes.WinError(error)
		if error == _ERROR_ALREADY_EXISTS:
			if not self._hidden:
				# The foreground launch transfers its right to activate the existing window.
				user = ctypes.WinDLL("user32", use_last_error=True)
				user.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
				user.AllowSetForegroundWindow.restype = wintypes.BOOL
				user.AllowSetForegroundWindow(0xFFFFFFFF)
				event = self._kernel.OpenEventW(_EVENT_MODIFY_STATE, False, name + "_show")
				if event:
					try:
						if not self._kernel.SetEvent(event):
							raise ctypes.WinError(ctypes.get_last_error())
					finally:
						self._kernel.CloseHandle(event)
			self.close()
			return False
		return True

	def listen(self, show_callback):
		if not self._mutex or self._thread:
			return
		self._closed_event = self._kernel.CreateEventW(None, True, False, None)
		if not self._closed_event:
			raise ctypes.WinError(ctypes.get_last_error())
		def wait():
			handles = (wintypes.HANDLE * 2)(self._closed_event, self._event)
			while not self._stop.is_set():
				result = self._kernel.WaitForMultipleObjects(2, handles, False, _INFINITE)
				if result == _WAIT_OBJECT_0 or self._stop.is_set():
					break
				if result == _WAIT_OBJECT_0 + 1:
					try:
						show_callback()
					except Exception:
						_log.exception("Could not show the existing window")
				else:
					_log.error("Show-event wait failed: %s", result)
					break
		self._thread = threading.Thread(target=wait, name="rfapp-show", daemon=True)
		self._thread.start()

	def close(self):
		self._stop.set()
		if self._closed_event:
			self._kernel.SetEvent(self._closed_event)
		if self._thread and self._thread is not threading.current_thread():
			self._thread.join(timeout=2)
			if self._thread.is_alive():
				# A show callback can still be using the handles; a later close may finish cleanup.
				_log.warning("Show callback is still stopping")
				return
		self._thread = None
		for attr in ("_mutex", "_event", "_closed_event"):
			handle = getattr(self, attr)
			if handle:
				self._kernel.CloseHandle(handle)
				setattr(self, attr, None)
