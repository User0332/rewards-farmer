import os
import sys
import time
import signal
import atexit
import subprocess
import glob
from pathlib import Path
from typing import Optional

from selenium import webdriver

from constants import USER_DATA_DIR, PROFILE_NAME


# Singleton files created by Chromium/Edge
CHROMIUM_LOCK_FILES = (
	"SingletonLock",
	"SingletonCookie",
	"SingletonSocket",
	"parent_unfreeze_signal",
)


def kill_orphaned_drivers(user_data_dir: Optional[str] = None):
	"""Gracefully terminate lingering msedgedriver and profile-specific Edge processes.
	
	Safety guarantee: Only terminates processes matching 'msedgedriver' or Edge processes
	specifically running with the automation '--user-data-dir' path. Does NOT touch standard
	user browser sessions.
	"""
	target_dir = os.path.abspath(user_data_dir or USER_DATA_DIR)
	
	try:
		# Query running processes on macOS / Unix
		ps_output = subprocess.check_output(
			["ps", "-eo", "pid,command"],
			text=True,
			stderr=subprocess.DEVNULL
		)
	except Exception as exc:
		print(f"[DEBUG] Could not query processes via ps: {exc}")
		return

	pids_to_terminate = []

	for line in ps_output.splitlines():
		line = line.strip()
		if not line:
			continue
		parts = line.split(maxsplit=1)
		if len(parts) < 2:
			continue
		pid_str, cmd = parts[0], parts[1]
		
		try:
			pid = int(pid_str)
		except ValueError:
			continue
			
		# Do not target current process
		if pid == os.getpid():
			continue

		# Target 1: Orphaned msedgedriver instances
		if "msedgedriver" in cmd:
			pids_to_terminate.append((pid, "msedgedriver"))
			continue

		# Target 2: Edge browser processes bound specifically to our custom user data dir
		if ("Microsoft Edge" in cmd or "msedge" in cmd) and f"--user-data-dir={target_dir}" in cmd:
			pids_to_terminate.append((pid, f"Edge (data-dir: {target_dir})"))

	if not pids_to_terminate:
		return

	print(f"[INFO] Cleaning up {len(pids_to_terminate)} orphaned driver/browser processes...")

	for pid, proc_desc in pids_to_terminate:
		try:
			os.kill(pid, signal.SIGTERM)
		except ProcessLookupError:
			pass
		except Exception as exc:
			print(f"[WARNING] Failed to send SIGTERM to PID {pid} ({proc_desc}): {exc}")

	# Give processes a brief moment to exit cleanly
	time.sleep(0.5)

	# Escalate to SIGKILL for any stubborn survivors
	for pid, proc_desc in pids_to_terminate:
		try:
			# Check if process is still alive (signal 0 raises if process is gone)
			os.kill(pid, 0)
			os.kill(pid, signal.SIGKILL)
			print(f"[INFO] Force killed lingering process PID {pid} ({proc_desc})")
		except ProcessLookupError:
			pass
		except Exception:
			pass


def cleanup_stale_locks(user_data_dir: Optional[str] = None):
	"""Remove stale Chromium lock files and symlinks that prevent session startup.
	
	Purges SingletonLock, SingletonCookie, SingletonSocket, and LevelDB LOCK files
	in the target profile directory.
	"""
	data_path = Path(user_data_dir or USER_DATA_DIR).resolve()
	if not data_path.exists():
		return

	# 1. Clean root Singleton lock files / symlinks
	for lock_name in CHROMIUM_LOCK_FILES:
		lock_path = data_path / lock_name
		try:
			if lock_path.is_symlink() or lock_path.exists():
				if lock_path.is_dir() and not lock_path.is_symlink():
					import shutil
					shutil.rmtree(lock_path, ignore_errors=True)
				else:
					lock_path.unlink(missing_ok=True)
				print(f"[INFO] Purged stale Chromium lock: {lock_path.name}")
		except Exception as exc:
			print(f"[WARNING] Could not remove lock file {lock_path}: {exc}")

	# 2. Clean LevelDB LOCK files in profile directories if present
	profile_dirs = [data_path / PROFILE_NAME, data_path / "Default", data_path]
	for p_dir in profile_dirs:
		if not p_dir.exists():
			continue
		for lock_file in p_dir.glob("**/LOCK"):
			try:
				if lock_file.is_file() or lock_file.is_symlink():
					lock_file.unlink(missing_ok=True)
			except Exception:
				pass


