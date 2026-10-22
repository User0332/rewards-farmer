import _bootstrap  # noqa: F401

from pathlib import Path
import tempfile
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from datetime import datetime as RealDatetime, time as DayTime, timedelta, timezone

from rfapp.bridge import Bridge
from rfapp import window


class RuntimeTests(unittest.TestCase):
	def setUp(self):
		for target in ("subprocess.Popen", "subprocess.run", "requests.sessions.Session.request"):
			guard = patch(target, side_effect=AssertionError("Unpatched external effect in runtime test"))
			guard.start()
			self.addCleanup(guard.stop)
		guard = patch.object(window.ctypes, "WinDLL", side_effect=AssertionError("Unpatched native API in runtime test"), create=True)
		guard.start()
		self.addCleanup(guard.stop)
		clock = Mock(wraps=RealDatetime)
		clock.now.return_value = RealDatetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
		guard = patch.object(window, "datetime", clock)
		guard.start()
		self.addCleanup(guard.stop)
		self.folder = tempfile.TemporaryDirectory()
		self.addCleanup(self.folder.cleanup)
		self.home = Path(self.folder.name)
		self.status = {"mode": "manual", "next_at": None, "next_is_check": False}
		self.schedule = SimpleNamespace(status=Mock(return_value=self.status), ensure=Mock(return_value=self.status), replace_legacy_task=Mock(return_value=self.status), from_settings=Mock(return_value=SimpleNamespace(mode="manual", active_accounts=1)), next_check=Mock(return_value=None))
		self.settings = SimpleNamespace(load=Mock(return_value={}), user_data_dir=lambda: self.home, validate=Mock(return_value={}), save=Mock(return_value={"values": {}, "schedule": self.status, "warnings": []}))
		self.runs = SimpleNamespace(overview=Mock(side_effect=lambda *args: {"accounts": [], "active_run": None}), LiveTail=Mock(), active_run_id=Mock(return_value=None))
		self.lock = SimpleNamespace(status=Mock(return_value=None))
		self.api = Bridge(dependencies={"settings": self.settings, "prefs": SimpleNamespace(load=lambda: {}), "runs": self.runs, "schedule": self.schedule, "run_lock": self.lock})
		self.runtime = window._Runtime.__new__(window._Runtime)
		self.runtime._bridge = self.api
		self.runtime._context_lock = threading.Lock()
		self.runtime._schedule_status = {}
		self.runtime._schedule_settings = None
		self.runtime._tray = Mock()
		self.runtime._tail = Mock()
		self.runtime._theme = "light"
		self.runtime._theme_at = self.runtime._overview_at = self.runtime._signin_at = 0
		self.runtime._signins = {}
		self.runtime._settings_mtime = None
		self.runtime._signature = None
		self.runtime._directory_signature = None
		self.runtime._scan_requested = True
		self.runtime._runs_path = self.home
		self.runtime._clock_deadline = None
		self.runtime._signin_files = {}
		self.runtime._visible = False
		self.runtime._tray_dirty = False
		self.runtime._change_waiter = Mock()
		self.runtime._web_sleep = Mock(state="awake")
		self.runtime._rewatch = False
		self.runtime._pending_runs = set()
		self.runtime._known_records = set()
		self.runtime._unseen_records = set()
		self.runtime._announced_runs = set()
		self.runtime._finished_runs = set()
		self.runtime._watched_dirs = None
		self.runtime._idle_active = False
		self.runtime.push = Mock()
		self.api._schedule_callback = self.runtime._cache_schedule
		self.patch = patch.object(window, "paths", SimpleNamespace(env_path=lambda: self.home / ".env", prefs_path=lambda: self.home / "app-prefs.json", runs_dir=lambda: self.home))
		self.patch.start()
		self.addCleanup(self.patch.stop)

	def test_observer_ticks_never_query_scheduler_and_use_cached_status(self):
		self.runtime._cache_schedule(self.schedule.ensure())
		with patch.object(window, "_theme", return_value="light"):
			for _ in range(12):
				self.runtime._observe_once()
		self.schedule.status.assert_not_called()
		self.schedule.ensure.assert_called_once()
		self.assertEqual(self.runs.overview.call_count, 1)
		self.assertEqual(self.runtime._tail.poll.call_count, 1)
		for call in self.runtime._tray.update.call_args_list:
			self.assertEqual(call.args[0]["schedule"], self.status)

	def test_env_mtime_change_refreshes_cache_only_once(self):
		(self.home / ".env").write_text("REWARDS_SCHEDULE_MODE=manual\n", encoding="utf-8")
		with patch.object(window, "_theme", return_value="light"):
			self.runtime._observe_once()
			self.runtime._observe_once()
		self.schedule.ensure.assert_called_once()
		self.schedule.status.assert_not_called()
		self.assertEqual(self.runtime._schedule_status, self.status)

	def test_save_replace_and_setup_feed_cache_without_extra_status_query(self):
		self.assertTrue(self.api.save_settings({"changes": {}})["ok"])
		self.assertEqual(self.runtime._schedule_status, self.status)
		self.assertTrue(self.api.replace_legacy_task({})["ok"])
		self.assertEqual(self.runtime._schedule_status, self.status)
		self.schedule.status.assert_not_called()

	def test_tray_stop_eligibility_comes_from_matching_live_lock(self):
		self.runs.overview.side_effect = lambda *args: {"active_run": {"run_id": "synthetic"}, "accounts": []}
		for supervisor in (None, 12345):
			self.lock.status.return_value = SimpleNamespace(run_id="synthetic", supervisor_pid=supervisor)
			self.assertEqual(self.runtime._tray_overview()["active_run"]["supervisor_pid"], supervisor)
		self.lock.status.return_value = SimpleNamespace(run_id="different", supervisor_pid=12345)
		self.assertIsNone(self.runtime._tray_overview()["active_run"]["supervisor_pid"])

	def test_context_refresh_serializes_without_holding_bridge_state_lock(self):
		entered, second_called, release, second_entered = (threading.Event() for _ in range(4))
		calls = []
		def refresh():
			calls.append(threading.current_thread().name)
			if len(calls) == 1:
				entered.set()
				if not release.wait(3):
					raise AssertionError("Context refresh was not released")
			else:
				second_entered.set()
		self.runtime._tray.refresh.side_effect = refresh
		first = threading.Thread(target=self.runtime._refresh_context)
		def second_call():
			second_called.set()
			self.runtime._refresh_context()
		second = threading.Thread(target=second_call)
		first.start()
		try:
			self.assertTrue(entered.wait(1))
			second.start()
			self.assertTrue(second_called.wait(1))
			self.assertFalse(second_entered.wait(.15))
			self.assertTrue(self.api._lock.acquire(timeout=1))
			self.api._lock.release()
		finally:
			release.set()
			first.join(4)
			if second.ident:
				second.join(4)
		self.assertFalse(first.is_alive())
		self.assertFalse(second.is_alive())
		self.assertEqual(len(calls), 2)

	def test_hidden_idle_waits_until_midnight_without_reducing_unchanged_records(self):
		clock = Mock(wraps=RealDatetime)
		clock.now.return_value = RealDatetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
		with patch.object(window, "datetime", clock), patch.object(window, "_theme") as theme:
			first = self.runtime._observe_once()
			second = self.runtime._observe_once()
		self.assertGreater(first, 60)
		self.assertEqual(first, second)
		self.runs.overview.assert_called_once()
		theme.assert_not_called()

	def test_schedule_boundary_without_run_advances_to_next_day_without_scheduler_query(self):
		from rfapp import schedule
		identity = patch.object(schedule.paths, "task_name", return_value="RewardsFarmerApp_test0000")
		identity.start()
		self.addCleanup(identity.stop)
		before = RealDatetime(2026, 10, 19, 12, 0).astimezone()
		config = schedule.ScheduleSettings("fixed", DayTime(9), DayTime(17), 1, DayTime(12, 1), frozenset(schedule._DAYS), 0, True, True, 1, 1)
		self.runtime._schedule_settings = config
		self.schedule.next_check.side_effect = schedule.next_check
		self.runtime._schedule_status = {"mode": "fixed", "next_at": schedule.next_check(config, before).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")}
		clock = Mock(wraps=RealDatetime)
		clock.now.return_value = before
		with patch.object(window, "datetime", clock):
			self.assertEqual(self.runtime._observe_once(), 60)
			clock.now.return_value = before + timedelta(minutes=1)
			self.runtime._observe_once()
		expected = schedule.next_check(config, before + timedelta(minutes=1)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
		self.assertEqual(self.runtime._schedule_status["next_at"], expected)
		self.assertEqual(self.runtime._tray.update.call_args.args[0]["schedule"]["next_at"], expected)
		self.runtime.push.assert_any_call("overview.changed", {})
		self.schedule.status.assert_not_called()
		self.schedule.ensure.assert_not_called()

	def test_old_schedule_projection_cannot_overwrite_a_concurrent_settings_refresh(self):
		self.runtime._schedule_settings = SimpleNamespace(mode="automatic", active_accounts=1)
		def next_check(config, now):
			self.runtime._schedule_settings = SimpleNamespace(mode="manual", active_accounts=1)
			self.runtime._schedule_status = {"mode": "manual", "next_at": None}
			return now + timedelta(minutes=1)
		self.schedule.next_check.side_effect = next_check
		self.runtime._advance_schedule(RealDatetime(2026, 10, 19, tzinfo=timezone.utc))
		self.assertEqual(self.runtime._schedule_status, {"mode": "manual", "next_at": None})

	def test_midnight_without_file_writes_refreshes_today_values_and_pushes_overview(self):
		clock = Mock(wraps=RealDatetime)
		clock.now.return_value = RealDatetime(2026, 10, 19, 23, 59, 59).astimezone()
		with patch.object(window, "datetime", clock):
			self.assertEqual(self.runtime._observe_once(), 1)
			self.runtime.push.reset_mock()
			clock.now.return_value += timedelta(seconds=1)
			self.runtime._observe_once()
		self.assertEqual(self.runs.overview.call_count, 2)
		self.runtime.push.assert_any_call("overview.changed", {})

	def test_real_overview_day_roll_clears_tray_today_arc_tooltip_tasks_and_rolls_chart(self):
		import json
		from rfapp import runs, settings, tray
		from PIL import Image
		before = RealDatetime(2026, 10, 19, 23, 59, 59).astimezone()
		run_id = "20261019T120000Z-aaaa"
		events = [{"type": "run_start", "accounts": ["account-1"], "plan": {}}, {"type": "account_start", "account": "account-1"},
			{"type": "state", "account": "account-1", "phase": "after", "balance": 100, "earned_today": 20, "searches_earned": 20, "searches_max": 30, "daily_set_open": 0, "cards_open": 0, "quests_open": 0, "open_points": 10, "unreadable": None},
			{"type": "task_end", "account": "account-1", "task": "searches", "outcome": "ok", "points_after": 100}, {"type": "run_end", "result": "ok"}]
		(self.home / (run_id + ".jsonl")).write_text("".join(json.dumps({"v": 1, "run": run_id, "ts": before.isoformat(), **event}) + "\n" for event in events), encoding="utf-8")
		self.runs.overview.side_effect = runs.overview
		self.runtime._known_records.add(run_id)
		clock = Mock(wraps=RealDatetime)
		clock.now.return_value = before
		icon = tray.Tray(Mock(), Mock(), Mock(), Mock(), image=Image.new("RGBA", (32, 32), "#000000"))
		icon._accent = "#0078D4"
		translator = SimpleNamespace(t=lambda key, **fields: key, number=str)
		with patch.object(window, "datetime", clock), patch.object(runs, "paths", window.paths), patch.object(runs.run_lock, "status", return_value=None), patch.object(settings, "active_accounts", return_value=["account-1"]), patch.object(settings, "paused_accounts", return_value=[]), patch.object(settings, "user_data_dir", return_value=self.home), patch.object(tray, "_dpi", return_value=96), patch.object(tray.i18n, "current", return_value=translator):
			self.runtime._observe_once()
			old = self.runtime._tray.update.call_args.args[0]
			icon._overview = old
			arc = icon._image().tobytes()
			self.assertTrue(old["accounts"][0]["state_is_today"])
			self.assertEqual(icon._tooltip(), "tray.tooltip.today")
			self.assertEqual(next(task for task in old["accounts"][0]["tasks_today"] if task["task"] == "searches")["status"], "ok")
			clock.now.return_value += timedelta(seconds=1)
			self.runtime._observe_once()
			new = self.runtime._tray.update.call_args.args[0]
			icon._overview = new
			self.assertFalse(new["accounts"][0]["state_is_today"])
			self.assertNotEqual(arc, icon._image().tobytes())
			self.assertEqual(icon._tooltip(), "tray.tooltip.idle")
			self.assertTrue(all(task["status"] == "none" for task in new["accounts"][0]["tasks_today"]))
			self.assertEqual(new["accounts"][0]["chart"][-1]["date"], (before + timedelta(days=1)).date().isoformat())

	def test_complete_short_run_between_observations_still_emits_one_start_and_finish(self):
		run_id = "20261019T120000Z-aaaa"
		detail = {"run_id": run_id, "ended_at": "2026-10-19T12:00:01Z", "accounts": []}
		self.runs.load_run = Mock(return_value=detail)
		self.runs._summary = Mock(return_value=detail)
		self.runtime._settle_unseen({run_id})
		self.runtime._settle_unseen({run_id})
		self.runtime.push.assert_any_call("run.started", detail)
		self.runtime.push.assert_any_call("run.finished", detail)
		self.assertEqual(self.runtime.push.call_count, 3)

	def test_future_event_timestamp_has_a_deadline_without_a_later_file_write(self):
		before = RealDatetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
		(self.home / "20261019T120000Z-aaaa.jsonl").write_text('{"type":"state","ts":"2026-10-19T12:00:10Z"}\n', encoding="utf-8")
		self.runtime._known_records.add("20261019T120000Z-aaaa")
		clock = Mock(wraps=RealDatetime)
		clock.now.return_value = before
		with patch.object(window, "datetime", clock):
			self.assertEqual(self.runtime._observe_once(), 10)
			clock.now.return_value += timedelta(seconds=10)
			self.runtime._observe_once()
		self.assertEqual(self.runs.overview.call_count, 2)
		self.assertIsNone(self.runtime._clock_deadline)
		self.runtime.push.assert_any_call("overview.changed", {})

	def test_runtime_never_holds_its_python_locks_across_ui_invoke(self):
		self.runtime._stop = threading.Event()
		self.runtime._condition = threading.Condition()
		self.runtime._queue = []
		self.runtime._window = Mock()
		self.runtime._visible = True
		def invoke(win, action):
			self.assertFalse(self.runtime._condition._is_owned())
			self.assertFalse(self.api._lock._is_owned())
			self.assertFalse(self.api._settings_lock._is_owned())
			action()
		with patch.object(window, "_on_ui", side_effect=invoke):
			self.runtime._dispatch_batch([{"topic": "overview.changed", "data": {}}])

	def test_record_write_invalidates_tray_but_app_log_noise_does_not(self):
		self.runtime._observe_once()
		(self.home / "app.log").write_text("Synthetic unrelated logger output", encoding="utf-8")
		self.runtime._observe_once()
		self.assertEqual(self.runs.overview.call_count, 1)
		self.assertEqual(self.runtime._tail.poll.call_count, 1)
		(self.home / "20261019T120000Z-aaaa.jsonl").write_text('{"type":"run_end"}\n', encoding="utf-8")
		# A real directory notification requests this scan even when a filesystem stamp is coarse.
		self.runtime._scan_requested = True
		self.runs.load_run = Mock(return_value={"ended_at": None})
		self.runtime._observe_once()
		self.assertEqual(self.runs.overview.call_count, 2)

	def test_idle_watchdog_checks_directory_and_known_signins_without_scanning_history(self):
		self.runtime._observe_once()
		with patch.object(window.os, "scandir", side_effect=AssertionError("Unchanged history was rescanned")):
			for _ in range(60):
				self.runtime._observe_once()
		self.runs.overview.assert_called_once()
		self.runtime._scan_requested = True
		with patch.object(window.os, "scandir", wraps=window.os.scandir) as scan:
			self.runtime._observe_once()
			scan.assert_called_once_with(self.home)

	def test_fast_tail_interval_continues_until_run_finish_settlement(self):
		self.runtime._pending_runs.add("synthetic")
		self.assertEqual(self.runtime._observe_once(), .5)
		self.runtime._pending_runs.clear()
		self.runtime._idle_active = False
		self.assertGreater(self.runtime._observe_once(), 60)

	def test_hidden_started_window_requests_suspend_only_after_bridge_ready(self):
		self.runtime._observe_once()
		self.runtime._web_sleep.suspend_after_ready.assert_not_called()
		self.api._ready.set()
		self.runtime._observe_once()
		self.runtime._web_sleep.suspend_after_ready.assert_called_once()

	def test_active_run_and_signin_keep_fast_updates(self):
		self.runs.active_run_id.return_value = "synthetic"
		self.assertEqual(self.runtime._observe_once(), .5)
		self.runs.active_run_id.return_value = None
		(self.home / "signin-account-1.json").write_text('{"account_id":"account-1"}', encoding="utf-8")
		self.api._dependencies["accounts_admin"] = SimpleNamespace(get_signin_status=Mock(return_value={"account_id": "account-1", "state": "waiting"}))
		self.runtime._signins = {"account-1": {"state": "waiting"}}
		self.assertEqual(self.runtime._observe_once(), .5)
		(self.home / "signin-account-1.json").unlink()
		self.runtime._signins = {}
		self.runtime._visible = True
		with patch.object(window, "_theme", return_value="light"):
			self.assertEqual(self.runtime._observe_once(), 5)

	def test_terminal_signin_file_is_read_only_after_its_stamp_changes(self):
		path = self.home / "signin-account-1.json"
		path.write_text('{"account_id":"account-1"}', encoding="utf-8")
		status = {"account_id": "account-1", "state": "signed_in"}
		admin = SimpleNamespace(get_signin_status=Mock(return_value=status))
		self.api._dependencies["accounts_admin"] = admin
		self.runtime._observe_once()
		self.runtime._observe_once()
		admin.get_signin_status.assert_called_once_with("account-1")

	def test_observer_uses_one_os_wait_not_a_half_second_poll_loop(self):
		self.runtime._stop = Mock()
		self.runtime._stop.is_set.side_effect = [False, True]
		self.runtime._observe_once = Mock(return_value=3600)
		self.runtime._observe()
		self.runtime._change_waiter.wait.assert_called_once_with(3600)
		self.runtime._change_waiter.close.assert_called_once()

	def test_error_log_change_cannot_turn_observer_failure_into_a_hot_loop(self):
		self.runtime._stop = Mock()
		self.runtime._stop.is_set.return_value = False
		self.runtime._stop.wait.return_value = True
		self.runtime._observe_once = Mock(side_effect=OSError("Synthetic read race"))
		self.runtime._observe()
		self.runtime._stop.wait.assert_called_once_with(5)
		self.runtime._change_waiter.wait.assert_not_called()
		self.runtime._change_waiter.close.assert_called_once()

	def test_hidden_sender_requeues_without_executing_script(self):
		self.runtime._stop = threading.Event()
		self.runtime._condition = threading.Condition()
		self.runtime._queue = []
		self.runtime._window = Mock()
		batch = [{"topic": "overview.changed", "data": {}}]
		with patch.object(window, "_on_ui", side_effect=lambda win, action: action()):
			self.runtime._dispatch_batch(batch)
		self.runtime._window.native.webview.CoreWebView2.ExecuteScriptAsync.assert_not_called()
		self.assertEqual(self.runtime._queue, batch)
		self.runtime._visible = True
		with patch.object(window, "_on_ui", side_effect=lambda win, action: action()):
			self.runtime._dispatch_batch(batch)
		self.runtime._window.native.webview.CoreWebView2.ExecuteScriptAsync.assert_called_once()

	def test_hidden_log_backlog_is_bounded_and_invalidations_coalesce(self):
		self.runtime._stop = threading.Event()
		self.runtime._condition = threading.Condition()
		self.runtime._queue = []
		self.api._redact_response = lambda data: data
		for offset in range(4):
			window._Runtime.push(self.runtime, "run.log", {"run_id": "synthetic", "text": "a" * 32768, "offset": offset * 32768, "next_offset": (offset + 1) * 32768})
		for _ in range(5):
			window._Runtime.push(self.runtime, "overview.changed", {})
		logs = [item for item in self.runtime._queue if item["topic"] == "run.log"]
		self.assertLessEqual(sum(len(item["data"]["text"]) for item in logs), 65536)
		self.assertEqual(logs[0]["data"]["offset"], 65536)
		self.assertEqual(sum(item["topic"] == "overview.changed" for item in self.runtime._queue), 1)

	def test_tray_show_resumes_before_restore_show_and_focus(self):
		events = []
		self.runtime._condition = threading.Condition()
		self.runtime._window = Mock()
		self.runtime._web_sleep.resume.side_effect = lambda: events.append("resume")
		self.runtime._window.restore.side_effect = lambda: events.append("restore")
		self.runtime._window.show.side_effect = lambda: events.append("show")
		with patch.object(window, "_focus_window", side_effect=lambda win: events.append("focus") or True):
			self.runtime.show()
		self.assertEqual(events, ["resume", "restore", "show", "focus"])
		self.assertTrue(self.runtime._visible)

	def test_close_to_tray_hides_before_async_suspend_and_keeps_runtime_alive(self):
		self.runtime._condition = threading.Condition()
		self.runtime._quitting = False
		self.runtime._save_window = Mock()
		self.runtime._window = Mock()
		self.api._dependencies["prefs"] = SimpleNamespace(load=lambda: {"close_to_tray": True, "tray_hint_shown": True})
		events = []
		self.runtime._window.hide.side_effect = lambda: events.append("hide")
		self.runtime._web_sleep.suspend_after_ready.side_effect = lambda: events.append("suspend")
		self.assertFalse(self.runtime._on_closing())
		self.assertEqual(events, ["hide", "suspend"])
		self.assertFalse(self.runtime._visible)
		self.runtime._window.destroy.assert_not_called()


class WebViewSleepTests(unittest.TestCase):
	def setUp(self):
		from enum import Enum
		class Level(Enum):
			Normal = 0
			Low = 1
		self.Level = Level
		self.core = SimpleNamespace(MemoryUsageTargetLevel=Level.Normal, TrySuspendAsync=Mock(return_value="task"), Resume=Mock())
		self.native = SimpleNamespace(Visible=False, webview=SimpleNamespace(CoreWebView2=self.core))
		self.controller = SimpleNamespace(IsVisible=True)
		self.native.webview.GetType = lambda: SimpleNamespace(GetField=lambda *args: SimpleNamespace(GetValue=lambda control: self.controller))
		reflection = patch.dict(sys.modules, {"System.Reflection": SimpleNamespace(BindingFlags=SimpleNamespace(Instance=4, NonPublic=32))})
		reflection.start()
		self.addCleanup(reflection.stop)
		self.window = SimpleNamespace(native=self.native)
		self.invoked = []
		def invoke(action):
			self.invoked.append(action)
			action()
		self.callbacks = []
		self.script_callbacks = []
		self.core.ExecuteScriptAsync = Mock(return_value="script-task")
		self.sleep = window._WebViewSleep(self.window, invoke=invoke, continue_task=lambda task, callback: self.callbacks.append(callback), continue_script=lambda task, callback: self.script_callbacks.append(callback))

	def test_hidden_suspend_is_async_low_memory_and_resume_precedes_show(self):
		self.sleep.suspend()
		self.assertEqual(self.core.MemoryUsageTargetLevel, self.Level.Low)
		self.assertFalse(self.native.webview.Visible)
		self.assertFalse(self.controller.IsVisible)
		self.assertEqual(self.sleep.state, "pending")
		self.core.TrySuspendAsync.assert_called_once()
		self.callbacks.pop()(SimpleNamespace(Result=True))
		self.assertEqual(self.sleep.state, "suspended")
		self.sleep.resume()
		self.core.Resume.assert_called_once()
		self.assertEqual(self.core.MemoryUsageTargetLevel, self.Level.Normal)
		self.assertTrue(self.native.webview.Visible)
		self.assertTrue(self.controller.IsVisible)
		self.assertEqual(self.sleep.state, "awake")
		self.assertEqual(len(self.invoked), 2)

	def test_visible_control_is_never_suspended(self):
		self.native.Visible = True
		self.sleep.suspend()
		self.core.TrySuspendAsync.assert_not_called()

	def test_initial_hidden_suspend_waits_for_ui_ready_response_to_reach_page(self):
		self.sleep.suspend_after_ready()
		self.core.TrySuspendAsync.assert_not_called()
		self.script_callbacks.pop()(SimpleNamespace(Result="false"))
		self.assertEqual(self.sleep.state, "awaiting_ready")
		self.sleep.suspend_after_ready()
		self.script_callbacks.pop()(SimpleNamespace(Result="true"))
		self.core.TrySuspendAsync.assert_called_once()
		self.callbacks.pop()(SimpleNamespace(Result=True))
		self.assertEqual(self.sleep.state, "suspended")

	def test_page_ready_retry_is_bounded_and_old_completion_cannot_suspend_a_reopened_page(self):
		for _ in range(20):
			self.sleep.suspend_after_ready()
			self.script_callbacks.pop()(SimpleNamespace(Result="false"))
		self.assertEqual(self.sleep.state, "failed")
		self.core.TrySuspendAsync.assert_not_called()
		self.sleep.suspend_after_ready()
		self.sleep.resume()
		self.script_callbacks.pop()(SimpleNamespace(Result="true"))
		self.core.TrySuspendAsync.assert_not_called()
		self.assertEqual(self.sleep.state, "awake")

	def test_open_during_pending_suspend_resumes_again_on_completion(self):
		self.sleep.suspend()
		self.sleep.resume()
		self.callbacks.pop()(SimpleNamespace(Result=True))
		self.assertEqual(self.core.Resume.call_count, 2)
		self.assertEqual(self.core.MemoryUsageTargetLevel, self.Level.Normal)

	def test_decline_and_missing_memory_property_have_bounded_failure(self):
		del self.core.MemoryUsageTargetLevel
		self.sleep.suspend()
		self.callbacks.pop()(SimpleNamespace(Result=False))
		self.assertEqual(self.sleep.state, "declined")
		self.sleep.resume()
		self.assertEqual(self.sleep.state, "awake")

	def test_new_hidden_generation_is_not_resumed_by_an_old_completion(self):
		self.sleep.suspend()
		self.sleep.resume()
		self.sleep.suspend()
		self.callbacks[0](SimpleNamespace(Result=True))
		self.assertEqual(self.core.Resume.call_count, 1)
		self.assertEqual(self.core.MemoryUsageTargetLevel, self.Level.Low)

	def test_failed_suspend_task_and_start_can_both_reopen(self):
		class FailedTask:
			@property
			def Result(self):
				raise RuntimeError("Synthetic suspension failure")
		self.sleep.suspend()
		self.callbacks.pop()(FailedTask())
		self.assertEqual(self.sleep.state, "failed")
		self.sleep.resume()
		self.assertEqual(self.sleep.state, "awake")
		self.core.TrySuspendAsync.side_effect = RuntimeError("Synthetic API failure")
		self.sleep.suspend()
		self.assertEqual(self.sleep.state, "failed")
		self.sleep.resume()
		self.assertEqual(self.sleep.state, "awake")

	def test_ui_marshalling_uses_direct_call_on_the_owner_thread(self):
		native = SimpleNamespace(InvokeRequired=False)
		action = Mock()
		window._on_ui(SimpleNamespace(native=native), action)
		action.assert_called_once_with()


class ChangeWaiterTests(unittest.TestCase):
	def test_native_wait_blocks_once_rearms_changed_directory_and_closes_owned_handles(self):
		kernel = Mock()
		kernel.CreateEventW.return_value = 101
		kernel.FindFirstChangeNotificationW.side_effect = [102, 103]
		kernel.WaitForMultipleObjects.side_effect = [2, 258, 258, 0]
		with patch.object(window.os, "name", "nt"), patch.object(window.ctypes, "WinDLL", return_value=kernel, create=True):
			waiter = window._ChangeWaiter(("synthetic-home", "synthetic-runs", "synthetic-home"))
			self.assertTrue(waiter.wait(3600))
			kernel.FindNextChangeNotification.assert_called_once_with(103)
			self.assertFalse(waiter.wait(.5))
			self.assertTrue(waiter.wait(3600))
			self.assertEqual(kernel.WaitForMultipleObjects.call_args_list[0].args[-1], 1000)
			self.assertEqual(kernel.WaitForMultipleObjects.call_args_list[1].args[-1], 20)
			self.assertEqual(kernel.WaitForMultipleObjects.call_args_list[2].args[-1], 500)
			waiter.wake()
			kernel.SetEvent.assert_called_once_with(101)
			waiter.close()
			waiter.close()
		self.assertEqual(kernel.FindFirstChangeNotificationW.call_count, 2)
		self.assertEqual([call.args[0] for call in kernel.FindCloseChangeNotification.call_args_list], [102, 103])
		kernel.CloseHandle.assert_called_once_with(101)

	def test_missing_directory_releases_partial_owned_handles(self):
		kernel = Mock()
		kernel.CreateEventW.return_value = 101
		kernel.FindFirstChangeNotificationW.side_effect = [102, window.ctypes.c_void_p(-1).value]
		with patch.object(window.os, "name", "nt"), patch.object(window.ctypes, "WinDLL", return_value=kernel, create=True), patch.object(window.ctypes, "get_last_error", return_value=3, create=True), patch.object(window.ctypes, "WinError", return_value=OSError("missing synthetic directory"), create=True):
			with self.assertRaises(OSError):
				window._ChangeWaiter(("synthetic-home", "missing"))
		kernel.FindCloseChangeNotification.assert_called_once_with(102)
		kernel.CloseHandle.assert_called_once_with(101)

	def test_no_native_watcher_has_a_bounded_event_fallback(self):
		with patch.object(window.threading, "Event") as constructor:
			waiter = window._ChangeWaiter(())
			waiter.wait(3600)
			constructor.return_value.wait.assert_called_once_with(1)
			waiter.wake()
			constructor.return_value.set.assert_called_once_with()
			waiter.close()
