"""Only non-window, non-browser frozen dispatch checks."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid


def main():
	parser = argparse.ArgumentParser()
	parser.add_argument("--exe", type=Path, required=True)
	parser.add_argument("--scratch", type=Path, required=True)
	parser.add_argument("--version", required=True)
	args = parser.parse_args()
	area = args.scratch / ("smoke-" + uuid.uuid4().hex[:8])
	area.mkdir(parents=True)
	results = []
	for label, flags in (("version", ["--version"]), ("farm-help", ["--farm", "--help"]), ("scheduled-manual", ["--scheduled"])):
		home = area / label
		home.mkdir()
		(home / "app-prefs.json").write_text(json.dumps({"v": 1, "update_check": False, "close_to_tray": False, "tray_hint_shown": True}), encoding="utf-8")
		if label == "scheduled-manual":
			(home / ".env").write_text("REWARDS_SCHEDULE_MODE=manual\n", encoding="utf-8")
		env = os.environ.copy()
		for name in tuple(env):
			if name.startswith(("REWARDS_", "SE_")) or name in ("USER_DATA_DIR", "RF_GUARD_UI", "RF_GUARD_LOG_DIR", "PYTHONPATH"):
				env.pop(name)
		env.update(REWARDS_FARMER_DATA_HOME=str(home), TEMP=str(home), TMP=str(home), PYTHONUTF8="1", PATH=os.pathsep.join([os.path.join(os.environ["WINDIR"], "System32"), os.environ["WINDIR"]]))
		result = subprocess.run([str(args.exe.resolve()), *flags], cwd=Path(os.environ["WINDIR"]) / "System32", env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
		stdout = result.stdout.decode("utf-8", errors="replace")
		stderr = result.stderr.decode("utf-8", errors="replace")
		(home / "stdout.txt").write_text(stdout, encoding="utf-8")
		(home / "stderr.txt").write_text(stderr, encoding="utf-8")
		row = {"mode": label, "exit_code": result.returncode, "stdout": stdout, "stderr": stderr, "data_home": str(home)}
		results.append(row)
		(area / "smoke.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
		print(json.dumps(row), flush=True)
		assert result.returncode == 0, f"Frozen {label} failed"
		if label == "version":
			assert stdout.strip() == args.version
		elif label == "farm-help":
			assert "--only-if-open" in stdout and "--check-only" in stdout
		else:
			assert not (home / "runs").exists(), "Manual scheduled dispatch must not write a run"
	print(f"SMOKE_RESULT={area / 'smoke.json'}")
	return 0


if __name__ == "__main__":
	sys.exit(main())
