import _bootstrap  # noqa: F401

import json
import importlib
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from rfapp import notify


class Translator:
	def __init__(self):
		self.messages = json.loads((Path(__file__).resolve().parents[1] / "ui/i18n/en/tray.json").read_text(encoding="utf-8"))["messages"]

	def t(self, key, **params):
		entry = self.messages.get(key)
		if not entry:
			return key
		text = entry.get("text") or entry["forms"]["one" if params[entry["plural"]] == 1 else "other"]
		return text.format(**params)

	def number(self, value):
		return f"{value:,}"


class NotifyTests(unittest.TestCase):
	def setUp(self):
		for target in ("rfapp.notify.subprocess.Popen", "requests.sessions.Session.request"):
			guard = patch(target, side_effect=AssertionError("Unpatched external effect in notification test"))
			guard.start()
			self.addCleanup(guard.stop)
		notify._set_tray(None)
		self.addCleanup(notify._set_tray, None)
		state = patch.object(notify.user_state, "notification_state", return_value=5)
		self.state_reader = state.start()
		self.addCleanup(state.stop)
		self.t = Translator()
		self.summary = {"result": "ok", "points_earned": 15, "accounts": [{"label": "Synthetic", "points_now": 1200, "state_after": {"unreadable": None}}]}
		with patch.dict(sys.modules, {"rfapp.i18n": SimpleNamespace(current=lambda: self.t)}):
			self.traymodule = importlib.import_module("rfapp.tray")
		guard = patch.object(self.traymodule, "_icon", side_effect=AssertionError("Unpatched real tray icon in notification test"))
		guard.start()
		self.addCleanup(guard.stop)

	def test_policy_table(self):
		for policy in ("always", "errors", "never"):
			for result in ("ok", "partial", "failed", "crashed", "config_error", "skipped", "checked", "busy", "stopped"):
				for ran in (False, True):
					with self.subTest(policy=policy, result=result, ran=ran):
						summary = {**self.summary, "result": result}
						problem = result in ("partial", "failed", "crashed", "config_error")
						expected = policy != "never" and result not in ("checked", "busy", "stopped") and (problem or (policy == "always" and ran))
						self.assertEqual(notify._eligible(summary, {"notify.policy": policy}, [{"type": "account_end", "ran_tasks": ran}]), expected)

	def test_signed_out_and_terms_notify_skipped_check(self):
		for code in ("signed_out", "terms_of_use", "page_blank"):
			self.summary.update(result="skipped")
			self.summary["accounts"][0]["state_after"]["unreadable"] = code
			self.assertEqual(notify._eligible(self.summary, {"notify.policy": "errors"}, []), code != "page_blank")

	def test_check_now_never_notifies_even_problem(self):
		self.summary["result"] = "crashed"
		self.assertFalse(notify._eligible(self.summary, {"notify.policy": "always"}, [{"type": "run_start", "plan": {"check_only": True}}]))

	def test_summary_known_balance_and_plurals(self):
		title, body = notify.summary_text(self.summary, self.t)
		self.assertEqual(title, "Rewards Farmer finished")
		self.assertEqual(body, "+15 points, balance 1,200")
		self.summary["points_earned"] = 1
		self.assertEqual(notify.summary_text(self.summary, self.t)[1], "+1 point, balance 1,200")

	def test_unknown_values_are_not_zero(self):
		self.summary["accounts"][0]["points_now"] = None
		self.assertEqual(notify.summary_text(self.summary, self.t)[1], "+15 points")
		self.summary["points_earned"] = None
		self.assertEqual(notify.summary_text(self.summary, self.t)[1], self.t.t("tray.notify.done.body_no_points"))

	def test_problem_and_signed_out_keys(self):
		self.summary["result"] = "partial"
		self.assertEqual(notify.summary_text(self.summary, self.t)[1], "errors.run.partial")
		self.summary["accounts"][0]["state_after"]["unreadable"] = "signed_out"
		self.assertIn("Synthetic needs you", notify.summary_text(self.summary, self.t)[1])
		self.summary["accounts"][0]["state_after"]["unreadable"] = "terms_of_use"
		self.assertEqual(notify.summary_text(self.summary, self.t)[1], "errors.state.terms_of_use")

	def test_webhook_payload_and_timeout(self):
		with patch.object(notify.requests, "post", return_value=Mock(status_code=204)) as post:
			self.assertEqual(notify.send_webhook("https://discord.com/api/webhooks/synthetic", "title\nbody"), (True, "HTTP 204"))
		post.assert_called_once_with("https://discord.com/api/webhooks/synthetic", json={"username": "Rewards Farmer", "content": "title body"}, timeout=10, allow_redirects=False)

	def test_webhook_redirects_fail_without_following_or_exposing_target(self):
		for status in (301, 302, 303, 307, 308):
			with self.subTest(status=status):
				response = Mock(status_code=status, headers={"Location": "http://insecure.invalid/private-token"})
				with patch.object(notify.requests, "post", return_value=response) as post:
					ok, detail = notify.send_webhook("https://hooks.invalid/synthetic", "private message")
				self.assertFalse(ok)
				self.assertIn(str(status), detail)
				self.assertNotIn("private-token", detail)
				self.assertFalse(post.call_args.kwargs["allow_redirects"])
				post.assert_called_once()
				response.raise_for_status.assert_not_called()

	def test_webhook_failure_does_not_leak_token(self):
		with patch.object(notify.requests, "post", side_effect=notify.requests.ConnectionError("url https://discord.com/secret-token")):
			ok, detail = notify.send_webhook("https://discord.com/secret-token", "x")
		self.assertFalse(ok)
		self.assertNotIn("secret-token", detail)
		with patch.object(notify.requests, "post") as post:
			self.assertFalse(notify.send_webhook("http://localhost/synthetic", "x")[0])
			post.assert_not_called()

	def test_running_tray_notifies_without_temporary_icon(self):
		tray = Mock()
		tray.notify.return_value = True
		notify._set_tray(tray)
		self.addCleanup(notify._set_tray, None)
		self.assertTrue(notify.toast("title", "body"))
		tray.notify.assert_called_once_with("title", "body")

	def test_temporary_toast_waits_and_stops(self):
		icon = Mock()
		def setup(**kwargs):
			kwargs["setup"](icon)
		icon.run_detached.side_effect = setup
		image = Mock()
		image.__enter__ = Mock(return_value=image)
		image.__exit__ = Mock(return_value=False)
		with patch.dict(sys.modules, {"rfapp.tray": self.traymodule}), patch.object(self.traymodule, "_icon", return_value=icon), patch("PIL.Image.open", return_value=image), patch.object(notify.paths, "resource", return_value=Path("synthetic.png")), patch.object(notify.paths, "install_identity", return_value="test0000"), patch.object(notify.threading.Event, "wait", return_value=True) as wait:
			self.assertTrue(notify.toast("title", "body"))
		icon.notify.assert_called_once_with("body", "title")
		icon.stop.assert_called_once()
		self.assertIn(unittest.mock.call(8), wait.call_args_list)

	def test_temporary_toast_failure_stops_icon(self):
		icon = Mock()
		icon.run_detached.side_effect = OSError("no tray")
		image = Mock()
		image.__enter__ = Mock(return_value=image)
		image.__exit__ = Mock(return_value=False)
		with patch.dict(sys.modules, {"rfapp.tray": self.traymodule}), patch.object(self.traymodule, "_icon", return_value=icon), patch("PIL.Image.open", return_value=image), patch.object(notify.paths, "resource", return_value=Path("synthetic.png")), patch.object(notify.paths, "install_identity", return_value="test0000"):
			self.assertFalse(notify.toast("title", "body"))
		icon.stop.assert_called_once()

	def _delivery_stubs(self, running=False):
		from rfapp import paths
		modules = {
			"rfapp.i18n": SimpleNamespace(current=lambda: self.t),
			"rfapp.prefs": SimpleNamespace(load=lambda: {}),
			"rfapp.settings": SimpleNamespace(user_data_dir=lambda: Path("synthetic")),
			"rfapp.runs": SimpleNamespace(load_run=lambda *args: self.summary),
			"rfapp.single_instance": SimpleNamespace(is_running=lambda: running),
			"run_record": SimpleNamespace(read_events=Mock(return_value=[{"type": "account_end", "ran_tasks": True}]), append_event=Mock()),
		}
		return modules

	def test_supervisor_toast_and_webhook_append_events(self):
		modules = self._delivery_stubs()
		with patch.dict(sys.modules, modules), patch.object(notify.paths, "runs_dir", return_value=Path("synthetic")), patch.object(notify, "toast", return_value=True), patch.object(notify, "send_webhook", return_value=(True, "HTTP 204")) as webhook:
			notify.after_run("20261014T180000Z-aaaa", {"notify.policy": "always", "notify.webhook_url": "https://discord.com/synthetic"})
		self.assertEqual(modules["run_record"].append_event.call_count, 2)
		self.assertEqual([call.kwargs["channel"] for call in modules["run_record"].append_event.call_args_list], ["toast", "webhook"])
		self.assertNotIn("\n", webhook.call_args.args[1])

	def test_window_owns_toast_supervisor_owns_webhook(self):
		modules = self._delivery_stubs(running=True)
		with patch.dict(sys.modules, modules), patch.object(notify.paths, "runs_dir", return_value=Path("synthetic")), patch.object(notify, "toast", return_value=True) as toast, patch.object(notify, "send_webhook", return_value=(True, "HTTP 204")) as webhook:
			s = {"notify.webhook_url": "https://discord.com/synthetic"}
			notify.after_run("20261014T180000Z-aaaa", s)
			toast.assert_not_called()
			webhook.assert_called_once()
			webhook.reset_mock()
			notify._deliver("20261014T180000Z-aaaa", s, window=True)
			toast.assert_called_once()
			webhook.assert_not_called()

	def test_already_notified_channels_not_repeated(self):
		modules = self._delivery_stubs()
		modules["run_record"].read_events.return_value.extend([{"type": "notified", "channel": "toast"}, {"type": "notified", "channel": "webhook"}])
		with patch.dict(sys.modules, modules), patch.object(notify.paths, "runs_dir", return_value=Path("synthetic")), patch.object(notify, "toast") as toast, patch.object(notify, "send_webhook") as webhook:
			notify.after_run("20261014T180000Z-aaaa", {"notify.webhook_url": "https://discord.com/synthetic"})
			toast.assert_not_called()
			webhook.assert_not_called()

	def test_each_user_state_gates_both_real_delivery_paths_without_webhook_change(self):
		for window in (False, True):
			for state in (*range(1, 8), None):
				with self.subTest(window=window, state=state):
					modules = self._delivery_stubs(running=window)
					reader = Mock(return_value=state)
					tray, icon = Mock(), Mock()
					tray.notify.return_value = True
					icon.run_detached.side_effect = lambda **kwargs: kwargs["setup"](icon)
					image = Mock()
					image.__enter__, image.__exit__ = Mock(return_value=image), Mock(return_value=False)
					notify._set_tray(tray if window else None)
					with patch.dict(sys.modules, {**modules, "rfapp.tray": self.traymodule}), patch.object(notify.paths, "runs_dir", return_value=Path("synthetic")), patch.object(self.traymodule, "_icon", return_value=icon) as factory, patch("PIL.Image.open", return_value=image), patch.object(notify.paths, "resource", return_value=Path("synthetic.png")), patch.object(notify.paths, "install_identity", return_value="synthetic"), patch.object(notify.threading.Event, "wait", return_value=True), patch.object(notify, "send_webhook", return_value=(True, "HTTP 204")) as webhook:
						notify._deliver("20261016T013000Z-aaaa", {"notify.policy": "always", "notify.webhook_url": "https://hooks.invalid/synthetic"}, window=window, state_reader=reader)
					allowed = state is None or state == 5
					reader.assert_called_once_with()
					self.assertEqual(tray.notify.call_count, int(allowed and window))
					self.assertEqual(factory.call_count, int(allowed and not window))
					self.assertEqual(icon.notify.call_count, int(allowed and not window))
					self.assertEqual(icon.stop.call_count, int(allowed and not window))
					calls = modules["run_record"].append_event.call_args_list
					self.assertEqual(len(calls), 1 if window else 2)
					self.assertEqual(calls[0].kwargs, {"channel": "toast", "ok": allowed, "detail": None if allowed else f"held_busy_{state}"})
					self.assertEqual(webhook.call_count, int(not window))
		self.state_reader.assert_not_called()

	def test_busy_toast_is_recorded_in_jsonl_and_never_retried_when_available(self):
		import run_record
		modules = self._delivery_stubs()
		modules["run_record"] = run_record
		run_id = "20261016T013000Z-bbbb"
		with tempfile.TemporaryDirectory() as tmp:
			home = Path(tmp)
			path = home / (run_id + ".jsonl")
			run_record.append_event(str(path), "account_end", run_id, ran_tasks=True)
			with patch.dict(sys.modules, modules), patch.object(notify.paths, "runs_dir", return_value=home), patch.object(notify, "toast") as toast, patch.object(notify, "send_webhook", return_value=(True, "HTTP 204")) as webhook:
				settings = {"notify.webhook_url": "https://hooks.invalid/synthetic"}
				notify._deliver(run_id, settings, state_reader=lambda: 3)
				written = [event for event in run_record.read_events(str(path)) if event["type"] == "notified"]
				self.assertEqual([(event["channel"], event["ok"], event["detail"]) for event in written], [("toast", False, "held_busy_3"), ("webhook", True, "HTTP 204")])
				reader = Mock(return_value=5)
				notify._deliver(run_id, settings, window=True, state_reader=reader)
				toast.assert_not_called()
				reader.assert_not_called()
				webhook.assert_called_once()
				self.assertEqual(len([event for event in run_record.read_events(str(path)) if event["type"] == "notified"]), 2)
				self.assertEqual(self.summary["result"], "ok")

	def test_catalog_source_shape(self):
		for key, entry in self.t.messages.items():
			self.assertTrue(key.startswith("tray."))
			self.assertTrue(entry["note"])
			self.assertIn(set(entry), ({"text", "note"}, {"plural", "forms", "note"}))
			self.assertNotIn("\u2014", json.dumps(entry, ensure_ascii=False))
			self.assertNotIn("\u2013", json.dumps(entry, ensure_ascii=False))


