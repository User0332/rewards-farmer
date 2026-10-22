import _bootstrap  # noqa: F401

import subprocess
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from rfapp import autostart


class AutostartTests(unittest.TestCase):
	def setUp(self):
		self.registry = MagicMock()
		self.registry.REG_BINARY = 3
		self.registry.REG_SZ = 1
		self.registry.KEY_SET_VALUE = 2
		self.modules = patch.dict(sys.modules, {"winreg": self.registry})
		self.modules.start()
		self.addCleanup(self.modules.stop)
		self.name = patch("rfapp.autostart.paths.task_name", return_value="RewardsFarmerApp_fake_test")
		self.name.start()
		self.addCleanup(self.name.stop)

	def test_no_value_disabled(self):
		self.registry.OpenKey.side_effect = FileNotFoundError()
		self.assertFalse(autostart.is_enabled())

	def test_task_manager_binary_flags(self):
		for value, kind, enabled in [(b"\x02", 3, True), (b"\x03", 3, False), (b"\x07", 3, False), (b"", 3, True), ("string", 1, True)]:
			with self.subTest(value=value):
				self.registry.QueryValueEx.side_effect = [("cmd", 1), (value, kind)]
				self.assertEqual(autostart.is_enabled(), enabled)

	def test_missing_approval_enabled(self):
		self.registry.QueryValueEx.side_effect = [("cmd", 1), FileNotFoundError()]
		self.assertTrue(autostart.is_enabled())

	def test_enable_quotes_command_and_removes_approval(self):
		command = [r"C:\has spaces\RewardsFarmer.exe", "--tray"]
		with patch("rfapp.autostart.paths.launcher_command", return_value=command):
			autostart.set_enabled(True)
		self.registry.SetValueEx.assert_called_once_with(self.registry.CreateKey.return_value.__enter__.return_value, "RewardsFarmerApp_fake_test", 0, 1, subprocess.list2cmdline(command))
		self.registry.DeleteValue.assert_called_once()

	def test_disable_removes_only_run_value(self):
		autostart.set_enabled(False)
		self.registry.SetValueEx.assert_not_called()
		self.registry.CreateKey.assert_not_called()
		self.registry.DeleteValue.assert_called_once()
		self.assertEqual(self.registry.OpenKey.call_args.args[1], autostart._RUN_KEY)

	def test_missing_disable_is_idempotent(self):
		self.registry.OpenKey.side_effect = FileNotFoundError()
		autostart.set_enabled(False)

	def test_write_failure_has_bridge_code(self):
		self.registry.CreateKey.side_effect = PermissionError("denied")
		with self.assertRaises(ValueError) as caught:
			autostart.set_enabled(True)
		self.assertEqual(caught.exception.code, "autostart.failed")


@unittest.skipUnless(sys.platform == "win32" and os.environ.get("REWARDS_APP_UI_TESTS") == "1", "Isolated current-user registry acceptance requires REWARDS_APP_UI_TESTS=1")
class RealAutostartTests(unittest.TestCase):
	def setUp(self):
		import winreg
		self.winreg = winreg
		self.name = "RewardsFarmerApp_test0000_test"
		self.patches = [patch("rfapp.autostart.paths.task_name", return_value=self.name), patch("rfapp.autostart.paths.launcher_command", return_value=["cmd.exe", "/c", "exit", "0"])]
		# Refuse to replace a value another run might own.
		for path in (autostart._RUN_KEY, autostart._APPROVED_KEY):
			try:
				with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
					winreg.QueryValueEx(key, self.name)
			except FileNotFoundError:
				continue
			raise RuntimeError("The isolated startup test value already exists")
		for item in self.patches:
			item.start()

	def tearDown(self):
		try:
			for path in (autostart._RUN_KEY, autostart._APPROVED_KEY):
				try:
					with self.winreg.OpenKey(self.winreg.HKEY_CURRENT_USER, path, 0, self.winreg.KEY_SET_VALUE) as key:
						self.winreg.DeleteValue(key, self.name)
				except FileNotFoundError:
					pass
			for path in (autostart._RUN_KEY, autostart._APPROVED_KEY):
				try:
					with self.winreg.OpenKey(self.winreg.HKEY_CURRENT_USER, path) as key:
						with self.assertRaises(FileNotFoundError):
							self.winreg.QueryValueEx(key, self.name)
				except FileNotFoundError:
					pass
			print("HKCU cleanup proven: RewardsFarmerApp_test0000_test Run and StartupApproved values absent")
		finally:
			for item in reversed(self.patches):
				item.stop()

	def test_real_isolated_value_lifecycle(self):
		self.assertFalse(autostart.is_enabled())
		autostart.set_enabled(True)
		self.assertTrue(autostart.is_enabled())
		with self.winreg.OpenKey(self.winreg.HKEY_CURRENT_USER, autostart._RUN_KEY) as key:
			value, kind = self.winreg.QueryValueEx(key, self.name)
			self.assertEqual(value, "cmd.exe /c exit 0")
			self.assertEqual(kind, self.winreg.REG_SZ)
		with self.winreg.CreateKey(self.winreg.HKEY_CURRENT_USER, autostart._APPROVED_KEY) as key:
			self.winreg.SetValueEx(key, self.name, 0, self.winreg.REG_BINARY, b"\x03" + b"\x00" * 11)
		self.assertFalse(autostart.is_enabled())
		autostart.set_enabled(True)
		self.assertTrue(autostart.is_enabled())
		autostart.set_enabled(False)
		self.assertFalse(autostart.is_enabled())


if __name__ == "__main__":
	unittest.main()
