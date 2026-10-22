import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import logging
from logging.handlers import RotatingFileHandler
import os
import sys


_install_mutex = None
_install_mutex_error = None
_INSTALL_MUTEX_NAME = "Local\\RewardsFarmer.Installed"


def _hold_install_mutex():
	global _install_mutex, _install_mutex_error
	if _install_mutex:
		return
	try:
		kernel = ctypes.WinDLL("kernel32", use_last_error=True)
		kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
		kernel.CreateMutexW.restype = wintypes.HANDLE
		handle = kernel.CreateMutexW(None, False, _INSTALL_MUTEX_NAME)
		if not handle:
			raise ctypes.WinError(ctypes.get_last_error())
		# Keep the shared install blocker alive after dispatch until this process exits.
		_install_mutex = handle
	except OSError as exc:
		_install_mutex_error = "Could not create Local\\RewardsFarmer.Installed: " + str(exc).replace("\r", " ").replace("\n", " ")


def _logging():
	from rfapp import paths
	logger = logging.getLogger("rfapp")
	logger.setLevel(logging.INFO)
	path = paths.app_log_path()
	path.parent.mkdir(parents=True, exist_ok=True)
	if not any(isinstance(handler, RotatingFileHandler) and handler.baseFilename == str(path.resolve()) for handler in logger.handlers):
		handler = RotatingFileHandler(path, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8")
		handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
		logger.addHandler(handler)
	logger.propagate = False


def _log_install_mutex_error(farm=False):
	global _install_mutex_error
	error = _install_mutex_error
	if error is None:
		return
	_install_mutex_error = None
	if not farm:
		logging.getLogger("rfapp").warning("%s", error)
		return
	try:
		from rfapp import paths
		path = paths.app_log_path()
		path.parent.mkdir(parents=True, exist_ok=True)
		# The farmer has no rfapp handler, so preserve this startup warning directly.
		with path.open("a", encoding="utf-8") as log:
			at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
			log.write(f"{at} WARNING rfapp: {error}\n")
	except Exception:
		pass


def _farm_exception(exc_type, exc, traceback):
	logging.getLogger("farm").critical("Uncaught farmer exception", exc_info=(exc_type, exc, traceback))


def main(argv=None):
	args = list(sys.argv[1:] if argv is None else argv)
	token = args[0] if args else ""
	from rfapp import paths
	paths.prepare_environment()
	if paths.is_frozen() and token != "--version":
		_hold_install_mutex()
	# Explorer does not supply console handles to the windowed executable.
	if sys.stdout is None:
		sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")
	if token != "--farm":
		_logging()
		_log_install_mutex_error()
	if token == "--farm":
		_log_install_mutex_error(farm=True)
		sys.excepthook = _farm_exception
		import main as farmer
		return farmer.cli(args[1:])
	if token in ("--scheduled", "--run-now", "--check-now"):
		from rfapp.supervisor import supervise
		kind = {"--scheduled": "scheduled", "--run-now": "run_now", "--check-now": "check_now"}[token]
		accounts = None
		if args[1:]:
			if token == "--scheduled" or len(args) != 3 or args[1] != "--accounts" or not args[2] or any(not part.strip() for part in args[2].split(",")):
				return 2
			accounts = [part.strip() for part in args[2].split(",")]
		return supervise(kind, accounts) if accounts is not None else supervise(kind)
	if token == "--signin":
		if len(args) not in (2, 3) or (len(args) == 3 and args[2] != "--new"):
			return 2
		from rfapp.signin import run
		return run(args[1], new="--new" in args)
	if token == "--version" and len(args) == 1:
		from rfapp import __version__
		print(__version__)
		return 0
	if token not in ("", "--tray") or len(args) > 1:
		return 2
	from rfapp.window import run
	run(start_hidden=(token == "--tray"))
	return 0


if __name__ == "__main__":
	sys.exit(main())
