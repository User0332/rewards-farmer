"""One short-lived owner for a farmer process, job and crash outcome."""

import ctypes
import logging
import os
import subprocess
import time

from rfapp import __version__, paths, user_state
from rfapp.winjob import Job
import run_lock
import run_record

logger = logging.getLogger("rfapp")
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def _set_execution_state(flags):
	if os.name != "nt":
		return
	try:
		set_state = ctypes.WinDLL("kernel32", use_last_error=True).SetThreadExecutionState
		set_state.argtypes = [ctypes.c_uint32]
		set_state.restype = ctypes.c_uint32
		if not set_state(flags):
			logger.warning("Windows did not accept the run's execution state")
	except Exception:
		logger.warning("Could not set the run's execution state", exc_info=True)


def _wait_until_available(*, clock=None, sleep=None, state_reader=None):
	clock = clock or time.monotonic
	sleep = sleep or time.sleep
	state_reader = state_reader or user_state.notification_state
	state = state_reader()
	if state not in (2, 3, 4):
		return None
	began = clock()
	last_busy = state
	logger.info("Scheduled check waiting while user notification state is %s", state)
	while state in (2, 3, 4):
		remaining = 2400 - (clock() - began)
		if remaining <= 0:
			break
		sleep(min(60, remaining))
		state = state_reader()
		if state in (2, 3, 4):
			last_busy = state
	deferred = {"state": last_busy, "waited_s": int(clock() - began), "gave_up": state in (2, 3, 4)}
	logger.info("Scheduled check wait ended after %s seconds (state %s, gave_up=%s)", deferred["waited_s"], state, deferred["gave_up"])
	return deferred


def _plan(args):
	return {"read_state": True, "only_if_open": "--only-if-open" in args, "check_only": "--check-only" in args,
		"min_points": int(args[args.index("--min-points") + 1]) if "--min-points" in args else None,
		"run_if_unreadable": "--run-if-unreadable" in args}


def _end(path, run_id, result, code, began, detail=None):
	events = run_record.read_events(str(path))
	if any(event.get("type") == "run_end" for event in events):
		return
	ends = [event for event in events if event.get("type") == "account_end"]
	run_record.append_event(str(path), "run_end", run_id, result=result, exit_code=code,
		accounts_started=sum(bool(event.get("started")) for event in ends), accounts_ran=sum(bool(event.get("ran_tasks")) for event in ends),
		duration_ms=round((time.monotonic() - began) * 1000), written_by="supervisor", detail=detail)


def supervise(kind: str, accounts: list[str] | None = None) -> int:
	from rfapp import settings
	if kind not in ("scheduled", "run_now", "check_now"):
		raise ValueError("Unknown supervisor kind")
	s = settings.load()
	if kind == "scheduled" and s["schedule.mode"] == "manual":
		return 0
	# An unconfigured app must not reach the farmer's implicit root-profile fallback.
	active_accounts = settings.active_accounts()
	if accounts is None and not active_accounts:
		logger.info("%s skipped because no active account is configured", kind)
		return 0 if kind == "scheduled" else 2
	if accounts is not None and (not accounts or any(account not in active_accounts for account in accounts)):
		logger.info("%s refused because a requested account is not active", kind)
		return 2
	if kind == "scheduled" and run_lock.status(directory=str(settings.user_data_dir())) is not None:
		logger.info("Scheduled check skipped because browser profiles are in use")
		return 3
	deferred = _wait_until_available() if kind == "scheduled" else None
	if deferred is not None and run_lock.status(directory=str(settings.user_data_dir())) is not None:
		logger.info("Scheduled check skipped because browser profiles are in use")
		return 3
	if kind == "check_now":
		args = ["--unattended", "--check-only"]
	elif kind == "run_now":
		args = ["--unattended", "--read-state"]
	elif s["schedule.mode"] == "fixed":
		args = ["--unattended", "--only-if-open", "--min-points", "1", "--run-if-unreadable"]
	else:
		args = ["--unattended", "--only-if-open", "--min-points", str(s["schedule.min_points"])]
	run_id = run_record.new_run_id()
	directory = paths.runs_dir()
	path = directory / (run_id + ".jsonl")
	stop = directory / (run_id + ".stop")
	began = time.monotonic()
	trigger = "scheduled" if kind == "scheduled" else "app"
	def failed(exc):
		detail = f"{type(exc).__name__}: {exc}"
		logger.error("Could not start farmer %s: %s", run_id, detail, exc_info=isinstance(exc, OSError))
		run_record.append_event(str(path), "run_start", run_id, trigger=trigger, accounts=accounts or [], plan=_plan(args), skip_tasks=[],
			pid=None, supervisor_pid=os.getpid(), app_version=__version__, written_by="supervisor")
		_end(path, run_id, "crashed", 5, began, detail)
		return 5
	if accounts and "@root" in accounts and accounts != ["@root"]:
		return failed(ValueError(f"@root cannot be mixed with other account ids: {accounts!r}"))
	env = {**os.environ, run_record.RUN_ID_ENV: run_id, run_record.TRIGGER_ENV: trigger,
		"REWARDS_STOP_FILE": str(stop), "REWARDS_FARMER_LOG_FILE": str(directory / (run_id + ".log")),
		"REWARDS_SUPERVISOR_PID": str(os.getpid()), run_record.APP_VERSION_ENV: __version__, "PYTHONUTF8": "1", "USE_VIRTUAL_DESKTOP": "0"}
	if accounts is not None:
		env["REWARDS_ACCOUNTS"] = "" if accounts == ["@root"] else ",".join(accounts)
	proc = job = None
	try:
		_set_execution_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
		try:
			command = paths.farmer_command(args)
			flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
			if deferred is not None:
				# Below-normal parents pass that class to the driver and Edge too.
				flags |= getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
				run_record.append_event(str(path), "deferred", run_id, **deferred)
			proc = subprocess.Popen(command, env=env, cwd=paths.data_home(), stdin=subprocess.DEVNULL,
				stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
			job = Job()
			job.assign(proc.pid)
		except Exception as exc:
			if proc is not None and proc.poll() is None:
				proc.kill()
				proc.wait(timeout=10)
			return failed(exc)
		stop_at = None
		while proc.poll() is None:
			if stop.exists():
				if stop_at is None:
					stop_at = time.monotonic()
				elif time.monotonic() - stop_at >= 45:
					job.terminate()
					proc.wait(timeout=10)
					_end(path, run_id, "stopped", proc.returncode, began)
					break
			try:
				proc.wait(timeout=1)
			except subprocess.TimeoutExpired:
				pass
		code = proc.returncode
		_end(path, run_id, "stopped" if stop.exists() else "crashed", code, began)
		if kind != "check_now":
			try:
				from rfapp import notify
				notify.after_run(run_id, s)
			except Exception:
				logger.exception("Run notification failed for %s", run_id)
		return code
	finally:
		try:
			try:
				stop.unlink(missing_ok=True)
				run_record.prune(str(directory))
			finally:
				if job is not None:
					job.close()
		finally:
			_set_execution_state(ES_CONTINUOUS)
