"""Hidden Windows options and passive version reads, never a browser launch."""

import contextlib
import ctypes
import ntpath
import os
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import accounts
import browser
import edge_version
import run_record


PROFILE = accounts.Account("synthetic", "synthetic-profiles/one", "Default")
BASE_ARGUMENTS = [
	"--disable-blink-features=AutomationControlled",
	"--user-data-dir=synthetic-profiles/one",
	"--profile-directory=Default",
	"--disable-gpu",
	"--accept-lang=en-US",
]
HIDDEN_ARGUMENTS = ["--headless=new", "--window-size=1920,1080"]
CONTAINER_ARGUMENTS = ["--no-sandbox", "--disable-dev-shm-usage"]


def user_agent(major):
	return f"--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36 Edg/{major}.0.0.0"


class BrowserOptionsTests(unittest.TestCase):
	def setUp(self):
		# Resolve Selenium's lazy option import before pretending to be Windows
		# on Linux; no driver or browser is created by this lookup.
		browser.webdriver.EdgeOptions
		self.stack = contextlib.ExitStack()
		self.addCleanup(self.stack.close)
		self.stack.enter_context(mock.patch.dict(os.environ, {
			"PROGRAMFILES(X86)": r"X:\Programs32", "PROGRAMFILES": r"X:\Programs64", "LOCALAPPDATA": r"X:\UserApps",
		}, clear=True))
		self.stack.enter_context(mock.patch.object(browser.sys, "platform", "win32"))
		self.stack.enter_context(mock.patch.object(browser, "HEADLESS", False))
		self.driver = self.stack.enter_context(mock.patch.object(browser.webdriver, "Edge"))
		self.popen = self.stack.enter_context(mock.patch.object(subprocess, "Popen"))
		self.run = self.stack.enter_context(mock.patch.object(subprocess, "run"))
		self.output = self.stack.enter_context(mock.patch.object(subprocess, "check_output"))
		self.emit = self.stack.enter_context(mock.patch.object(run_record, "emit"))

	def assert_passive(self):
		self.driver.assert_not_called()
		self.popen.assert_not_called()
		self.run.assert_not_called()
		self.output.assert_not_called()
		self.emit.assert_not_called()

	def assert_shared_options(self, options):
		self.assertEqual(options.experimental_options, {"excludeSwitches": ["enable-automation"], "useAutomationExtension": False})
		self.assertEqual(options.timeouts, {"pageLoad": 60_000, "script": 30_000})

	def test_hidden_windows_uses_explicit_binary_major_before_beacon(self):
		os.environ["EDGE_BINARY"] = r"X:\Chosen\msedge.exe"
		with mock.patch.object(edge_version, "_binary_major", return_value=155) as binary, mock.patch.object(edge_version, "_beacon_major") as beacon, mock.patch.object(edge_version.os.path, "isfile") as exists:
			options = browser.build_options(PROFILE, headless=True)
		self.assertEqual(options.arguments, BASE_ARGUMENTS + HIDDEN_ARGUMENTS + [user_agent(155)])
		self.assertEqual(options.binary_location, r"X:\Chosen\msedge.exe")
		binary.assert_called_once_with(r"X:\Chosen\msedge.exe")
		beacon.assert_not_called()
		exists.assert_not_called()
		self.assert_shared_options(options)
		self.assert_passive()

	def test_each_default_install_is_read_without_starting_selenium_or_a_program(self):
		for folder in (r"X:\Programs32", r"X:\Programs64", r"X:\UserApps"):
			with self.subTest(folder=folder):
				expected = ntpath.join(folder, "Microsoft", "Edge", "Application", "msedge.exe")
				with mock.patch.object(edge_version.os.path, "isfile", side_effect=lambda path: path == expected), mock.patch.object(edge_version, "_binary_major", return_value=154) as binary, mock.patch.object(edge_version, "_beacon_major") as beacon:
					options = browser.build_options(PROFILE, headless=True)
				self.assertEqual(options.arguments, BASE_ARGUMENTS + HIDDEN_ARGUMENTS + [user_agent(154)])
				binary.assert_called_once_with(expected)
				beacon.assert_not_called()
				self.assert_passive()

	def test_unreadable_explicit_binary_uses_beacon_not_another_install(self):
		os.environ["EDGE_BINARY"] = r"X:\Chosen\msedge.exe"
		with mock.patch.object(edge_version, "_binary_major", return_value=None) as binary, mock.patch.object(edge_version, "_beacon_major", return_value=153) as beacon, mock.patch.object(edge_version.os.path, "isfile") as exists:
			options = browser.build_options(PROFILE, headless=True)
		self.assertEqual(options.arguments, BASE_ARGUMENTS + HIDDEN_ARGUMENTS + [user_agent(153)])
		binary.assert_called_once_with(r"X:\Chosen\msedge.exe")
		beacon.assert_called_once_with()
		exists.assert_not_called()
		self.assert_passive()

	def test_unreadable_default_binary_falls_back_to_beacon(self):
		with mock.patch.object(edge_version.os.path, "isfile", return_value=True), mock.patch.object(edge_version, "_binary_major", return_value=None) as binary, mock.patch.object(edge_version, "_beacon_major", return_value=152) as beacon:
			options = browser.build_options(PROFILE, headless=True)
		self.assertEqual(options.arguments, BASE_ARGUMENTS + HIDDEN_ARGUMENTS + [user_agent(152)])
		binary.assert_called_once_with(r"X:\Programs32\Microsoft\Edge\Application\msedge.exe")
		beacon.assert_called_once_with()
		self.assert_passive()

	def test_missing_default_binary_uses_beacon_without_reading_a_different_file(self):
		with mock.patch.object(edge_version.os.path, "isfile", return_value=False), mock.patch.object(edge_version, "_binary_major") as binary, mock.patch.object(edge_version, "_beacon_major", return_value=151):
			options = browser.build_options(PROFILE, headless=True)
		self.assertEqual(options.arguments, BASE_ARGUMENTS + HIDDEN_ARGUMENTS + [user_agent(151)])
		binary.assert_not_called()
		self.assert_passive()

	def test_unknown_version_sets_no_override_and_inspection_emits_nothing(self):
		with mock.patch.object(edge_version.os.path, "isfile", return_value=False), mock.patch.object(edge_version, "_beacon_major", return_value=None), mock.patch.object(browser.logger, "warning") as warning:
			options = browser.build_options(PROFILE, headless=True)
		self.assertEqual(options.arguments, BASE_ARGUMENTS + HIDDEN_ARGUMENTS)
		warning.assert_not_called()
		self.assert_passive()

	def test_unknown_version_warns_once_for_each_account_start_not_once_per_process(self):
		other = accounts.Account("other", "synthetic-profiles/two", "Default")
		with mock.patch.object(browser, "HEADLESS", True), mock.patch.object(edge_version, "installed_major", return_value=None), mock.patch.object(browser, "build_service", return_value="synthetic service"), self.assertLogs(browser.logger, level="WARNING") as logs:
			for account in (PROFILE, PROFILE, other):
				browser.start_driver(account)
		self.assertEqual(self.driver.call_count, 3)
		self.assertEqual(len(logs.output), 3)
		detail = "Edge version could not be read; using the browser's own user agent in hidden mode."
		self.assertEqual(logs.output, ["WARNING:browser:" + detail] * 3)
		self.assertEqual(self.emit.call_args_list, [mock.call("warning", account=account.name, code="edge_version_unknown", detail=detail) for account in (PROFILE, PROFILE, other)])
		for call, account in zip(self.driver.call_args_list, (PROFILE, PROFILE, other)):
			expected = BASE_ARGUMENTS.copy()
			expected[1] = f"--user-data-dir={account.user_data_dir}"
			self.assertEqual(call.kwargs["options"].arguments, expected + HIDDEN_ARGUMENTS)
		self.popen.assert_not_called()

	def test_known_hidden_version_has_no_warning(self):
		with mock.patch.object(browser, "HEADLESS", True), mock.patch.object(edge_version, "installed_major", return_value=155), mock.patch.object(browser, "build_service"), mock.patch.object(browser.logger, "warning") as warning:
			browser.start_driver(PROFILE)
		warning.assert_not_called()
		self.emit.assert_not_called()

	def test_unknown_version_warning_is_written_to_the_real_account_record_once(self):
		import tempfile
		with tempfile.TemporaryDirectory() as folder:
			recorder = run_record.open_run("cli", [PROFILE.name], {}, [], directory=folder)
			try:
				with mock.patch.object(browser, "HEADLESS", True), mock.patch.object(edge_version, "installed_major", return_value=None), mock.patch.object(browser, "build_service"), mock.patch.object(run_record, "emit", wraps=recorder.emit), self.assertLogs(browser.logger, level="WARNING"):
					browser.start_driver(PROFILE)
				warnings = [event for event in run_record.read_events(recorder.path) if event["type"] == "warning"]
				self.assertEqual(len(warnings), 1)
				self.assertEqual(warnings[0]["code"], "edge_version_unknown")
				self.assertEqual(warnings[0]["account"], PROFILE.name)
				self.assertEqual(warnings[0]["detail"], "Edge version could not be read; using the browser's own user agent in hidden mode.")
			finally:
				run_record.close_run("checked", 0)

	def test_visible_options_are_unchanged_on_all_platforms_and_never_read_a_version(self):
		os.environ["EDGE_BINARY"] = r"X:\Chosen\msedge.exe"
		for platform in ("win32", "linux", "darwin"):
			with self.subTest(platform=platform), mock.patch.object(browser.sys, "platform", platform), mock.patch.object(edge_version, "installed_major") as version:
				options = browser.build_options(PROFILE, headless=False)
				self.assertEqual(options.arguments, BASE_ARGUMENTS)
				self.assertEqual(options.binary_location, r"X:\Chosen\msedge.exe")
				self.assert_shared_options(options)
				version.assert_not_called()
		self.assert_passive()

	def test_non_windows_hidden_options_are_unchanged_and_never_read_a_version(self):
		for platform in ("linux", "darwin"):
			with self.subTest(platform=platform), mock.patch.object(browser.sys, "platform", platform), mock.patch.object(edge_version, "installed_major") as version:
				options = browser.build_options(PROFILE, headless=True)
				self.assertEqual(options.arguments, BASE_ARGUMENTS + HIDDEN_ARGUMENTS + CONTAINER_ARGUMENTS)
				self.assert_shared_options(options)
				version.assert_not_called()
		self.assert_passive()

	def test_default_mode_still_uses_headless_setting_and_inspection_does_not_change_it(self):
		with mock.patch.object(edge_version, "installed_major", return_value=155), mock.patch.object(browser, "HEADLESS", True):
			self.assertEqual(browser.build_options(PROFILE).arguments, BASE_ARGUMENTS + HIDDEN_ARGUMENTS + [user_agent(155)])
			self.assertEqual(browser.build_options(PROFILE, headless=False).arguments, BASE_ARGUMENTS)
			self.assertTrue(browser.HEADLESS)
		self.assert_passive()

	def test_unknown_version_does_not_warn_for_visible_or_non_windows_starts(self):
		for platform, hidden in (("win32", False), ("linux", True)):
			with self.subTest(platform=platform, hidden=hidden), mock.patch.object(browser.sys, "platform", platform), mock.patch.object(browser, "HEADLESS", hidden), mock.patch.object(edge_version, "installed_major") as version, mock.patch.object(browser, "build_service"), mock.patch.object(browser.logger, "warning") as warning:
				browser.start_driver(PROFILE)
				version.assert_not_called()
				warning.assert_not_called()
		self.emit.assert_not_called()


