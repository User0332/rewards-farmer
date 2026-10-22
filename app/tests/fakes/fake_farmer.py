"""Test-only worker, reachable only through an injected farmer command."""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
import run_lock
import run_record


def main():
	parser = argparse.ArgumentParser()
	parser.add_argument("scenario", choices=("finish", "crash", "stop", "ignore", "grandchild"))
	args = parser.parse_args()
	# Import time in the real farmer gives the supervisor time to assign its job.
	time.sleep(0.2)
	recorder = run_record.open_run(os.environ.get(run_record.TRIGGER_ENV, "app"), ["default"], {"check_only": False}, [])
	lock = run_lock.acquire("farmer", recorder.run_id)
	if lock is None:
		run_record.close_run("busy", 3)
		return 3
	try:
		with run_record.account_context("default"):
			run_record.emit("account_start", profile=run_lock.lock_dir())
			run_record.emit("state", phase="before", balance=100, earned_today=0, searches_earned=0, searches_max=60,
				daily_set_open=3, cards_open=1, quests_open=0, open_points=100, unreadable=None)
			if args.scenario == "crash":
				os._exit(9)
			if args.scenario == "grandchild":
				child = subprocess.Popen([sys.executable, "-c", "import os,time; print(os.getpid(),flush=True); time.sleep(120)"],
					stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True)
				# A venv launcher may still be starting its interpreter after Popen.
				interpreter = int(child.stdout.readline())
				child.stdout.close()
				run_record.emit("test_child", pid=child.pid, interpreter_pid=interpreter, farmer_pid=os.getpid())
			if args.scenario in ("stop", "ignore", "grandchild"):
				while not Path(os.environ["REWARDS_STOP_FILE"]).exists() or args.scenario != "stop":
					time.sleep(0.05)
				run_record.emit("stop_requested", by="app")
				run_record.close_run("stopped", 4)
				return 4
			run_record.emit("task_start", task="searches", name="Required searches", points_before=100)
			run_record.emit("task_end", task="searches", name="Required searches", outcome="ok", reason_key=None,
				detail=None, progress="sent 20 searches", points_before=100, points_after=160, duration_ms=1)
			run_record.emit("state", phase="after", balance=160, earned_today=60, searches_earned=60, searches_max=60,
				daily_set_open=3, cards_open=1, quests_open=0, open_points=40, unreadable=None)
			run_record.emit("account_end", started=True, ran_tasks=True, error_code=None, detail=None, duration_ms=1)
		run_record.close_run("ok", 0)
		return 0
	finally:
		lock.release()


if __name__ == "__main__":
	sys.exit(main())
