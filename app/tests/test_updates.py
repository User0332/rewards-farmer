import _bootstrap  # noqa: F401

from datetime import datetime, timedelta, timezone
import importlib
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class UpdateTests(unittest.TestCase):
	def setUp(self):
		guard = patch("requests.sessions.Session.request", side_effect=AssertionError("Unpatched HTTP transport in update test"))
		guard.start()
		self.addCleanup(guard.stop)
		self.p = {"update_check": True, "update_last_checked": None, "update_dismissed_version": None}
		self.prefs = SimpleNamespace(load=lambda: dict(self.p), save=lambda changes: self.p.update(changes))
		with patch.dict(sys.modules, {"rfapp.prefs": self.prefs}):
			self.module = importlib.import_module("rfapp.updates")
		self.patch = patch.object(self.module, "prefs", self.prefs)
		self.patch.start()
		self.addCleanup(self.patch.stop)
		self.module._cached = None
		self.http = patch.object(self.module.requests, "get")
		self.get = self.http.start()
		self.addCleanup(self.http.stop)
		self.get.return_value = Mock(status_code=200)
		self.get.return_value.json.return_value = {"tag_name": "v0.2.0", "html_url": "https://github.com/User0332/rewards-farmer/releases/tag/v0.2.0", "draft": False, "prerelease": False}

	def test_new_release_and_headers(self):
		result = self.module.check()
		self.assertTrue(result["available"])
		self.assertEqual(result["latest"], "0.2.0")
		self.assertEqual(self.get.call_args.kwargs["timeout"], 10)
		self.assertEqual(self.get.call_args.kwargs["headers"]["Accept"], "application/vnd.github+json")
		self.assertNotIn("Authorization", self.get.call_args.kwargs["headers"])

	def test_numeric_version(self):
		self.assertGreater(self.module._version("v1.10.0"), self.module._version("1.9.99"))
		self.assertEqual(self.module._version("1.0"), self.module._version("1.0.0"))
		for value in (None, "v1.0-beta", "junk", "1..2", ""):
			with self.assertRaises(self.module.UpdateError):
				self.module._version(value)

	def test_404_is_no_release(self):
		self.get.return_value.status_code = 404
		self.assertFalse(self.module.check()["available"])
		self.get.return_value.raise_for_status.assert_not_called()

	def test_daily_cache_and_force(self):
		self.module.check()
		self.module.check()
		self.assertEqual(self.get.call_count, 1)
		self.module.check(force=True)
		self.assertEqual(self.get.call_count, 2)

	def test_disabled_and_expired(self):
		self.p["update_check"] = False
		self.assertFalse(self.module.check()["available"])
		self.get.assert_not_called()
		self.p.update(update_check=True, update_last_checked=(datetime.now(timezone.utc) - timedelta(hours=25)).isoformat())
		self.assertTrue(self.module.check()["available"])

	def test_dismissed_version(self):
		self.p["update_dismissed_version"] = "0.2.0"
		self.assertFalse(self.module.check()["available"])

	def test_draft_and_prerelease(self):
		for key in ("draft", "prerelease"):
			self.get.return_value.json.return_value = {key: True}
			self.assertFalse(self.module.check(force=True)["available"])

	def test_network_failure_retains_daily_backoff(self):
		self.get.side_effect = self.module.requests.ConnectionError("offline")
		with self.assertRaises(self.module.UpdateError):
			self.module.check()
		self.assertIsNotNone(self.p["update_last_checked"])
		self.assertFalse(self.module.check()["available"])
		self.assertEqual(self.get.call_count, 1)

	def test_untrusted_release_page_fails_closed(self):
		self.get.return_value.json.return_value["html_url"] = "https://github.com/attacker/project/releases/tag/v0.2.0"
		with self.assertRaises(self.module.UpdateError):
			self.module.check()

	def test_blocked_bridge_update_allows_goal_and_settings_without_lost_fields(self):
		from rfapp.bridge import Bridge
		from rfapp import bridge
		with tempfile.TemporaryDirectory() as tmp:
			home = Path(tmp)
			path = home / "app-prefs.json"
			path.write_text(json.dumps(self.p), encoding="utf-8")
			file_lock = threading.RLock()
			def load():
				with file_lock:
					return json.loads(path.read_text(encoding="utf-8"))
			def save(changes):
				# Match prefs.save's own file synchronization while testing the bridge transaction.
				with file_lock:
					state = load()
					state.update(changes)
					path.write_text(json.dumps(state), encoding="utf-8")
					return state
			prefs = SimpleNamespace(load=load, save=save)
			def save_settings(changes):
				save({"synthetic_saved_setting": changes["notify.policy"]})
				return {"values": dict(changes), "schedule": {"mode": "manual"}, "warnings": []}
			account = {"id": "account-1", "paused": False}
			deps = {"updates": self.module, "prefs": prefs, "settings": SimpleNamespace(load=lambda: {}, save=save_settings, validate=lambda *args: {}, user_data_dir=lambda: home), "accounts_admin": SimpleNamespace(list_accounts=lambda: {"accounts": [account]}), "runs": SimpleNamespace(overview=lambda *args: {"accounts": [{"account_id": "account-1", "goal": {"target": load()["goals"]["account-1"]}}]}), "schedule": SimpleNamespace(status=lambda: {"mode": "manual"})}
			api = Bridge(dependencies=deps)
			self.addCleanup(api._close)
			entered, release, goal_done, settings_done, module_lock_free = (threading.Event() for _ in range(5))
			response = self.get.return_value
			def request(*args, **kwargs):
				entered.set()
				if not release.wait(4):
					raise AssertionError("Update request was not released")
				return response
			self.get.side_effect = request
			results = {}
			def set_goal():
				results["goal"] = api.set_goal({"account_id": "account-1", "target": 1000})
				goal_done.set()
			def set_settings():
				results["settings"] = api.save_settings({"changes": {"notify.policy": "errors"}})
				settings_done.set()
			def module_probe():
				if self.module._lock.acquire(timeout=1):
					module_lock_free.set()
					self.module._lock.release()
			worker = threading.Thread(target=lambda: results.update(update=api.check_updates({"force": True})))
			goal = threading.Thread(target=set_goal)
			settings = threading.Thread(target=set_settings)
			probe = threading.Thread(target=module_probe)
			with patch.object(self.module, "prefs", prefs), patch.object(bridge, "paths", SimpleNamespace(env_path=lambda: home / ".env", runs_dir=lambda: home)):
				worker.start()
				try:
					self.assertTrue(entered.wait(1))
					self.assertIsNotNone(load()["update_last_checked"])
					goal.start()
					settings.start()
					probe.start()
					self.assertTrue(goal_done.wait(1), "Goal waited for the update HTTP call")
					self.assertTrue(settings_done.wait(1), "Settings waited for the update HTTP call")
					self.assertTrue(module_lock_free.wait(1), "HTTP held the update module lock")
				finally:
					release.set()
					for thread in (worker, goal, settings, probe):
						if thread.ident:
							thread.join(5)
				self.assertTrue(all(not thread.is_alive() for thread in (worker, goal, settings, probe)))
				self.assertTrue(all(result["ok"] for result in results.values()), results)
				state = load()
				self.assertEqual(state["goals"], {"account-1": 1000})
				self.assertEqual(state["synthetic_saved_setting"], "errors")
				self.assertEqual(state["update_last_checked"], results["update"]["data"]["checked_at"])
				self.assertEqual(self.module._cached["latest"], "0.2.0")

	def test_request_releases_default_lock_and_observes_later_dismissal(self):
		entered, release, probe_done = (threading.Event() for _ in range(3))
		response = self.get.return_value
		def request(*args, **kwargs):
			entered.set()
			if not release.wait(3):
				raise AssertionError("Default update request was not released")
			return response
		self.get.side_effect = request
		result = {}
		worker = threading.Thread(target=lambda: result.update(self.module.check()))
		def probe():
			with self.module._lock:
				self.p["update_dismissed_version"] = "0.2.0"
				probe_done.set()
		other = threading.Thread(target=probe)
		worker.start()
		try:
			self.assertTrue(entered.wait(1))
			other.start()
			self.assertTrue(probe_done.wait(1))
		finally:
			release.set()
			worker.join(4)
			if other.ident:
				other.join(4)
		self.assertFalse(worker.is_alive())
		self.assertFalse(other.is_alive())
		self.assertEqual(result["latest"], "0.2.0")
		self.assertFalse(result["available"])

	def test_preference_transactions_and_result_cache_use_passed_lock(self):
		class ProbeLock:
			def __init__(self): self.depth, self.entries = 0, 0
			def __enter__(self):
				self.depth += 1
				self.entries += 1
			def __exit__(self, *args): self.depth -= 1
		lock = ProbeLock()
		def load():
			self.assertEqual(lock.depth, 1)
			return dict(self.p)
		def save(changes):
			self.assertEqual(lock.depth, 1)
			self.p.update(changes)
		response = self.get.return_value
		def request(*args, **kwargs):
			self.assertEqual(lock.depth, 0)
			return response
		self.get.side_effect = request
		with patch.object(self.module, "prefs", SimpleNamespace(load=load, save=save)):
			self.assertTrue(self.module.check(mutation_lock=lock)["available"])
		self.assertEqual(lock.entries, 2)
		self.assertEqual(lock.depth, 0)