class TrayTests(unittest.TestCase):
	def setUp(self):
		self.translator = Translator()
		self.translator.time = lambda dt: "09:45"
		self.i18n = SimpleNamespace(current=lambda: self.translator)
		with patch.dict(sys.modules, {"rfapp.i18n": self.i18n}):
			self.module = importlib.import_module("rfapp.tray")
		self.patch = patch.object(self.module, "i18n", self.i18n)
		self.patch.start()
		self.addCleanup(self.patch.stop)
		patcher = patch.object(self.module, "_dpi", return_value=96)
		self.dpi = patcher.start()
		self.addCleanup(patcher.stop)
		from PIL import Image
		self.show, self.run_now, self.stop, self.quit = Mock(), Mock(), Mock(), Mock()
		self.tray = self.module.Tray(self.show, self.run_now, self.stop, self.quit, image=Image.new("RGBA", (64, 64), "#000000"))
		self.tray._accent = "#0078D4"

	def test_icon_progress_only_for_complete_today_data(self):
		for dpi, size in ((96, 32), (192, 64)):
			with self.subTest(dpi=dpi):
				self.dpi.return_value = dpi
				self.tray._overview = {}
				image = self.tray._image()
				self.assertEqual(image.size, (size, size))
				base = image.tobytes()
				self.tray._overview = {"accounts": [{"paused": False, "state_is_today": True, "state": {"earned_today": 50, "open_points": 50, "unreadable": None}}]}
				self.assertNotEqual(base, self.tray._image().tobytes())
				self.tray._overview["accounts"][0]["state_is_today"] = False
				self.assertEqual(base, self.tray._image().tobytes())
				self.tray._overview["active_run"] = {"run_id": "synthetic"}
				self.assertNotEqual(base, self.tray._image().tobytes())

	def test_tooltip_plural_unknown_and_length(self):
		self.assertEqual(self.tray._tooltip(), "Rewards Farmer")
		self.tray._overview = {"accounts": [{"state_is_today": True, "state": {"earned_today": 1, "open_points": 0}}]}
		self.assertEqual(self.tray._tooltip(), "Today: 1 of 1 point")
		self.tray._overview["active_run"] = {}
		self.tray._overview["accounts"][0]["state"]["open_points"] = 3
		self.assertEqual(self.tray._tooltip(), "Today: 1 of 4 points")
		self.tray._overview["active_run"] = {"run_id": "synthetic", "supervisor_pid": 12345}
		self.translator.t = lambda *args, **kwargs: "x" * 200
		self.assertEqual(len(self.tray._tooltip()), 127)

	@unittest.skipUnless(sys.platform == "win32" and importlib.util.find_spec("pystray") is not None, "Windows pystray menu requires the app dependency group")
	def test_menu_order_actions_and_schedule_line(self):
		import pystray
		self.tray._overview = {"schedule": {"mode": "automatic", "next_at": "2026-10-15T07:45:00Z", "next_is_check": True}}
		menu = self.tray._menu()
		items = list(menu)
		self.assertEqual(len(items), 6)
		self.assertTrue(items[0].default)
		self.assertEqual(items[0].text, "Open Rewards Farmer")
		self.assertIs(items[2], pystray.Menu.SEPARATOR)
		self.assertFalse(items[3].enabled)
		self.assertIn("Next check", items[3].text)
		items[0](None)
		items[1](None)
		items[-1](None)
		self.show.assert_called_once()
		self.run_now.assert_called_once()
		self.quit.assert_called_once()
		self.tray._overview["active_run"] = {"run_id": "synthetic", "supervisor_pid": 12345}
		items = list(self.tray._menu())
		self.assertEqual(items[1].text, "Stop run")
		items[1](None)
		self.stop.assert_called_once()
		self.tray._overview["active_run"]["supervisor_pid"] = None
		items = list(self.tray._menu())
		self.assertFalse(items[1].enabled)
		self.assertNotEqual(items[1].text, "Stop run")

	def test_warning_clears_on_open_and_same_snapshot_does_not_rebuild(self):
		overview = {"last_run": {"run_id": "synthetic", "result": "partial"}}
		self.tray.update(overview)
		self.assertTrue(self.tray._warning)
		self.tray.opened()
		self.assertFalse(self.tray._warning)
		self.tray._icon = Mock()
		self.tray.update(overview)
		self.tray._icon.update_menu.assert_not_called()

	def test_warning_includes_config_error_and_signin_state(self):
		self.tray.update({"last_run": {"run_id": "first", "result": "config_error"}})
		self.assertTrue(self.tray._warning)
		self.tray.opened()
		self.tray.update({"last_run": {"run_id": "second", "result": "skipped"}, "accounts": [{"account_id": "account-1", "state": {"unreadable": "signed_out"}}]})
		self.assertTrue(self.tray._warning)
		self.tray.opened()
		self.tray.update({"last_run": {"run_id": "third", "result": "skipped"}, "accounts": [{"account_id": "account-1", "state": {"unreadable": "page_blank"}}]})
		self.assertFalse(self.tray._warning)

	def test_notify_rejection_is_false(self):
		self.assertFalse(self.tray.notify("title", "body"))
		self.tray._ready.set()
		self.tray._icon = Mock()
		self.tray._icon.notify.side_effect = OSError("shell refused")
		self.assertFalse(self.tray.notify("title", "body"))

	def test_balloon_click_navigates_to_notified_run(self):
		self.tray._ready.set()
		self.tray._notification_run = "20261014T180000Z-aaaa"
		self.tray._notification_clicked()
		self.show.assert_called_once_with("#/history/20261014T180000Z-aaaa")

	def test_private_balloon_hook_keeps_left_click_behavior(self):
		clicked = Mock()
		class FakeIcon:
			def __init__(self, *args, **kwargs): pass
			def _on_notify(self, *args): original(*args)
		original = Mock()
		with patch.dict(sys.modules, {"pystray": SimpleNamespace(Icon=FakeIcon)}):
			icon = self.module._icon("synthetic-click", clicked=clicked)
			with patch.object(self.module.threading, "Thread") as thread:
				icon._on_notify(0, 1029)
				self.assertIs(thread.call_args.kwargs["target"], clicked)
				thread.return_value.start.assert_called_once()
			icon._on_notify(0, 514)
			original.assert_called_once_with(0, 514)

	def test_balloon_info_flags_respect_quiet_time_without_changing_other_messages(self):
		sent = Mock(return_value="accepted")
		class FakeIcon:
			def __init__(self, *args, **kwargs): pass
			def _message(self, *args, **kwargs): return sent(*args, **kwargs)
		with patch.dict(sys.modules, {"pystray": SimpleNamespace(Icon=FakeIcon)}):
			icon = self.module._icon("synthetic-quiet-time")
			for flags in (0, 1, 0x24):
				with self.subTest(info_flags=flags):
					self.assertEqual(icon._message(1, 0x10, szInfo="Synthetic body", szInfoTitle="Synthetic title", dwInfoFlags=flags), "accepted")
					sent.assert_called_with(1, 0x10, szInfo="Synthetic body", szInfoTitle="Synthetic title", dwInfoFlags=flags | 0x80)
			icon._message(1, 0x10, szInfo="Synthetic body")
			sent.assert_called_with(1, 0x10, szInfo="Synthetic body", dwInfoFlags=0x80)
			icon._message(1, 0x10, szInfo="")
			sent.assert_called_with(1, 0x10, szInfo="")
			icon._message(0, 0x04, szTip="Synthetic title")
			sent.assert_called_with(0, 0x04, szTip="Synthetic title")

	@unittest.skipUnless(sys.platform == "win32" and importlib.util.find_spec("pystray") is not None, "Windows pystray NOTIFYICONDATAW requires the app dependency group")
	def test_quiet_time_flag_reaches_shell_data_for_running_and_temporary_icons(self):
		from pystray._util import win32
		import pystray
		for temporary in (False, True):
			with self.subTest(temporary=temporary), patch("pystray._win32.Icon._register_class", return_value=0), patch.object(win32, "Shell_NotifyIcon", return_value=True) as shell:
				icon = self.module._icon("synthetic-notification", title="Fallback title")
				icon._hwnd = None
				if temporary:
					icon.notify("Synthetic body", "Synthetic title")
				else:
					self.tray._icon = icon
					self.tray._ready.set()
					self.assertTrue(self.tray.notify("Synthetic title", "Synthetic body"))
				shell.assert_called_once()
				code, data = shell.call_args.args
				self.assertEqual(code, win32.NIM_MODIFY)
				self.assertEqual(data.uFlags, win32.NIF_INFO)
				self.assertEqual(data.dwInfoFlags & 0x80, 0x80)
				self.assertEqual(data.szInfo, "Synthetic body")
				self.assertEqual(data.szInfoTitle, "Synthetic title")
				self.assertFalse(icon._running)
				self.tray._icon = None