def create_stealth_driver(
	user_data_dir: Optional[str] = None,
	profile_name: Optional[str] = None,
	headless: bool = False,
	mobile: bool = False,
) -> webdriver.Edge:
	"""Pre-flights environment, cleans stale locks, and constructs a stealth Edge WebDriver.
	
	Includes Linux server stability flags (--disable-dev-shm-usage), Windows User-Agent spoofing,
	and CDP hooks to mask headless signatures, navigator.webdriver, and WebGL SwiftShader.
	"""
	from constants import WINDOWS_DESKTOP_UA, MOBILE_UA

	target_data_dir = os.path.abspath(user_data_dir or USER_DATA_DIR)
	target_profile = profile_name or PROFILE_NAME

	# Pre-flight cleanup
	kill_orphaned_drivers(target_data_dir)
	cleanup_stale_locks(target_data_dir)

	options = webdriver.EdgeOptions()

	# Anti-detection flags
	options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
	options.add_experimental_option("useAutomationExtension", False)
	options.add_argument("--disable-blink-features=AutomationControlled")
	
	# Stability & stealth arguments (Critical for Linux Ubuntu Server & container stability)
	options.add_argument("--disable-dev-shm-usage")
	options.add_argument("--no-first-run")
	options.add_argument("--no-default-browser-check")
	options.add_argument("--disable-infobars")
	options.add_argument("--disable-notifications")
	options.add_argument("--disable-features=Translate,OptimizationHints,MediaRouter")
	options.add_argument("--disable-default-apps")
	options.add_argument("--disable-component-update")
	
	# User-Agent & Window sizing
	if mobile:
		options.add_argument(f"--user-agent={MOBILE_UA}")
		options.add_argument("--window-size=393,852")
	else:
		# Spoof Windows 10/11 Edge User-Agent on Linux server
		options.add_argument(f"--user-agent={WINDOWS_DESKTOP_UA}")
		options.add_argument("--window-size=1366,768")
		options.add_argument("--start-maximized")

	# Profile isolation
	options.add_argument(f"--user-data-dir={target_data_dir}")
	options.add_argument(f"--profile-directory={target_profile}")

	if headless:
		options.add_argument("--headless=new")

	def apply_stealth_cdp_hooks(drv: webdriver.Edge):
		"""Inject CDP scripts to mask webdriver flag, SwiftShader GPU, and chrome runtime."""
		stealth_script = """
		// 1. Mask navigator.webdriver
		Object.defineProperty(navigator, 'webdriver', {
			get: () => undefined,
			configurable: true
		});

		// 2. Mask WebGL vendor and renderer (hides Google SwiftShader / llvmpipe on headless Linux servers)
		const maskWebGL = (proto) => {
			if (!proto) return;
			const origGetParameter = proto.getParameter;
			proto.getParameter = function(param) {
				// UNMASKED_VENDOR_WEBGL
				if (param === 37445) return 'Intel Inc.';
				// UNMASKED_RENDERER_WEBGL
				if (param === 37446) return 'Intel(R) UHD Graphics 630';
				return origGetParameter.apply(this, arguments);
			};
		};
		try {
			if (typeof WebGLRenderingContext !== 'undefined') maskWebGL(WebGLRenderingContext.prototype);
			if (typeof WebGL2RenderingContext !== 'undefined') maskWebGL(WebGL2RenderingContext.prototype);
		} catch (e) {}

		// 3. Ensure window.chrome runtime object exists in headless
		if (!window.chrome) {
			window.chrome = {};
		}
		if (!window.chrome.runtime) {
			window.chrome.runtime = {
				PlatformOs: { MAC: 'mac', WIN: 'win', ANDROID: 'android', CROS: 'cros', LINUX: 'linux' }
			};
		}

		// 4. Ensure plugins array looks authentic
		if (!navigator.plugins || navigator.plugins.length === 0) {
			Object.defineProperty(navigator, 'plugins', {
				get: () => [1, 2, 3, 4, 5],
				configurable: true
			});
		}
		"""
		try:
			drv.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": stealth_script})
		except Exception as exc:
			print(f"[DEBUG] Could not inject stealth CDP script: {exc}")

		# Set focus on protocol level
		try:
			drv.execute_cdp_cmd("Emulation.setFocusEmulationEnabled", {"enabled": True})
		except Exception:
			pass

	try:
		driver = webdriver.Edge(options=options)
		apply_stealth_cdp_hooks(driver)
	except Exception as exc:
		# If session failed, attempt emergency lock purge and retry once
		print(f"[WARNING] WebDriver creation failed ({exc}). Retrying after emergency lock purge...")
		kill_orphaned_drivers(target_data_dir)
		cleanup_stale_locks(target_data_dir)
		time.sleep(1.0)
		driver = webdriver.Edge(options=options)
		apply_stealth_cdp_hooks(driver)

	return driver


