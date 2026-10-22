import _bootstrap  # noqa: F401

import contextlib
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

import rfapp
from rfapp import paths, runs, settings as settings_module, supervisor
from rfapp.winjob import Job
import run_lock
import run_record

_REAL_POPEN = subprocess.Popen


class _JobTree:
	"""Retain identities before termination; a launcher pid is not its tree."""
	def __init__(self):
		self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
		for name, args, result in (
			("QueryInformationJobObject", [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
			("OpenProcess", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
			("QueryFullProcessImageNameW", [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
			("GetProcessTimes", [wintypes.HANDLE] + [ctypes.c_void_p] * 4, wintypes.BOOL),
			("WaitForSingleObject", [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
			("TerminateProcess", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
			("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
		):
			function = getattr(self.kernel, name)
			function.argtypes, function.restype = args, result
		self.members = {}
		self.waited = set()
		self.terminated = False

	def capture(self, job):
		class ProcessList(ctypes.Structure):
			_fields_ = [("assigned", wintypes.DWORD), ("count", wintypes.DWORD), ("pids", ctypes.c_size_t * 64)]
		info = ProcessList()
		if not self.kernel.QueryInformationJobObject(job._handle, 3, ctypes.byref(info), ctypes.sizeof(info), None):
			raise ctypes.WinError(ctypes.get_last_error())
		if info.count != info.assigned or not 0 < info.count <= 64 or os.getpid() in info.pids[:info.count]:
			raise AssertionError("Incomplete or non-isolated fake farmer job: " + str(list(info.pids[:info.count])))
		for pid in info.pids[:info.count]:
			if pid in self.members:
				continue
			handle = self.kernel.OpenProcess(0x100000 | 0x1000 | 0x1, False, pid)
			if not handle:
				raise ctypes.WinError(ctypes.get_last_error())
			self.members[pid] = {"handle": handle}
			image, length = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
			if not self.kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(length)):
				raise ctypes.WinError(ctypes.get_last_error())
			created, exited, kernel, user = (ctypes.c_uint64() for _ in range(4))
			if not self.kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
				raise ctypes.WinError(ctypes.get_last_error())
			self.members[pid].update(image=image.value, created=created.value)

	def alive(self):
		alive = []
		for pid, member in self.members.items():
			state = self.kernel.WaitForSingleObject(member["handle"], 0)
			if state == 258:
				alive.append(pid)
			elif state != 0:
				raise ctypes.WinError(ctypes.get_last_error())
		return alive

	def describe(self, pids):
		return "; ".join(f"pid={pid} image={self.members[pid].get('image', 'unknown')} created={self.members[pid].get('created', 'unknown')}" for pid in pids)

	def wait(self, timeout=10):
		began = time.monotonic()
		self.waited.clear()
		for pid, member in self.members.items():
			milliseconds = max(0, int((timeout - (time.monotonic() - began)) * 1000))
			state = self.kernel.WaitForSingleObject(member["handle"], milliseconds)
			if state == 0:
				self.waited.add(pid)
			elif state != 258:
				raise ctypes.WinError(ctypes.get_last_error())
		alive = self.alive()
		if alive:
			raise AssertionError(f"Fake farmer tree still alive after {time.monotonic() - began:.3f}s (bound={timeout}s): {self.describe(alive)}")

	def close(self):
		try:
			try:
				self.wait()
			except BaseException:
				# Failure cleanup uses retained owned handles, never a pid tree walk.
				for pid in self.alive():
					if not self.kernel.TerminateProcess(self.members[pid]["handle"], 1):
						raise ctypes.WinError(ctypes.get_last_error())
				self.wait()
				raise
		finally:
			for member in self.members.values():
				self.kernel.CloseHandle(member["handle"])
			self.members.clear()


class SupervisorTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.home = Path(self.temp.name)
		self.folder = self.home / "runs"
		self.settings = {"schedule.mode": "automatic", "schedule.min_points": 15}
		self.active_accounts = ["@root", "one"]
		self.notify = mock.Mock()
		self.stack = contextlib.ExitStack()
		self.addCleanup(self.stack.close)
		self.stack.enter_context(mock.patch.dict(os.environ, {"REWARDS_FARMER_DATA_HOME": str(self.home), "USER_DATA_DIR": str(self.home / "profile")}, clear=False))
		modules = {
			"rfapp.settings": types.SimpleNamespace(load=lambda: self.settings, user_data_dir=lambda: self.home / "profile", active_accounts=lambda: self.active_accounts),
			"rfapp.notify": types.SimpleNamespace(after_run=self.notify),
			"rfapp.i18n": types.SimpleNamespace(current=lambda: types.SimpleNamespace(t=lambda key, **params: "Account " + str(params.get("number", 1)))),
		}
		self.modules = modules
		self.stack.enter_context(mock.patch.dict(sys.modules, modules))
		for name, module in modules.items():
			self.stack.enter_context(mock.patch.object(rfapp, name.split(".")[-1], module, create=True))
		# A missing injection fails before Popen rather than reaching a real account.
		self.stack.enter_context(mock.patch.object(paths, "farmer_command", side_effect=AssertionError("Real farmer forbidden in supervisor tests")))
		self.spawn = self.stack.enter_context(mock.patch.object(supervisor.subprocess, "Popen", side_effect=AssertionError("Unpatched process spawn forbidden in supervisor tests")))
		self.stack.enter_context(mock.patch.object(run_record.constants, "RUNS_DIR", str(self.folder)))
		self.state_reader = self.stack.enter_context(mock.patch.object(supervisor.user_state, "notification_state", return_value=5))
		self.execution_state = self.stack.enter_context(mock.patch.object(supervisor, "_set_execution_state"))

	def record(self):
		ids = run_record.list_run_ids(str(self.folder))
		self.assertEqual(len(ids), 1)
		return ids[0], run_record.read_events(str(self.folder / (ids[0] + ".jsonl")))

	def inject_command(self, scenario):
		fake = Path(__file__).parent / "fakes" / "fake_farmer.py"
		self.stack.enter_context(mock.patch.object(paths, "farmer_command", lambda args: [sys.executable, str(fake), scenario]))
		self.stack.enter_context(mock.patch.object(supervisor.subprocess, "Popen", _REAL_POPEN))

	@unittest.skipUnless(sys.platform == "win32", "Real Windows job foundation")
	def test_real_fake_farmer_job_record_reducer_foundation(self):
		self.inject_command("finish")
		self.assertEqual(supervisor.supervise("run_now", ["@root"]), 0)
		run_id, events = self.record()
		self.assertEqual(events[0]["trigger"], "app")
		self.assertEqual(events[-1]["result"], "ok")
		self.assertEqual(events[-1]["written_by"], "farmer")
		detail = runs.load_run(run_id, {}, str(self.home / "profile"))
		self.assertFalse(detail["active"])
		self.assertEqual(detail["points_earned"], 60)
		self.assertEqual(detail["accounts"][0]["account_id"], "@root")
		self.assertEqual(detail["accounts"][0]["tasks"][5]["status"], "ok")
		self.notify.assert_called_once_with(run_id, self.settings)
		self.assertIsNone(run_lock.status())
		with run_lock.acquire("farmer") as lock:
			self.assertIsNotNone(lock)

	def test_manual_and_busy_scheduled_invocations_leave_no_record(self):
		self.settings["schedule.mode"] = "manual"
		self.assertEqual(supervisor.supervise("scheduled"), 0)
		self.settings["schedule.mode"] = "automatic"
		with mock.patch.object(run_lock, "status", return_value=mock.Mock()):
			self.assertEqual(supervisor.supervise("scheduled"), 3)
		self.assertFalse(self.folder.exists())

	def test_popen_failure_and_bad_root_list_leave_crashed_record(self):
		with mock.patch.object(paths, "farmer_command", return_value=["fake"]), mock.patch.object(supervisor.subprocess, "Popen", side_effect=OSError("cannot launch")):
			self.assertEqual(supervisor.supervise("check_now"), 5)
		_, events = self.record()
		self.assertEqual([e["type"] for e in events], ["run_start", "run_end"])
		self.assertEqual(events[-1]["result"], "crashed")
		self.assertIn("cannot launch", events[-1]["detail"])
		self.notify.assert_not_called()

	def test_root_mixed_with_names_fails_without_starting_a_process(self):
		with mock.patch.object(supervisor.subprocess, "Popen") as popen:
			self.assertEqual(supervisor.supervise("run_now", ["@root", "one"]), 5)
			popen.assert_not_called()
		_, events = self.record()
		self.assertIn("@root", events[-1]["detail"])
		self.assertIn("one", events[-1]["detail"])

	def test_exact_args_environment_cwd_and_root_encoding(self):
		for kind, mode, expected in (
			("scheduled", "automatic", ["--unattended", "--only-if-open", "--min-points", "15"]),
			("scheduled", "fixed", ["--unattended", "--only-if-open", "--min-points", "1", "--run-if-unreadable"]),
			("run_now", "automatic", ["--unattended", "--read-state"]),
			("check_now", "automatic", ["--unattended", "--check-only"]),
		):
			with self.subTest(kind=kind, mode=mode):
				self.settings["schedule.mode"] = mode
				proc = mock.Mock(pid=123, returncode=0)
				proc.poll.return_value = 0
				with mock.patch.object(supervisor.subprocess, "Popen", return_value=proc) as popen, mock.patch.object(supervisor, "Job") as job, mock.patch.object(paths, "farmer_command", return_value=["fake"]) as command:
					self.assertEqual(supervisor.supervise(kind, ["@root"]), 0)
					command.assert_called_once_with(expected)
					kwargs = popen.call_args.kwargs
					self.assertEqual(kwargs["env"]["REWARDS_ACCOUNTS"], "")
					self.assertEqual(kwargs["env"]["REWARDS_SUPERVISOR_PID"], str(os.getpid()))
					self.assertEqual(kwargs["env"]["PYTHONUTF8"], "1")
					self.assertEqual(kwargs["cwd"], self.home)
					self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
					job.return_value.assign.assert_called_once_with(123)
					job.return_value.close.assert_called_once()

	@unittest.skipUnless(sys.platform == "win32", "Real Windows job")
	def test_crash_is_closed_by_supervisor_and_releases_lock(self):
		self.inject_command("crash")
		self.assertEqual(supervisor.supervise("check_now"), 9)
		run_id, events = self.record()
		self.assertEqual(events[-1]["result"], "crashed")
		self.assertEqual(events[-1]["written_by"], "supervisor")
		self.assertEqual(runs.load_run(run_id, {}, str(self.home / "profile"))["result"], "crashed")
		self.assertIsNone(run_lock.status())

	def request_stop(self, event_type="state"):
		done = threading.Event()
		def request():
			while not done.wait(0.02):
				ids = run_record.list_run_ids(str(self.folder))
				if ids and any(e.get("type") == event_type for e in run_record.read_events(str(self.folder / (ids[0] + ".jsonl")))):
					(self.folder / (ids[0] + ".stop")).write_text('{"by":"app"}', encoding="utf-8")
					return
		thread = threading.Thread(target=request, daemon=True)
		thread.start()
		self.addCleanup(thread.join, 2)
		self.addCleanup(done.set)

	@unittest.skipUnless(sys.platform == "win32", "Real Windows job")
	def test_cooperative_stop_crosses_job_record_and_reducer(self):
		self.inject_command("stop")
		self.request_stop()
		self.assertEqual(supervisor.supervise("run_now"), 4)
		run_id, events = self.record()
		self.assertEqual(events[-1]["result"], "stopped")
		self.assertIn("stop_requested", [event["type"] for event in events])
		self.assertEqual(runs.load_run(run_id, {}, str(self.home / "profile"))["accounts"][0]["tasks"][0]["reason_key"], "errors.task.stopped")
		self.assertFalse((self.folder / (run_id + ".stop")).exists())
		self.assertIsNone(run_lock.status())

	def test_hard_stop_occurs_only_after_45_seconds_and_does_not_duplicate_end(self):
		now = [0.0]
		proc = mock.Mock(pid=123, returncode=None)
		proc.poll.side_effect = lambda: proc.returncode
		def wait(timeout):
			if proc.returncode is None:
				now[0] += timeout
				stop = next(iter(self.folder.glob("*.stop")), None)
				if stop is None:
					ids = run_record.list_run_ids(str(self.folder))
					if not ids:
						# The stub child has no writer, so use its bound environment.
						stop = Path(popen.call_args.kwargs["env"]["REWARDS_STOP_FILE"])
						stop.write_text("{}", encoding="utf-8")
				raise subprocess.TimeoutExpired("fake", timeout)
		proc.wait.side_effect = wait
		job = mock.Mock()
		terminated_at = []
		def terminate():
			terminated_at.append(now[0])
			proc.returncode = 1
		job.terminate.side_effect = terminate
		with mock.patch.object(paths, "farmer_command", return_value=["fake"]), mock.patch.object(supervisor.subprocess, "Popen", return_value=proc) as popen, mock.patch.object(supervisor, "Job", return_value=job), mock.patch.object(supervisor.time, "monotonic", side_effect=lambda: now[0]):
			self.assertEqual(supervisor.supervise("check_now"), 1)
		self.assertEqual(terminated_at, [46.0])
		_, events = self.record()
		self.assertEqual(len([e for e in events if e["type"] == "run_end"]), 1)
		self.assertEqual(events[-1]["result"], "stopped")

	@unittest.skipUnless(sys.platform == "win32", "Real Windows job descendants")
	def test_job_close_kills_child_and_grandchild_but_not_supervisor(self):
		code = "import subprocess,sys,time; time.sleep(.2); child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']); print(child.pid,flush=True); time.sleep(120)"
		proc = _REAL_POPEN([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
		job = Job()
		try:
			job.assign(proc.pid)
			grandchild = int(proc.stdout.readline())
			self.assertIsNotNone(run_lock._process_created(grandchild))
			job.close()
			proc.wait(timeout=10)
			deadline = time.monotonic() + 5
			while run_lock._process_created(grandchild) is not None and time.monotonic() < deadline:
				time.sleep(0.02)
			self.assertIsNone(run_lock._process_created(grandchild))
			self.assertIsNotNone(run_lock._process_created(os.getpid()))
		finally:
			job.close()
			if proc.poll() is None:
				proc.kill()
				proc.wait(timeout=10)
			proc.stdout.close()
			proc.stderr.close()

	@unittest.skipUnless(sys.platform == "win32", "Real Windows supervisor job termination")
	def test_unresponsive_farmer_hard_stop_kills_its_real_grandchild(self):
		self.inject_command("grandchild")
		# Wait for interpreter readiness, not just the grandchild's launcher.
		self.request_stop("test_child")
		tree = _JobTree()
		self.addCleanup(tree.close)
		class ObservedJob(Job):
			def assign(self, pid):
				tree.launcher = pid
				super().assign(pid)
			def terminate(self, *args):
				tree.capture(self)
				tree.terminated = True
				return super().terminate(*args)
			def close(self):
				if self._handle and not tree.members:
					tree.capture(self)
				return super().close()
		began = time.monotonic()
		clock = types.SimpleNamespace(monotonic=lambda: (time.monotonic() - began) * 50)
		with mock.patch.object(supervisor, "time", clock), mock.patch.object(supervisor, "Job", ObservedJob):
			try:
				code = supervisor.supervise("check_now")
			except subprocess.TimeoutExpired as exc:
				self.fail(f"Hard stop did not finish after {exc.timeout}s; termination_called={tree.terminated}; {tree.describe(tree.members)}")
		tree.wait()
		self.assertEqual(tree.waited, set(tree.members), "Every captured job member must have a signaled process handle")
		self.assertTrue(tree.terminated, "The real hard-stop path must call job termination")
		self.assertEqual(code, 1)
		run_id, events = self.record()
		self.assertEqual(events[-1]["result"], "stopped")
		self.assertEqual(events[-1]["written_by"], "supervisor")
		child = next(event for event in events if event.get("type") == "test_child")
		self.assertTrue({tree.launcher, child["farmer_pid"], child["pid"], child["interpreter_pid"]} <= set(tree.members), tree.describe(tree.members))
		self.assertGreaterEqual(len({tree.launcher, child["farmer_pid"], child["pid"], child["interpreter_pid"]}), 2)
		self.assertIsNone(run_lock._process_created(child["pid"]))
		self.assertIsNone(run_lock.status())
		self.assertEqual(runs.load_run(run_id, {}, str(self.home / "profile"))["result"], "stopped")

	@unittest.skipUnless(sys.platform == "win32", "Real Windows launcher and interpreter identities")
	def test_tree_wait_names_live_interpreters_and_waits_beyond_launcher_exit(self):
		code = "import os,time; print(os.getpid(),flush=True); time.sleep(120)"
		proc = _REAL_POPEN([sys.executable, "-c", code], cwd=self.home, stdin=subprocess.DEVNULL,
			stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
		job, tree = Job(), _JobTree()
		try:
			job.assign(proc.pid)
			interpreter = int(proc.stdout.readline())
			tree.capture(job)
			self.assertIn(interpreter, tree.alive())
			with self.assertRaisesRegex(AssertionError, f"still alive after .*bound=0.1s.*pid={interpreter}"):
				tree.wait(timeout=0.1)
			if interpreter == proc.pid:
				self.assertEqual(len([member for member in tree.members.values() if Path(member["image"]).name.lower() == "python.exe"]), 1)
			else:
				proc.kill()
				proc.wait(timeout=10)
				self.assertIsNone(run_lock._process_created(proc.pid))
		finally:
			job.terminate()
			try:
				tree.wait()
				self.assertEqual(tree.waited, set(tree.members))
			finally:
				job.close()
				tree.close()
				proc.wait(timeout=10)
				proc.stdout.close()
				proc.stderr.close()

	def test_job_assignment_failure_stops_only_the_owned_child_and_records_cause(self):
		proc = mock.Mock(pid=123, returncode=1)
		proc.poll.return_value = None
		with mock.patch.object(paths, "farmer_command", return_value=["fake"]), mock.patch.object(supervisor.subprocess, "Popen", return_value=proc), mock.patch.object(supervisor, "Job") as job:
			job.return_value.assign.side_effect = OSError("job assignment refused")
			self.assertEqual(supervisor.supervise("check_now"), 5)
			proc.kill.assert_called_once()
			proc.wait.assert_called_once_with(timeout=10)
			job.return_value.close.assert_called_once()
		_, events = self.record()
		self.assertIn("job assignment refused", events[-1]["detail"])

	def _use_configured_accounts(self):
		self.modules["rfapp.settings"].active_accounts = settings_module.active_accounts
		self.modules["rfapp.settings"].user_data_dir = settings_module.user_data_dir

	def _assert_refused_before_run(self, kind, accounts, expected):
		before = sorted(path.relative_to(self.home).as_posix() for path in self.home.rglob("*"))
		with mock.patch.object(run_lock, "status") as lock, mock.patch.object(run_record, "new_run_id") as new_id, mock.patch.object(paths, "runs_dir") as run_dir, self.assertLogs(supervisor.logger, level="INFO") as logs:
			self.assertEqual(supervisor.supervise(kind, accounts), expected)
			lock.assert_not_called()
			new_id.assert_not_called()
			run_dir.assert_not_called()
		self.spawn.assert_not_called()
		self.notify.assert_not_called()
		self.assertEqual(len(logs.output), 1)
		self.assertFalse(self.folder.exists())
		self.assertEqual(sorted(path.relative_to(self.home).as_posix() for path in self.home.rglob("*")), before)

	def test_scheduled_without_accounts_returns_zero_before_any_run_effect(self):
		self._use_configured_accounts()
		self.assertEqual(settings_module.active_accounts(), [])
		self._assert_refused_before_run("scheduled", None, 0)

	def test_manual_requests_without_accounts_return_two_without_run_files(self):
		self._use_configured_accounts()
		for kind in ("run_now", "check_now"):
			with self.subTest(kind=kind):
				self._assert_refused_before_run(kind, None, 2)

	def test_empty_account_list_returns_two_before_any_run_effect(self):
		for kind in ("run_now", "check_now"):
			with self.subTest(kind=kind), mock.patch.object(run_lock, "status", side_effect=AssertionError("Empty selection reached lock check")) as lock, mock.patch.object(run_record, "new_run_id", side_effect=AssertionError("Empty selection allocated a run id")) as new_id, mock.patch.object(paths, "runs_dir", side_effect=AssertionError("Empty selection reached run files")) as run_dir, self.assertLogs(supervisor.logger, level="INFO") as logs:
				self.assertEqual(supervisor.supervise(kind, []), 2)
				self.spawn.assert_not_called()
				lock.assert_not_called()
				new_id.assert_not_called()
				run_dir.assert_not_called()
				self.assertFalse(self.folder.exists())
				self.assertEqual(list(self.home.iterdir()), [])
				self.assertEqual(len(logs.output), 1)

	def test_unknown_paused_and_unconfigured_root_requests_are_refused(self):
		self._use_configured_accounts()
		(self.home / ".env").write_text("REWARDS_ACCOUNTS=one\nREWARDS_PAUSED_ACCOUNTS=paused\n", encoding="utf-8")
		self.assertEqual(settings_module.active_accounts(), ["one"])
		self.assertEqual(settings_module.paused_accounts(), ["paused"])
		for kind in ("scheduled", "run_now", "check_now"):
			for requested in (["unknown"], ["paused"], ["one", "paused"], ["@root"]):
				with self.subTest(kind=kind, requested=requested):
					self._assert_refused_before_run(kind, requested, 2)

	@unittest.skipUnless(sys.platform == "win32", "Real fake farmer in Windows job")
	def test_configured_account_spawns_only_fake_farmer_and_records_run(self):
		self._use_configured_accounts()
		(self.home / ".env").write_text("REWARDS_ACCOUNTS=one\n", encoding="utf-8")
		self.inject_command("finish")
		with mock.patch.object(supervisor.subprocess, "Popen", wraps=_REAL_POPEN) as spawn:
			self.assertEqual(supervisor.supervise("run_now", ["one"]), 0)
			self.assertEqual(Path(spawn.call_args.args[0][1]).name, "fake_farmer.py")
			self.assertEqual(spawn.call_args.kwargs["env"]["REWARDS_ACCOUNTS"], "one")
		_, events = self.record()
		self.assertEqual(events[-1]["result"], "ok")
		self.assertEqual(events[-1]["written_by"], "farmer")

	@unittest.skipUnless(sys.platform == "win32", "Real fake farmer in Windows job")
	def test_root_with_local_state_is_active_and_runs_fake_farmer(self):
		self._use_configured_accounts()
		profile = self.home / "data-dir"
		profile.mkdir()
		(profile / "Local State").write_text("{}", encoding="utf-8")
		self.assertEqual(settings_module.active_accounts(), ["@root"])
		self.inject_command("finish")
		with mock.patch.object(supervisor.subprocess, "Popen", wraps=_REAL_POPEN) as spawn:
			self.assertEqual(supervisor.supervise("check_now", ["@root"]), 0)
			self.assertEqual(spawn.call_args.kwargs["env"]["REWARDS_ACCOUNTS"], "")
		_, events = self.record()
		self.assertEqual(events[-1]["result"], "ok")

	def _wait_with_states(self, states):
		now = [0.0]
		self.sleeps = []
		def sleep(seconds):
			self.assertFalse(self.folder.exists())
			self.execution_state.assert_not_called()
			self.sleeps.append(seconds)
			now[0] += seconds
		reader = mock.Mock(side_effect=states)
		wait = supervisor._wait_until_available
		self.stack.enter_context(mock.patch.object(supervisor, "_wait_until_available", side_effect=lambda: wait(clock=lambda: now[0], sleep=sleep, state_reader=reader)))
		return reader

	def _stub_finished_farmer(self):
		proc = mock.Mock(pid=123, returncode=0)
		proc.poll.return_value = 0
		def spawn(command, **kwargs):
			run_id = kwargs["env"][run_record.RUN_ID_ENV]
			path = self.folder / (run_id + ".jsonl")
			self.assertEqual(self.execution_state.call_args_list, [mock.call(supervisor.ES_CONTINUOUS | supervisor.ES_SYSTEM_REQUIRED)])
			run_record.append_event(str(path), "run_start", run_id, trigger=kwargs["env"][run_record.TRIGGER_ENV], accounts=[], plan={})
			run_record.append_event(str(path), "run_end", run_id, result="checked", exit_code=0, duration_ms=1, written_by="farmer")
			return proc
		self.stack.enter_context(mock.patch.object(paths, "farmer_command", return_value=["synthetic-farmer"]))
		popen = self.stack.enter_context(mock.patch.object(supervisor.subprocess, "Popen", side_effect=spawn))
		self.stack.enter_context(mock.patch.object(supervisor, "Job"))
		return popen

	def test_scheduled_wait_ends_early_records_last_busy_state_before_run_start(self):
		reader = self._wait_with_states([2, 3, 5])
		popen = self._stub_finished_farmer()
		with mock.patch.object(run_lock, "status", return_value=None) as lock, mock.patch.object(supervisor.subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x4000, create=True), self.assertLogs(supervisor.logger, level="INFO") as logs:
			self.assertEqual(supervisor.supervise("scheduled", ["one"]), 0)
			self.assertEqual([call for call in lock.call_args_list if "directory" in call.kwargs], [mock.call(directory=str(self.home / "profile"))] * 2)
		self.assertEqual(len(logs.output), 2)
		self.assertEqual(self.sleeps, [60, 60])
		self.assertEqual(reader.call_count, 3)
		self.assertEqual(popen.call_args.kwargs["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0) | 0x4000)
		_, events = self.record()
		self.assertEqual([event["type"] for event in events], ["deferred", "run_start", "run_end"])
		self.assertEqual({key: events[0][key] for key in ("state", "waited_s", "gave_up")}, {"state": 3, "waited_s": 120, "gave_up": False})
		self.assertEqual(self.execution_state.call_args_list, [mock.call(0x80000001), mock.call(0x80000000)])

	def test_scheduled_wait_gives_up_at_forty_minutes_and_starts_below_normal(self):
		reader = self._wait_with_states([4] * 41)
		popen = self._stub_finished_farmer()
		with mock.patch.object(run_lock, "status", return_value=None), mock.patch.object(supervisor.subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x4000, create=True), self.assertLogs(supervisor.logger, level="INFO") as logs:
			self.assertEqual(supervisor.supervise("scheduled"), 0)
		self.assertEqual(len(logs.output), 2)
		self.assertEqual(self.sleeps, [60] * 40)
		self.assertEqual(reader.call_count, 41)
		self.assertEqual(popen.call_args.kwargs["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0) | 0x4000)
		_, events = self.record()
		self.assertEqual([event["type"] for event in events].count("deferred"), 1)
		self.assertEqual({key: events[0][key] for key in ("state", "waited_s", "gave_up")}, {"state": 4, "waited_s": 2400, "gave_up": True})

	def test_profile_lock_taken_during_wait_skips_before_any_record_or_farmer(self):
		self._wait_with_states([3, 5])
		with mock.patch.object(run_lock, "status", side_effect=[None, mock.Mock()]) as lock, mock.patch.object(run_record, "new_run_id", side_effect=AssertionError("run id before lock recheck")) as new_id, self.assertLogs(supervisor.logger, level="INFO") as logs:
			self.assertEqual(supervisor.supervise("scheduled"), 3)
		self.assertEqual(lock.call_count, 2)
		self.assertEqual(self.sleeps, [60])
		self.assertEqual(len(logs.output), 3)
		new_id.assert_not_called()
		self.spawn.assert_not_called()
		self.execution_state.assert_not_called()
		self.assertFalse(self.folder.exists())

	def test_nonbusy_states_do_not_wait_defer_recheck_or_change_priority(self):
		popen = self._stub_finished_farmer()
		for state in (None, 1, 5, 6, 7):
			with self.subTest(state=state):
				self.execution_state.reset_mock()
				self.state_reader.return_value = state
				with mock.patch.object(run_lock, "status", return_value=None) as lock, mock.patch.object(supervisor.time, "sleep", side_effect=AssertionError("nonbusy state slept")), mock.patch.object(supervisor.subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x4000, create=True):
					self.assertEqual(supervisor.supervise("scheduled"), 0)
				self.assertEqual([call for call in lock.call_args_list if "directory" in call.kwargs], [mock.call(directory=str(self.home / "profile"))])
				self.assertEqual(popen.call_args.kwargs["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))
		for run_id in run_record.list_run_ids(str(self.folder)):
			self.assertNotIn("deferred", [event["type"] for event in run_record.read_events(str(self.folder / (run_id + ".jsonl")))])

	def test_user_requested_runs_ignore_busy_gate_and_still_hold_awake(self):
		self._stub_finished_farmer()
		for kind in ("run_now", "check_now"):
			with self.subTest(kind=kind), mock.patch.object(supervisor, "_wait_until_available", side_effect=AssertionError("manual run gated")) as wait:
				self.execution_state.reset_mock()
				self.assertEqual(supervisor.supervise(kind), 0)
				wait.assert_not_called()
				self.assertEqual(self.execution_state.call_args_list, [mock.call(0x80000001), mock.call(0x80000000)])

	def test_keep_awake_release_runs_when_farmer_start_raises(self):
		with mock.patch.object(paths, "farmer_command", return_value=["synthetic-farmer"]), mock.patch.object(supervisor.subprocess, "Popen", side_effect=OSError("cannot start")):
			self.assertEqual(supervisor.supervise("check_now"), 5)
		self.assertEqual(self.execution_state.call_args_list, [mock.call(0x80000001), mock.call(0x80000000)])

	def test_keep_awake_release_runs_even_if_final_cleanup_raises(self):
		self._stub_finished_farmer()
		with mock.patch.object(run_record, "prune", side_effect=RuntimeError("cleanup failed")), self.assertRaisesRegex(RuntimeError, "cleanup failed"):
			supervisor.supervise("run_now")
		self.assertEqual(self.execution_state.call_args_list, [mock.call(0x80000001), mock.call(0x80000000)])

	def test_farmer_environment_disables_virtual_desktops_even_when_dotenv_enables_it(self):
		popen = self._stub_finished_farmer()
		(self.home / ".env").write_text("USE_VIRTUAL_DESKTOP=true\n", encoding="utf-8")
		with mock.patch.dict(os.environ, {"USE_VIRTUAL_DESKTOP": "true"}):
			self.assertEqual(supervisor.supervise("run_now"), 0)
		env = popen.call_args.kwargs["env"]
		self.assertEqual(env["USE_VIRTUAL_DESKTOP"], "0")
		code = """
import importlib.util,io,json,os,sys
from pathlib import Path
from unittest import mock
import main,desktop_utils
seen={}
def farmer(unattended=False,plan=None):
 seen.update(value=os.environ['USE_VIRTUAL_DESKTOP'],enabled=desktop_utils.is_virtual_desktop_enabled())
 return 0
with mock.patch.object(main,'main',farmer):
 code=main.cli(['--unattended','--check-only'])
print(json.dumps(seen))
raise SystemExit(code)
"""
		env = {**env, "PYTHONPATH": os.pathsep.join(filter(None, (os.environ.get("PYTHONPATH"), str(Path(run_record.__file__).parent))))}
		with mock.patch.object(supervisor.subprocess, "Popen", _REAL_POPEN):
			proc = subprocess.run([sys.executable, "-c", code], env=env, cwd=self.home, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
		self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
		self.assertEqual(json.loads(proc.stdout), {"value": "0", "enabled": False})

	@unittest.skipUnless(sys.platform == "win32", "Real deferred fake farmer through Windows job")
	def test_real_fake_farmer_appends_after_deferred_event(self):
		self._wait_with_states([2, 5])
		self.inject_command("finish")
		priorities = []
		def spawn(*args, **kwargs):
			proc = _REAL_POPEN(*args, **kwargs)
			try:
				kernel = supervisor.ctypes.WinDLL("kernel32", use_last_error=True)
				kernel.GetPriorityClass.argtypes = [supervisor.ctypes.c_void_p]
				kernel.GetPriorityClass.restype = supervisor.ctypes.c_uint32
				priorities.append(kernel.GetPriorityClass(int(proc._handle)))
				return proc
			except BaseException:
				proc.kill()
				proc.wait(timeout=10)
				raise
		with mock.patch.object(supervisor.subprocess, "Popen", side_effect=spawn), self.assertLogs(supervisor.logger, level="INFO"):
			self.assertEqual(supervisor.supervise("scheduled"), 0)
		self.assertEqual(priorities, [subprocess.BELOW_NORMAL_PRIORITY_CLASS])
		run_id, events = self.record()
		self.assertEqual([event["type"] for event in events][:2], ["deferred", "run_start"])
		self.assertEqual([event["type"] for event in events].count("deferred"), 1)
		self.assertEqual(runs.load_run(run_id, {}, str(self.home / "profile"))["deferred"], {"state": 2, "waited_s": 60, "gave_up": False})

	def test_admission_failures_return_before_querying_user_state(self):
		with mock.patch.object(supervisor, "_wait_until_available", side_effect=AssertionError("Busy gate before admission")) as wait:
			self.settings["schedule.mode"] = "manual"
			self.assertEqual(supervisor.supervise("scheduled"), 0)
			self.settings["schedule.mode"] = "automatic"
			self.active_accounts = []
			self.assertEqual(supervisor.supervise("scheduled"), 0)
			self.active_accounts = ["one"]
			with mock.patch.object(run_lock, "status", return_value=mock.Mock()):
				self.assertEqual(supervisor.supervise("scheduled"), 3)
			wait.assert_not_called()
		self.state_reader.assert_not_called()
		self.execution_state.assert_not_called()
		self.spawn.assert_not_called()
		self.assertFalse(self.folder.exists())


class ScheduledAvailabilityTests(unittest.TestCase):
	def test_injected_state_clock_and_sleep_wait_without_real_time(self):
		now = [0.0]
		sleeps = []
		def sleep(seconds):
			sleeps.append(seconds)
			now[0] += seconds
		with self.assertLogs(supervisor.logger, level="INFO") as logs:
			deferred = supervisor._wait_until_available(clock=lambda: now[0], sleep=sleep, state_reader=mock.Mock(side_effect=[2, 4, None]))
		self.assertEqual(deferred, {"state": 4, "waited_s": 120, "gave_up": False})
		self.assertEqual(sleeps, [60, 60])
		self.assertEqual(len(logs.output), 2)

	def test_execution_state_wrapper_uses_only_system_required_not_display(self):
		set_state = mock.Mock(return_value=0x80000000)
		with mock.patch.object(supervisor, "os", types.SimpleNamespace(name="nt")), mock.patch.object(supervisor.ctypes, "WinDLL", return_value=types.SimpleNamespace(SetThreadExecutionState=set_state), create=True):
			supervisor._set_execution_state(0x80000001)
			supervisor._set_execution_state(0x80000000)
		self.assertEqual(set_state.call_args_list, [mock.call(0x80000001), mock.call(0x80000000)])
		self.assertEqual(set_state.argtypes, [supervisor.ctypes.c_uint32])
		self.assertIs(set_state.restype, supervisor.ctypes.c_uint32)

	def test_execution_state_failure_is_diagnostic_and_does_not_raise(self):
		with mock.patch.object(supervisor, "os", types.SimpleNamespace(name="nt")), mock.patch.object(supervisor.ctypes, "WinDLL", side_effect=OSError("no kernel"), create=True), self.assertLogs(supervisor.logger, level="WARNING"):
			supervisor._set_execution_state(0x80000001)


class FarmerIsolationRegression(unittest.TestCase):
	def test_all_app_tests_refuse_a_real_farmer_spawn(self):
		code = """
import ctypes,io,json,os,subprocess,sys,unittest
from pathlib import Path
violations=[]
def audit(event,fields):
 if event != 'subprocess.Popen': return
 executable,args,cwd,env=fields
 if isinstance(args,(str,bytes)):
  command=os.fsdecode(args)
  if sys.platform=='win32':
   count=ctypes.c_int()
   parse=ctypes.windll.shell32.CommandLineToArgvW
   parse.argtypes=[ctypes.c_wchar_p,ctypes.POINTER(ctypes.c_int)]
   parse.restype=ctypes.POINTER(ctypes.c_wchar_p)
   pointer=parse(command,ctypes.byref(count))
   if not pointer: raise RuntimeError('COMMAND_PARSE_FAILED')
   try: args=[pointer[index] for index in range(count.value)]
   finally:
    free=ctypes.windll.kernel32.LocalFree
    free.argtypes=[ctypes.c_void_p]
    free(pointer)
  else:
   import shlex
   args=shlex.split(command)
 args=[os.fsdecode(value) for value in args]
 exe=Path(os.fsdecode(executable or args[0])).name.lower()
 script=Path(args[1]).name.lower() if len(args)>1 else ''
 forbidden=(script=='main.py' or (script=='launcher.py' and '--farm' in args) or (exe=='rewardsfarmer.exe' and '--farm' in args) or (args[1:3]==['-m','main']))
 if forbidden:
  violations.append(args)
  raise RuntimeError('REAL_FARMER_FORBIDDEN_IN_APP_TESTS')
for args in ([sys.executable,'src/main.py','--check-only'],[sys.executable,'app/launcher.py','--farm','--check-only']):
 for shape in (args,subprocess.list2cmdline(args)):
  try: audit('subprocess.Popen',(None,shape,None,None))
  except RuntimeError: pass
  else: raise AssertionError('audit negative branch failed')
assert len(violations)==4
violations.clear()
sys.addaudithook(audit)
def flatten(suite):
 for item in suite:
  if isinstance(item,unittest.TestSuite): yield from flatten(item)
  else: yield item
suite=unittest.defaultTestLoader.discover(sys.argv[1])
tests=unittest.TestSuite(test for test in flatten(suite) if 'FarmerIsolationRegression' not in test.id())
output=io.StringIO()
class ProgressResult(unittest.TextTestResult):
 def startTest(self,test):
  print('FARMER_ISOLATION_TEST '+test.id(),file=sys.stderr,flush=True)
  super().startTest(test)
result=unittest.TextTestRunner(stream=output,resultclass=ProgressResult).run(tests)
if not result.wasSuccessful(): print(output.getvalue())
print(json.dumps({'tests':result.testsRun,'violations':violations,'ok':result.wasSuccessful()}))
raise SystemExit(0 if result.wasSuccessful() and not violations else 1)
"""
		# The guard lives only in this test child, never in the product entrypoint.
		env = dict(os.environ)
		env.pop("REWARDS_APP_UI_TESTS", None)
		# On 2026-10-17 this took 80 s alone and exceeded 120 s under shared
		# load. Use the parent's 600000 ms overlay command budget, not a speed
		# assertion; it still bounds a hang within the Windows CI job's 45 min.
		timeout = 600
		began = time.monotonic()
		def diagnostic(state, stdout, stderr):
			stdout = stdout.decode("utf-8", errors="replace") if isinstance(stdout, bytes) else stdout or ""
			stderr = stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else stderr or ""
			prefix = "FARMER_ISOLATION_TEST "
			last = next((line[len(prefix):] for line in reversed(stderr.splitlines()) if line.startswith(prefix)), "unavailable (before the first test or no progress captured)")
			summary = f"Farmer isolation nested suite {state}: elapsed={time.monotonic() - began:.3f}s, bound={timeout}s, last_test={last}"
			# Keep actual errors without repeating hundreds of progress markers.
			errors = "\n".join(line for line in stderr.splitlines() if not line.startswith(prefix))
			return summary, "\n".join((summary, stdout, errors))
		try:
			proc = subprocess.run([sys.executable, "-c", code, str(Path(__file__).parent)], env=env, cwd=Path(__file__).resolve().parents[2],
				stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
		except subprocess.TimeoutExpired as exc:
			self.fail(diagnostic("timed out", exc.stdout, exc.stderr)[1])
		summary, failure = diagnostic(f"exit={proc.returncode}", proc.stdout, proc.stderr)
		print(summary, flush=True)
		self.assertEqual(proc.returncode, 0, failure)
		try:
			proof = json.loads(proc.stdout.splitlines()[-1])
		except (IndexError, json.JSONDecodeError):
			self.fail("Invalid nested proof\n" + failure)
		self.assertGreater(proof["tests"], 10)
		self.assertEqual(proof["violations"], [])
		self.assertTrue(proof["ok"])
