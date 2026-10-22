import _bootstrap  # noqa: F401

import ctypes
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from rfapp import single_instance


class SingleInstanceTests(unittest.TestCase):
	def test_existing_launch_signals_and_closes_handles(self):
		kernel = Mock()
		kernel.CreateEventW.return_value = 10
		kernel.CreateMutexW.return_value = 20
		kernel.OpenEventW.return_value = 30
		user = Mock()
		with patch.object(single_instance, "_kernel", return_value=kernel), patch.object(single_instance.paths, "install_identity", return_value="test0000"), patch.object(ctypes, "get_last_error", return_value=183, create=True), patch.object(ctypes, "WinDLL", return_value=user, create=True):
			instance = single_instance.SingleInstance()
			self.assertFalse(instance.acquire())
		user.AllowSetForegroundWindow.assert_called_once_with(0xFFFFFFFF)
		kernel.SetEvent.assert_called_once_with(30)
		self.assertEqual(kernel.CloseHandle.call_count, 3)

	def test_existing_tray_launch_is_silent(self):
		kernel = Mock()
		with patch.object(single_instance, "_kernel", return_value=kernel), patch.object(single_instance.paths, "install_identity", return_value="test0000"), patch.object(ctypes, "get_last_error", return_value=183, create=True):
			self.assertFalse(single_instance.SingleInstance(start_hidden=True).acquire())
		kernel.OpenEventW.assert_not_called()
		kernel.SetEvent.assert_not_called()

	def test_probe_only_opens_and_closes_mutex(self):
		kernel = Mock()
		kernel.OpenMutexW.return_value = 123
		with patch.object(single_instance, "_kernel", return_value=kernel), patch.object(single_instance.paths, "install_identity", return_value="test0000"):
			self.assertTrue(single_instance.is_running())
		kernel.CloseHandle.assert_called_once_with(123)
		kernel.CreateMutexW.assert_not_called()

	def test_idle_listener_blocks_until_owned_close_event_without_polling(self):
		kernel = Mock()
		kernel.CreateEventW.return_value = 30
		waiting, released = threading.Event(), threading.Event()
		def wait(count, handles, all_handles, timeout):
			self.assertEqual((count, list(handles), all_handles, timeout), (2, [30, 10], False, 0xFFFFFFFF))
			waiting.set()
			self.assertTrue(released.wait(3))
			return 0
		kernel.WaitForMultipleObjects.side_effect = wait
		kernel.SetEvent.side_effect = lambda handle: released.set()
		with patch.object(single_instance, "_kernel", return_value=kernel):
			instance = single_instance.SingleInstance()
			instance._mutex, instance._event = 20, 10
			shown = Mock()
			instance.listen(shown)
			self.assertTrue(waiting.wait(1))
			instance.close()
			instance.close()
		kernel.WaitForMultipleObjects.assert_called_once()
		kernel.WaitForSingleObject.assert_not_called()
		shown.assert_not_called()
		self.assertEqual([call.args[0] for call in kernel.CloseHandle.call_args_list], [20, 10, 30])

	def test_show_event_dispatch_and_failed_wait_end_without_a_timer(self):
		kernel = Mock()
		kernel.CreateEventW.return_value = 30
		kernel.WaitForMultipleObjects.side_effect = [1, 0xFFFFFFFF]
		with patch.object(single_instance, "_kernel", return_value=kernel):
			instance = single_instance.SingleInstance()
			instance._mutex, instance._event = 20, 10
			shown = Mock()
			instance.listen(shown)
			instance._thread.join(2)
			self.assertFalse(instance._thread.is_alive())
			instance.close()
		shown.assert_called_once()
		self.assertEqual(kernel.WaitForMultipleObjects.call_count, 2)

	@unittest.skipUnless(sys.platform == "win32", "Windows named handles")
	def test_real_second_launch_and_silent_tray_event(self):
		with tempfile.TemporaryDirectory() as home, patch.object(single_instance.paths, "data_home", return_value=Path(home)):
			first = single_instance.SingleInstance()
			self.addCleanup(first.close)
			self.assertTrue(first.acquire())
			self.assertTrue(single_instance.is_running())
			shown = threading.Event()
			first.listen(shown.set)
			second = single_instance.SingleInstance()
			self.assertFalse(second.acquire())
			self.assertTrue(shown.wait(2))
			shown.clear()
			third = single_instance.SingleInstance(start_hidden=True)
			self.assertFalse(third.acquire())
			self.assertFalse(shown.wait(.3))
			first.close()
			first.close()
			self.assertFalse(single_instance.is_running())