class VersionReaderTests(unittest.TestCase):
	def fake_api(self, *, signature=0xFEEF04BD, major=155, length=None, fail=None):
		info = edge_version._FixedFileInfo()
		info.signature = signature
		info.file_version_ms = major << 16
		info.file_version_ls = (4283 << 16) | 45
		def query(data, key, pointer, size):
			ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.cast(ctypes.pointer(info), ctypes.c_void_p)
			ctypes.cast(size, ctypes.POINTER(ctypes.c_uint))[0] = ctypes.sizeof(info) if length is None else length
			return 0 if fail == "query" else 1
		return types.SimpleNamespace(
			GetFileVersionInfoSizeW=mock.Mock(return_value=0 if fail == "size" else 64),
			GetFileVersionInfoW=mock.Mock(return_value=0 if fail == "read" else 1),
			VerQueryValueW=mock.Mock(side_effect=query),
		)

	def test_binary_file_version_resource_is_read_without_processes_or_a_driver(self):
		api = self.fake_api()
		with mock.patch.object(edge_version.ctypes, "WinDLL", return_value=api, create=True) as loader, mock.patch.object(subprocess, "Popen") as process, mock.patch.object(subprocess, "run") as run, mock.patch.object(browser.webdriver, "Edge") as driver:
			self.assertEqual(edge_version._binary_major(r"X:\Chosen\msedge.exe"), 155)
		loader.assert_called_once_with("version", use_last_error=True)
		self.assertEqual(api.GetFileVersionInfoSizeW.call_args.args[0], r"X:\Chosen\msedge.exe")
		self.assertEqual(api.GetFileVersionInfoW.call_args.args[:3], (r"X:\Chosen\msedge.exe", 0, 64))
		self.assertEqual(api.VerQueryValueW.call_args.args[1], "\\")
		process.assert_not_called()
		run.assert_not_called()
		driver.assert_not_called()

	def test_invalid_or_unreadable_file_resource_is_unknown(self):
		for kwargs in ({"signature": 0}, {"major": 0}, {"length": 0}, {"fail": "size"}, {"fail": "read"}, {"fail": "query"}):
			with self.subTest(kwargs=kwargs), mock.patch.object(edge_version.ctypes, "WinDLL", return_value=self.fake_api(**kwargs), create=True):
				self.assertIsNone(edge_version._binary_major("synthetic.exe"))
		with mock.patch.object(edge_version.ctypes, "WinDLL", side_effect=OSError("synthetic missing version library"), create=True):
			self.assertIsNone(edge_version._binary_major("synthetic.exe"))

	def test_beacon_is_opened_read_only_and_its_handle_is_closed(self):
		key = mock.MagicMock()
		registry = types.SimpleNamespace(HKEY_CURRENT_USER="synthetic user", KEY_READ=0x20019, OpenKey=mock.Mock(return_value=key), QueryValueEx=mock.Mock(return_value=("155.0.4283.45", 1)))
		with mock.patch.dict(sys.modules, {"winreg": registry}):
			self.assertEqual(edge_version._beacon_major(), 155)
		registry.OpenKey.assert_called_once_with("synthetic user", r"Software\Microsoft\Edge\BLBeacon", 0, 0x20019)
		registry.QueryValueEx.assert_called_once_with(key.__enter__.return_value, "version")
		key.__exit__.assert_called_once()

	def test_invalid_or_missing_beacon_is_unknown(self):
		for value in ("", "Headless/155", "0.0.0.0", "155", "155.0.0.0 extra", None, 155):
			with self.subTest(value=value):
				registry = types.SimpleNamespace(HKEY_CURRENT_USER=1, KEY_READ=1, OpenKey=mock.MagicMock(), QueryValueEx=mock.Mock(return_value=(value, 1)))
				with mock.patch.dict(sys.modules, {"winreg": registry}):
					self.assertIsNone(edge_version._beacon_major())
		registry.OpenKey.side_effect = OSError("synthetic missing beacon")
		with mock.patch.dict(sys.modules, {"winreg": registry}):
			self.assertIsNone(edge_version._beacon_major())

	def test_version_reader_off_windows_never_touches_windows_resources(self):
		with mock.patch.object(edge_version.sys, "platform", "linux"), mock.patch.object(edge_version, "_binary_major") as binary, mock.patch.object(edge_version, "_beacon_major") as beacon:
			self.assertIsNone(edge_version.installed_major("synthetic.exe"))
		binary.assert_not_called()
		beacon.assert_not_called()


if __name__ == "__main__":
	unittest.main()
