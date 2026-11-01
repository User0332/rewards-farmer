import os
from pathlib import Path
import sys
import tempfile
import subprocess
import json
import time
import types
from dataclasses import asdict
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import run_lock
from file_replace import replace_file


class RunLockTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.directory = self.temp.name

	def test_busy_release_and_idempotent_context(self):
		with run_lock.acquire("farmer", "test", directory=self.directory) as lock:
			self.assertIsNotNone(lock)
			self.assertEqual(run_lock.status(self.directory).run_id, "test")
			self.assertIsNone(run_lock.acquire("signin", directory=self.directory))
		lock.release()
		self.assertIsNone(run_lock.status(self.directory))
		self.assertFalse(Path(self.directory, run_lock.INFO_FILE_NAME).exists())
		next_lock = run_lock.acquire("signin", account="one", directory=self.directory)
		self.assertEqual(next_lock.info.account, "one")
		next_lock.release()

	def test_status_never_opens_or_locks_the_lock_file(self):
		lock = run_lock.acquire("farmer", directory=self.directory)
		self.addCleanup(lock.release)
		with mock.patch.object(run_lock.os, "open", side_effect=AssertionError("probe took lock")):
			self.assertEqual(run_lock.status(self.directory), lock.info)

	@unittest.skipUnless(sys.platform == "win32", "Windows process creation time")
	def test_pid_reuse_is_not_a_live_holder(self):
		lock = run_lock.acquire("farmer", directory=self.directory)
		self.addCleanup(lock.release)
		with mock.patch.object(run_lock, "_process_created", return_value=lock.info.pid_created + 1):
			self.assertIsNone(run_lock.status(self.directory))

	def test_missing_dead_or_malformed_info_is_not_a_holder(self):
		self.assertIsNone(run_lock.status(self.directory))
		path = Path(self.directory, run_lock.INFO_FILE_NAME)
		path.write_text("broken", encoding="utf-8")
		self.assertIsNone(run_lock.status(self.directory))

	def test_holder_process_death_frees_os_lock(self):
		self.addCleanup(self._wait_for_lock_file_release)
		code = "import run_lock,sys,time; lock=run_lock.acquire('farmer','child',directory=sys.argv[1]); print('locked',flush=True); time.sleep(60)"
		env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, (os.environ.get("PYTHONPATH"), str(Path(run_lock.__file__).parent))))}
		proc = subprocess.Popen([sys.executable, "-c", code, self.directory], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
		try:
			self.assertEqual(proc.stdout.readline().strip(), "locked")
			self.assertIsNone(run_lock.acquire("farmer", directory=self.directory))
			proc.kill()
			proc.wait(timeout=10)
			self.assertIsNone(run_lock.status(self.directory))
			replacement = run_lock.acquire("farmer", "replacement", directory=self.directory)
			self.assertIsNotNone(replacement)
			with replacement as lock:
				self.assertEqual(run_lock.status(self.directory).run_id, "replacement")
		finally:
			if proc.poll() is None:
				proc.kill()
				proc.wait(timeout=10)
			proc.stdout.close()
			proc.stderr.close()

	def _wait_for_lock_file_release(self):
		deadline = time.monotonic() + 5
		while True:
			try:
				Path(self.directory, run_lock.LOCK_FILE_NAME).unlink(missing_ok=True)
				return
			except PermissionError:
				remaining = deadline - time.monotonic()
				if remaining <= 0:
					raise
				time.sleep(min(0.05, remaining))

	def test_directory_override(self):
		with mock.patch.dict(os.environ, {"USER_DATA_DIR": self.directory}):
			with run_lock.acquire("farmer") as lock:
				self.assertEqual(lock.info.pid, os.getpid())
				self.assertEqual(run_lock.lock_dir(), self.directory)

	def test_metadata_replace_failure_releases_the_os_lock(self):
		with mock.patch.object(run_lock.os, "replace", side_effect=OSError("cannot replace")), self.assertRaises(OSError):
			run_lock.acquire("farmer", directory=self.directory)
		with run_lock.acquire("farmer", directory=self.directory) as lock:
			self.assertIsNotNone(lock)

	def test_metadata_uses_shared_helper_after_one_permission_failure(self):
		real_replace = os.replace
		attempts = []
		def replace(source, target):
			attempts.append((source, target))
			if len(attempts) == 1:
				raise PermissionError("status reader open")
			real_replace(source, target)
		with mock.patch.object(run_lock, "replace_file", wraps=replace_file) as helper, mock.patch("file_replace.os.replace", side_effect=replace), mock.patch("file_replace.time.sleep"):
			with run_lock.acquire("signin", account="account-1", directory=self.directory) as lock:
				self.assertIsNotNone(lock)
				self.assertEqual(run_lock.status(self.directory), lock.info)
				helper.assert_called_once()
		self.assertEqual(len(attempts), 2)
		self.assertFalse(Path(self.directory, run_lock.INFO_FILE_NAME).exists())

	def test_metadata_status_read_retries_once_instead_of_hiding_holder(self):
		with run_lock.acquire("signin", account="account-1", directory=self.directory) as lock:
			real_open = open
			attempts = []
			def open_file(*args, **kwargs):
				attempts.append(True)
				if len(attempts) == 1:
					raise PermissionError("delete-pending")
				return real_open(*args, **kwargs)
			with mock.patch("file_replace.builtins.open", side_effect=open_file):
				self.assertEqual(run_lock.status(self.directory), lock.info)
			self.assertEqual(len(attempts), 2)


class DeadHolderRetryTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.directory = self.temp.name
		self.clock = 0.0
		self.sleeps = []
		self.time = types.SimpleNamespace(monotonic=lambda: self.clock, sleep=self._sleep)

	def _sleep(self, seconds):
		self.sleeps.append(seconds)
		self.clock += seconds

	def _info(self, live):
		pid = os.getpid() if live else 2_147_483_647
		created = run_lock._process_created(pid)
		self.assertEqual(created is not None, live)
		info = run_lock.LockInfo("farmer", pid, created, "holder", None, "2026-10-15T16:40:00.000Z")
		Path(self.directory, run_lock.INFO_FILE_NAME).write_text(json.dumps(asdict(info)), encoding="utf-8")
		return info

	def _primitive(self):
		if os.name == "nt":
			return mock.patch("msvcrt.locking")
		return mock.patch("fcntl.flock")

	def _attempts(self, primitive):
		if os.name == "nt":
			import msvcrt
			return [call for call in primitive.call_args_list if call.args[1:] == (msvcrt.LK_NBLCK, 1)]
		import fcntl
		return [call for call in primitive.call_args_list if call.args[1] == fcntl.LOCK_EX | fcntl.LOCK_NB]

	def test_dead_holder_retries_twice_then_acquires(self):
		self._info(live=False)
		with self._primitive() as primitive, mock.patch.object(run_lock, "time", self.time):
			primitive.side_effect = [OSError("release pending"), OSError("release pending"), None, None]
			lock = run_lock.acquire("farmer", "replacement", directory=self.directory)
			self.assertIsNotNone(lock)
			self.addCleanup(lock.release)
			self.assertEqual(run_lock.status(self.directory), lock.info)
			self.assertEqual(len(self._attempts(primitive)), 3)
			self.assertEqual(self.sleeps, [0.05, 0.05])
			lock.release()

	def test_live_holder_returns_after_one_attempt_without_sleep(self):
		info = self._info(live=True)
		with self._primitive() as primitive, mock.patch.object(run_lock, "time", self.time):
			primitive.side_effect = OSError("busy")
			self.assertIsNone(run_lock.acquire("signin", directory=self.directory))
			self.assertEqual(run_lock.status(self.directory), info)
			self.assertEqual(len(self._attempts(primitive)), 1)
			self.assertEqual(self.sleeps, [])
			self.assertEqual(self.clock, 0)

	def test_dead_holder_never_frees_returns_at_five_seconds(self):
		self._info(live=False)
		with self._primitive() as primitive, mock.patch.object(run_lock, "time", self.time):
			primitive.side_effect = OSError("release pending")
			self.assertIsNone(run_lock.acquire("farmer", directory=self.directory))
			self.assertGreater(len(self._attempts(primitive)), 1)
		self.assertEqual(self.clock, 5)
		self.assertTrue(all(0 < seconds <= 0.05 for seconds in self.sleeps))
		self.assertAlmostEqual(sum(self.sleeps), 5)
		self.assertIsNone(run_lock.status(self.directory))

	def test_interrupted_retry_closes_its_file_descriptor(self):
		self._info(live=False)
		with self._primitive() as primitive, mock.patch.object(run_lock, "time", self.time), mock.patch.object(run_lock.os, "close", wraps=os.close) as close:
			primitive.side_effect = OSError("release pending")
			self.time.sleep = mock.Mock(side_effect=KeyboardInterrupt)
			with self.assertRaises(KeyboardInterrupt):
				run_lock.acquire("farmer", directory=self.directory)
			close.assert_called_once()

	def test_killed_holder_cleanup_waits_for_a_lingering_file_handle(self):
		case = RunLockTests()
		case.directory = self.directory
		with mock.patch(__name__ + ".time", self.time), mock.patch.object(Path, "unlink", side_effect=[PermissionError("handle pending"), None]) as unlink:
			case._wait_for_lock_file_release()
		self.assertEqual(unlink.call_count, 2)
		self.assertEqual(self.sleeps, [0.05])

	def test_killed_holder_cleanup_raises_if_file_stays_busy_for_five_seconds(self):
		case = RunLockTests()
		case.directory = self.directory
		with mock.patch(__name__ + ".time", self.time), mock.patch.object(Path, "unlink", side_effect=PermissionError("handle pending")), self.assertRaises(PermissionError):
			case._wait_for_lock_file_release()
		self.assertEqual(self.clock, 5)
