import _bootstrap  # noqa: F401

import inspect
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from rfapp.bridge import Bridge
from rfapp import bridge as module

RUN_ID = "20261014T180000Z-aaaa"


class Failure(ValueError):
	def __init__(self, code, fields=None):
		self.code = code
		self.fields = fields
		self.params = {"synthetic": 1}
		self.detail = "synthetic detail"


class BridgeTests(unittest.TestCase):
	def setUp(self):
		for target in ("rfapp.bridge.subprocess.Popen", "rfapp.bridge.subprocess.run", "requests.sessions.Session.request", "rfapp.bridge.os.startfile"):
			guard = patch(target, side_effect=AssertionError("Unpatched external effect in bridge test"), create=True)
			guard.start()
			self.addCleanup(guard.stop)
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.home = Path(self.temp.name)
		(self.home / (RUN_ID + ".jsonl")).write_text('{"v":1}\n', encoding="utf-8")
		(self.home / (RUN_ID + ".log")).write_text("synthetic log", encoding="utf-8")
		(self.home / "index.json").write_text('{"locales":[{"code":"en","name":"English","dir":"ltr"}]}', encoding="utf-8")
		self.p = {"language": "system", "setup_done": True, "goals": {}}
		self.account = {"id": "account-1", "label": "Synthetic", "paused": False}
		self.signin = {"account_id": "account-1", "state": "waiting", "at": "2026-10-14T18:00:00Z"}
		self.data = {"run_id": RUN_ID, "active": True, "accounts": []}
		self.deps = {
			"prefs": SimpleNamespace(load=lambda: dict(self.p), save=lambda changes: self.p.update(changes)),
			"i18n": SimpleNamespace(resolve=lambda *args: ("en", "ltr", "en-US"), current=lambda: SimpleNamespace(t=lambda key, **params: key), reload=Mock()),
			"settings": SimpleNamespace(load=Mock(return_value={}), validate=Mock(return_value={}), save=Mock(return_value={"values": {}, "schedule": {}, "warnings": []}), user_data_dir=lambda: self.home / "profiles"),
			"runs": SimpleNamespace(load_run=Mock(return_value=self.data), active_run_id=Mock(return_value=RUN_ID), history=Mock(return_value={"runs": [], "next_before": None}), overview=Mock(return_value={"accounts": [{"account_id": "account-1", "goal": {"target": 100}}]})),
			"schedule": SimpleNamespace(status=Mock(return_value={"mode": "manual"}), ensure=Mock(return_value={}), replace_legacy_task=Mock(return_value={"mode": "manual"})),
			"run_lock": SimpleNamespace(status=Mock(return_value=None)),
			"run_record": SimpleNamespace(read_events=Mock(return_value=[]), append_event=Mock()),
			"accounts_admin": SimpleNamespace(list_accounts=Mock(return_value={"accounts": [self.account]}), add_account=Mock(return_value={"account_id": "account-1", "signin": self.signin}), sign_in_again=Mock(return_value={"signin": self.signin}), get_signin_status=Mock(return_value=self.signin), cancel_signin=Mock(return_value={}), rename_account=Mock(return_value={"account": self.account}), set_account_paused=Mock(return_value={"account": self.account}), remove_account=Mock(return_value={"accounts": [], "warnings": []}), get_legacy_install=Mock(return_value={"found": False, "path": None, "accounts": []}), import_legacy_install=Mock(return_value={"accounts": [self.account]})),
			"updates": SimpleNamespace(check=Mock(return_value={"available": True, "latest": "0.2.0"})),
			"diagnostics": SimpleNamespace(export=Mock()),
		}
		self.paths = SimpleNamespace(runs_dir=lambda: self.home, data_home=lambda: self.home, env_path=lambda: self.home / ".env", resource=lambda *parts: self.home / "index.json", is_frozen=lambda: False, launcher_command=lambda args: ["synthetic.exe", *args])
		self.patch = patch.object(module, "paths", self.paths)
		self.patch.start()
		self.addCleanup(self.patch.stop)
		self.push = Mock()
		self.window = Mock()
		self.api = Bridge(window=self.window, dependencies=self.deps, push=self.push)
		self.addCleanup(self.api._close)

	def test_exact_methods_one_object_and_no_public_state(self):
		expected = {"ui_ready", "app_info", "open_external", "get_overview", "get_stats", "get_live", "get_history", "get_run", "get_run_log", "start_run", "stop_run", "set_goal", "get_settings", "save_settings", "get_schedule_status", "replace_legacy_task", "pick_path", "list_ollama_models", "export_diagnostics", "check_updates", "dismiss_update", "get_legacy_install", "import_legacy_install", "complete_setup", "list_accounts", "get_account_picture", "add_account", "sign_in_again", "get_signin_status", "cancel_signin", "rename_account", "set_account_paused", "remove_account"}
		public = {name for name in dir(self.api) if not name.startswith("_")}
		self.assertEqual(public, expected)
		for name in public:
			self.assertTrue(callable(getattr(self.api, name)), name)
			self.assertEqual(list(inspect.signature(getattr(self.api, name)).parameters), ["request"])
		self.assertFalse(any(not name.startswith("_") for name in vars(self.api)))

	def test_every_method_rejects_non_objects_and_unknown_fields(self):
		for name in dir(self.api):
			if name.startswith("_"):
				continue
			for request in (None, [], "bad", {"unexpected": True}):
				with self.subTest(method=name, request=request):
					result = getattr(self.api, name)(request)
					self.assertFalse(result["ok"])
					self.assertEqual(result["error"]["code"], "bad_request")

	def test_account_picture_returns_backend_data_with_and_without_image(self):
		for rev, data_url in ((None, None), ("synthetic-rev", "data:image/png;base64,c3ludGhldGlj")):
			with self.subTest(rev=rev):
				result = {"account_id": "account-1", "rev": rev, "data_url": data_url}
				backend = Mock(return_value=result)
				self.deps["accounts_admin"].get_account_picture = backend
				self.assertEqual(self.api.get_account_picture({"account_id": "account-1"}), {"ok": True, "data": result})
				backend.assert_called_once_with("account-1")

	def test_account_picture_bad_request_and_unknown_account_do_not_read_picture(self):
		backend = Mock()
		self.deps["accounts_admin"].get_account_picture = backend
		for request in ({}, {"account_id": None}, {"account_id": 1}, {"account_id": ""}, {"account_id": "../outside"}, {"account_id": "account-1", "url": "https://example.invalid/picture"}):
			with self.subTest(request=request):
				self.assertEqual(self.api.get_account_picture(request)["error"]["code"], "bad_request")
		self.assertEqual(self.api.get_account_picture({"account_id": "account-999"})["error"]["code"], "accounts.unknown")
		backend.assert_not_called()

	def test_bad_field_types_and_ranges_return_bad_request(self):
		cases = (("start_run", {"account_ids": "account-1"}), ("start_run", {"account_ids": ["account-1", "account-1"]}), ("start_run", {"account_ids": [None]}), ("start_run", {"check_only": 1}), ("get_run_log", {"run_id": RUN_ID, "offset": -1}), ("get_run_log", {"run_id": RUN_ID, "max_bytes": True}), ("set_goal", {"account_id": "missing", "target": False}), ("rename_account", {"account_id": "missing", "label": "bad\nlabel"}), ("rename_account", {"account_id": "account-1", "label": " "}), ("set_account_paused", {"account_id": "missing", "paused": "true"}), ("remove_account", {"account_id": "missing", "delete_data": 1}), ("add_account", {"confirm_migration": "yes"}), ("check_updates", {"force": 1}), ("pick_path", {"kind": []}), ("save_settings", {"changes": []}), ("dismiss_update", {"version": "v0.2.0"}), ("list_ollama_models", {"base_url": "http://[bad"}))
		for method, request in cases:
			with self.subTest(method=method, request=request):
				self.assertEqual(getattr(self.api, method)(request)["error"]["code"], "bad_request")

	def test_readiness_and_app_info_shape(self):
		from rfapp import window
		with patch.object(window, "_theme", return_value="dark"), patch.object(window, "_reduced_motion", return_value=True):
			result = self.api.app_info({})
		self.assertTrue(result["ok"])
		self.assertEqual(set(result["data"]), {"version", "frozen", "data_home", "platform", "lang", "dir", "format_locale", "theme", "reduced_motion", "setup_done", "has_accounts", "task_ids", "locales"})
		self.assertEqual(len(result["data"]["task_ids"]), 7)
		self.assertEqual(self.api.ui_ready({}), {"ok": True, "data": {}})
		self.assertTrue(self.api._ready.is_set())

	def test_external_host_allowlist_and_bad_urls(self):
		with patch.object(module.os, "startfile", create=True) as open_url:
			for url in ("https://github.com/User0332/rewards-farmer", "https://rewards.bing.com/", "https://account.microsoft.com/"):
				self.assertTrue(self.api.open_external({"url": url})["ok"])
			for url in ("file:///C:/windows", "http://github.com/", "https://github.com.evil.test/", "https://user@github.com/", "https://github.com:123/", "https://github.com/\nanything"):
				self.assertEqual(self.api.open_external({"url": url})["error"]["code"], "url_not_allowed")
				self.assertEqual(self.api.open_external({"url": url})["error"]["message_key"], "errors.app.url_not_allowed")
			self.assertEqual(open_url.call_count, 3)

	def test_overview_enrichment_and_goal(self):
		result = self.api.get_overview({})["data"]
		self.assertEqual(result["schedule"], {"mode": "manual"})
		self.assertIsNone(result["signin_active"])
		self.assertTrue(result["generated_at"].endswith("Z"))
		self.assertTrue(self.api.set_goal({"account_id": "account-1", "target": 100})["ok"])
		self.assertEqual(self.p["goals"], {"account-1": 100})
		self.assertTrue(self.api.set_goal({"account_id": "account-1", "target": None})["ok"])
		self.assertEqual(self.p["goals"], {})
		for target in (True, 99, 1000001, "100"):
			self.assertEqual(self.api.set_goal({"account_id": "account-1", "target": target})["error"]["code"], "bad_request")
		self.assertEqual(self.api.set_goal({"account_id": "missing", "target": 100})["error"]["code"], "accounts.unknown")

	def test_get_stats_passes_request_settings_prefs_and_current_utc(self):
		from datetime import datetime, timezone
		backend = Mock(return_value={"days": [], "range": 7})
		self.deps["stats"] = SimpleNamespace(get_stats=backend)
		request = {"range": 7, "account_id": "account-1", "week_start": 7}
		before = datetime.now(timezone.utc)
		self.assertEqual(self.api.get_stats(request), {"ok": True, "data": {"days": [], "range": 7}})
		self.assertEqual(backend.call_args.args, (request,))
		self.assertEqual(backend.call_args.kwargs["settings"], {})
		self.assertEqual(backend.call_args.kwargs["prefs"], self.p)
		self.assertGreaterEqual(backend.call_args.kwargs["now"], before)
		self.assertEqual(backend.call_args.kwargs["now"].tzinfo, timezone.utc)

	def test_get_stats_translates_backend_errors_and_rejects_extra_fields(self):
		backend = Mock(side_effect=Failure("accounts.unknown"))
		self.deps["stats"] = SimpleNamespace(get_stats=backend)
		self.assertEqual(self.api.get_stats({"account_id": "account-999"})["error"]["code"], "accounts.unknown")
		self.assertEqual(self.api.get_stats({"unknown": 1})["error"]["code"], "bad_request")
		self.assertEqual(backend.call_count, 1)

	def test_history_request_validation(self):
		self.assertTrue(self.api.get_history({})["ok"])
		self.deps["runs"].history.assert_called_once_with(limit=50, before=None, include_checks=True, labels={}, user_data_dir=str(self.home / "profiles"))
		for request in ({"limit": True}, {"limit": 0}, {"limit": 201}, {"before": "../bad"}, {"include_checks": "yes"}):
			self.assertEqual(self.api.get_history(request)["error"]["code"], "bad_request")

	def test_run_log_byte_offsets_and_unknown(self):
		self.assertEqual(self.api.get_run({"run_id": RUN_ID})["data"], self.data)
		result = self.api.get_run_log({"run_id": RUN_ID, "offset": 2, "max_bytes": 3})["data"]
		self.assertEqual(result, {"text": "nth", "next_offset": 5, "eof": False, "size": 13})
		self.assertTrue(self.api.get_live({})["ok"])
		self.deps["runs"].active_run_id.return_value = None
		self.assertEqual(self.api.get_live({})["data"], {"run": None, "log": None})
		self.assertEqual(self.api.get_run({"run_id": "20261014T180000Z-bbbb"})["error"]["code"], "run.unknown")
		self.assertEqual(self.api.get_run({"run_id": "../outside"})["error"]["code"], "bad_request")

	def test_run_log_utf8_small_pages_reassemble_exactly(self):
		text = "aäéΩ €漢界 \U0001f600\U00010437z\nä€\U0001f600"
		encoded = text.encode("utf-8")
		(self.home / (RUN_ID + ".log")).write_bytes(encoded)
		for max_bytes in range(1, 10):
			with self.subTest(max_bytes=max_bytes):
				offset, pages = 0, []
				for _ in range(len(encoded) + 1):
					result = self.api.get_run_log({"run_id": RUN_ID, "offset": offset, "max_bytes": max_bytes})
					self.assertTrue(result["ok"])
					page = result["data"]
					self.assertEqual(page["size"], len(encoded))
					self.assertGreater(page["next_offset"], offset)
					self.assertNotIn("\ufffd", page["text"])
					self.assertEqual(encoded[offset:page["next_offset"]].decode("utf-8"), page["text"])
					self.assertEqual(offset + len(page["text"].encode("utf-8")), page["next_offset"])
					pages.append(page["text"])
					offset = page["next_offset"]
					if page["eof"]:
						break
				else:
					self.fail("UTF-8 paging did not reach EOF")
				self.assertEqual(offset, len(encoded))
				self.assertEqual("".join(pages), text)

	def test_run_log_utf8_start_skips_continuation_bytes(self):
		for character in ("ä", "€", "\U0001f600"):
			encoded = (character + "tail").encode("utf-8")
			(self.home / (RUN_ID + ".log")).write_bytes(encoded)
			for offset in range(1, len(character.encode("utf-8"))):
				with self.subTest(character=character, offset=offset):
					page = self.api.get_run_log({"run_id": RUN_ID, "offset": offset})["data"]
					self.assertEqual(page, {"text": "tail", "next_offset": len(encoded), "eof": True, "size": len(encoded)})

	def test_run_log_utf8_end_backs_off_and_small_limit_advances(self):
		(self.home / (RUN_ID + ".log")).write_bytes("a\U0001f600z".encode("utf-8"))
		for max_bytes in (2, 3, 4):
			with self.subTest(max_bytes=max_bytes):
				page = self.api.get_run_log({"run_id": RUN_ID, "max_bytes": max_bytes})["data"]
				self.assertEqual(page, {"text": "a", "next_offset": 1, "eof": False, "size": 6})
		page = self.api.get_run_log({"run_id": RUN_ID, "offset": 1, "max_bytes": 1})["data"]
		self.assertEqual(page, {"text": "\U0001f600", "next_offset": 5, "eof": False, "size": 6})
		page = self.api.get_run_log({"run_id": RUN_ID, "offset": 5, "max_bytes": 1})["data"]
		self.assertEqual(page, {"text": "z", "next_offset": 6, "eof": True, "size": 6})

	def test_get_live_utf8_tail_start_aligns_forward(self):
		for character in ("ä", "€", "\U0001f600"):
			for offset in range(1, len(character.encode("utf-8"))):
				with self.subTest(character=character, offset=offset):
					prefix = "prefix".encode("utf-8")
					multibyte = character.encode("utf-8")
					tail = "x" * (65536 - len(multibyte) + offset)
					encoded = prefix + multibyte + tail.encode("utf-8")
					(self.home / (RUN_ID + ".log")).write_bytes(encoded)
					self.assertEqual(len(encoded) - 65536, len(prefix) + offset)
					result = self.api.get_live({})
					self.assertTrue(result["ok"])
					self.assertEqual(result["data"]["log"], {"text": tail, "next_offset": len(encoded)})

	def test_run_log_utf8_eof_empty_and_missing(self):
		encoded = "ä€\U0001f600".encode("utf-8")
		path = self.home / (RUN_ID + ".log")
		path.write_bytes(encoded)
		for offset in (len(encoded) - 1, len(encoded), len(encoded) + 10):
			with self.subTest(offset=offset):
				self.assertEqual(self.api.get_run_log({"run_id": RUN_ID, "offset": offset, "max_bytes": 1})["data"], {"text": "", "next_offset": len(encoded), "eof": True, "size": len(encoded)})
		path.write_bytes(b"")
		self.assertEqual(self.api.get_run_log({"run_id": RUN_ID})["data"], {"text": "", "next_offset": 0, "eof": True, "size": 0})
		path.unlink()
		self.assertEqual(self.api.get_run_log({"run_id": RUN_ID})["data"], {"text": "", "next_offset": 0, "eof": True, "size": 0})

	def test_active_run_log_waits_for_incomplete_utf8_tail(self):
		path = self.home / (RUN_ID + ".log")
		path.write_bytes(b"prefix\xe2\x82")
		page = self.api.get_run_log({"run_id": RUN_ID})["data"]
		self.assertEqual(page, {"text": "prefix", "next_offset": 6, "eof": False, "size": 8})
		with path.open("ab") as source:
			source.write(b"\xac")
		page = self.api.get_run_log({"run_id": RUN_ID, "offset": page["next_offset"]})["data"]
		self.assertEqual(page, {"text": "€", "next_offset": 9, "eof": True, "size": 9})

	def test_active_run_log_incomplete_utf8_tail_does_not_force_advance(self):
		path = self.home / (RUN_ID + ".log")
		for character in ("¢", "€", "\U0001f600", "\U0010ffff"):
			encoded = character.encode("utf-8")
			for written in range(1, len(encoded)):
				for max_bytes in (1, 65536):
					with self.subTest(character=character, written=written, max_bytes=max_bytes):
						path.write_bytes(encoded[:written])
						page = self.api.get_run_log({"run_id": RUN_ID, "max_bytes": max_bytes})["data"]
						self.assertEqual(page, {"text": "", "next_offset": 0, "eof": False, "size": written})
						with path.open("ab") as source:
							source.write(encoded[written:])
						page = self.api.get_run_log({"run_id": RUN_ID, "offset": page["next_offset"], "max_bytes": max_bytes})["data"]
						self.assertEqual(page, {"text": character, "next_offset": len(encoded), "eof": True, "size": len(encoded)})

	def test_finished_run_log_incomplete_utf8_tail_reaches_eof(self):
		(self.home / (RUN_ID + ".log")).write_bytes(b"prefix\xe2\x82")
		for active_run_id in (None, "20261014T180000Z-bbbb"):
			with self.subTest(active_run_id=active_run_id):
				self.deps["runs"].active_run_id.return_value = active_run_id
				page = self.api.get_run_log({"run_id": RUN_ID})["data"]
				self.assertEqual(page, {"text": "prefix\ufffd", "next_offset": 8, "eof": True, "size": 8})

	def test_get_live_waits_for_active_incomplete_utf8_tail(self):
		path = self.home / (RUN_ID + ".log")
		path.write_bytes(b"prefix\xe2\x82")
		self.assertEqual(self.api.get_live({})["data"]["log"], {"text": "prefix", "next_offset": 6})
		with path.open("ab") as source:
			source.write(b"\xac")
		self.assertEqual(self.api.get_live({})["data"]["log"], {"text": "prefix€", "next_offset": 9})

	def test_active_run_log_stray_or_invalid_tail_reaches_eof(self):
		for tail in (b"\x80", b"\xbf", b"\xc0", b"\xc1", b"\xf5", b"\xff", b"\xe2\xff", b"\xe2A"):
			with self.subTest(tail=tail):
				encoded = b"prefix" + tail
				(self.home / (RUN_ID + ".log")).write_bytes(encoded)
				page = self.api.get_run_log({"run_id": RUN_ID})["data"]
				self.assertEqual(page, {"text": encoded.decode("utf-8", errors="replace"), "next_offset": len(encoded), "eof": True, "size": len(encoded)})
		self.deps["runs"].active_run_id.assert_not_called()

	def test_start_run_detached_and_retry(self):
		with patch.object(module.subprocess, "Popen", side_effect=[OSError("breakaway refused"), Mock()]) as popen:
			self.assertEqual(self.api.start_run({"account_ids": ["account-1"], "check_only": True}), {"ok": True, "data": {"started": True}})
		self.assertEqual(popen.call_count, 2)
		self.assertEqual(popen.call_args.args[0], ["synthetic.exe", "--check-now", "--accounts", "account-1"])
		self.assertEqual(popen.call_args.kwargs["stdout"], module.subprocess.DEVNULL)
		self.assertEqual(popen.call_args.kwargs["cwd"], self.home)
		self.deps["run_lock"].status.return_value = object()
		self.assertEqual(self.api.start_run({})["error"]["code"], "run.busy")
		self.deps["run_lock"].status.return_value = None
		self.deps["accounts_admin"].list_accounts.return_value = {"accounts": []}
		self.assertEqual(self.api.start_run({})["error"]["code"], "run.no_accounts")

	def test_stop_file_and_not_active(self):
		self.assertEqual(self.api.stop_run({"run_id": RUN_ID})["error"]["code"], "run.not_active")
		self.deps["run_lock"].status.return_value = SimpleNamespace(run_id=RUN_ID, supervisor_pid=12345)
		with patch.object(self.api, "_stop_fallback"):
			self.assertTrue(self.api.stop_run({"run_id": RUN_ID})["ok"])
		self.assertEqual(json.loads((self.home / (RUN_ID + ".stop")).read_text())["by"], "app")

	def test_cli_run_cannot_create_a_stop_request_or_worker(self):
		self.deps["run_lock"].status.return_value = SimpleNamespace(run_id=RUN_ID, supervisor_pid=None)
		with patch.object(module.threading, "Thread") as worker:
			response = self.api.stop_run({"run_id": RUN_ID})
		self.assertEqual(response["error"]["code"], "run.not_stoppable")
		self.assertEqual(response["error"]["message_key"], "errors.app.run_not_stoppable")
		self.assertFalse((self.home / (RUN_ID + ".stop")).exists())
		worker.assert_not_called()

	def test_hard_stop_names_only_matching_supervisor(self):
		closed = self.api._closed
		self.addCleanup(setattr, self.api, "_closed", closed)
		self.api._closed = SimpleNamespace(wait=lambda *args: False)
		info = SimpleNamespace(run_id=RUN_ID, supervisor_pid=12345)
		self.deps["run_lock"].status.side_effect = [info, None, None]
		with patch.object(module.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as kill:
			self.api._stop_fallback(RUN_ID)
		self.assertEqual(kill.call_args.args[0], ["taskkill", "/PID", "12345", "/F"])
		self.deps["run_record"].append_event.assert_called_once()
		self.assertEqual(self.deps["run_record"].append_event.call_args.kwargs["written_by"], "app")
		self.api._closed = threading.Event()

	def test_settings_envelope_validation_and_exceptions(self):
		self.assertTrue(self.api.get_settings({})["ok"])
		self.assertTrue(self.api.save_settings({"changes": {}})["ok"])
		self.assertTrue(self.api.save_settings({"changes": {"app.language": "en"}})["ok"])
		self.deps["i18n"].reload.assert_called_once()
		self.deps["settings"].validate.return_value = {"schedule.time": "out_of_range"}
		result = self.api.save_settings({"changes": {"schedule.time": "bad"}})
		self.assertEqual(result["error"]["fields"], {"schedule.time": "out_of_range"})
		self.assertEqual(result["error"]["message_key"], "errors.app.settings_invalid")
		self.deps["settings"].validate.return_value = {}
		for code in ("schedule.failed", "autostart.failed"):
			self.deps["settings"].save.side_effect = Failure(code)
			self.assertEqual(self.api.save_settings({"changes": {}})["error"]["code"], code)

	def test_schedule_methods_and_failure_codes(self):
		self.assertTrue(self.api.get_schedule_status({})["ok"])
		self.assertTrue(self.api.replace_legacy_task({})["ok"])
		for code in ("schedule.legacy_not_found", "schedule.failed"):
			self.deps["schedule"].replace_legacy_task.side_effect = Failure(code)
			self.assertEqual(self.api.replace_legacy_task({})["error"]["code"], code)

	def test_dialogs_cancel_and_export(self):
		webview = SimpleNamespace(FileDialog=SimpleNamespace(OPEN=1, FOLDER=2, SAVE=3))
		with patch.dict(sys.modules, {"webview": webview}):
			self.window.create_file_dialog.return_value = None
			self.assertEqual(self.api.pick_path({"kind": "data_folder"})["data"], {"path": None})
			self.assertEqual(self.api.export_diagnostics({})["data"], {"path": None})
			self.window.create_file_dialog.return_value = (str(self.home / "synthetic.exe"),)
			for kind in ("edge", "driver"):
				self.assertTrue(self.api.pick_path({"kind": kind})["ok"])
			self.assertTrue(self.api.export_diagnostics({})["ok"])
			self.deps["diagnostics"].export.side_effect = OSError("denied")
			self.assertEqual(self.api.export_diagnostics({})["error"]["code"], "diagnostics.failed")

	def test_ollama_http_mock(self):
		response = Mock()
		response.json.return_value = {"models": [{"name": "z"}, {"name": "a"}]}
		with patch.object(module.requests, "get", return_value=response) as get:
			self.assertEqual(self.api.list_ollama_models({"base_url": "http://localhost:11434/v1/"})["data"], {"models": ["a", "z"]})
			get.assert_called_once_with("http://localhost:11434/api/tags", timeout=5)
		with patch.object(module.requests, "get", side_effect=module.requests.ConnectionError("offline")):
			self.assertEqual(self.api.list_ollama_models({"base_url": "http://localhost:11434/v1"})["error"]["code"], "ollama.unreachable")

	def test_update_and_dismiss(self):
		self.assertTrue(self.api.check_updates({"force": True})["ok"])
		self.push.assert_called_with("update.available", {"available": True, "latest": "0.2.0"})
		self.assertTrue(self.api.dismiss_update({"version": "0.2.0"})["ok"])
		self.assertEqual(self.p["update_dismissed_version"], "0.2.0")
		self.deps["updates"].check.side_effect = Failure("updates.failed")
		self.assertEqual(self.api.check_updates({})["error"]["code"], "updates.failed")

	def test_account_methods_and_each_failure_code(self):
		requests = {"list_accounts": {}, "get_legacy_install": {}, "import_legacy_install": {}, "add_account": {"confirm_migration": True}, "sign_in_again": {"account_id": "account-1"}, "get_signin_status": {"account_id": "account-1"}, "cancel_signin": {"account_id": "account-1"}, "rename_account": {"account_id": "account-1", "label": " Renamed "}, "set_account_paused": {"account_id": "account-1", "paused": True}, "remove_account": {"account_id": "account-1", "delete_data": False}}
		for name, request in requests.items():
			self.assertTrue(getattr(self.api, name)(request)["ok"], name)
		self.deps["accounts_admin"].rename_account.assert_called_once_with("account-1", "Renamed")
		for name, codes in (("add_account", ("accounts.migration_needed", "accounts.signin_active", "run.busy")), ("sign_in_again", ("accounts.signin_active", "run.busy")), ("set_account_paused", ("accounts.last_active",)), ("remove_account", ("accounts.root_needs_delete", "run.busy")), ("import_legacy_install", ("accounts.legacy_not_found", "run.busy")), ("get_signin_status", ("accounts.unknown",))):
			for code in codes:
				getattr(self.deps["accounts_admin"], name).side_effect = Failure(code)
				self.assertEqual(getattr(self.api, name)(requests[name])["error"]["code"], code)

	def test_setup_env_default_only_when_absent(self):
		env = Mock()
		env.get.return_value = None
		with patch.dict(sys.modules, {"rfapp.envfile": SimpleNamespace(EnvFile=SimpleNamespace(load=lambda *args: env))}):
			self.assertTrue(self.api.complete_setup({})["ok"])
			env.set.assert_called_once_with("QUERY_SOURCE", "trends")
			self.assertTrue(self.p["setup_done"])
			env.set.reset_mock()
			env.get.return_value = "llm"
			self.assertTrue(self.api.complete_setup({})["ok"])
			env.set.assert_not_called()

	def test_internal_exception_never_crosses_bridge(self):
		for exception in (RuntimeError("synthetic failure"), KeyboardInterrupt(), SystemExit(2)):
			self.deps["settings"].load.side_effect = exception
			result = self.api.get_settings({})
			self.assertEqual(result["error"]["code"], "internal")
			self.assertEqual(set(result["error"]), {"code", "message_key", "params", "detail", "fields"})

	def test_run_pruned_between_probe_and_read_is_unknown(self):
		self.deps["runs"].load_run.side_effect = FileNotFoundError("pruned")
		self.assertEqual(self.api.get_run({"run_id": RUN_ID})["error"]["code"], "run.unknown")

	def test_threads_serialize_mutations(self):
		active = []
		overlaps = []
		def save(changes):
			if active:
				overlaps.append(True)
			active.append(1)
			threading.Event().wait(.01)
			active.pop()
			return {}
		self.deps["settings"].save.side_effect = save
		threads = [threading.Thread(target=self.api.save_settings, args=({"changes": {}},)) for _ in range(8)]
		for thread in threads: thread.start()
		for thread in threads: thread.join(2)
		self.assertEqual(overlaps, [])

	def test_dialog_http_and_file_reads_do_not_hold_the_shared_lock(self):
		from rfapp import window
		webview = SimpleNamespace(FileDialog=SimpleNamespace(OPEN=1, FOLDER=2, SAVE=3))
		for operation in ("pick_path", "export_dialog", "export_file", "models", "updates", "log_read", "slow_mutation"):
			with self.subTest(operation=operation):
				entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
				def blocking(value):
					entered.set()
					if not release.wait(3):
						raise AssertionError("Blocked test operation was not released")
					return value
				self.deps["run_lock"].status.return_value = SimpleNamespace(run_id=RUN_ID, supervisor_pid=12345)
				response = Mock()
				response.json.return_value = {"models": []}
				self.window.create_file_dialog.side_effect = None
				self.window.create_file_dialog.return_value = (str(self.home / "export.zip"),)
				self.deps["diagnostics"].export.side_effect = None
				self.deps["updates"].check.side_effect = None
				self.deps["accounts_admin"].remove_account.side_effect = None
				with patch.dict(sys.modules, {"webview": webview}), patch.object(module.requests, "get", side_effect=lambda *args, **kwargs: blocking(response) if operation == "models" else response), patch.object(self.api, "_read_log", side_effect=lambda *args: blocking({"text": "", "next_offset": 0, "size": 0, "eof": True}) if operation == "log_read" else {}), patch.object(self.api, "_stop_fallback"):
					if operation in ("pick_path", "export_dialog"):
						self.window.create_file_dialog.side_effect = lambda *args, **kwargs: blocking(None)
					if operation == "export_file":
						self.deps["diagnostics"].export.side_effect = lambda *args: blocking(None)
					if operation == "updates":
						self.deps["updates"].check.side_effect = lambda **kwargs: blocking({"available": False})
					if operation == "slow_mutation":
						self.deps["accounts_admin"].remove_account.side_effect = lambda *args: blocking({"accounts": [], "warnings": []})
					target, request = {"pick_path": (self.api.pick_path, {"kind": "edge"}), "export_dialog": (self.api.export_diagnostics, {}), "export_file": (self.api.export_diagnostics, {}), "models": (self.api.list_ollama_models, {"base_url": "http://localhost:11434/v1"}), "updates": (self.api.check_updates, {}), "log_read": (self.api.get_run_log, {"run_id": RUN_ID}), "slow_mutation": (self.api.remove_account, {"account_id": "account-1", "delete_data": True})}[operation]
					worker = threading.Thread(target=target, args=(request,))
					worker.start()
					stopper = None
					try:
						self.assertTrue(entered.wait(1))
						def stop():
							self.assertTrue(self.api.stop_run({"run_id": RUN_ID})["ok"])
							with self.api._lock:
								stopped.set()
						stopper = threading.Thread(target=stop)
						stopper.start()
						self.assertTrue(stopped.wait(1), "Stop or live shared state waited for " + operation)
					finally:
						release.set()
						worker.join(4)
						if stopper:
							stopper.join(4)
					self.assertFalse(worker.is_alive())
					self.assertFalse(stopper.is_alive())

	def test_settings_and_pause_writes_serialize_and_preserve_both_changes(self):
		path = self.home / ".env"
		path.write_text("STATE='{}'\n", encoding="utf-8")
		save_entered, both_called, pause_entered, release = (threading.Event() for _ in range(4))
		results = {}
		def write(changes):
			before = json.loads(module.dotenv_values(path)["STATE"])
			if "notify.policy" in changes:
				save_entered.set()
				if not both_called.wait(3) or not release.wait(3):
					raise AssertionError("Overlapping writers were not released")
			else:
				pause_entered.set()
			before.update(changes)
			path.write_text("STATE='" + json.dumps(before) + "'\n", encoding="utf-8")
			return before
		def save(changes):
			return {"values": write(changes), "schedule": {}, "warnings": []}
		def pause(*args):
			write({"account.paused": True})
			return {"account": {**self.account, "paused": True}}
		self.deps["settings"].save.side_effect = save
		self.deps["accounts_admin"].set_account_paused.side_effect = pause
		first = threading.Thread(target=lambda: results.update(save=self.api.save_settings({"changes": {"notify.policy": "errors"}})))
		def change_pause():
			both_called.set()
			results["pause"] = self.api.set_account_paused({"account_id": "account-1", "paused": True})
		second = threading.Thread(target=change_pause)
		first.start()
		try:
			self.assertTrue(save_entered.wait(1))
			second.start()
			self.assertTrue(both_called.wait(1))
			overlapped = pause_entered.wait(.15)
		finally:
			release.set()
			first.join(4)
			if second.ident:
				second.join(4)
		self.assertFalse(first.is_alive())
		self.assertFalse(second.is_alive())
		self.assertTrue(results["save"]["ok"])
		self.assertTrue(results["pause"]["ok"])
		self.assertEqual(json.loads(module.dotenv_values(path)["STATE"]), {"notify.policy": "errors", "account.paused": True})
		self.assertFalse(overlapped, "Account mutation entered a concurrent read/write transaction")

	def test_all_mutation_entrypoints_use_one_lock_but_stop_does_not(self):
		class ProbeLock:
			def __init__(self): self.entries, self.depth = 0, 0
			def __enter__(self):
				self.entries += 1
				self.depth += 1
			def __exit__(self, *args): self.depth -= 1
		lock = ProbeLock()
		self.api._settings_lock = lock
		requests = {"start_run": {}, "set_goal": {"account_id": "account-1", "target": 100}, "save_settings": {"changes": {}}, "replace_legacy_task": {}, "dismiss_update": {"version": "0.2.0"}, "import_legacy_install": {}, "complete_setup": {}, "add_account": {}, "sign_in_again": {"account_id": "account-1"}, "cancel_signin": {"account_id": "account-1"}, "rename_account": {"account_id": "account-1", "label": "Renamed"}, "set_account_paused": {"account_id": "account-1", "paused": True}, "remove_account": {"account_id": "account-1", "delete_data": False}}
		env = Mock()
		env.get.return_value = "trends"
		with patch.dict(sys.modules, {"rfapp.envfile": SimpleNamespace(EnvFile=SimpleNamespace(load=lambda *args: env))}), patch.object(self.api, "_spawn"):
			for method, request in requests.items():
				with self.subTest(method=method):
					before = lock.entries
					self.assertTrue(getattr(self.api, method)(request)["ok"])
					self.assertGreater(lock.entries, before)
		self.deps["run_lock"].status.return_value = SimpleNamespace(run_id=RUN_ID, supervisor_pid=12345)
		before = lock.entries
		with patch.object(self.api, "_stop_fallback"):
			self.assertTrue(self.api.stop_run({"run_id": RUN_ID})["ok"])
		self.assertEqual(lock.entries, before)

	def test_settings_secret_presence_replace_clear_and_merged_validation(self):
		values = {"run.search_source": "openrouter", "run.openrouter_key": "synthetic-key-original", "notify.webhook_url": "https://hooks.invalid/synthetic-token", "notify.policy": "always"}
		settings = self.deps["settings"]
		settings.load.side_effect = lambda: dict(values)
		def save(changes):
			values.update(changes)
			return {"values": dict(values), "schedule": {"mode": "manual"}, "warnings": []}
		settings.save.side_effect = save
		response = self.api.get_settings({})["data"]
		self.assertEqual(response["secrets_set"], {"run.openrouter_key": True, "notify.webhook_url": True})
		self.assertFalse(set(module._SECRET_SETTINGS) & response["values"].keys())
		self.assertTrue(self.api.save_settings({"changes": {"notify.policy": "errors"}})["ok"])
		self.assertEqual(values["run.openrouter_key"], "synthetic-key-original")
		self.assertEqual(values["notify.webhook_url"], "https://hooks.invalid/synthetic-token")
		response = self.api.save_settings({"changes": {"run.openrouter_key": "synthetic-key-new", "notify.webhook_url": ""}})["data"]
		self.assertEqual(values["run.openrouter_key"], "synthetic-key-new")
		self.assertEqual(values["notify.webhook_url"], "")
		self.assertEqual(response["secrets_set"], {"run.openrouter_key": True, "notify.webhook_url": False})
		settings.validate.assert_called_with({"run.openrouter_key": "synthetic-key-new", "notify.webhook_url": ""}, {"run.search_source": "openrouter", "run.openrouter_key": "synthetic-key-original", "notify.webhook_url": "https://hooks.invalid/synthetic-token", "notify.policy": "errors"})
		settings.validate.return_value = {"run.openrouter_key": "key_required"}
		calls = settings.save.call_count
		self.assertEqual(self.api.save_settings({"changes": {"run.openrouter_key": ""}})["error"]["fields"], {"run.openrouter_key": "key_required"})
		self.assertEqual(settings.save.call_count, calls)

	def test_secret_changes_use_the_real_merged_settings_validator(self):
		from rfapp import settings
		self.deps["settings"].validate.side_effect = settings.validate
		import rfapp
		cached = patch.object(rfapp, "prefs", self.deps["prefs"], create=True)
		cached.start()
		self.addCleanup(cached.stop)
		self.deps["settings"].load.return_value = {**settings._DEFAULTS, "run.search_source": "openrouter", "run.openrouter_key": "synthetic-valid-existing-key"}
		self.assertTrue(self.api.save_settings({"changes": {"schedule.min_points": 20}})["ok"])
		self.deps["settings"].save.reset_mock()
		response = self.api.save_settings({"changes": {"run.openrouter_key": ""}})
		self.assertEqual(response["error"]["fields"], {"run.openrouter_key": "key_required"})
		self.deps["settings"].save.assert_not_called()

	def test_every_bridge_response_json_excludes_planted_secrets(self):
		secrets = {"run.openrouter_key": "planted-openrouter-value-42", "notify.webhook_url": "https://hooks.invalid/planted-webhook-token-42"}
		(self.home / ".env").write_text("OPENROUTER_API_KEY=" + secrets["run.openrouter_key"] + "\nREWARDS_WEBHOOK_URL=" + secrets["notify.webhook_url"] + "\n", encoding="utf-8")
		(self.home / (RUN_ID + ".log")).write_text(" ".join(secrets.values()), encoding="utf-8")
		self.deps["settings"].load.return_value = dict(secrets)
		self.deps["settings"].save.return_value = {"values": dict(secrets), "schedule": {}, "warnings": [{"detail": secrets["run.openrouter_key"]}]}
		self.deps["run_lock"].status.return_value = SimpleNamespace(run_id=RUN_ID, supervisor_pid=12345)
		webview = SimpleNamespace(FileDialog=SimpleNamespace(OPEN=1, FOLDER=2, SAVE=3))
		self.window.create_file_dialog.return_value = None
		requests = {"open_external": {"url": "https://github.com/"}, "get_run": {"run_id": RUN_ID}, "get_run_log": {"run_id": RUN_ID}, "start_run": {}, "stop_run": {"run_id": RUN_ID}, "set_goal": {"account_id": "account-1", "target": 100}, "save_settings": {"changes": dict(secrets)}, "pick_path": {"kind": "edge"}, "list_ollama_models": {"base_url": "http://localhost:11434/v1"}, "dismiss_update": {"version": "0.2.0"}, "sign_in_again": {"account_id": "account-1"}, "get_signin_status": {"account_id": "account-1"}, "cancel_signin": {"account_id": "account-1"}, "rename_account": {"account_id": "account-1", "label": "Renamed"}, "set_account_paused": {"account_id": "account-1", "paused": False}, "remove_account": {"account_id": "account-1", "delete_data": False}}
		env = Mock()
		env.get.return_value = "trends"
		response = Mock()
		response.json.return_value = {"models": [{"name": secrets["run.openrouter_key"]}]}
		from rfapp import window
		with patch.dict(sys.modules, {"webview": webview, "rfapp.envfile": SimpleNamespace(EnvFile=SimpleNamespace(load=lambda *args: env))}), patch.object(module.requests, "get", return_value=response), patch.object(module.os, "startfile", create=True), patch.object(window, "_theme", return_value="light"), patch.object(window, "_reduced_motion", return_value=False), patch.object(self.api, "_stop_fallback"):
			for name in (name for name in dir(self.api) if not name.startswith("_")):
				with self.subTest(method=name):
					result = getattr(self.api, name)(requests.get(name, {}))
					serialized = json.dumps(result)
					for secret in secrets.values():
						self.assertNotIn(secret, serialized)
					if name in ("get_settings", "save_settings"):
						self.assertTrue(result["ok"])
						self.assertFalse(set(secrets) & result["data"]["values"].keys())
			self.deps["settings"].load.side_effect = RuntimeError(secrets["run.openrouter_key"])
			self.assertNotIn(secrets["run.openrouter_key"], json.dumps(self.api.get_settings({})))
		self.api._push("test.synthetic", {"detail": secrets["run.openrouter_key"]})
		self.assertNotIn(secrets["run.openrouter_key"], json.dumps(self.push.call_args.args[1]))

	def test_short_secret_redaction_preserves_schema_and_unrelated_text(self):
		webhook = "https://hooks.invalid/short-secret-token"
		(self.home / ".env").write_text("OPENROUTER_API_KEY=run\nREWARDS_WEBHOOK_URL=" + webhook + "\n", encoding="utf-8")
		self.deps["settings"].load.return_value = {"run.openrouter_key": "run", "notify.webhook_url": webhook, "run.search_source": "openrouter"}
		unchanged = "runtime running run_id run.log rerun Run RUN xrange"
		self.deps["runs"].overview.return_value = {"accounts": [], "run_id": RUN_ID, "run": {"run_id": RUN_ID}, "detail": unchanged + " run " + webhook}
		self.data["detail"] = unchanged
		(self.home / (RUN_ID + ".log")).write_text(unchanged + " run " + webhook, encoding="utf-8")
		overview = self.api.get_overview({})["data"]
		self.assertEqual(set(overview), {"accounts", "run_id", "run", "detail", "schedule", "generated_at", "signin_active"})
		self.assertEqual(overview["run"], {"run_id": RUN_ID})
		# A dot is a token boundary, but an underscore or letter is not.
		expected = unchanged.replace("run.log", "<redacted>.log")
		self.assertEqual(overview["detail"], expected + " <redacted> <redacted>")
		live = self.api.get_live({})["data"]
		self.assertEqual(set(live), {"run", "log"})
		self.assertEqual(live["run"]["detail"], expected)
		self.assertEqual(live["log"]["text"], expected + " <redacted> <redacted>")
		settings = self.api.get_settings({})["data"]
		self.assertEqual(set(settings["secrets_set"]), set(module._SECRET_SETTINGS))
		self.assertEqual(settings["values"], {"run.search_source": "openrouter"})
		self.api._push("run.log", {"run_id": RUN_ID, "text": unchanged + " run " + webhook})
		self.assertEqual(self.push.call_args.args, ("run.log", {"run_id": RUN_ID, "text": expected + " <redacted> <redacted>"}))
		error = Failure("settings.invalid", fields={"run.openrouter_key": "key_required"})
		error.detail = unchanged + " run " + webhook
		self.deps["settings"].load.side_effect = error
		response = self.api.get_settings({})["error"]
		self.assertEqual(response["code"], "settings.invalid")
		self.assertEqual(response["message_key"], "errors.app.settings_invalid")
		self.assertEqual(response["fields"], {"run.openrouter_key": "key_required"})
		self.assertEqual(response["detail"], expected + " <redacted> <redacted>")
		self.api._remember_secrets({"run.openrouter_key": "x"})
		self.assertEqual(self.api._redact_response({"x": "x xrange prefix_x X", "run_id": RUN_ID}), {"x": "<redacted> xrange prefix_x X", "run_id": RUN_ID})

	def test_secret_file_is_reloaded_only_when_its_mtime_changes(self):
		path = self.home / ".env"
		path.write_text("OPENROUTER_API_KEY=first-secret\n", encoding="utf-8")
		with patch.object(module, "dotenv_values", wraps=module.dotenv_values) as read:
			for _ in range(4):
				self.assertEqual(self.api._redact_response({"detail": "first-secret"}), {"detail": "<redacted>"})
			self.api._push("run.log", {"text": "first-secret"})
			self.assertEqual(read.call_count, 1)
			mtime = path.stat().st_mtime_ns
			path.write_text("OPENROUTER_API_KEY=other-secret\n", encoding="utf-8")
			module.os.utime(path, ns=(mtime + 1000000, mtime + 1000000))
			self.assertEqual(self.api._redact_response({"detail": "other-secret first-secret"}), {"detail": "<redacted> <redacted>"})
			self.assertEqual(read.call_count, 2)
			self.deps["settings"].load.return_value = {"run.openrouter_key": "read-secret"}
			self.assertTrue(self.api.get_settings({})["ok"])
			self.assertEqual(self.api._redact_response({"detail": "read-secret"}), {"detail": "<redacted>"})
			self.deps["settings"].save.return_value = {"values": {"run.openrouter_key": "save-secret"}, "schedule": {}, "warnings": []}
			self.assertTrue(self.api.save_settings({"changes": {"run.openrouter_key": "save-secret"}})["ok"])
			self.assertEqual(self.api._redact_response({"detail": "save-secret"}), {"detail": "<redacted>"})
			self.assertEqual(read.call_count, 2)


class WindowContractTests(unittest.TestCase):
	def setUp(self):
		for target in ("rfapp.bridge.subprocess.Popen", "rfapp.bridge.subprocess.run", "requests.sessions.Session.request"):
			guard = patch(target, side_effect=AssertionError("Unpatched external effect in window test"))
			guard.start()
			self.addCleanup(guard.stop)
		from rfapp import window
		guard = patch.object(window.ctypes, "WinDLL", side_effect=AssertionError("Unpatched native API in window contract test"), create=True)
		guard.start()
		self.addCleanup(guard.stop)

	def test_window_options_and_page_seam(self):
		from rfapp import window
		webview = SimpleNamespace(create_window=Mock())
		deps = {"prefs": SimpleNamespace(load=lambda: {"window": {"width": 1000, "height": 700, "x": 1, "y": 2, "maximized": True}}), "i18n": SimpleNamespace(current=lambda: SimpleNamespace(t=lambda key: "Localized"))}
		api = Bridge(dependencies=deps)
		with patch.dict(sys.modules, {"webview": webview}), patch.object(window, "_theme", return_value="dark"):
			window._create_window(api, True, Path("scratch.html"))
		options = webview.create_window.call_args.kwargs
		self.assertEqual(options["url"], "scratch.html")
		self.assertEqual(options["min_size"], (860, 600))
		self.assertIs(options["text_select"], True)
		self.assertTrue(options["hidden"])
		self.assertEqual(options["background_color"], "#0F0F0F")
		self.assertIs(options["js_api"], api)

	def test_sender_waits_until_ui_ready_and_batches(self):
		from rfapp import window
		runtime = window._Runtime.__new__(window._Runtime)
		runtime._stop = threading.Event()
		runtime._queue = []
		runtime._bridge = Bridge()
		runtime._window = Mock()
		runtime._tray = Mock()
		runtime._visible = True
		runtime._pending_runs = set()
		class Condition:
			def __enter__(self): return self
			def __exit__(self, *args): pass
			def notify(self): pass
			def wait_for(self, predicate):
				runtime._window.evaluate_js.assert_not_called()
				self_test.assertFalse(predicate())
				runtime._bridge.ui_ready({})
				self_test.assertTrue(predicate())
			def wait(self, seconds): self_test.assertEqual(seconds, .25)
		self_test = self
		runtime._condition = Condition()
		runtime.push("overview.changed", {})
		runtime.push("navigate", {"route": "#/live"})
		sent = Mock(side_effect=lambda *args: runtime._stop.set())
		runtime._dispatch_batch = sent
		runtime._send()
		self.assertEqual(sent.call_count, 1)
		self.assertEqual(len(sent.call_args.args[0]), 2)

	def test_close_to_tray_hint_once_geometry_and_quit_no_farmer_stop(self):
		from rfapp import window
		p = {"close_to_tray": True, "tray_hint_shown": False}
		api = Bridge(dependencies={"prefs": SimpleNamespace(load=lambda: dict(p), save=lambda changes: p.update(changes)), "i18n": SimpleNamespace(current=lambda: SimpleNamespace(t=lambda key: key))})
		runtime = window._Runtime.__new__(window._Runtime)
		runtime._bridge = api
		runtime._window = Mock(width=1100, height=760, x=20, y=30)
		runtime._window.native.WindowState = "Normal"
		runtime._quitting = False
		runtime._condition = threading.Condition()
		runtime._web_sleep = Mock()
		runtime._change_waiter = None
		runtime._cleanup = Mock()
		with patch("rfapp.notify.toast", return_value=True) as toast:
			self.assertFalse(runtime._on_closing())
			self.assertFalse(runtime._on_closing())
			toast.assert_called_once()
		self.assertTrue(p["tray_hint_shown"])
		self.assertEqual(p["window"]["width"], 1100)
		runtime._cleanup.assert_not_called()
		runtime.quit()
		runtime.quit()
		runtime._cleanup.assert_called_once()
		runtime._window.destroy.assert_called_once()
		self.assertFalse(api._stop_threads)

	def test_close_without_tray_allows_window_close(self):
		from rfapp import window
		runtime = window._Runtime.__new__(window._Runtime)
		runtime._bridge = Bridge(dependencies={"prefs": SimpleNamespace(load=lambda: {"close_to_tray": False})})
		runtime._quitting = False
		runtime._save_window = Mock()
		runtime._cleanup = Mock()
		self.assertTrue(runtime._on_closing())
		runtime._cleanup.assert_called_once()
		self.assertTrue(runtime._quitting)

	def test_runtime_starts_every_owned_worker(self):
		from rfapp import window
		runtime = window._Runtime.__new__(window._Runtime)
		runtime._instance = Mock()
		runtime._bridge = Bridge(dependencies={"schedule": SimpleNamespace(ensure=Mock(return_value={"mode": "manual"}), from_settings=Mock(return_value=SimpleNamespace(mode="manual", active_accounts=0))), "prefs": SimpleNamespace(load=lambda: {}), "settings": SimpleNamespace(load=lambda: {}, user_data_dir=lambda: Path("synthetic")), "runs": SimpleNamespace(LiveTail=Mock())})
		runtime._threads = []
		runtime.show = Mock()
		runtime._send = Mock()
		runtime._observe = Mock()
		runtime._updates = Mock()
		runtime._window = Mock()
		runtime._visible = False
		runtime._web_sleep = Mock()
		runtime._change_waiter = Mock()
		runtime._new_waiter = Mock(return_value=runtime._change_waiter)
		runtime.quit = Mock()
		with patch.object(window.threading, "Thread") as constructor, patch.object(window.paths, "env_path", return_value=Path("no-synthetic-env")), patch.object(window.paths, "runs_dir", return_value=Path("no-synthetic-runs")):
			runtime.start()
		self.assertEqual(constructor.call_count, 3)
		self.assertEqual(constructor.return_value.start.call_count, 3)
		runtime._tail.start.assert_not_called()
		runtime.quit.assert_not_called()

	def test_refresh_context_preserves_live_tail_object(self):
		from rfapp import window
		runtime = window._Runtime.__new__(window._Runtime)
		runtime._bridge = Bridge(dependencies={"prefs": SimpleNamespace(load=lambda: {"account_labels": {"account-1": "Renamed"}}), "settings": SimpleNamespace(user_data_dir=lambda: Path("new-profile-root"))})
		runtime._context_lock = threading.Lock()
		runtime._tail = Mock()
		runtime._tray = Mock()
		runtime._change_waiter = None
		tail = runtime._tail
		runtime._refresh_context()
		self.assertIs(runtime._tail, tail)
		tail.set_context.assert_called_once_with({"account-1": "Renamed"}, str(Path("new-profile-root")))
		runtime._tray.refresh.assert_called_once()

	def test_daily_update_worker_stops_at_owned_event(self):
		from rfapp import window
		runtime = window._Runtime.__new__(window._Runtime)
		runtime._bridge = SimpleNamespace(check_updates=Mock(return_value={"ok": True}))
		runtime._stop = Mock()
		runtime._stop.is_set.return_value = False
		runtime._stop.wait.return_value = True
		runtime._updates()
		runtime._bridge.check_updates.assert_called_once_with({})
		runtime._stop.wait.assert_called_once_with(86400)
