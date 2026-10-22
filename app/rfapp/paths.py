"""Resource paths and user state without importing the farmer."""

import hashlib
import os
import sys
from pathlib import Path


def is_frozen() -> bool:
	return bool(getattr(sys, "frozen", False))


def prepare_environment() -> None:
	# Child farmers inherit this opt-out before Selenium Manager can run.
	os.environ.setdefault("SE_AVOID_STATS", "true")
	if is_frozen():
		# An explicit data home keeps disposable tests away from installed state.
		os.environ.setdefault("REWARDS_FARMER_DATA_HOME", str(data_home()))
		os.environ.setdefault("SE_CACHE_PATH", str(data_home() / "selenium"))
		os.environ["REWARDS_FARMER_RESOURCE_ROOT"] = str(repo_root())
	else:
		source = str(repo_root() / "src")
		sys.path[:] = [entry for entry in sys.path if entry != source]
		sys.path.insert(0, source)


def repo_root() -> Path:
	return Path(sys._MEIPASS) if is_frozen() else Path(__file__).resolve().parents[2]


def resource(*parts: str) -> Path:
	return repo_root().joinpath("app", *parts)


def ui_dir() -> Path:
	return resource("ui")


def data_home() -> Path:
	if value := os.environ.get("REWARDS_FARMER_DATA_HOME"):
		return Path(value)
	if is_frozen():
		return Path(os.environ["LOCALAPPDATA"]) / "RewardsFarmer"
	return repo_root()


def env_path() -> Path:
	return data_home() / ".env"


def runs_dir() -> Path:
	directory = data_home() / "runs"
	directory.mkdir(parents=True, exist_ok=True)
	return directory


def prefs_path() -> Path:
	return data_home() / "app-prefs.json"


def app_log_path() -> Path:
	return data_home() / "app.log"


def install_identity() -> str:
	username = os.environ["USERNAME"].lower()
	identity = f"{username}|{os.path.normcase(str(data_home()))}"
	return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8]


def task_name() -> str:
	return "RewardsFarmerApp_" + install_identity()


def launcher_command(args: list[str]) -> list[str]:
	if is_frozen():
		return [sys.executable, *args]

	pythonw = Path(sys.executable).with_name("pythonw.exe")
	interpreter = str(pythonw) if pythonw.is_file() else sys.executable
	return [interpreter, str(repo_root() / "app" / "launcher.py"), *args]


def farmer_command(args: list[str]) -> list[str]:
	return launcher_command(["--farm", *args])
