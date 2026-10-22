import _bootstrap  # noqa: F401

import ctypes
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from rfapp import accounts_admin as admin, prefs, settings, signin
from rfapp.envfile import EnvFile
import run_lock

_REAL_LOCK_STATUS = run_lock.status


class AccountsAdminTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.root = Path(self.temp.name)
		self.profiles = self.root / "profiles"
		self.profiles.mkdir()
		admin._identity_cache.clear()
		self.addCleanup(admin._identity_cache.clear)
		item = patch.dict(os.environ, {"REWARDS_FARMER_DATA_HOME": str(self.root), "USER_DATA_DIR": str(self.profiles), "LOCALAPPDATA": str(self.root)})
		item.start()
		self.addCleanup(item.stop)
		(self.root / ".env").write_text("USER_DATA_DIR=" + str(self.profiles) + "\n")
		translator = ModuleType("rfapp.i18n")
		translator.current = lambda: SimpleNamespace(t=lambda key, **params: f"Account {params['number']}")
		item = patch.dict("sys.modules", {"rfapp.i18n": translator})
		item.start()
		self.addCleanup(item.stop)
		import rfapp
		item = patch.object(rfapp, "i18n", translator, create=True)
		item.start()
		self.addCleanup(item.stop)
		for target in ("rfapp.schedule.ensure", "rfapp.schedule.remove", "run_lock.status"):
			item = patch(target, return_value=None)
			mock = item.start()
			self.addCleanup(item.stop)
			setattr(self, target.rsplit(".", 1)[-1], mock)
		for target in ("rfapp.accounts_admin.subprocess.Popen", "rfapp.accounts_admin._recycle"):
			item = patch(target, side_effect=AssertionError("Unpatched external effect in account test"))
			item.start()
			self.addCleanup(item.stop)

	def accounts(self, active, paused=()):
		editor = EnvFile.load(self.root / ".env")
		editor.set("REWARDS_ACCOUNTS", ",".join(active))
		editor.set("REWARDS_PAUSED_ACCOUNTS", ",".join(paused))
		editor.save()
		for name in [*active, *paused]:
			(self.profiles / name).mkdir(exist_ok=True)

	def assert_code(self, code, function, *args):
		with self.assertRaises(ValueError) as caught:
			function(*args)
		self.assertEqual(caught.exception.code, code)

	def test_fresh_setup_has_no_accounts(self):
		self.assertEqual(admin._list_accounts(), [])
		self.assertEqual(admin._free_ids(), ["account-1"])
		self.assertFalse((self.profiles / "Local State").exists())

	def test_root_only_with_local_state_and_profile_path(self):
		(self.profiles / "Local State").write_text("synthetic")
		rows = admin._list_accounts()
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0], {"id": "@root", "label": "Account 1", "dir": str(self.profiles), "paused": False, "is_root": True, "signed_in": None, "balance": None, "balance_at": None, "last_run_at": None, "last_result": None, "email": None, "name": None, "picture_rev": None})

	def local_state(self, account_id, info):
		profile = self.profiles if account_id == "@root" else self.profiles / account_id
		profile.mkdir(exist_ok=True)
		path = profile / "Local State"
		path.write_text(json.dumps({"profile": {"info_cache": info}}), encoding="utf-8")
		return path

	def test_profile_identity_reads_each_own_default_without_private_side_effects(self):
		self.accounts(["account-1"], ["account-2"])
		self.local_state("@root", {"Default": {"user_name": "root@example.invalid", "gaia_name": "Synthetic Root"}})
		self.local_state("account-1", {"Default": {"user_name": "first@example.invalid", "gaia_name": "Synthetic First"}, "Profile 1": {"user_name": "other@example.invalid", "gaia_name": "Wrong Profile"}})
		self.local_state("account-2", {"Default": {"user_name": "second@example.invalid", "gaia_name": "Synthetic Second"}})
		before_env = (self.root / ".env").read_bytes()
		with patch.object(admin._logger, "log") as log, patch.object(admin._logger, "warning") as warning, patch.object(admin._logger, "exception") as exception:
			rows = admin.list_accounts()["accounts"]
			log.assert_not_called()
			warning.assert_not_called()
			exception.assert_not_called()
		self.assertEqual([(row["email"], row["name"]) for row in rows], [("first@example.invalid", "Synthetic First"), ("second@example.invalid", "Synthetic Second")])
		self.assertEqual((self.root / ".env").read_bytes(), before_env)
		self.assertFalse((self.root / "app-prefs.json").exists())
		self.assertFalse((self.root / "app.log").exists())
		self.assertFalse((self.root / "runs").exists())
		self.ensure.assert_not_called()
		self.remove.assert_not_called()

	def test_root_identity_uses_the_default_profile_in_root_local_state(self):
		self.local_state("@root", {"Default": {"user_name": "root@example.invalid", "gaia_name": "Synthetic Root"}})
		row = admin.list_accounts()["accounts"][0]
		self.assertEqual((row["id"], row["email"], row["name"]), ("@root", "root@example.invalid", "Synthetic Root"))

	def test_identity_missing_locked_malformed_or_oversized_is_null(self):
		self.accounts(["account-1"])
		unknown = {"email": None, "name": None}
		self.assertEqual(admin._profile_identity("account-1"), unknown)
		path = self.local_state("account-1", {"Default": {"user_name": "first@example.invalid", "gaia_name": "Synthetic First"}})
		with patch("rfapp.accounts_admin._open_shared", side_effect=PermissionError("locked")):
			self.assertEqual(admin._profile_identity("account-1"), unknown)
		for text in ("{", "[]", "null", '{"profile": []}', '{"profile": {"info_cache": {"Default": null}}}'):
			with self.subTest(text=text):
				admin._identity_cache.clear()
				path.write_text(text, encoding="utf-8")
				self.assertEqual(admin._profile_identity("account-1"), unknown)
		path.write_bytes(b"x" * (admin._LOCAL_STATE_LIMIT + 1))
		with patch("rfapp.accounts_admin._open_shared", side_effect=AssertionError("Oversized file must not be read")):
			self.assertEqual(admin._profile_identity("account-1"), unknown)

	def test_identity_ignores_non_address_and_non_string_fields(self):
		self.accounts(["account-1"])
		for info, expected in [({"user_name": "local-user", "gaia_name": "Synthetic Name"}, {"email": None, "name": "Synthetic Name"}), ({"user_name": 1, "gaia_name": []}, {"email": None, "name": None}), ({"user_name": "valid@example.invalid", "gaia_name": ""}, {"email": "valid@example.invalid", "name": None})]:
			with self.subTest(info=info):
				admin._identity_cache.clear()
				self.local_state("account-1", {"Default": info})
				self.assertEqual(admin._profile_identity("account-1"), expected)

	def test_identity_cache_tracks_path_mtime_and_size_and_recovers_after_missing(self):
		self.accounts(["account-1", "account-2"])
		path = self.local_state("account-1", {"Default": {"user_name": "first@example.invalid", "gaia_name": "One"}})
		self.local_state("account-2", {"Default": {"user_name": "other@example.invalid", "gaia_name": "Two"}})
		with patch("rfapp.accounts_admin._open_shared", wraps=admin._open_shared) as opened:
			first = admin._profile_identity("account-1")
			first["email"] = "caller-change@example.invalid"
			self.assertEqual(admin._profile_identity("account-1")["email"], "first@example.invalid")
			self.assertEqual(opened.call_count, 1)
			self.assertEqual(admin._profile_identity("account-2")["email"], "other@example.invalid")
			self.assertEqual(opened.call_count, 2)
			stat = path.stat()
			self.local_state("account-1", {"Default": {"user_name": "third@example.invalid", "gaia_name": "One"}})
			self.assertEqual(path.stat().st_size, stat.st_size)
			os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10000000))
			self.assertEqual(admin._profile_identity("account-1")["email"], "third@example.invalid")
			self.assertEqual(opened.call_count, 3)
			stat = path.stat()
			self.local_state("account-1", {"Default": {"user_name": "longer-address@example.invalid", "gaia_name": "One"}})
			os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
			self.assertEqual(admin._profile_identity("account-1")["email"], "longer-address@example.invalid")
			self.assertEqual(opened.call_count, 4)
			path.unlink()
			self.assertEqual(admin._profile_identity("account-1"), {"email": None, "name": None})
			self.local_state("account-1", {"Default": {"user_name": "back@example.invalid", "gaia_name": "Back"}})
			self.assertEqual(admin._profile_identity("account-1")["email"], "back@example.invalid")
			self.assertEqual(opened.call_count, 5)

	@unittest.skipUnless(os.name == "nt", "Windows shared-delete reader")
	def test_local_state_shared_reader_allows_atomic_replacefile(self):
		self.accounts(["account-1"])
		path = self.local_state("account-1", {"Default": {"user_name": "first@example.invalid", "gaia_name": "Before"}})
		replacement = path.with_suffix(".tmp")
		replacement.write_text(json.dumps({"profile": {"info_cache": {"Default": {"user_name": "after@example.invalid", "gaia_name": "After"}}}}), encoding="utf-8")
		from ctypes import wintypes
		replace = ctypes.WinDLL("kernel32", use_last_error=True).ReplaceFileW
		replace.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p]
		replace.restype = wintypes.BOOL
		with path.open("rb"):
			self.assertFalse(replace(str(path), str(replacement), None, 0, None, None))
			self.assertIn(ctypes.get_last_error(), (5, 32))
		with admin._open_shared(path) as reader:
			self.assertTrue(replace(str(path), str(replacement), None, 0, None, None), ctypes.get_last_error())
			self.assertIn(b"first@example.invalid", reader.read())
		self.assertEqual(admin._profile_identity("account-1"), {"email": "after@example.invalid", "name": "After"})

	def test_new_ids_skip_folders_and_active_paused_entries(self):
		self.accounts(["account-2"], ["account-4"])
		(self.profiles / "account-1").mkdir()
		self.assertEqual(admin._free_ids(2), ["account-3", "account-5"])

	def assert_add_unsupported_without_effects(self):
		before_env = (self.root / ".env").read_bytes()
		before_tree = {str(path.relative_to(self.root)): path.read_bytes() if path.is_file() else None for path in self.root.rglob("*")}
		with patch("rfapp.accounts_admin._ensure_idle", side_effect=AssertionError("Refusal must precede lock work")) as idle, patch("rfapp.accounts_admin.migrate_root_profile", side_effect=AssertionError("Refusal must precede migration")) as migrate, patch("rfapp.accounts_admin._migrate_root_profile", side_effect=AssertionError("Refusal must precede migration")) as private_migrate, patch("rfapp.accounts_admin._start_signin", side_effect=AssertionError("Refusal must precede sign-in")) as start:
			for confirm in (False, True):
				with self.subTest(confirm_migration=confirm):
					self.assert_code("accounts.add_unsupported", admin.add_account, confirm)
			idle.assert_not_called()
			migrate.assert_not_called()
			private_migrate.assert_not_called()
			start.assert_not_called()
		self.assertEqual((self.root / ".env").read_bytes(), before_env)
		self.assertEqual({str(path.relative_to(self.root)): path.read_bytes() if path.is_file() else None for path in self.root.rglob("*")}, before_tree)
		self.assertFalse((self.root / "runs").exists())
		self.status.assert_not_called()
		self.ensure.assert_not_called()
		self.remove.assert_not_called()

	def test_add_named_account_refuses_without_any_effect(self):
		self.accounts(["account-1"])
		self.assert_add_unsupported_without_effects()

	def test_add_root_account_refuses_even_with_migration_confirmation(self):
		(self.profiles / "Local State").write_text("synthetic")
		self.assert_add_unsupported_without_effects()

	def test_add_several_configured_accounts_refuses_without_any_effect(self):
		self.accounts(["account-1", "account-2", "existing-cli-profile"])
		self.assert_add_unsupported_without_effects()

	def test_add_paused_account_also_refuses_without_any_effect(self):
		self.accounts([], ["account-1"])
		self.assert_add_unsupported_without_effects()

	def test_add_first_account_remains_allowed(self):
		self.assertEqual(admin.list_accounts(), {"accounts": []})
		with patch("rfapp.accounts_admin._start_signin", return_value={"state": "starting"}) as start, patch("rfapp.accounts_admin.migrate_root_profile") as migrate:
			self.assertEqual(admin.add_account(), {"account_id": "account-1", "signin": {"state": "starting"}})
			start.assert_called_once_with("account-1", True)
			migrate.assert_not_called()

	def test_add_busy_or_other_signin_refuses(self):
		self.status.return_value = SimpleNamespace(owner="farmer")
		self.assert_code("run.busy", admin.add_account)
		self.status.return_value = None
		signin._write_status("account-1", "waiting")
		self.assert_code("accounts.signin_active", admin.add_account)

	def test_detached_signin_and_breakaway_retry(self):
		with patch("rfapp.accounts_admin.subprocess.Popen", side_effect=[OSError("breakaway"), SimpleNamespace(pid=22)]) as popen:
			result = admin._start_signin("account-1", True)
		self.assertEqual(result["state"], "starting")
		self.assertEqual(json.loads((self.root / "runs" / "signin-account-1.json").read_text())["pid"], 0)
		self.assertEqual(popen.call_count, 2)
		first, second = popen.call_args_list
		self.assertEqual(first.args[0][-3:], ["--signin", "account-1", "--new"])
		self.assertEqual(second.kwargs["cwd"], self.root)
		self.assertEqual(first.kwargs["creationflags"] & ~getattr(admin.subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0), second.kwargs["creationflags"])

	def test_process_launch_failure_is_recorded(self):
		with patch("rfapp.accounts_admin.subprocess.Popen", side_effect=OSError("denied")), self.assertLogs("rfapp", "ERROR"):
			status = admin._start_signin("account-1", True)
		self.assertEqual(status["state"], "failed")
		self.assertIn("denied", status["detail"])

	def test_status_grace_dead_pid_and_cancel(self):
		starting = signin._write_status("account-1", "starting")
		with patch("rfapp.accounts_admin._pid_alive", return_value=False):
			self.assertEqual(admin._signin_status("account-1")["state"], "starting")
			starting["at"] = (datetime.now(timezone.utc) - timedelta(seconds=31)).isoformat().replace("+00:00", "Z")
			(self.root / "runs" / "signin-account-1.json").write_text(json.dumps(starting))
			self.assertEqual(admin._signin_status("account-1")["error_code"], "crashed")
			signin._write_status("account-1", "waiting")
			self.assertEqual(admin._signin_status("account-1")["state"], "failed")
		admin._cancel_signin("account-1")
		self.assertTrue((self.root / "runs" / "signin-account-1.cancel").is_file())
		self.assert_code("accounts.unknown", admin._signin_status, "missing")

	def test_rename_only_prefs_trims_no_folder_change(self):
		self.accounts(["account-1"])
		before = (self.root / ".env").read_bytes()
		row = admin._rename_account("account-1", "  Mine  ")
		self.assertEqual(row["label"], "Mine")
		self.assertTrue((self.profiles / "account-1").is_dir())
		self.assertEqual((self.root / ".env").read_bytes(), before)
		for label in ("", "a" * 41, "has\ncontrol"):
			self.assert_code("bad_request", admin._rename_account, "account-1", label)
		self.assert_code("accounts.unknown", admin._rename_account, "missing", "label")

	def test_pause_resume_appends_and_rebuilds_task(self):
		self.accounts(["account-1", "account-2"], ["account-3"])
		row = admin._set_account_paused("account-1", True)
		self.assertTrue(row["paused"])
		self.assertEqual(settings.active_accounts(), ["account-2"])
		self.assertEqual(settings.paused_accounts(), ["account-3", "account-1"])
		self.assert_code("accounts.last_active", admin._set_account_paused, "account-2", True)
		admin._set_account_paused("account-1", False)
		self.assertEqual(settings.active_accounts(), ["account-2", "account-1"])
		self.assertEqual(self.ensure.call_count, 2)

	def test_resume_paused_account_with_root_never_writes_root_id(self):
		self.accounts([], ["account-2"])
		(self.profiles / "Local State").write_text("{}")
		self.assertEqual(settings.active_accounts(), ["@root"])
		self.assertEqual([row["id"] for row in admin.list_accounts()["accounts"]], ["@root", "account-2"])
		row = admin.set_account_paused("account-2", False)["account"]
		self.assertEqual(row["id"], "account-2")
		self.assertFalse(row["paused"])
		editor = EnvFile.load(self.root / ".env")
		self.assertEqual(editor.get("REWARDS_ACCOUNTS"), "account-2")
		self.assertIsNone(editor.get("REWARDS_PAUSED_ACCOUNTS"))
		self.assertNotIn("@root", (self.root / ".env").read_text())
		self.assertEqual(settings.active_accounts(), ["account-2"])
		self.assertTrue((self.profiles / "Local State").exists())
		self.ensure.assert_called_once()

	def test_root_cannot_pause_or_remove_without_data(self):
		(self.profiles / "Local State").touch()
		self.assert_code("accounts.last_active", admin._set_account_paused, "@root", True)
		self.assert_code("accounts.root_needs_delete", admin._remove_account, "@root", False)

	def test_remove_keeps_data_without_delete_and_last_removes_task(self):
		self.accounts(["account-1", "account-2"])
		prefs.save({"account_labels": {"account-1": "Mine"}, "goals": {"account-1": 5000}})
		result = admin._remove_account("account-1", False)
		self.assertEqual([row["id"] for row in result["accounts"]], ["account-2"])
		self.assertTrue((self.profiles / "account-1").is_dir())
		self.assertNotIn("account-1", prefs.load()["goals"])
		self.ensure.assert_called_once()
		admin._remove_account("account-2", False)
		self.remove.assert_called_once()
		self.assertIsNone(EnvFile.load(self.root / ".env").get("REWARDS_ACCOUNTS"))
		self.assertEqual(settings.active_accounts(), [])

	def test_remove_failed_recycle_returns_path_warning_and_keeps_folder(self):
		self.accounts(["account-1"])
		with patch("rfapp.accounts_admin._recycle", return_value=False):
			result = admin._remove_account("account-1", True)
		self.assertTrue((self.profiles / "account-1").is_dir())
		self.assertEqual(result["accounts"], [])
		self.assertEqual(result["warnings"][0]["params"]["path"], str(self.profiles / "account-1"))

	def test_remove_busy_does_not_recycle_or_change_env(self):
		self.accounts(["account-1"])
		before = (self.root / ".env").read_bytes()
		self.status.return_value = SimpleNamespace(owner="signin")
		with patch("rfapp.accounts_admin._recycle") as recycle:
			self.assert_code("run.busy", admin._remove_account, "account-1", True)
			recycle.assert_not_called()
		self.assertEqual((self.root / ".env").read_bytes(), before)

	def root_tree(self):
		(self.profiles / "Local State").write_text("synthetic key")
		(self.profiles / "Default").mkdir()
		(self.profiles / "Default" / "Cookies").write_text("synthetic cookies")
		(self.profiles / "lockfile").write_text("synthetic lock")
		for name in admin._LOCK_NAMES:
			(self.profiles / name).touch()
		(self.profiles / "old-profile").mkdir()
		(self.profiles / "old-profile" / "Local State").touch()
		prefs.save({"account_labels": {"@root": "Mine", "old-profile": "Old"}, "goals": {"@root": 5000}})
		self.status.return_value = SimpleNamespace(owner="signin", pid=os.getpid())

	def test_root_migration_leaves_locks_other_profiles_moves_label_goal(self):
		self.root_tree()
		self.assertEqual(admin._migrate_root_profile("account-2"), "account-1")
		self.assertEqual(settings.active_accounts(), ["account-1"])
		self.assertFalse((self.profiles / "Local State").exists())
		self.assertEqual((self.profiles / "account-1" / "Local State").read_text(), "synthetic key")
		self.assertEqual((self.profiles / "account-1" / "Default" / "Cookies").read_text(), "synthetic cookies")
		self.assertTrue((self.profiles / "old-profile" / "Local State").exists())
		self.assertTrue(all((self.profiles / name).exists() for name in admin._LOCK_NAMES))
		self.assertEqual(prefs.load()["account_labels"]["account-1"], "Mine")
		self.assertEqual(prefs.load()["goals"], {"account-1": 5000})
		self.assertNotIn("@root", prefs.load()["account_labels"])

	def test_root_migration_rollback_on_rename_failure(self):
		self.root_tree()
		before = (self.root / ".env").read_bytes()
		rename = os.rename
		calls = []
		def fail(source, target):
			calls.append((Path(source), Path(target)))
			if Path(source).name == "Local State":
				raise PermissionError("Edge lockfile")
			return rename(source, target)
		with patch("rfapp.accounts_admin.os.rename", side_effect=fail):
			self.assert_code("profile_in_use", admin._migrate_root_profile, "account-2")
		self.assertEqual((self.root / ".env").read_bytes(), before)
		self.assertTrue((self.profiles / "Local State").exists())
		self.assertTrue((self.profiles / "Default" / "Cookies").exists())
		self.assertFalse((self.profiles / "account-1").exists())
		self.assertEqual(prefs.load()["goals"], {"@root": 5000})
		self.assertTrue(any(source.parent.name == "account-1" for source, target in calls))

	def test_migration_refuses_without_owned_signin_lock(self):
		(self.profiles / "Local State").touch()
		for info in (None, SimpleNamespace(owner="farmer", pid=os.getpid()), SimpleNamespace(owner="signin", pid=os.getpid() + 1)):
			self.status.return_value = info
			self.assert_code("run.busy", admin._migrate_root_profile, "account-2")
		self.assertFalse((self.profiles / "account-1").exists())

	def test_migration_env_failure_rolls_back_without_changing_prefs(self):
		self.root_tree()
		with patch("rfapp.accounts_admin.EnvFile.save", side_effect=OSError("read only")):
			self.assert_code("profile_in_use", admin._migrate_root_profile, "account-2")
		self.assertTrue((self.profiles / "Local State").exists())
		self.assertTrue((self.profiles / "Default" / "Cookies").exists())
		self.assertFalse((self.profiles / "account-1").exists())
		self.assertEqual(prefs.load()["goals"], {"@root": 5000})

	def test_list_accounts_uses_newest_state_balance_and_profile_identity(self):
		self.accounts(["default"], ["account-2"])
		prefs.save({"account_labels": {"default": "Named default"}})
		from run_record import append_event
		path = self.root / "runs" / "20261014T100000Z-abcd.jsonl"
		path.parent.mkdir(exist_ok=True)
		append_event(str(path), "run_start", "20261014T100000Z-abcd", trigger="app", accounts=["default", "account-2"])
		append_event(str(path), "account_start", "20261014T100000Z-abcd", account="default", profile=str(self.profiles / "default"))
		append_event(str(path), "state", "20261014T100000Z-abcd", account="default", phase="before", balance=10, unreadable=None)
		append_event(str(path), "state", "20261014T100000Z-abcd", account="default", phase="after", balance=15, unreadable=None)
		append_event(str(path), "account_start", "20261014T100000Z-abcd", account="account-2", profile=str(self.profiles / "account-2"))
		append_event(str(path), "state", "20261014T100000Z-abcd", account="account-2", phase="before", balance=None, unreadable="page_blank")
		append_event(str(path), "run_end", "20261014T100000Z-abcd", result="ok")
		rows = admin._list_accounts()
		self.assertEqual(rows[0]["id"], "default")
		self.assertFalse(rows[0]["is_root"])
		self.assertEqual(rows[0]["label"], "Named default")
		self.assertEqual(rows[0]["balance"], 15)
		self.assertTrue(rows[0]["signed_in"])
		self.assertEqual(rows[0]["last_result"], "ok")
		self.assertTrue(rows[1]["paused"])
		self.assertIsNone(rows[1]["signed_in"])

	def test_recent_signin_status_informs_account_without_run(self):
		self.accounts(["account-1"])
		signin._write_status("account-1", "signed_in")
		self.assertTrue(admin._list_accounts()[0]["signed_in"])

	def test_root_removal_surviving_local_state_preserves_settings(self):
		self.root_tree()
		self.status.return_value = None
		before = (self.root / ".env").read_bytes()
		(self.profiles / "notes.txt").write_text("keep this file")
		(self.profiles / "unrelated-folder").mkdir()
		with patch("rfapp.accounts_admin._recycle", return_value=False) as recycle:
			self.assert_code("accounts.root_recycle_failed", admin._remove_account, "@root", True)
		entries = recycle.call_args.args[0]
		self.assertEqual(entries, [self.profiles / "Default", self.profiles / "Local State"])
		self.assertEqual((self.root / ".env").read_bytes(), before)
		self.assertEqual((self.profiles / "notes.txt").read_text(), "keep this file")
		self.assertTrue((self.profiles / "unrelated-folder").is_dir())
		self.assertEqual(settings.active_accounts(), ["@root"])
		self.assertEqual(prefs.load()["goals"], {"@root": 5000})
		self.ensure.assert_not_called()
		self.remove.assert_not_called()

	def test_root_removal_recycles_only_default_and_local_state(self):
		self.root_tree()
		self.status.return_value = None
		(self.profiles / "notes.txt").write_text("keep this file")
		(self.profiles / "unrelated-folder").mkdir()
		(self.profiles / "unrelated-folder" / "document.txt").write_text("keep this document")
		def recycle(entries):
			self.assertEqual(entries, [self.profiles / "Default", self.profiles / "Local State"])
			shutil.rmtree(self.profiles / "Default")
			(self.profiles / "Local State").unlink()
			return True
		with patch("rfapp.accounts_admin._recycle", side_effect=recycle) as recycled:
			result = admin.remove_account("@root", True)
		recycled.assert_called_once()
		self.assertEqual(result, {"accounts": [], "warnings": []})
		self.assertEqual((self.profiles / "notes.txt").read_text(), "keep this file")
		self.assertEqual((self.profiles / "unrelated-folder" / "document.txt").read_text(), "keep this document")
		self.assertEqual((self.profiles / "lockfile").read_text(), "synthetic lock")
		self.assertTrue((self.profiles / "old-profile" / "Local State").exists())
		self.assertTrue(all((self.profiles / name).exists() for name in admin._LOCK_NAMES))
		self.assertFalse((self.profiles / "Default").exists())
		self.assertFalse((self.profiles / "Local State").exists())
		self.assertEqual(prefs.load()["goals"], {})
		self.remove.assert_called_once()

	def test_root_removal_skips_absent_default(self):
		(self.profiles / "Local State").write_text("{}")
		def recycle(entries):
			self.assertEqual(entries, [self.profiles / "Local State"])
			entries[0].unlink()
			return True
		with patch("rfapp.accounts_admin._recycle", side_effect=recycle):
			self.assertEqual(admin.remove_account("@root", True), {"accounts": [], "warnings": []})

	def test_root_partial_recycle_without_local_state_removes_with_warning(self):
		self.root_tree()
		self.status.return_value = None
		def partial(entries):
			self.assertEqual(entries, [self.profiles / "Default", self.profiles / "Local State"])
			(self.profiles / "Local State").unlink()
			return False
		with patch("rfapp.accounts_admin._recycle", side_effect=partial):
			result = admin.remove_account("@root", True)
		self.assertEqual(result["accounts"], [])
		self.assertEqual(result["warnings"][0]["code"], "accounts.recycle_failed")
		self.assertEqual(result["warnings"][0]["params"]["path"], str(self.profiles / "Default"))
		self.assertEqual(prefs.load()["goals"], {})
		self.assertTrue((self.profiles / "Default").exists())
		self.remove.assert_called_once()

	def test_last_named_removal_truthfully_falls_back_to_existing_root(self):
		self.accounts(["account-1"])
		(self.profiles / "Local State").touch()
		result = admin.remove_account("account-1", False)
		self.assertEqual([row["id"] for row in result["accounts"]], ["@root"])
		self.ensure.assert_called_once()
		self.remove.assert_not_called()

	def test_legacy_detection_source_never_inspects_installed_state(self):
		with patch("rfapp.paths.is_frozen", return_value=False), patch.object(Path, "is_file", side_effect=AssertionError("No installed file read")):
			self.assertEqual(admin._legacy_install(), {"found": False, "path": None, "accounts": []})

	def test_legacy_import_shares_profiles_preserves_other_keys(self):
		legacy = self.root / "rewards-farmer"
		legacy.mkdir()
		(legacy / ".env").write_text("QUERY_SOURCE=trends\nREWARDS_ACCOUNTS=old\nLOCAL_LLM_API_KEY=fake-key\nREWARDS_NOTIFY=never\n")
		(legacy / "data-dir" / "old").mkdir(parents=True)
		with patch("rfapp.paths.is_frozen", return_value=True):
			self.assertEqual(admin._legacy_install()["accounts"], ["old"])
			result = admin._import_legacy_install()
		self.assertEqual(result[0]["id"], "old")
		self.assertEqual(settings.user_data_dir(), legacy / "data-dir")
		self.assertEqual(EnvFile.load(self.root / ".env").get("LOCAL_LLM_API_KEY"), "fake-key")
		self.assertIsNone(EnvFile.load(self.root / ".env").get("REWARDS_NOTIFY"))
		self.assertEqual((legacy / ".env").read_text(), "QUERY_SOURCE=trends\nREWARDS_ACCOUNTS=old\nLOCAL_LLM_API_KEY=fake-key\nREWARDS_NOTIFY=never\n")

	def test_pid_observation_and_unsafe_profile_name(self):
		self.assertTrue(admin._pid_alive(os.getpid()))
		self.assertFalse(admin._pid_alive(0))
		self.assert_code("accounts.unknown", admin._profile_path, "../outside")

	def test_public_functions_return_exact_bridge_data_objects(self):
		self.accounts(["account-1", "account-2"])
		self.assertEqual(set(admin.list_accounts()), {"accounts"})
		self.assertEqual(admin.rename_account("account-1", "Mine")["account"]["label"], "Mine")
		self.assertEqual(set(admin.set_account_paused("account-1", True)), {"account"})
		with patch("rfapp.accounts_admin._start_signin", return_value={"account_id": "account-1", "state": "starting"}):
			self.assertEqual(set(admin.sign_in_again("account-1")), {"signin"})
			self.assert_code("accounts.add_unsupported", admin.add_account)
		signin._write_status("account-1", "waiting")
		self.assertEqual(admin.get_signin_status("account-1")["state"], "waiting")
		self.assertEqual(admin.cancel_signin("account-1"), {})
		signin._write_status("account-1", "cancelled")
		self.assertEqual(set(admin.get_legacy_install()), {"found", "path", "accounts"})
		self.assert_code("accounts.legacy_not_found", admin.import_legacy_install)
		with patch("rfapp.accounts_admin._recycle", return_value=False):
			self.assertEqual(set(admin.remove_account("account-1", True)), {"accounts", "warnings"})

	def test_public_migration_signature(self):
		self.root_tree()
		self.assertEqual(admin.migrate_root_profile("account-2"), "account-1")

	def test_root_migration_with_real_temporary_run_lock(self):
		self.root_tree()
		with patch("run_lock.status", _REAL_LOCK_STATUS):
			lock = run_lock.acquire("signin", account="account-2", directory=str(self.profiles))
			self.assertIsNotNone(lock)
			try:
				self.assertEqual(admin.migrate_root_profile("account-2"), "account-1")
				self.assertEqual(_REAL_LOCK_STATUS(str(self.profiles)).owner, "signin")
			finally:
				lock.release()
			self.assertIsNone(_REAL_LOCK_STATUS(str(self.profiles)))
		self.assertTrue((self.profiles / ".rewards-farmer.lock").exists())
		self.assertFalse((self.profiles / ".rewards-farmer.lock.json").exists())


if __name__ == "__main__":
	unittest.main()
