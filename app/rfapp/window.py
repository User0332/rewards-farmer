import ctypes
from datetime import datetime, timedelta, timezone
from ctypes import wintypes
import json
import logging
import os
import threading
import time

from file_replace import read_file
from rfapp import paths
from rfapp.bridge import Bridge

_log = logging.getLogger("rfapp.window")


def _on_ui(window, action):
	if window.native.InvokeRequired:
		from System import Action
		window.native.Invoke(Action(action))
	else:
		action()


class _WebViewSleep:
	def __init__(self, window, invoke=None, continue_task=None, continue_script=None):
		self._window = window
		self._invoke = invoke or (lambda action: _on_ui(window, action))
		self._continue_task = continue_task or self._continue
		self._continue_script = continue_script or self._script_continuation
		self._generation = 0
		self._hidden = False
		self.state = "awake"
		self._page_ready = False
		self._ready_checks = 0
		self._ready_generation = 0

	@staticmethod
	def _continue(task, callback):
		from System import Action, Boolean
		from System.Threading.Tasks import Task, TaskScheduler
		task.ContinueWith(Action[Task[Boolean]](callback), TaskScheduler.FromCurrentSynchronizationContext())

	@staticmethod
	def _script_continuation(task, callback):
		from System import Action, String
		from System.Threading.Tasks import Task, TaskScheduler
		task.ContinueWith(Action[Task[String]](callback), TaskScheduler.FromCurrentSynchronizationContext())

	def suspend_after_ready(self):
		if self._page_ready:
			self.suspend()
			return
		self._hidden = True
		self._ready_generation += 1
		generation = self._ready_generation
		self.state = "checking_ready"
		def check():
			try:
				core = self._window.native.webview.CoreWebView2
				self._ready_checks += 1
				self.state = "checking_ready"
				def completed(task):
					try:
						if not self._hidden or generation != self._ready_generation:
							return
						if json.loads(task.Result) is True:
							self._page_ready = True
							self.suspend()
						elif self._ready_checks < 20:
							self.state = "awaiting_ready"
						else:
							self.state = "failed"
							_log.warning("Hidden page never became ready; suspension left disabled")
					except Exception:
						self.state = "failed"
						_log.exception("Could not check the initial page readiness")
				self._continue_script(core.ExecuteScriptAsync("document.getElementById('app')?.dataset.ready === 'true'"), completed)
			except Exception:
				self.state = "failed"
				_log.exception("Could not check the initial page readiness")
		self._invoke(check)

	@staticmethod
	def _memory(core, name):
		if hasattr(core, "MemoryUsageTargetLevel"):
			core.MemoryUsageTargetLevel = getattr(type(core.MemoryUsageTargetLevel), name)

	def _visibility(self, visible):
		from System.Reflection import BindingFlags
		control = self._window.native.webview
		control.Visible = visible
		# WinForms 1.0.3856.49 keeps IsVisible true after hiding its parent or control.
		# TrySuspend requires the native controller to be hidden, not just the form.
		field = control.GetType().GetField("_coreWebView2Controller", BindingFlags.Instance | BindingFlags.NonPublic)
		if field is None:
			raise RuntimeError("WebView2 controller visibility is unavailable")
		field.GetValue(control).IsVisible = visible

	def suspend(self):
		self.state = "pending"
		def suspend():
			try:
				if self._window.native.Visible:
					self.state = "awake"
					return
				self._generation += 1
				generation = self._generation
				self._hidden = True
				# Hiding the parent form alone leaves the native controller visible on WinForms.
				self._visibility(False)
				core = self._window.native.webview.CoreWebView2
				self._memory(core, "Low")
				self.state = "pending"
				def completed(task):
					try:
						if generation != self._generation or not self._hidden:
							if not self._hidden:
								core.Resume()
								self._memory(core, "Normal")
							return
						self.state = "suspended" if task.Result else "declined"
						if not task.Result:
							_log.warning("WebView2 declined hidden suspension")
					except Exception:
						self.state = "failed"
						_log.exception("WebView2 suspension did not complete")
				self._continue_task(core.TrySuspendAsync(), completed)
			except Exception:
				self.state = "failed"
				_log.exception("Could not suspend the hidden WebView2")
		self._invoke(suspend)

	def resume(self):
		def resume():
			self._generation += 1
			self._ready_generation += 1
			self._hidden = False
			try:
				core = self._window.native.webview.CoreWebView2
				core.Resume()
				self._memory(core, "Normal")
				self._visibility(True)
				self.state = "awake"
			except Exception:
				self.state = "failed"
				_log.exception("Could not resume WebView2")
		self._invoke(resume)