class BrowserManager:
	"""Context manager for reliable browser lifecycle, signal handling, and clean teardown."""

	def __init__(
		self,
		user_data_dir: Optional[str] = None,
		profile_name: Optional[str] = None,
		headless: bool = False,
		mobile: bool = False,
	):
		self.user_data_dir = os.path.abspath(user_data_dir or USER_DATA_DIR)
		self.profile_name = profile_name or PROFILE_NAME
		self.headless = headless
		self.mobile = mobile
		self.driver: Optional[webdriver.Edge] = None
		self._original_sigint = None
		self._original_sigterm = None
		self._cleaned_up = False

	def __enter__(self) -> webdriver.Edge:
		self._setup_signal_handlers()
		self.driver = create_stealth_driver(
			user_data_dir=self.user_data_dir,
			profile_name=self.profile_name,
			headless=self.headless,
			mobile=self.mobile,
		)
		atexit.register(self.cleanup)
		return self.driver

	def __exit__(self, exc_type, exc_val, exc_tb):
		self.cleanup()
		self._restore_signal_handlers()
		return False

	def _setup_signal_handlers(self):
		def handle_signal(signum, frame):
			sig_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
			print(f"\n[INFO] Intercepted {sig_name}. Gracefully terminating browser...")
			self.cleanup()
			sys.exit(130 if signum == signal.SIGINT else 143)

		try:
			self._original_sigint = signal.signal(signal.SIGINT, handle_signal)
			self._original_sigterm = signal.signal(signal.SIGTERM, handle_signal)
		except (ValueError, AttributeError):
			# Not in main thread or platform does not support signal
			pass

	def _restore_signal_handlers(self):
		try:
			if self._original_sigint is not None:
				signal.signal(signal.SIGINT, self._original_sigint)
			if self._original_sigterm is not None:
				signal.signal(signal.SIGTERM, self._original_sigterm)
		except (ValueError, AttributeError):
			pass

	def cleanup(self):
		if self._cleaned_up:
			return
		self._cleaned_up = True

		if self.driver:
			try:
				print("[INFO] Closing browser session...")
				self.driver.quit()
			except Exception as exc:
				print(f"[DEBUG] Error during driver.quit(): {exc}")
			finally:
				self.driver = None

		# Post-session lock cleanup
		cleanup_stale_locks(self.user_data_dir)
