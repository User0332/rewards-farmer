import _bootstrap  # noqa: F401

import builtins
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import traceback
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from file_replace import read_file, replace_file
from rfapp import accounts_admin, bridge, envfile, prefs, settings, signin, window


class FileReaderCallsiteTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.home = Path(self.temp.name)
		self.env = self.home / ".env"
		self.env.write_text("QUERY_SOURCE=trends\nUSER_DATA_DIR=" + str(self.home / "profiles") + "\nF61_EXISTING=file\nF61_NEW='from file'\n", encoding="utf-8")
		item = patch.dict(os.environ, {"REWARDS_FARMER_DATA_HOME": str(self.home), "F61_EXISTING": "environment"})
		item.start()
		self.addCleanup(item.stop)
		os.environ.pop("F61_NEW", None)
		for target in ("subprocess.Popen", "subprocess.run", "selenium.webdriver.Edge"):
			item = patch(target, side_effect=AssertionError("No process or browser in reader test"))
			item.start()
			self.addCleanup(item.stop)

	@contextmanager
	def first_open_fails(self, path):
		real_open = builtins.open
		attempts = []
		def open_file(name, *args, **kwargs):
			if not isinstance(name, int) and Path(name) == path:
				attempts.append(name)
				if len(attempts) == 1:
					raise PermissionError("delete-pending")
			return real_open(name, *args, **kwargs)
		with patch("file_replace.builtins.open", side_effect=open_file):
			yield attempts
		self.assertGreaterEqual(len(attempts), 2)

	def status_file(self):
		path = self.home / "runs" / "signin-account-1.json"
		path.parent.mkdir()
		value = {"account_id": "account-1", "state": "waiting", "error_code": None, "message_key": "accounts.signin.waiting", "detail": None, "at": "2026-10-15T23:00:00Z", "pid": os.getpid()}
		path.write_text(json.dumps(value), encoding="utf-8")
		return path, value

	def test_settings_env_retries_and_keeps_dotenv_semantics(self):
		self.env.write_text("export F61_BASE=first\nF61_BASE=last\nF61_QUOTED='two words'\nF61_INTERPOLATED=${F61_BASE}\n", encoding="utf-8")
		with self.first_open_fails(self.env):
			self.assertEqual(settings._env(), {"F61_BASE": "last", "F61_QUOTED": "two words", "F61_INTERPOLATED": "last"})
		self.assertEqual(os.environ["F61_EXISTING"], "environment")
		self.env.unlink()
		self.assertEqual(settings._env(), {})

	def test_settings_env_retries_transient_missing_instead_of_defaulting(self):
		real_open = builtins.open
		attempts = []
		def open_file(path, *args, **kwargs):
			attempts.append(path)
			if len(attempts) == 1:
				raise FileNotFoundError("atomic replacement gap")
			return real_open(path, *args, **kwargs)
		with patch("file_replace.builtins.open", side_effect=open_file):
			self.assertEqual(settings._env()["QUERY_SOURCE"], "trends")
		self.assertEqual(attempts, [self.env, self.env])

	@unittest.skipUnless(sys.platform == "win32", "Windows target gap can outlast absence confirmation")
	def test_settings_env_waits_for_live_replacement_past_one_hundred_ms(self):
		gap = threading.Event()
		failures = []
		pending = self.home / ".env.pending"
		pending.write_text("QUERY_SOURCE=trends\nF61_VALUE=after\n", encoding="utf-8")
		def replace(source, target):
			os.replace(target, self.home / ".before")
			gap.set()
			time.sleep(0.3)
			os.replace(source, target)
		def writer():
			try:
				replace_file(pending, self.env, replace=replace)
			except BaseException as exc:
				failures.append({"worker": "writer", "type": type(exc).__name__, "errno": getattr(exc, "errno", None), "winerror": getattr(exc, "winerror", None), "traceback": traceback.format_exc()})
		thread = threading.Thread(target=writer)
		thread.start()
		try:
			self.assertTrue(gap.wait(3))
			self.assertEqual(settings._env().get("F61_VALUE"), "after")
		finally:
			thread.join(4)
		self.assertFalse(thread.is_alive())
		self.assertEqual(failures, [])

	def test_envfile_load_retries_and_preserves_bytes(self):
		text = "\ufeff# keep\r\nA='two words'\r\n"
		self.env.write_bytes(text.encode())
		with self.first_open_fails(self.env):
			editor = envfile.EnvFile.load(self.env)
		self.assertEqual(editor.get("A"), "two words")
		editor.save()
		self.assertEqual(self.env.read_bytes(), text.encode())

	def test_preferences_load_retries_without_defaulting(self):
		path = self.home / "app-prefs.json"
		path.write_text('{"setup_done":true,"language":"de"}', encoding="utf-8")
		with self.first_open_fails(path):
			self.assertTrue(prefs.load()["setup_done"])
		self.assertEqual(prefs.load()["language"], "de")

	def test_account_signin_status_read_retries(self):
		path, value = self.status_file()
		with self.first_open_fails(path):
			status = accounts_admin.get_signin_status("account-1")
		self.assertEqual(status, {key: item for key, item in value.items() if key != "pid"})

	def test_account_signin_glob_read_retries(self):
		path, _ = self.status_file()
		with self.first_open_fails(path):
			self.assertEqual(accounts_admin._signin_active()["state"], "waiting")

	def test_signin_process_dotenv_retries_and_does_not_override(self):
		with self.first_open_fails(self.env), patch("run_lock.acquire", return_value=None):
			self.assertEqual(signin.run("account-1", False), 3)
		self.assertEqual(os.environ["F61_EXISTING"], "environment")
		self.assertEqual(os.environ["F61_NEW"], "from file")

	def test_bridge_secret_dotenv_retries_without_interpolation(self):
		self.env.write_text("OPENROUTER_API_KEY='${F61_EXISTING}'\n", encoding="utf-8")
		api = bridge.Bridge()
		self.addCleanup(api._close)
		with self.first_open_fails(self.env):
			api._refresh_secrets()
		self.assertIn("${F61_EXISTING}", api._secret_values)
		self.assertNotIn("environment", api._secret_values)

	def test_bridge_signin_glob_read_retries(self):
		path, value = self.status_file()
		api = bridge.Bridge(dependencies={"accounts_admin": SimpleNamespace(get_signin_status=Mock(return_value=value))})
		self.addCleanup(api._close)
		with self.first_open_fails(path):
			self.assertEqual(api._signin_active(), {"account_id": "account-1"})

	def test_window_observer_signin_glob_read_retries(self):
		from unittest.mock import MagicMock
		path, value = self.status_file()
		admin = SimpleNamespace(get_signin_status=Mock(return_value=value))
		api = bridge.Bridge(dependencies={"accounts_admin": admin,
			"settings": SimpleNamespace(load=lambda: {}, user_data_dir=lambda: self.home / "profiles"),
			"prefs": SimpleNamespace(load=lambda: {}),
			"runs": SimpleNamespace(active_run_id=Mock(return_value=None), overview=Mock(return_value={"accounts": [], "active_run": None})),
			"run_lock": SimpleNamespace(status=Mock(return_value=None))})
		self.addCleanup(api._close)
		with patch("rfapp.tray.Tray"), patch.object(window, "_theme", return_value="light"), patch.object(window.ctypes, "WinDLL", side_effect=AssertionError("No native API in reader test"), create=True):
			runtime = window._Runtime(MagicMock(hidden=True), api, Mock())
			runtime._tail = Mock()
			runtime._settings_mtime = runtime._stamp(self.env)
			runtime._change_waiter = Mock()
			runtime.push = Mock()
			# One observer pass: only read_file may recover the first open, not a later loop tick.
			runtime._change_waiter.wait.side_effect = lambda timeout: runtime._stop.set() or False
			with self.first_open_fails(path) as attempts, patch.object(window._log, "exception") as error, patch.object(runtime._stop, "wait", side_effect=lambda timeout: runtime._stop.set() or True) as backoff:
				runtime._observe()
				error.assert_not_called()
				backoff.assert_not_called()
			self.assertEqual(len(attempts), 2)
			self.assertEqual(runtime._signins, {"account-1": value})
			admin.get_signin_status.assert_called_once_with("account-1")
			runtime.push.assert_any_call("signin.status", value)
			runtime._change_waiter.wait.assert_called_once_with(.5)
			runtime._change_waiter.close.assert_called_once()

	@unittest.skipUnless(sys.platform == "win32", "Windows delete-pending file race")
	def test_windows_env_reads_survive_repeated_replacement(self):
		start = threading.Event()
		stop = threading.Event()
		first_pending = threading.Event()
		reader_observed_pending = threading.Event()
		failures = []
		reads, writes = [], []
		gap_reads = []
		real_open = builtins.open
		reader_attempts = 0
		injected = []
		read_observation = {}
		actual_read = settings.read_file
		actual_replace = os.replace
		def replace(source, target):
			if not first_pending.is_set():
				# Expose one real in-flight replacement to the reader. This
				# prevents pacing from turning the test into disjoint I/O.
				first_pending.set()
				if not reader_observed_pending.wait(1):
					raise AssertionError("Reader did not observe the pending replacement")
			actual_replace(source, target)
		def open_file(path, *args, **kwargs):
			nonlocal reader_attempts
			if threading.current_thread().name == "f61-env-reader" and Path(path) == self.env:
				reader_attempts += 1
				if reader_attempts == 1:
					injected.append("PermissionError")
					raise PermissionError("deterministic delete-pending branch")
				if reader_attempts == 2:
					injected.append("FileNotFoundError")
					raise FileNotFoundError("deterministic replacement gap")
			try:
				return real_open(path, *args, **kwargs)
			except FileNotFoundError:
				if threading.current_thread().name == "f61-env-reader" and Path(path) == self.env:
					read_observation["met_gap"] = True
				raise
		def observed_read(path, **kwargs):
			import file_replace
			read_observation.clear()
			read_observation["expected_path"] = path == self.env
			read_observation["met_gap"] = file_replace._replacement_pending(path)
			if read_observation["met_gap"]:
				reader_observed_pending.set()
			try:
				text = actual_read(path, **kwargs)
				read_observation.update(result="text", length=len(text))
				return text
			except OSError as exc:
				read_observation.update(result=type(exc).__name__, errno=exc.errno)
				if isinstance(exc, FileNotFoundError):
					read_observation["replacement_pending"] = file_replace._replacement_pending(path)
					read_observation["source_names"] = [entry.name for entry in self.home.glob(".rfr-*.tmp")]
				raise
		def writer():
			start.wait()
			try:
				while not stop.is_set():
					pending = self.home / ".env.pending"
					pending.write_text("F61_VALUE=" + str(len(writes)) + "\n", encoding="utf-8")
					replace_file(pending, self.env, replace=replace)
					writes.append(True)
					# F57 promises bounded retries, not progress under a writer
					# monopolizing the target for its entire retry deadline.
					stop.wait(0.005)
			except BaseException as exc:
				failures.append({"worker": "writer", "type": type(exc).__name__, "errno": getattr(exc, "errno", None), "winerror": getattr(exc, "winerror", None), "traceback": traceback.format_exc()})
		def reader():
			start.wait()
			try:
				if not first_pending.wait(1):
					raise AssertionError("Writer did not publish a pending replacement")
				while not stop.is_set():
					values = settings._env()
					self.assertTrue("F61_VALUE" in values or "QUERY_SOURCE" in values, dict(read_observation))
					reads.append(True)
					if read_observation["met_gap"]:
						gap_reads.append(True)
					stop.wait(0.002)
			except BaseException as exc:
				failures.append({"worker": "reader", "type": type(exc).__name__, "errno": getattr(exc, "errno", None), "winerror": getattr(exc, "winerror", None), "observation": dict(read_observation), "traceback": traceback.format_exc()})
		threads = [threading.Thread(target=writer, name="f61-env-writer"), threading.Thread(target=reader, name="f61-env-reader")]
		with patch("file_replace.builtins.open", side_effect=open_file), patch.object(settings, "read_file", side_effect=observed_read):
			for thread in threads:
				thread.start()
			timeout = 30
			started = time.monotonic()
			try:
				start.set()
				# Require repeated coverage, not throughput in a fixed window.
				# Allow 1.5 s per required write under concurrent suite load.
				deadline = started + timeout
				while len(writes) < 20 or len(reads) < 20 or not gap_reads:
					remaining = deadline - time.monotonic()
					if failures or remaining <= 0:
						break
					stop.wait(min(0.01, remaining))
			finally:
				stop.set()
				for thread in threads:
					thread.join(5)
				alive = [thread for thread in threads if thread.is_alive()]
				for thread in alive:
					frame = sys._current_frames().get(thread.ident)
					failures.append({"worker": thread.name, "type": "WorkerDidNotStop", "traceback": "".join(traceback.format_stack(frame)) if frame else None})
					# Keep the failure, but let an already-entered OS call return
					# before TemporaryDirectory cleanup touches its owned files.
					thread.join(15)
		elapsed = time.monotonic() - started
		progress = f"reads={len(reads)}, writes={len(writes)}, gap_reads={len(gap_reads)}, elapsed={elapsed:.3f}s, deadline={timeout:g}s"
		self.assertFalse(alive, failures)
		self.assertEqual(failures, [])
		self.assertEqual(injected, ["PermissionError", "FileNotFoundError"])
		self.assertGreater(len(reads), 1, progress)
		self.assertGreater(len(writes), 1, progress)
		self.assertGreater(len(gap_reads), 0, progress)
		self.assertGreaterEqual(len(reads), 20, progress)
		self.assertGreaterEqual(len(writes), 20, progress)
		# The repeat harness can collect coverage without changing this test
		# or putting measurements in the repository or an app data folder.
		if metrics := os.environ.get("RF_READER_TEST_METRICS"):
			with open(metrics, "a", encoding="utf-8") as stream:
				stream.write(json.dumps({"reads": len(reads), "writes": len(writes), "gap_reads": len(gap_reads)}) + "\n")