class _ChangeWaiter:
	"""Wait on OS directory changes, not repeated reads of unchanged run records."""
	def __init__(self, directories):
		self._event = threading.Event()
		self._kernel = None
		self._changes = []
		self._closed = False
		if os.name != "nt" or not directories:
			return
		kernel = ctypes.WinDLL("kernel32", use_last_error=True)
		for name, args, result in (
			("CreateEventW", [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
			("SetEvent", [wintypes.HANDLE], wintypes.BOOL),
			("FindFirstChangeNotificationW", [wintypes.LPCWSTR, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
			("FindNextChangeNotification", [wintypes.HANDLE], wintypes.BOOL),
			("FindCloseChangeNotification", [wintypes.HANDLE], wintypes.BOOL),
			("WaitForMultipleObjects", [wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL, wintypes.DWORD], wintypes.DWORD),
			("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
		):
			function = getattr(kernel, name)
			function.argtypes, function.restype = args, result
		self._kernel = kernel
		self._wake = kernel.CreateEventW(None, False, False, None)
		if not self._wake:
			raise ctypes.WinError(ctypes.get_last_error())
		try:
			for directory in dict.fromkeys(str(path) for path in directories):
				handle = kernel.FindFirstChangeNotificationW(directory, False, 0x01 | 0x02 | 0x08 | 0x10)
				if not handle or handle == ctypes.c_void_p(-1).value:
					raise ctypes.WinError(ctypes.get_last_error())
				self._changes.append(handle)
		except Exception:
			self.close()
			raise

	def wake(self):
		if self._kernel:
			self._kernel.SetEvent(self._wake)
		else:
			self._event.set()

	def wait(self, timeout):
		if not self._kernel:
			changed = self._event.wait(min(timeout, 1))
			self._event.clear()
			return changed
		handles = (wintypes.HANDLE * (len(self._changes) + 1))(self._wake, *self._changes)
		# A lost OS notification must not lose a run-end toast. This watchdog scans metadata only.
		result = self._kernel.WaitForMultipleObjects(len(handles), handles, False, max(1, int(min(timeout, 1) * 1000)))
		if result == 258:
			return False
		if result >= len(handles):
			raise ctypes.WinError(ctypes.get_last_error())
		if result:
			if not self._kernel.FindNextChangeNotification(handles[result]):
				raise ctypes.WinError(ctypes.get_last_error())
			# Fold the related rename/size/write signals from one atomic publish into one scan.
			changes = (wintypes.HANDLE * len(self._changes))(*self._changes)
			for _ in range(32):
				pending = self._kernel.WaitForMultipleObjects(len(changes), changes, False, 20)
				if pending == 258:
					break
				if pending >= len(changes) or not self._kernel.FindNextChangeNotification(changes[pending]):
					raise ctypes.WinError(ctypes.get_last_error())
		return True

	def close(self):
		if self._closed:
			return
		self._closed = True
		if self._kernel:
			for handle in self._changes:
				self._kernel.FindCloseChangeNotification(handle)
			self._kernel.CloseHandle(self._wake)


def _theme():
	try:
		import winreg
		with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
			return "light" if winreg.QueryValueEx(key, "AppsUseLightTheme")[0] else "dark"
	except OSError:
		return "light"


def _reduced_motion():
	value = wintypes.BOOL(True)
	if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(value), 0):
		return not bool(value.value)
	return False


def _create_window(bridge, start_hidden=False, page=None):
	import webview
	p = bridge._module("prefs").load().get("window", {})
	window = webview.create_window(
		title=bridge._module("i18n").current().t("shell.window.title"),
		url=str(page if page is not None else paths.ui_dir() / "index.html"),
		js_api=bridge,
		width=p.get("width", 1100), height=p.get("height", 760),
		x=p.get("x"), y=p.get("y"), maximized=p.get("maximized", False),
		min_size=(860, 600), hidden=start_hidden,
		background_color="#0F0F0F" if _theme() == "dark" else "#FFFFFF",
		text_select=True,
	)
	bridge._window = window
	return window


def _focus_window(window):
	from System import Action
	result = {}
	def focus():
		user = ctypes.WinDLL("user32", use_last_error=True)
		user.SetForegroundWindow.argtypes = [wintypes.HWND]
		user.SetForegroundWindow.restype = wintypes.BOOL
		user.GetForegroundWindow.restype = wintypes.HWND
		user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
		user.GetWindowThreadProcessId.restype = wintypes.DWORD
		user.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
		user.AttachThreadInput.restype = wintypes.BOOL
		kernel = ctypes.WinDLL("kernel32", use_last_error=True)
		kernel.GetCurrentThreadId.restype = wintypes.DWORD
		current = kernel.GetCurrentThreadId()
		foreground = user.GetWindowThreadProcessId(user.GetForegroundWindow(), None)
		# Activation belongs on the UI thread whose input queue owns this window.
		attached = foreground != current and user.AttachThreadInput(current, foreground, True)
		try:
			window.native.Activate()
			window.native.BringToFront()
			result["accepted"] = bool(user.SetForegroundWindow(int(window.native.Handle.ToInt64())))
		finally:
			if attached:
				user.AttachThreadInput(current, foreground, False)
	window.native.Invoke(Action(focus))
	return result.get("accepted", False)


class _Runtime:
	def __init__(self, window, bridge, instance, tray_image=None):
		self._window = window
		self._bridge = bridge
		self._instance = instance
		self._stop = threading.Event()
		self._queue = []
		self._condition = threading.Condition()
		self._quitting = False
		self._threads = []
		self._tail = None
		self._change_waiter = None
		self._visible = not bool(getattr(window, "hidden", False))
		self._web_sleep = _WebViewSleep(window)
		self._tray_dirty = True
		self._signature = None
		self._directory_signature = None
		self._scan_requested = True
		self._runs_path = paths.runs_dir()
		self._clock_deadline = None
		self._signin_files = {}
		self._rewatch = False
		self._idle_active = False
		self._pending_runs = set()
		self._known_records = set()
		self._unseen_records = set()
		self._announced_runs = set()
		self._finished_runs = set()
		self._watched_dirs = None
		self._settings_mtime = None
		self._theme = _theme()
		self._theme_at = 0
		self._overview_at = 0
		self._signin_at = 0
		self._signins = {}
		self._context_lock = threading.Lock()
		self._schedule_status = {}
		self._schedule_settings = None
		from rfapp.tray import Tray
		self._tray = Tray(self.show, self._run_now, self._stop_run, self.quit, image=tray_image)
		self._bridge._push_callback = self.push
		self._bridge._context_callback = self._refresh_context
		self._bridge._schedule_callback = self._cache_schedule
		self._bridge._ready_callback = self._on_ready
		window.events.closing += self._on_closing
		window.events.closed += self._on_closed

	def _on_ready(self):
		with self._condition:
			self._condition.notify_all()
		self._wake_observer()

	def push(self, topic, data):
		if self._stop.is_set():
			return
		data = self._bridge._redact_response(data)
		with self._condition:
			if topic in ("run.started", "run.finished"):
				seen = self._announced_runs if topic == "run.started" else self._finished_runs
				if data["run_id"] in seen:
					return
				seen.add(data["run_id"])
			if topic in ("overview.changed", "settings.changed", "theme.changed"):
				self._queue = [item for item in self._queue if item["topic"] != topic]
			if not self._visible and topic == "run.snapshot":
				self._queue = [item for item in self._queue if item["topic"] != topic or item["data"]["run_id"] != data["run_id"]]
			self._queue.append({"topic": topic, "data": data})
			if not self._visible and topic == "run.log":
				# The page joins a gap through get_run_log, so hidden runs need not retain unbounded text.
				logs = [item for item in self._queue if item["topic"] == "run.log"]
				while sum(len(item["data"]["text"].encode("utf-8")) for item in logs) > 65536 and len(logs) > 1:
					self._queue.remove(logs.pop(0))
			self._condition.notify()
		if topic == "run.started":
			self._pending_runs.add(data["run_id"])
		if topic == "run.finished":
			self._pending_runs.discard(data["run_id"])
			self._tray._notification_run = data["run_id"]
			def notify():
				from rfapp.notify import _deliver
				try:
					_deliver(data["run_id"], self._bridge._module("settings").load(), window=True)
				except Exception:
					_log.exception("Window completion notification failed")
			thread = threading.Thread(target=notify, name="rfapp-notify", daemon=True)
			self._threads.append(thread)
			thread.start()
		if topic in ("run.started", "run.finished", "overview.changed", "settings.changed", "signin.status"):
			self._tray_dirty = True

	def _wake_observer(self):
		self._tray_dirty = True
		if self._change_waiter:
			self._change_waiter.wake()

	def _refresh_context(self):
		with self._context_lock:
			p = self._bridge._module("prefs").load()
			if self._tail:
				self._tail.set_context(p.get("account_labels", {}), str(self._bridge._module("settings").user_data_dir()))
			self._tray.refresh()
		self._wake_observer()

	def _ensure_schedule(self):
		with self._bridge._settings_lock:
			status = self._bridge._module("schedule").ensure()
		self._cache_schedule(status)

	def _cache_schedule(self, status=None):
		if status is None:
			status = self._bridge._module("schedule").status()
		config = self._bridge._module("schedule").from_settings(self._bridge._module("settings").load())
		with self._bridge._lock:
			self._schedule_status = dict(status)
			self._schedule_settings = config
		self._wake_observer()

	def _advance_schedule(self, now):
		with self._bridge._lock:
			config = self._schedule_settings
			status = dict(self._schedule_status)
		if config is None:
			return
		if status.get("next_at") and datetime.fromisoformat(status["next_at"].replace("Z", "+00:00")) > now:
			return
		at = self._bridge._module("schedule").next_check(config, now) if config.mode != "manual" and config.active_accounts else None
		next_at = at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if at else None
		if status.get("next_at") != next_at:
			with self._bridge._lock:
				if self._schedule_settings is not config:
					return
				self._schedule_status["next_at"] = next_at
			self.push("overview.changed", {})
			self._tray_dirty = True

	def _settle_unseen(self, record_ids):
		self._unseen_records.update(record_ids - self._known_records)
		self._known_records = record_ids
		for run_id in tuple(self._unseen_records):
			if run_id in self._announced_runs or run_id not in record_ids:
				self._unseen_records.discard(run_id)
				continue
			module = self._bridge._module("runs")
			detail = module.load_run(run_id, self._bridge._module("prefs").load().get("account_labels", {}), str(self._bridge._module("settings").user_data_dir()))
			if detail.get("ended_at"):
				# A complete short run can fit between two lock observations, even with a healthy watcher.
				self.push("run.started", detail)
				self.push("run.finished", module._summary(detail))
				self.push("overview.changed", {})
				self._unseen_records.discard(run_id)

	def _watch_dirs(self):
		root = self._bridge._module("settings").user_data_dir()
		while not root.is_dir() and root != root.parent:
			root = root.parent
		return tuple(dict.fromkeys((paths.data_home(), paths.runs_dir(), root)))

	def _new_waiter(self):
		directories = self._watch_dirs()
		waiter = _ChangeWaiter(directories)
		self._watched_dirs = directories
		return waiter

	def _tray_overview(self):
		settings = self._bridge._module("settings")
		result = self._bridge._module("runs").overview(settings.load(), self._bridge._module("prefs").load(), datetime.now(timezone.utc))
		with self._bridge._lock:
			result["schedule"] = dict(self._schedule_status)
		info = self._bridge._module("run_lock").status(directory=str(settings.user_data_dir()))
		if result.get("active_run"):
			result["active_run"] = {**result["active_run"], "supervisor_pid": info.supervisor_pid if info and info.run_id == result["active_run"]["run_id"] else None}
		return result

	def _send(self):
		while not self._stop.is_set():
			with self._condition:
				self._condition.wait_for(lambda: self._stop.is_set() or (self._visible and self._bridge._ready.is_set() and self._queue))
				if self._stop.is_set():
					return
				self._condition.wait(.25)
				if not self._visible:
					continue
				batch, self._queue = self._queue, []
			try:
				self._dispatch_batch(batch)
			except Exception:
				if not self._stop.is_set():
					_log.exception("Push batch failed")

	def _dispatch_batch(self, batch):
		def send():
			if self._stop.is_set():
				return
			with self._condition:
				if not self._visible:
					self._queue = batch + self._queue
					return
			# Do not hold a Python lock while Invoke waits for the UI thread, or while JS calls back.
			self._window.native.webview.CoreWebView2.ExecuteScriptAsync("window.rf && window.rf.receive(" + json.dumps(batch, ensure_ascii=True) + ")")
		_on_ui(self._window, send)

	@staticmethod
	def _stamp(path):
		try:
			value = path.stat()
			return value.st_mtime_ns, value.st_size
		except FileNotFoundError:
			return None

	def _observe_once(self):
		now = datetime.now().astimezone()
		self._advance_schedule(now)
		runs = self._runs_path
		env_stamp = self._stamp(paths.env_path())
		prefs_stamp = self._stamp(paths.prefs_path())
		known_signins = self._signature[3] if self._signature else ()
		directory_signature = (env_stamp, prefs_stamp, self._stamp(runs), tuple((path, self._stamp(path)) for path, stamp in known_signins))
		if self._scan_requested or self._idle_active or self._pending_runs or directory_signature != self._directory_signature:
			signins, records = {}, []
			# File creation changes the directory stamp even if its OS notification was missed.
			# Existing active files are scanned at .5s; idle history only on an actual signal/change.
			with os.scandir(runs) as entries:
				for entry in entries:
					if not (entry.name.endswith(".jsonl") or entry.name.startswith("signin-") and entry.name.endswith(".json")):
						continue
					try:
						value = entry.stat()
						stamp = value.st_mtime_ns, value.st_size
						if entry.name.endswith(".jsonl"):
							records.append((entry.name, stamp))
						else:
							signins[runs / entry.name] = stamp
					except FileNotFoundError:
						continue
			records = tuple(sorted(records))
			signins = dict(sorted(signins.items()))
			self._directory_signature = directory_signature
			self._scan_requested = False
		else:
			records = self._signature[2]
			signins = dict(known_signins)
		signature = (env_stamp, prefs_stamp, records, tuple(signins.items()), now.date())
		day_changed = self._signature is not None and signature[-1] != self._signature[-1]
		clock_changed = self._clock_deadline is not None and now >= self._clock_deadline
		changed = signature != self._signature
		self._signature = signature
		if changed or clock_changed:
			future = []
			for name, stamp in records:
				for line in read_file(runs / name).splitlines():
					try:
						at = datetime.fromisoformat(json.loads(line)["ts"].replace("Z", "+00:00"))
						if at > now:
							future.append(at)
					except (ValueError, KeyError, TypeError):
						continue
			self._clock_deadline = min(future, default=None)
			if clock_changed:
				changed = True
				self.push("overview.changed", {})
		if (changed or self._tray_dirty) and self._watched_dirs is not None and self._watched_dirs != self._watch_dirs():
			self._rewatch = True
		# An app.log write or duplicate atomic-rename signal is not a reason to reload all history.
		if changed or self._tray_dirty or self._idle_active or self._pending_runs:
			self._tail.poll()
			self._settle_unseen({name[:-6] for name, stamp in records})
		if day_changed:
			self.push("overview.changed", {})
		mtime = env_stamp
		if mtime != self._settings_mtime:
			self._settings_mtime = mtime
			self.push("settings.changed", {})
			self.push("overview.changed", {})
			self._ensure_schedule()
			self._refresh_context()
		if self._visible:
			theme = _theme()
			if theme != self._theme:
				self._theme = theme
				self.push("theme.changed", {"theme": theme})
				self._refresh_context()
		for status_file, stamp in signins.items():
			if self._signin_files.get(status_file) == stamp:
				continue
			account_id = json.loads(read_file(status_file))["account_id"]
			status = self._bridge._module("accounts_admin").get_signin_status(account_id)
			if self._signins.get(account_id) != status:
				self._signins[account_id] = status
				self.push("signin.status", status)
				self.push("overview.changed", {})
				self._refresh_context()
			if status["state"] not in ("starting", "waiting", "verifying"):
				self._signin_files[status_file] = self._stamp(status_file)
		self._signin_files = {path: stamp for path, stamp in self._signin_files.items() if path in signins}
		live_signin_ids = {status_file.stem[len("signin-"):] for status_file in signins}
		self._signins = {account: status for account, status in self._signins.items() if account.replace("@", "") in live_signin_ids}
		active = bool(self._bridge._module("runs").active_run_id()) if changed or self._tray_dirty or self._idle_active else False
		signin_active = any(status["state"] in ("starting", "waiting", "verifying") for status in self._signins.values())
		self._idle_active = active or signin_active or bool(self._pending_runs)
		if changed or self._tray_dirty:
			self._tray_dirty = False
			self._tray.update(self._tray_overview())
		if not self._visible and self._bridge._ready.is_set() and self._web_sleep.state in ("awake", "awaiting_ready"):
			self._web_sleep.suspend_after_ready()
		# Pure schedule projection advances even when a trigger was skipped and produced no file.
		if active or signin_active or self._pending_runs or self._web_sleep.state in ("checking_ready", "awaiting_ready"):
			return .5
		if self._visible:
			return 5
		midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time()).astimezone()
		deadlines = [midnight]
		if self._clock_deadline:
			deadlines.append(self._clock_deadline)
		next_at = self._schedule_status.get("next_at")
		if next_at:
			deadlines.append(datetime.fromisoformat(next_at.replace("Z", "+00:00")))
		return max(.001, min((deadline - now).total_seconds() for deadline in deadlines))

	def _observe(self):
		try:
			while not self._stop.is_set():
				try:
					timeout = self._observe_once()
					if self._rewatch:
						self._rewatch = False
						old = self._change_waiter
						self._change_waiter = self._new_waiter()
						old.close()
						self._change_waiter.wake()
				except Exception:
					_log.exception("Window state observation failed")
					self._tray_dirty = True
					# The error log itself changes a watched directory; do not let that create a hot loop.
					if self._stop.wait(5):
						return
					continue
				try:
					self._scan_requested = self._change_waiter.wait(timeout)
				except OSError:
					_log.exception("Directory watcher failed; retrying with bounded fallback")
					self._change_waiter.close()
					self._change_waiter = _ChangeWaiter(())
					self._rewatch = True
		finally:
			self._change_waiter.close()

	def start(self, on_started=None):
		self._instance.listen(self.show)
		try:
			self._ensure_schedule()
			p = self._bridge._module("prefs").load()
			self._tail = self._bridge._module("runs").LiveTail(self.push, p.get("account_labels", {}), str(self._bridge._module("settings").user_data_dir()))
			self._known_records = {path.stem for path in paths.runs_dir().glob("*.jsonl")}
			self._settings_mtime = self._stamp(paths.env_path())
			try:
				self._change_waiter = self._new_waiter()
			except OSError:
				_log.exception("Directory change watcher unavailable; using bounded fallback")
				self._change_waiter = _ChangeWaiter(())
			for target, name in ((self._send, "rfapp-push"), (self._observe, "rfapp-observe"), (self._updates, "rfapp-updates")):
				thread = threading.Thread(target=target, name=name, daemon=True)
				self._threads.append(thread)
				thread.start()
			if on_started:
				on_started(self._window, self)
		except Exception:
			_log.exception("Window runtime failed to start")
			self.quit()

	def _updates(self):
		while not self._stop.is_set():
			result = self._bridge.check_updates({})
			if not result["ok"]:
				_log.warning("Daily update check failed: %s", result["error"]["code"])
			if self._stop.wait(24 * 60 * 60):
				break

	def _save_window(self):
		try:
			native = self._window.native
			maximized = str(native.WindowState) == "Maximized"
			self._bridge._module("prefs").save({"window": {"width": self._window.width, "height": self._window.height, "x": self._window.x, "y": self._window.y, "maximized": maximized}})
		except Exception:
			_log.exception("Could not save window geometry")

	def _on_closing(self):
		self._save_window()
		p = self._bridge._module("prefs").load()
		if p.get("close_to_tray", True) and not self._quitting:
			with self._condition:
				self._visible = False
				self._condition.notify_all()
			self._window.hide()
			self._web_sleep.suspend_after_ready()
			self._wake_observer()
			if not p.get("tray_hint_shown"):
				from rfapp import notify
				t = self._bridge._module("i18n").current()
				notify.toast(t.t("shell.window.title"), t.t("tray.notify.closed_to_tray"))
				self._bridge._module("prefs").save({"tray_hint_shown": True})
			return False
		self._quitting = True
		self._cleanup()
		return True

	def _on_closed(self):
		self._cleanup()

	def _cleanup(self):
		if self._stop.is_set():
			return
		self._stop.set()
		if self._change_waiter:
			self._change_waiter.wake()
		with self._condition:
			self._condition.notify_all()
		self._bridge._close()
		self._tray.stop()
		self._instance.close()

	def show(self, route=None):
		self._web_sleep.resume()
		with self._condition:
			self._visible = True
			self._condition.notify_all()
		self._window.restore()
		# WinForms show() activates the native form, including a hidden tray window.
		self._window.show()
		if not _focus_window(self._window):
			_log.warning("Windows declined foreground activation")
		self._tray.opened()
		self._wake_observer()
		if route:
			self.push("navigate", {"route": route})

	def _run_now(self):
		result = self._bridge.start_run({})
		self.show("#/live")
		if not result["ok"]:
			_log.warning("Tray run request failed: %s", result["error"]["code"])

	def _stop_run(self):
		run_id = self._bridge._module("runs").active_run_id()
		if run_id:
			self._bridge.stop_run({"run_id": run_id})

	def quit(self):
		if self._quitting:
			return
		self._quitting = True
		self._save_window()
		self._cleanup()
		self._window.destroy()


def run(start_hidden=False, page=None, dependencies=None, tray_image=None, on_started=None):
	from rfapp.single_instance import SingleInstance
	instance = SingleInstance(start_hidden=start_hidden)
	if not instance.acquire():
		return
	runtime = None
	try:
		bridge = Bridge(dependencies=dependencies)
		window = _create_window(bridge, start_hidden, page)
		runtime = _Runtime(window, bridge, instance, tray_image=tray_image)
		runtime._tray.start()
		import webview
		webview.start(runtime.start, args=(on_started,), gui="edgechromium", http_server=True, private_mode=True, debug=os.environ.get("REWARDS_APP_DEBUG") == "1")
	finally:
		if runtime:
			runtime._cleanup()
			for thread in runtime._threads:
				if thread is not threading.current_thread():
					thread.join(timeout=12)
		instance.close()
