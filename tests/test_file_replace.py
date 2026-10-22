import os
import hashlib
from pathlib import Path
import sys
import tempfile
import threading
import time
import subprocess
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from file_replace import read_file, replace_file
import file_replace


class FileReplaceTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.source = Path(self.temp.name) / "pending"
		self.target = Path(self.temp.name) / "stored"
		self.source.write_text("new", encoding="utf-8")
		self.target.write_text("old", encoding="utf-8")
		self.now = 0.0
		self.sleeps = []

	def sleep(self, seconds):
		self.sleeps.append(seconds)
		self.now += seconds

	def test_staged_name_has_at_most_seventy_two_characters_for_largest_pid(self):
		current_filetime = time.time_ns() // 100 + 116444736000000000
		for stamp in (current_filetime, 0xffffffffffffffff):
			with self.subTest(stamp=stamp):
				name = file_replace._source_name(self.target, 0xffffffff, stamp, "a" * 16)
				self.assertEqual(len(name), 68)
				self.assertLessEqual(len(name), 72)
				self.assertRegex(name, r"^\.rfr-[0-9a-f]{16}-ffffffff-[0-9a-f]{16}-[0-9a-f]{16}\.tmp$")
				self.assertEqual(file_replace._source_owner(self.target, name), (0xffffffff, stamp))

	def test_only_the_new_fixed_width_name_format_is_recognized(self):
		stamp = time.time_ns() // 100 + 116444736000000000
		prefix = file_replace._source_prefix(self.target)
		old_hash = hashlib.sha256(os.path.normcase(str(self.target.absolute())).encode("utf-8")).hexdigest()
		old = f".rf-replace-{old_hash}-{os.getpid()}-{stamp}-{'a' * 32}.tmp"
		for name in (old, prefix + "1-1-" + "a" * 16 + ".tmp", prefix + "0" * 8 + "-" + "0" * 16 + "-" + "a" * 32 + ".tmp", prefix + "0" * 8 + "-" + "g" * 16 + "-" + "a" * 16 + ".tmp"):
			with self.subTest(name=name):
				self.assertIsNone(file_replace._source_owner(self.target, name))
		self.assertIsNone(file_replace._source_owner(self.target.with_name("other"), file_replace._source_name(self.target, 1, stamp, "a" * 16)))

	@unittest.skipUnless(os.name == "nt", "Windows MAX_PATH with a long custom data folder")
	def test_replace_and_read_in_a_folder_with_absolute_length_one_hundred_eighty(self):
		root = Path(self.temp.name).absolute()
		folder = root / ("x" * (180 - len(str(root)) - 1))
		self.assertEqual(len(str(folder)), 180)
		folder.mkdir()
		source, target = folder / "pending", folder / "stored"
		source.write_text("new", encoding="utf-8")
		target.write_text("old", encoding="utf-8")
		seen = []
		def replace(pending, target):
			seen.append(Path(pending))
			self.assertLess(len(str(pending)), 260)
			self.assertTrue(file_replace._replacement_pending(target))
			os.replace(pending, target)
		replace_file(source, target, replace=replace)
		self.assertEqual(read_file(target), "new")
		self.assertEqual(len(seen), 1)
		self.assertEqual(len(seen[0].name), 68)
		self.assertFalse(source.exists())
		self.assertFalse(seen[0].exists())
		target.unlink()
		folder.rmdir()
		self.assertFalse(folder.exists())

	def test_retry_succeeds_after_two_permission_errors(self):
		calls = []
		def replace(source, target):
			calls.append((source, target))
			if len(calls) < 3:
				raise PermissionError("reader still open")
			os.replace(source, target)
		self.assertIsNone(replace_file(self.source, self.target, replace=replace, clock=lambda: self.now, sleep=self.sleep))
		self.assertEqual(len(calls), 3)
		self.assertEqual(self.sleeps, [0.05, 0.05])
		self.assertEqual(self.target.read_text(), "new")
		self.assertFalse(self.source.exists())

	def test_give_up_after_two_seconds_removes_temp_and_raises_last_error(self):
		errors = []
		def replace(source, target):
			error = PermissionError(f"reader attempt {len(errors) + 1}")
			errors.append(error)
			raise error
		with self.assertRaises(PermissionError) as failure:
			replace_file(self.source, self.target, replace=replace, clock=lambda: self.now, sleep=self.sleep)
		self.assertIs(failure.exception, errors[-1])
		self.assertEqual(self.now, 2)
		self.assertEqual(len(errors), 41)
		self.assertTrue(all(0 < value <= 0.05 for value in self.sleeps))
		self.assertAlmostEqual(sum(self.sleeps), 2)
		self.assertFalse(self.source.exists())
		self.assertEqual(self.target.read_text(), "old")

	def test_non_permission_error_is_not_retried(self):
		error = OSError("not a sharing failure")
		replace = Mock(side_effect=error)
		with self.assertRaises(OSError) as failure:
			replace_file(self.source, self.target, replace=replace, clock=lambda: self.now, sleep=self.sleep)
		self.assertIs(failure.exception, error)
		replace.assert_called_once()
		self.assertEqual(self.sleeps, [])
		self.assertFalse(self.source.exists())
		self.assertEqual(self.target.read_text(), "old")

	def test_success_has_no_sleep_and_default_replace_works(self):
		replace_file(self.source, self.target, clock=lambda: self.now, sleep=self.sleep)
		self.assertEqual(self.target.read_text(), "new")
		self.assertFalse(self.source.exists())
		self.assertEqual(self.sleeps, [])

	def test_read_retries_permission_error_and_returns_text(self):
		attempts = []
		def open_file(*args, **kwargs):
			attempts.append((args, kwargs))
			if len(attempts) < 3:
				raise PermissionError("delete-pending")
			return open(*args, **kwargs)
		self.assertEqual(read_file(self.target, open_file=open_file, clock=lambda: self.now, sleep=self.sleep), "old")
		self.assertEqual(len(attempts), 3)
		self.assertEqual(self.sleeps, [0.05, 0.05])
		self.assertEqual(attempts[-1][1], {"encoding": "utf-8", "errors": "strict", "newline": None})

	def test_read_gives_up_after_two_seconds_and_raises_last_error(self):
		errors = []
		def open_file(*args, **kwargs):
			error = PermissionError(f"delete-pending {len(errors)}")
			errors.append(error)
			raise error
		with self.assertRaises(PermissionError) as failure:
			read_file(self.target, open_file=open_file, clock=lambda: self.now, sleep=self.sleep)
		self.assertIs(failure.exception, errors[-1])
		self.assertEqual(self.now, 2)
		self.assertEqual(len(errors), 41)
		self.assertTrue(all(0 < value <= 0.05 for value in self.sleeps))
		self.assertEqual(self.target.read_text(), "old")

	def test_read_other_errors_do_not_retry_or_change_files(self):
		for error in (IsADirectoryError("directory"), OSError("other")):
			with self.subTest(error=type(error).__name__):
				opener = Mock(side_effect=error)
				with self.assertRaises(type(error)) as failure:
					read_file(self.target, open_file=opener, clock=lambda: self.now, sleep=self.sleep)
				self.assertIs(failure.exception, error)
				opener.assert_called_once()
		self.assertEqual(self.sleeps, [])
		self.assertEqual(self.source.read_text(), "new")
		self.assertEqual(self.target.read_text(), "old")

	def test_read_retries_transient_missing_during_atomic_replace(self):
		attempts = []
		def open_file(*args, **kwargs):
			attempts.append(True)
			if len(attempts) <= 3:
				raise FileNotFoundError("replacement gap")
			return open(*args, **kwargs)
		self.assertEqual(read_file(self.target, open_file=open_file, clock=lambda: self.now, sleep=self.sleep), "old")
		self.assertEqual(len(attempts), 4)
		self.assertEqual(self.sleeps, [0.01, 0.01, 0.01])

	def test_read_truly_missing_is_confirmed_for_one_hundred_ms(self):
		missing = Path(self.temp.name) / "never-created"
		errors = []
		def open_file(*args, **kwargs):
			error = FileNotFoundError("truly missing")
			errors.append(error)
			raise error
		with self.assertRaises(FileNotFoundError) as failure:
			read_file(missing, open_file=open_file, clock=lambda: self.now, sleep=self.sleep)
		self.assertIs(failure.exception, errors[-1])
		self.assertEqual(self.now, 0.1)
		self.assertGreater(len(errors), 1)
		self.assertAlmostEqual(sum(self.sleeps), 0.1)
		self.assertTrue(all(0 < value <= 0.01 for value in self.sleeps))
		self.assertFalse(missing.exists())

	def test_read_present_path_with_persistent_missing_error_uses_shared_deadline(self):
		errors = []
		def open_file(*args, **kwargs):
			error = FileNotFoundError("present path transient-open failure")
			errors.append(error)
			raise error
		with self.assertRaises(FileNotFoundError) as failure:
			read_file(self.target, open_file=open_file, clock=lambda: self.now, sleep=self.sleep)
		self.assertIs(failure.exception, errors[-1])
		self.assertEqual(self.now, 2)
		self.assertGreater(len(errors), 1)

	def test_read_retries_even_while_replaced_path_is_temporarily_absent(self):
		self.target.unlink()
		def sleep(seconds):
			self.sleep(seconds)
			if self.now >= 0.03:
				self.target.write_text("new", encoding="utf-8")
		self.assertEqual(read_file(self.target, clock=lambda: self.now, sleep=sleep), "new")
		self.assertAlmostEqual(self.now, 0.03)

	def test_read_permission_missing_transition_keeps_original_two_second_cap(self):
		errors = []
		def open_file(*args, **kwargs):
			error = PermissionError("pending") if self.now < 1.95 else FileNotFoundError("absent")
			errors.append(error)
			raise error
		with self.assertRaises(FileNotFoundError) as failure:
			read_file(self.target, open_file=open_file, clock=lambda: self.now, sleep=self.sleep)
		self.assertIs(failure.exception, errors[-1])
		self.assertEqual(self.now, 2)

	def test_read_empty_content_remains_valid_and_is_not_retried(self):
		self.target.write_text("", encoding="utf-8")
		opener = Mock(wraps=open)
		self.assertEqual(read_file(self.target, open_file=opener, clock=lambda: self.now, sleep=self.sleep), "")
		opener.assert_called_once()
		self.assertEqual(self.sleeps, [])

	@unittest.skipUnless(os.name == "nt", "Windows atomic replacement namespace")
	def test_read_recovers_short_thread_replacement_gap(self):
		gap = threading.Event()
		finish = threading.Event()
		errors = []
		backup = self.target.with_suffix(".old")
		def replace(source, target):
			os.replace(target, backup)
			gap.set()
			self.assertTrue(finish.wait(3))
			os.replace(source, target)
		def writer():
			try:
				replace_file(self.source, self.target, replace=replace)
			except BaseException as exc:
				errors.append(exc)
		def release():
			self.assertTrue(gap.wait(3))
			time.sleep(1.2)
			finish.set()
		threads = [threading.Thread(target=writer), threading.Thread(target=release)]
		for thread in threads:
			thread.start()
		try:
			self.assertTrue(gap.wait(3))
			self.assertEqual(read_file(self.target), "new")
		finally:
			finish.set()
			for thread in threads:
				thread.join(4)
		self.assertEqual(errors, [])
		self.assertTrue(all(not thread.is_alive() for thread in threads))

	@unittest.skipUnless(os.name == "nt", "Windows cross-process atomic replacement namespace")
	def test_read_recovers_short_process_replacement_gap(self):
		ready = self.target.with_suffix(".ready")
		code = "\n".join([
			"import os,sys,time",
			"from pathlib import Path",
			f"sys.path.insert(0,{str(Path(__file__).resolve().parents[1] / 'src')!r})",
			"from file_replace import replace_file",
			"source,target,ready=map(Path,sys.argv[1:])",
			"def replace(source,target):",
			"\tos.replace(target,target.with_suffix('.old'))",
			"\tready.write_text('ready')",
			"\ttime.sleep(1.25)",
			"\tos.replace(source,target)",
			"replace_file(source,target,replace=replace)",
		])
		process = subprocess.Popen([sys.executable, "-B", "-c", code, str(self.source), str(self.target), str(ready)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
		try:
			deadline = time.monotonic() + 5
			while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
				time.sleep(0.01)
			self.assertTrue(ready.exists())
			self.assertEqual(read_file(self.target), "new")
			stdout, stderr = process.communicate(timeout=5)
			self.assertEqual(process.returncode, 0, stderr.decode(errors="replace"))
		finally:
			if process.poll() is None:
				process.kill()
				process.communicate(timeout=5)

	@unittest.skipUnless(os.name == "nt", "Windows writer process exits without coordination state")
	def test_crashed_writer_leaves_no_coordination_to_block_reader_or_writer(self):
		for operation in ("read", "replace"):
			with self.subTest(operation=operation):
				ready = self.target.with_suffix(".abandoned-" + operation)
				self.source.write_text("new", encoding="utf-8")
				code = "\n".join([
					"import os,sys,time",
					"from pathlib import Path",
					f"sys.path.insert(0,{str(Path(__file__).resolve().parents[1] / 'src')!r})",
					"from file_replace import replace_file",
					"target,ready=map(Path,sys.argv[1:])",
					"pending=target.with_suffix('.crashed')",
					"pending.write_text('from writer')",
					"def crash(source,target):",
					"\tready.write_text(str(source))",
					"\tos._exit(0)",
					"replace_file(pending,target,replace=crash)",
				])
				process = subprocess.Popen([sys.executable, "-B", "-c", code, str(self.target), str(ready)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
				try:
					deadline = time.monotonic() + 5
					while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
						time.sleep(0.005)
					self.assertTrue(ready.exists())
					process.communicate(timeout=5)
					staged = Path(ready.read_text())
					self.assertTrue(staged.exists())
					self.assertFalse(file_replace._replacement_pending(self.target))
					started = time.monotonic()
					if operation == "read":
						self.assertIn(read_file(self.target), ("old", "new"))
					else:
						replace_file(self.source, self.target)
						self.assertEqual(self.target.read_text(), "new")
					self.assertLess(time.monotonic() - started, 2)
					stdout, stderr = process.communicate(timeout=5)
					self.assertEqual(process.returncode, 0, stderr.decode(errors="replace"))
				finally:
					if process.poll() is None:
						process.kill()
						process.communicate(timeout=5)

	@unittest.skipUnless(os.name == "nt", "Windows actual replacement source is visible")
	def test_replacement_source_is_target_bound_and_live_owner_bound(self):
		seen = []
		def replace(source, target):
			seen.append(Path(source))
			self.assertFalse(self.source.exists())
			self.assertEqual(Path(source).read_text(), "new")
			self.assertTrue(file_replace._replacement_pending(self.target))
			self.assertFalse(file_replace._replacement_pending(self.target.with_name("other")))
			os.replace(source, target)
		replace_file(self.source, self.target, replace=replace)
		self.assertEqual(len(seen), 1)
		self.assertFalse(seen[0].exists())
		self.assertFalse(file_replace._replacement_pending(self.target))
		self.assertEqual(self.target.read_text(), "new")

	@unittest.skipUnless(os.name == "nt", "Windows process identity protects staging recognition")
	def test_wrong_process_birth_and_non_data_entries_do_not_extend_absence(self):
		prefix = file_replace._source_prefix(self.target)
		stamp = file_replace._process_stamp(os.getpid())
		wrong = self.target.parent / file_replace._source_name(self.target, os.getpid(), stamp + 1, "a" * 16)
		wrong.write_text("stale", encoding="utf-8")
		(self.target.parent / f"{prefix}bad-owner.tmp").write_text("bad", encoding="utf-8")
		self.assertFalse(file_replace._replacement_pending(self.target))

	@unittest.skipUnless(os.name == "nt", "Windows shared replacement deadline")
	def test_live_pending_source_covers_long_gap_but_never_exceeds_two_seconds(self):
		self.target.unlink()
		with patch.object(file_replace, "_replacement_pending", return_value=True):
			with self.assertRaises(FileNotFoundError):
				read_file(self.target, clock=lambda: self.now, sleep=self.sleep)
		self.assertEqual(self.now, 2)

	def test_observed_replacement_completion_does_not_become_false_absence(self):
		self.target.unlink()
		attempts = []
		def opener(*args, **kwargs):
			attempts.append(True)
			if self.now < 0.25:
				raise FileNotFoundError("long replacement gap")
			self.target.write_text("completed", encoding="utf-8")
			return open(*args, **kwargs)
		with patch.object(file_replace, "_replacement_pending", side_effect=[True, False]):
			self.assertEqual(read_file(self.target, open_file=opener, clock=lambda: self.now, sleep=self.sleep), "completed")
		self.assertGreaterEqual(self.now, 0.25)
		self.assertLess(self.now, 2)

	def test_target_published_between_missing_checks_is_opened_before_absence_return(self):
		self.target.unlink()
		attempts = []
		def opener(*args, **kwargs):
			attempts.append(True)
			return open(*args, **kwargs)
		def pending(path):
			if self.now >= 0.1:
				self.target.write_text("published", encoding="utf-8")
			return False
		with patch.object(file_replace, "_replacement_pending", side_effect=pending), patch.object(file_replace.os.path, "lexists", return_value=False):
			self.assertEqual(read_file(self.target, open_file=opener, clock=lambda: self.now, sleep=self.sleep), "published")
		self.assertGreater(len(attempts), 1)
		self.assertLess(self.now, 2)

	@unittest.skipUnless(os.name == "nt", "Windows staging rename shares F57 retry")
	def test_staging_permission_failure_retries_and_cleans_all_temporary_paths(self):
		original = os.rename
		attempts = []
		def rename(source, target):
			attempts.append(True)
			if len(attempts) == 1:
				raise PermissionError("scanner holds temporary source")
			return original(source, target)
		with patch.object(file_replace.os, "rename", side_effect=rename):
			replace_file(self.source, self.target, clock=lambda: self.now, sleep=self.sleep)
		self.assertEqual(attempts, [True, True])
		self.assertEqual(self.sleeps, [0.05])
		self.assertEqual(self.target.read_text(), "new")
		self.assertEqual(list(self.target.parent.iterdir()), [self.target])

	@unittest.skipUnless(os.name == "nt", "Windows dead writer cannot strand reader")
	def test_missing_target_after_writer_death_still_costs_only_confirmation_window(self):
		self.target.unlink()
		staged = self.target.parent / file_replace._source_name(self.target, 0, 0, "a" * 16)
		staged.write_text("orphaned bytes", encoding="utf-8")
		with self.assertRaises(FileNotFoundError):
			read_file(self.target, clock=lambda: self.now, sleep=self.sleep)
		self.assertEqual(self.now, 0.1)
		self.assertTrue(staged.exists())

	def test_successful_read_has_no_platform_coordination_or_delay(self):
		with patch("ctypes.WinDLL", side_effect=AssertionError("No platform lock"), create=True):
			self.assertEqual(read_file(self.target, clock=lambda: self.now, sleep=self.sleep), "old")
		self.assertEqual(self.sleeps, [])

	def test_default_absence_bound_is_independent_of_a_callers_frozen_clock(self):
		from time import monotonic
		started = monotonic()
		with patch("file_replace.time.monotonic", return_value=0), patch("file_replace.time.sleep", side_effect=AssertionError("Caller sleep is not the I/O clock")):
			with self.assertRaises(FileNotFoundError):
				read_file(Path(self.temp.name) / "absent")
		self.assertGreaterEqual(monotonic() - started, 0.1)
		self.assertLess(monotonic() - started, 0.5)

	def test_read_preserves_requested_newlines_encoding_and_decode_errors(self):
		self.target.write_bytes(b"\xef\xbb\xbfA=one\r\nB=two\r\n")
		self.assertEqual(read_file(self.target, newline=""), "\ufeffA=one\r\nB=two\r\n")
		self.assertEqual(read_file(self.target, encoding="utf-8-sig"), "A=one\nB=two\n")
		self.target.write_bytes(b"\xff")
		with self.assertRaises(UnicodeDecodeError):
			read_file(self.target)
		self.assertEqual(read_file(self.target, errors="replace"), "\ufffd")


if __name__ == "__main__":
	unittest.main()
