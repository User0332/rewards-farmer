import _bootstrap  # noqa: F401

import ctypes
import sys
import types
import unittest
from unittest import mock

from rfapp import user_state


class NotificationStateTests(unittest.TestCase):
	def _query(self, state, result=0):
		def query(pointer):
			pointer._obj.value = state
			return result
		return mock.Mock(side_effect=query)

	def test_success_returns_integer_and_uses_the_native_signature(self):
		for state in range(1, 8):
			with self.subTest(state=state):
				query = self._query(state)
				with mock.patch.object(user_state, "sys", types.SimpleNamespace(platform="win32")), mock.patch.object(user_state.ctypes, "WinDLL", return_value=types.SimpleNamespace(SHQueryUserNotificationState=query), create=True) as library:
					value = user_state.notification_state()
				self.assertIs(type(value), int)
				self.assertEqual(value, state)
				library.assert_called_once_with("shell32", use_last_error=True)
				self.assertEqual(query.argtypes, [ctypes.POINTER(ctypes.c_int)])
				self.assertIs(query.restype, ctypes.c_int32)

	def test_failed_hresult_returns_none_including_unsigned_failure(self):
		for result in (-2147467259, 0x80004005):
			with self.subTest(result=result), mock.patch.object(user_state, "sys", types.SimpleNamespace(platform="win32")), mock.patch.object(user_state.ctypes, "WinDLL", return_value=types.SimpleNamespace(SHQueryUserNotificationState=self._query(5, result)), create=True):
				self.assertIsNone(user_state.notification_state())

	def test_missing_library_function_or_any_exception_returns_none(self):
		with mock.patch.object(user_state, "sys", types.SimpleNamespace(platform="win32")):
			for library in (types.SimpleNamespace(), types.SimpleNamespace(SHQueryUserNotificationState=mock.Mock(side_effect=RuntimeError("query failed")))):
				with self.subTest(library=library), mock.patch.object(user_state.ctypes, "WinDLL", return_value=library, create=True):
					self.assertIsNone(user_state.notification_state())
			with mock.patch.object(user_state.ctypes, "WinDLL", side_effect=OSError("library missing"), create=True):
				self.assertIsNone(user_state.notification_state())

	def test_off_windows_does_not_load_a_windows_library(self):
		with mock.patch.object(user_state, "sys", types.SimpleNamespace(platform="linux")), mock.patch.object(user_state.ctypes, "WinDLL", side_effect=AssertionError("Windows library on Linux"), create=True) as library:
			self.assertIsNone(user_state.notification_state())
			library.assert_not_called()

	@unittest.skipUnless(sys.platform == "win32", "Harmless real Windows notification-state read")
	def test_real_windows_notification_state_is_in_documented_range(self):
		state = user_state.notification_state()
		self.assertIs(type(state), int)
		self.assertIn(state, range(1, 8))
