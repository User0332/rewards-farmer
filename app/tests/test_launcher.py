import _bootstrap  # noqa: F401

import ctypes
from ctypes import wintypes
from datetime import datetime
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch
import uuid


_PATH = Path(__file__).resolve().parents[1] / "launcher.py"
_SPEC = importlib.util.spec_from_file_location("rf_launcher", _PATH)
launcher = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
	def setUp(self):
		import rfapp
		self.real_popen = subprocess.Popen
		self.real_windll = getattr(ctypes, "WinDLL", None)
		guard = patch.object(subprocess, "Popen", side_effect=AssertionError("Unpatched process in launcher test"))
		guard.start()
		self.addCleanup(guard.stop)
		guard = patch.object(launcher.ctypes, "WinDLL", side_effect=AssertionError("Unpatched native API in launcher test"), create=True)
		guard.start()
		self.addCleanup(guard.stop)
		if sys.platform != "win32":
			# Keep mocked dispatch and failure reporting testable without Windows ctypes APIs.
			guard = patch.object(launcher.ctypes, "get_last_error", side_effect=AssertionError("Unpatched native error lookup in launcher test"), create=True)
			guard.start()
			self.addCleanup(guard.stop)
			guard = patch.object(launcher.ctypes, "WinError", side_effect=lambda code: OSError(f"[WinError {code}] synthetic native error"), create=True)
			guard.start()
			self.addCleanup(guard.stop)
		state = patch.object(launcher, "_install_mutex", None)
		state.start()
		self.addCleanup(state.stop)
		state = patch.object(launcher, "_install_mutex_error", None)
		state.start()
		self.addCleanup(state.stop)
		self.addCleanup(setattr, sys, "excepthook", sys.excepthook)
		self.paths = SimpleNamespace(prepare_environment=Mock(), app_log_path=Mock(), is_frozen=Mock(return_value=False))
		self.patch = patch.dict(sys.modules, {"rfapp.paths": self.paths})
		self.patch.start()
		self.cached = patch.object(rfapp, "paths", self.paths, create=True)
		self.cached.start()
		self.addCleanup(self.cached.stop)
		self.log = patch.object(launcher, "_logging")
		self.logging = self.log.start()
		self.addCleanup(self.patch.stop)
		self.addCleanup(self.log.stop)

	def test_supervisor_dispatch(self):
		for token, kind in (("--scheduled", "scheduled"), ("--run-now", "run_now"), ("--check-now", "check_now")):
			with self.subTest(token=token):
				call = Mock(return_value=5)
				with patch.dict(sys.modules, {"rfapp.supervisor": SimpleNamespace(supervise=call)}):
					self.assertEqual(launcher.main([token]), 5)
				call.assert_called_once_with(kind)

	def test_accounts_passthrough(self):
		call = Mock(return_value=0)
		with patch.dict(sys.modules, {"rfapp.supervisor": SimpleNamespace(supervise=call)}):
			self.assertEqual(launcher.main(["--run-now", "--accounts", "account-1,account-2"]), 0)
		call.assert_called_once_with("run_now", ["account-1", "account-2"])

	def test_farm_rest_is_untouched(self):
		call = Mock(return_value=4)
		old_hook = sys.excepthook
		self.addCleanup(setattr, sys, "excepthook", old_hook)
		with patch.dict(sys.modules, {"main": SimpleNamespace(cli=call)}):
			self.assertEqual(launcher.main(["--farm", "--a", "--accounts", "literal"]), 4)
		call.assert_called_once_with(["--a", "--accounts", "literal"])
		self.logging.assert_not_called()
		self.assertIs(sys.excepthook, launcher._farm_exception)

	def test_signin(self):
		call = Mock(return_value=3)
		with patch.dict(sys.modules, {"rfapp.signin": SimpleNamespace(run=call)}):
			self.assertEqual(launcher.main(["--signin", "account-1", "--new"]), 3)
		call.assert_called_once_with("account-1", new=True)

	def test_window_dispatch(self):
		for args, hidden in (([], False), (["--tray"], True)):
			call = Mock()
			with patch.dict(sys.modules, {"rfapp.window": SimpleNamespace(run=call)}):
				self.assertEqual(launcher.main(args), 0)
			call.assert_called_once_with(start_hidden=hidden)

	def test_bad_commands(self):
		for args in (["--garbage"], ["--scheduled", "--accounts", "a"], ["--run-now", "--accounts"], ["--run-now", "--accounts", "a,,b"], ["--signin"], ["--signin", "a", "unknown"]):
			with self.subTest(args=args):
				self.assertEqual(launcher.main(args), 2)

	def test_version(self):
		from rfapp import __version__
		with patch("sys.stdout", new_callable=io.StringIO) as output:
			self.assertEqual(launcher.main(["--version"]), 0)
			self.assertEqual(output.getvalue().strip(), __version__)

	def test_windowless_handles(self):
		sink = io.StringIO()
		with patch("sys.stdout", None), patch("sys.stderr", None), patch("builtins.open", return_value=sink) as opened:
			launcher.main(["--version"])
			self.assertIs(sys.stdout, sink)
			self.assertIs(sys.stderr, sink)
			opened.assert_called_once_with(launcher.os.devnull, "w", encoding="utf-8")

	def test_frozen_modes_hold_install_mutex_before_dispatch(self):
		self.paths.is_frozen.return_value = True
		cases = (([], "rfapp.window", "run", None), (["--tray"], "rfapp.window", "run", None), (["--scheduled"], "rfapp.supervisor", "supervise", 7), (["--run-now"], "rfapp.supervisor", "supervise", 7), (["--check-now"], "rfapp.supervisor", "supervise", 7), (["--farm", "--help"], "main", "cli", 4), (["--signin", "account-1"], "rfapp.signin", "run", 3))
		for args, module, method, result in cases:
			with self.subTest(args=args):
				launcher._install_mutex = None
				events = []
				self.paths.prepare_environment.side_effect = lambda: events.append("prepare")
				create = Mock(side_effect=lambda *args: events.append("mutex") or 0x123456789)
				kernel = SimpleNamespace(CreateMutexW=create)
				def dispatch(*args, **kwargs):
					events.append("dispatch")
					self.assertEqual(launcher._install_mutex, 0x123456789)
					return result
				with patch.object(launcher.ctypes, "WinDLL", return_value=kernel) as dll, patch.dict(sys.modules, {module: SimpleNamespace(**{method: dispatch})}):
					self.assertEqual(launcher.main(args), 0 if result is None else result)
				self.assertEqual(events, ["prepare", "mutex", "dispatch"])
				create.assert_called_once_with(None, False, "Local\\RewardsFarmer.Installed")
				dll.assert_called_once_with("kernel32", use_last_error=True)
				self.assertEqual(create.argtypes, [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR])
				self.assertIs(create.restype, wintypes.HANDLE)
				self.assertEqual(launcher._install_mutex, 0x123456789)

	def test_frozen_version_creates_no_install_mutex(self):
		self.paths.is_frozen.return_value = True
		with patch("sys.stdout", new_callable=io.StringIO):
			self.assertEqual(launcher.main(["--version"]), 0)
		self.assertIsNone(launcher._install_mutex)
		launcher.ctypes.WinDLL.assert_not_called()

	def test_source_dispatch_creates_no_install_mutex(self):
		call = Mock(return_value=7)
		with patch.dict(sys.modules, {"rfapp.supervisor": SimpleNamespace(supervise=call)}):
			self.assertEqual(launcher.main(["--scheduled"]), 7)
		call.assert_called_once_with("scheduled")
		self.assertIsNone(launcher._install_mutex)
		launcher.ctypes.WinDLL.assert_not_called()

	def test_production_install_mutex_name_matches_inno(self):
		self.assertEqual(launcher._INSTALL_MUTEX_NAME, "Local\\RewardsFarmer.Installed")
		script = (_PATH.parent / "packaging" / "RewardsFarmer.iss").read_text(encoding="utf-8")
		self.assertEqual([line.strip() for line in script.splitlines() if line.strip().startswith("AppMutex=")], ["AppMutex=RewardsFarmer.Installed"])

	def test_install_mutex_existing_object_and_retained_handle_are_normal(self):
		self.paths.is_frozen.return_value = True
		create = Mock(return_value=123)
		call = Mock(return_value=7)
		with patch.object(launcher.ctypes, "WinDLL", return_value=SimpleNamespace(CreateMutexW=create)), patch.object(launcher.ctypes, "get_last_error", return_value=183), patch.object(launcher.logging, "getLogger") as logger, patch.dict(sys.modules, {"rfapp.supervisor": SimpleNamespace(supervise=call)}):
			self.assertEqual(launcher.main(["--scheduled"]), 7)
			self.assertEqual(launcher.main(["--scheduled"]), 7)
			logger.assert_not_called()
		create.assert_called_once()
		self.assertEqual(launcher._install_mutex, 123)
		self.assertEqual(call.call_count, 2)

	def test_failed_install_mutex_is_logged_and_dispatch_still_runs(self):
		self.paths.is_frozen.return_value = True
		call = Mock(return_value=7)
		create = Mock(return_value=None)
		with patch.object(launcher.ctypes, "WinDLL", return_value=SimpleNamespace(CreateMutexW=create)), patch.object(launcher.ctypes, "get_last_error", return_value=5), patch.object(launcher.logging, "getLogger") as logger, patch.dict(sys.modules, {"rfapp.supervisor": SimpleNamespace(supervise=call)}):
			self.assertEqual(launcher.main(["--scheduled"]), 7)
			self.logging.assert_called_once()
			logger.assert_called_once_with("rfapp")
			logger.return_value.warning.assert_called_once()
			self.assertEqual(logger.return_value.warning.call_args.args[0], "%s")
			self.assertIn("Could not create Local\\RewardsFarmer.Installed: [WinError 5]", logger.return_value.warning.call_args.args[1])
		call.assert_called_once_with("scheduled")
		self.assertIsNone(launcher._install_mutex)
		self.assertIsNone(launcher._install_mutex_error)

	def test_install_mutex_failure_is_deferred_until_logging_ready(self):
		create = Mock(return_value=None)
		with patch.object(launcher.ctypes, "WinDLL", return_value=SimpleNamespace(CreateMutexW=create)), patch.object(launcher.ctypes, "get_last_error", return_value=5), patch.object(launcher.logging, "getLogger") as logger:
			launcher._hold_install_mutex()
			logger.assert_not_called()
		self.assertIn("[WinError 5]", launcher._install_mutex_error)

	def _isolate_app_logger(self):
		logger = launcher.logging.getLogger("rfapp")
		handlers, level, propagate, disabled = logger.handlers[:], logger.level, logger.propagate, logger.disabled
		logger.handlers = []
		logger.disabled = False
		def restore():
			for handler in logger.handlers[:]:
				logger.removeHandler(handler)
				handler.close()
			logger.handlers, logger.level, logger.propagate, logger.disabled = handlers, level, propagate, disabled
		self.addCleanup(restore)
		return logger

	def test_windowed_frozen_mutex_failure_writes_warning_to_app_log(self):
		folder = tempfile.TemporaryDirectory()
		self.addCleanup(folder.cleanup)
		path = Path(folder.name) / "data" / "app.log"
		self.paths.app_log_path.return_value = path
		self.paths.is_frozen.return_value = True
		logger = self._isolate_app_logger()
		self.log.stop()
		def dispatch(**kwargs):
			self.assertIn("WARNING rfapp: Could not create Local\\RewardsFarmer.Installed: [WinError 5]", path.read_text(encoding="utf-8"))
		call = Mock(side_effect=dispatch)
		with patch.object(launcher.ctypes, "WinDLL", return_value=SimpleNamespace(CreateMutexW=Mock(return_value=None))), patch.object(launcher.ctypes, "get_last_error", return_value=5), patch.dict(sys.modules, {"rfapp.window": SimpleNamespace(run=call)}), patch("sys.stdout", None), patch("sys.stderr", None):
			try:
				self.assertEqual(launcher.main([]), 0)
			finally:
				if sys.stdout is not None:
					sys.stdout.close()
		self.assertEqual(len(logger.handlers), 1)
		self.assertIsInstance(logger.handlers[0], launcher.RotatingFileHandler)
		lines = path.read_text(encoding="utf-8").splitlines()
		self.assertEqual(len(lines), 1)
		self.assertIn("WARNING rfapp:", lines[0])
		launcher._log_install_mutex_error()
		self.assertEqual(path.read_text(encoding="utf-8").splitlines(), lines)
		call.assert_called_once_with(start_hidden=False)

	def test_frozen_farm_mutex_failure_appends_timestamped_warning_once(self):
		folder = tempfile.TemporaryDirectory()
		self.addCleanup(folder.cleanup)
		path = Path(folder.name) / "data" / "app.log"
		self.paths.app_log_path.return_value = path
		self.paths.is_frozen.return_value = True
		def dispatch(args):
			self.assertIn("Could not create Local\\RewardsFarmer.Installed: [WinError 5]", path.read_text(encoding="utf-8"))
			return 4
		call = Mock(side_effect=dispatch)
		with patch.object(launcher.ctypes, "WinDLL", return_value=SimpleNamespace(CreateMutexW=Mock(return_value=None))), patch.object(launcher.ctypes, "get_last_error", return_value=5), patch.dict(sys.modules, {"main": SimpleNamespace(cli=call)}), patch.object(launcher.logging, "getLogger", side_effect=AssertionError("Farmer startup must use its direct log fallback")):
			self.assertEqual(launcher.main(["--farm", "--help"]), 4)
		self.logging.assert_not_called()
		lines = path.read_text(encoding="utf-8").splitlines()
		self.assertEqual(len(lines), 1)
		at, message = lines[0].split(" ", 1)
		self.assertTrue(at.endswith("Z"))
		self.assertIsNotNone(datetime.fromisoformat(at.replace("Z", "+00:00")).tzinfo)
		self.assertIn("WARNING rfapp: Could not create Local\\RewardsFarmer.Installed: [WinError 5]", message)
		launcher._log_install_mutex_error(farm=True)
		self.assertEqual(path.read_text(encoding="utf-8").splitlines(), lines)
		self.assertIsNone(launcher._install_mutex_error)
		call.assert_called_once_with(["--help"])
		path.unlink()

	def test_frozen_farm_runs_when_direct_warning_write_fails(self):
		self.paths.is_frozen.return_value = True
		path = Mock()
		self.paths.app_log_path.return_value = path
		call = Mock(return_value=4)
		for stage in ("parent", "open", "write"):
			with self.subTest(stage=stage):
				path.reset_mock()
				path.parent.mkdir.side_effect = OSError("denied") if stage == "parent" else None
				path.open.side_effect = OSError("denied") if stage == "open" else None
				writer = Mock()
				writer.write.side_effect = OSError("denied") if stage == "write" else None
				context = MagicMock()
				context.__enter__.return_value = writer
				path.open.return_value = context
				with patch.object(launcher.ctypes, "WinDLL", return_value=SimpleNamespace(CreateMutexW=Mock(return_value=None))), patch.object(launcher.ctypes, "get_last_error", return_value=5), patch.dict(sys.modules, {"main": SimpleNamespace(cli=call)}):
					self.assertEqual(launcher.main(["--farm", "--help"]), 4)
				if stage == "write":
					writer.write.assert_called_once()
					context.__exit__.assert_called_once()
				else:
					writer.write.assert_not_called()
		self.assertEqual(call.call_count, 3)
		self.assertIsNone(launcher._install_mutex_error)

	@unittest.skipUnless(sys.platform == "win32", "Windows installed mutex lifecycle")
	def test_real_install_mutex_exists_until_frozen_child_exits(self):
		kernel = self.real_windll("kernel32", use_last_error=True)
		kernel.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
		kernel.OpenMutexW.restype = wintypes.HANDLE
		kernel.CloseHandle.argtypes = [wintypes.HANDLE]
		kernel.CloseHandle.restype = wintypes.BOOL
		name = f"Local\\RewardsFarmer.Test.Install.{os.getpid()}.{uuid.uuid4().hex}"
		self.assertNotEqual(name, launcher._INSTALL_MUTEX_NAME)
		def exists(mutex_name=name):
			handle = kernel.OpenMutexW(0x00100000, False, mutex_name)
			if handle:
				self.assertTrue(kernel.CloseHandle(handle))
				return True
			self.assertEqual(ctypes.get_last_error(), 2)
			return False
		self.assertFalse(exists(), "The unique test mutex already exists")
		holder_code = """
import ctypes, os, sys, threading
from ctypes import wintypes
timer = threading.Timer(20, lambda: os._exit(3))
timer.daemon = True
timer.start()
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel.CreateMutexW.restype = wintypes.HANDLE
kernel.CloseHandle.argtypes = [wintypes.HANDLE]
kernel.CloseHandle.restype = wintypes.BOOL
handle = kernel.CreateMutexW(None, False, sys.argv[1])
assert handle
try:
	print('PRODUCTION_MUTEX_HELD', flush=True)
	assert sys.stdin.readline().strip() == 'EXIT'
finally:
	assert kernel.CloseHandle(handle)
	timer.cancel()
"""
		code = """
import importlib.util, os, sys, threading, types
timer = threading.Timer(15, lambda: os._exit(3))
timer.daemon = True
timer.start()
sys.path.insert(0, sys.argv[1])
paths = types.ModuleType('rfapp.paths')
paths.prepare_environment = lambda: None
paths.is_frozen = lambda: True
sys.modules['rfapp.paths'] = paths
supervisor = types.ModuleType('rfapp.supervisor')
supervisor.supervise = lambda kind: 0
sys.modules['rfapp.supervisor'] = supervisor
spec = importlib.util.spec_from_file_location('entry', sys.argv[2])
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)
entry._INSTALL_MUTEX_NAME = sys.argv[3]
entry._logging = lambda: None
assert entry.main(['--scheduled']) == 0
assert entry._install_mutex
assert 'webview' not in sys.modules and 'pystray' not in sys.modules
print('INSTALL_MUTEX_READY', flush=True)
assert sys.stdin.readline().strip() == 'EXIT'
timer.cancel()
"""
		with self.real_popen([sys.executable, "-c", holder_code, launcher._INSTALL_MUTEX_NAME], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as holder:
			try:
				self.assertEqual(holder.stdout.readline().strip(), "PRODUCTION_MUTEX_HELD")
				self.assertTrue(exists(launcher._INSTALL_MUTEX_NAME))
				self.assertIsNone(holder.poll())
				self.assertFalse(exists())
				with self.real_popen([sys.executable, "-c", code, str(_PATH.parent), str(_PATH), name], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
					try:
						self.assertEqual(child.stdout.readline().strip(), "INSTALL_MUTEX_READY")
						self.assertTrue(exists())
						self.assertTrue(exists(launcher._INSTALL_MUTEX_NAME))
						self.assertIsNone(child.poll())
					finally:
						output, errors = child.communicate("EXIT\n", timeout=20)
					self.assertEqual(child.returncode, 0, output + errors)
				self.assertFalse(exists())
				self.assertTrue(exists(launcher._INSTALL_MUTEX_NAME))
			finally:
				output, errors = holder.communicate("EXIT\n", timeout=25)
			self.assertEqual(holder.returncode, 0, output + errors)
		self.assertFalse(exists())

	def test_clean_child_dispatch_has_no_gui_modules(self):
		# A fresh interpreter catches import-time GUI coupling that in-process mocks miss.
		code = """
import importlib.util, sys, types
sys.path.insert(0, sys.argv[1])
paths = types.ModuleType('rfapp.paths')
paths.prepare_environment = lambda: None
paths.is_frozen = lambda: True
sys.modules['rfapp.paths'] = paths
import ctypes
from unittest.mock import Mock
ctypes.WinDLL = Mock(return_value=types.SimpleNamespace(CreateMutexW=Mock(return_value=123)))
supervisor = types.ModuleType('rfapp.supervisor')
supervisor.supervise = lambda *args: 7
sys.modules['rfapp.supervisor'] = supervisor
farmer = types.ModuleType('main')
farmer.cli = lambda args: 4
sys.modules['main'] = farmer
spec = importlib.util.spec_from_file_location('entry', sys.argv[2])
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)
entry._logging = lambda: None
assert entry.main([sys.argv[3]]) == int(sys.argv[4])
assert entry._install_mutex == 123
assert 'webview' not in sys.modules and 'pystray' not in sys.modules
print('DISPATCH_NO_GUI_PASS')
"""
		for token, result in (("--scheduled", 7), ("--farm", 4)):
			# Only this literal fake-module script may use a real interpreter.
			with patch.object(subprocess, "Popen", self.real_popen):
				proc = subprocess.run([sys.executable, "-c", code, str(_PATH.parent), str(_PATH), token, str(result)], capture_output=True, text=True, timeout=20)
			self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
			self.assertIn("DISPATCH_NO_GUI_PASS", proc.stdout)
