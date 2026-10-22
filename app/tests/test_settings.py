import _bootstrap  # noqa: F401

import json
import os
import runpy
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from dotenv import dotenv_values
from rfapp import accounts_admin, bridge, prefs, settings
from rfapp.envfile import EnvFile


class SettingsTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.root = Path(self.temp.name)
		self.env = patch.dict(os.environ, {"REWARDS_FARMER_DATA_HOME": str(self.root)})
		self.env.start()
		self.addCleanup(self.env.stop)
		self.autostart = patch("rfapp.autostart.is_enabled", return_value=False)
		self.autostart.start()
		self.addCleanup(self.autostart.stop)
		self.registry = patch("rfapp.autostart.set_enabled")
		self.registry_mock = self.registry.start()
		self.addCleanup(self.registry.stop)
		self.ensure = patch("rfapp.schedule.ensure", return_value={"in_sync": True, "error_code": None})
		self.ensure_mock = self.ensure.start()
		self.addCleanup(self.ensure.stop)
		self.status = patch("rfapp.schedule.status", return_value={"in_sync": True, "error_code": None})
		self.status_mock = self.status.start()
		self.addCleanup(self.status.stop)
		for target in ("subprocess.Popen", "subprocess.run"):
			item = patch(target, side_effect=AssertionError("Unpatched process in settings test"))
			item.start()
			self.addCleanup(item.stop)

	def envfile(self, text):
		(self.root / ".env").write_text(text, encoding="utf-8")

	def test_all_ids_in_contract_order_and_defaults(self):
		values = settings.load()
		self.assertEqual(tuple(values), settings.SETTING_IDS)
		self.assertEqual(len(values), 27)
		self.assertNotIn("advanced.export", values)
		self.assertEqual(values, settings._DEFAULTS)
		self.assertEqual(settings.active_accounts(), [])
		self.assertEqual(settings.paused_accounts(), [])
		self.assertEqual(settings.user_data_dir(), self.root / "data-dir")
		json.dumps(values)
		self.assertEqual(settings.validate({}, values), {})

	def test_every_direct_key_read_mapping(self):
		data = {"REWARDS_SCHEDULE_MODE": "fixed", "REWARDS_SCHEDULE_WINDOW": "07:15-19:30", "REWARDS_SCHEDULE_CHECKS": "5", "REWARDS_SCHEDULE_TIME": "11:00", "REWARDS_SCHEDULE_DAYS": "mon,wed,fri", "REWARDS_SCHEDULE_RANDOM_DELAY": "30", "REWARDS_SCHEDULE_CATCH_UP": "NO", "REWARDS_SCHEDULE_ON_BATTERY": "YES", "REWARDS_MIN_POINTS": "500", "REWARDS_SKIP_TASKS": "daily_set,quests", "LOCAL_LLM_BASE_URL": "https://example.test/v1", "LOCAL_LLM_MODEL": "model:8b", "OPENROUTER_API_KEY": "synthetic-key", "OPENROUTER_MODEL": "openrouter/free", "REWARDS_NOTIFY": "errors", "REWARDS_WEBHOOK_URL": "https://example.test/hook", "EDGE_BINARY": "C:/edge/msedge.exe", "MSEDGEDRIVER_PATH": "C:/edge/msedgedriver.exe", "REWARDS_FARMER_LOG_LEVEL": "DEBUG", "USER_DATA_DIR": str(self.root / "profiles")}
		self.envfile("\n".join(f"{key}={value}" for key, value in data.items()))
		values = settings.load()
		for setting_id, key in settings._KEYS.items():
			if key in data:
				default = settings._DEFAULTS[setting_id]
				expected = settings._truthy(data[key]) if isinstance(default, bool) else int(data[key]) if isinstance(default, int) else data[key].split(",") if isinstance(default, list) else data[key]
				self.assertEqual(values[setting_id], expected, setting_id)
		self.assertEqual(values["schedule.window_start"], "07:15")
		self.assertEqual(values["schedule.window_end"], "19:30")

	def test_browser_mode_priority_and_write_pairs(self):
		for headless, desktop, expected in [("true", "true", "hidden"), ("1", "0", "hidden"), ("yes", "true", "hidden"), ("no", "YES", "visible"), ("false", "false", "visible"), ("", "true", "visible")]:
			with self.subTest(headless=headless, desktop=desktop):
				self.envfile(f"REWARDS_HEADLESS={headless}\nUSE_VIRTUAL_DESKTOP={desktop}\nSWITCH_BACK_TO_MAIN_DESKTOP=true\nSWITCH_BACK_DELAY_SECONDS=1.5\nCLEANUP_VIRTUAL_DESKTOP=true\n")
				before = (self.root / ".env").read_bytes()
				self.assertEqual(settings.load()["run.browser_mode"], expected)
				self.assertEqual((self.root / ".env").read_bytes(), before)
		for mode in ("visible", "hidden"):
			with self.subTest(mode=mode):
				self.envfile("REWARDS_HEADLESS=true\nUSE_VIRTUAL_DESKTOP=true\nSWITCH_BACK_TO_MAIN_DESKTOP=true\nSWITCH_BACK_DELAY_SECONDS=1.5\nCLEANUP_VIRTUAL_DESKTOP=true\n")
				settings.save({"run.browser_mode": mode})
				data = dotenv_values(self.root / ".env")
				self.assertEqual(data["REWARDS_HEADLESS"], str(mode == "hidden").lower())
				self.assertEqual(data["USE_VIRTUAL_DESKTOP"], "false")
				self.assertEqual(data["SWITCH_BACK_TO_MAIN_DESKTOP"], "true")
				self.assertEqual(data["SWITCH_BACK_DELAY_SECONDS"], "1.5")
				self.assertEqual(data["CLEANUP_VIRTUAL_DESKTOP"], "true")

	def test_unrelated_save_forces_true_virtual_desktop_off(self):
		for value in ("true", "TRUE", "1", "yes", "YES"):
			with self.subTest(value=value):
				self.envfile("QUERY_SOURCE=trends\nUSE_VIRTUAL_DESKTOP=" + value + "\nREWARDS_HEADLESS=false\nSWITCH_BACK_TO_MAIN_DESKTOP=true\nSWITCH_BACK_DELAY_SECONDS=2.5\nCLEANUP_VIRTUAL_DESKTOP=true\n")
				result = settings.save({"notify.policy": "never"})
				data = dotenv_values(self.root / ".env")
				self.assertEqual(data["USE_VIRTUAL_DESKTOP"], "false")
				self.assertEqual(data["REWARDS_HEADLESS"], "false")
				self.assertEqual(data["SWITCH_BACK_TO_MAIN_DESKTOP"], "true")
				self.assertEqual(data["SWITCH_BACK_DELAY_SECONDS"], "2.5")
				self.assertEqual(data["CLEANUP_VIRTUAL_DESKTOP"], "true")
				self.assertEqual(result["values"]["run.browser_mode"], "visible")
		self.ensure_mock.assert_not_called()

	def test_empty_save_also_disables_imported_virtual_desktop_flag(self):
		self.envfile("QUERY_SOURCE=trends\nUSE_VIRTUAL_DESKTOP=true\n")
		settings.save({})
		self.assertEqual(dotenv_values(self.root / ".env")["USE_VIRTUAL_DESKTOP"], "false")
		self.assertNotIn("REWARDS_HEADLESS", dotenv_values(self.root / ".env"))

	def test_imported_virtual_desktop_reads_visible_until_unrelated_save_clears_it(self):
		legacy = self.root / "legacy-desktop"
		legacy.mkdir()
		text = "QUERY_SOURCE=trends\nREWARDS_ACCOUNTS=account-1\nREWARDS_HEADLESS=false\nUSE_VIRTUAL_DESKTOP=true\nSWITCH_BACK_TO_MAIN_DESKTOP=false\nSWITCH_BACK_DELAY_SECONDS=3.5\nCLEANUP_VIRTUAL_DESKTOP=false\n"
		(legacy / ".env").write_text(text, encoding="utf-8")
		with patch.object(accounts_admin, "_ensure_idle"), patch.object(accounts_admin, "_legacy_install", return_value={"found": True, "path": str(legacy), "accounts": ["account-1"]}), patch.object(accounts_admin, "_list_accounts", return_value=[]):
			accounts_admin.import_legacy_install()
		self.assertEqual(dotenv_values(self.root / ".env")["USE_VIRTUAL_DESKTOP"], "true")
		self.assertEqual(settings.load()["run.browser_mode"], "visible")
		settings.save({"notify.policy": "errors"})
		data = dotenv_values(self.root / ".env")
		self.assertEqual(data["USE_VIRTUAL_DESKTOP"], "false")
		self.assertEqual(data["REWARDS_HEADLESS"], "false")
		for key in ("SWITCH_BACK_TO_MAIN_DESKTOP", "SWITCH_BACK_DELAY_SECONDS", "CLEANUP_VIRTUAL_DESKTOP"):
			self.assertEqual(data[key], dotenv_values(legacy / ".env")[key])
		self.assertEqual((legacy / ".env").read_text(encoding="utf-8"), text)
		self.ensure_mock.assert_not_called()

	def test_desktop_mode_is_a_field_error_without_writing_anything(self):
		self.envfile("QUERY_SOURCE=trends\nUSE_VIRTUAL_DESKTOP=true\n")
		before = (self.root / ".env").read_bytes()
		self.assertEqual(settings.validate({"run.browser_mode": "desktop"}, settings.load()), {"run.browser_mode": "out_of_range"})
		api = bridge.Bridge(dependencies={"settings": settings})
		try:
			result = api.save_settings({"changes": {"run.browser_mode": "desktop"}})
		finally:
			api._close()
		self.assertFalse(result["ok"])
		self.assertEqual(result["error"]["code"], "settings.invalid")
		self.assertEqual(result["error"]["message_key"], "errors.app.settings_invalid")
		self.assertEqual(result["error"]["fields"], {"run.browser_mode": "out_of_range"})
		self.assertEqual((self.root / ".env").read_bytes(), before)
		self.assertFalse((self.root / "app-prefs.json").exists())
		self.ensure_mock.assert_not_called()
		self.status_mock.assert_not_called()

	def test_search_source_existing_aliases_and_empty_cli_default(self):
		for source, provider, expected in [(None, None, "ollama"), ("llm", "local", "ollama"), ("llm", "ollama", "ollama"), ("llm", "open-router", "openrouter"), ("llm", "openrouter", "openrouter"), ("trends", "openrouter", "trends")]:
			self.envfile("\n".join(f"{key}={value}" for key, value in [("QUERY_SOURCE", source), ("LLM_PROVIDER", provider)] if value is not None))
			self.assertEqual(settings.load()["run.search_source"], expected)

	def test_search_source_writes_and_explicit_local_model(self):
		for source in ("trends", "ollama", "openrouter"):
			self.envfile("QUERY_SOURCE=trends\n")
			changes = {"run.search_source": source}
			if source == "openrouter":
				changes["run.openrouter_key"] = "fake-key"
			settings.save(changes)
			data = dotenv_values(self.root / ".env")
			self.assertEqual(data["QUERY_SOURCE"], "trends" if source == "trends" else "llm")
			if source != "trends":
				self.assertEqual(data["LLM_PROVIDER"], "local" if source == "ollama" else "openrouter")
			if source == "ollama":
				self.assertEqual(data["LOCAL_LLM_MODEL"], "gemma3:4b")

	def test_new_setup_first_save_defaults_to_trends_without_llm_side_effects(self):
		self.envfile("# existing\nREWARDS_ACCOUNTS=account-1\nUNKNOWN=keep\n")
		self.assertFalse(prefs.load()["setup_done"])
		changes = {"schedule.mode": "automatic", "schedule.checks": 3}
		def ensure():
			data = dotenv_values(self.root / ".env")
			self.assertEqual(data["QUERY_SOURCE"], "trends")
			self.assertNotIn("LOCAL_LLM_MODEL", data)
			return {"in_sync": True, "error_code": None}
		self.ensure_mock.side_effect = ensure
		result = settings.save(changes)
		data = dotenv_values(self.root / ".env")
		self.assertEqual(data["QUERY_SOURCE"], "trends")
		self.assertEqual(result["values"]["run.search_source"], "trends")
		self.assertFalse(any(key.startswith(("LOCAL_LLM_", "OPENROUTER_", "LLM_")) for key in data))
		self.assertEqual(data["UNKNOWN"], "keep")
		self.assertEqual(changes, {"schedule.mode": "automatic", "schedule.checks": 3})
		self.assertFalse(prefs.load()["setup_done"])
		self.ensure_mock.assert_called_once()

	def test_new_setup_missing_env_empty_save_still_persists_trends(self):
		self.assertFalse((self.root / ".env").exists())
		result = settings.save({})
		self.assertEqual(dotenv_values(self.root / ".env"), {"QUERY_SOURCE": "trends"})
		self.assertEqual(result["values"]["run.search_source"], "trends")
		self.assertFalse(prefs.load()["setup_done"])

	def test_setup_schedule_failure_still_leaves_safe_source_persisted(self):
		self.ensure_mock.return_value = {"error_code": "schedule.failed"}
		with self.assertRaises(ValueError) as failure:
			settings.save({"schedule.mode": "fixed"})
		self.assertEqual(failure.exception.code, "schedule.failed")
		data = dotenv_values(self.root / ".env")
		self.assertEqual(data["QUERY_SOURCE"], "trends")
		self.assertNotIn("LOCAL_LLM_MODEL", data)
		self.assertFalse(prefs.load()["setup_done"])

	def test_first_save_explicit_search_source_wins_over_setup_default(self):
		for source in ("trends", "ollama", "openrouter"):
			with self.subTest(source=source):
				self.envfile("# existing\n")
				changes = {"schedule.mode": "manual", "run.search_source": source}
				if source == "openrouter":
					changes["run.openrouter_key"] = "synthetic-key"
				self.assertEqual(settings.save(changes)["values"]["run.search_source"], source)
				data = dotenv_values(self.root / ".env")
				self.assertEqual(data["QUERY_SOURCE"], "trends" if source == "trends" else "llm")
				if source == "ollama":
					self.assertEqual(data["LLM_PROVIDER"], "local")
					self.assertEqual(data["LOCAL_LLM_MODEL"], "gemma3:4b")
				elif source == "openrouter":
					self.assertEqual(data["LLM_PROVIDER"], "openrouter")
					self.assertNotIn("LOCAL_LLM_MODEL", data)

	def test_legacy_import_keeps_imported_source_during_setup_save(self):
		legacy = self.root / "legacy"
		legacy.mkdir()
		(legacy / ".env").write_text("QUERY_SOURCE=llm\nLLM_PROVIDER=local\nLOCAL_LLM_MODEL=legacy:8b\nREWARDS_ACCOUNTS=account-1\n", encoding="utf-8")
		with patch.object(accounts_admin, "_ensure_idle"), patch.object(accounts_admin, "_legacy_install", return_value={"found": True, "path": str(legacy), "accounts": ["account-1"]}), patch.object(accounts_admin, "_list_accounts", return_value=[]):
			accounts_admin.import_legacy_install()
		self.assertFalse(prefs.load()["setup_done"])
		result = settings.save({"schedule.mode": "manual"})
		data = dotenv_values(self.root / ".env")
		self.assertEqual(data["QUERY_SOURCE"], "llm")
		self.assertEqual(data["LLM_PROVIDER"], "local")
		self.assertEqual(data["LOCAL_LLM_MODEL"], "legacy:8b")
		self.assertEqual(result["values"]["run.search_source"], "ollama")

	def test_after_setup_missing_source_stays_absent_and_reads_as_before(self):
		prefs.save({"setup_done": True})
		self.envfile("# existing\n")
		result = settings.save({"schedule.mode": "manual"})
		data = dotenv_values(self.root / ".env")
		self.assertNotIn("QUERY_SOURCE", data)
		self.assertEqual(result["values"]["run.search_source"], "ollama")
		self.assertEqual(data["LOCAL_LLM_MODEL"], "gemma3:4b")

	def test_complete_setup_retains_missing_and_existing_source_rules(self):
		for source in (None, "llm", "trends"):
			with self.subTest(source=source):
				prefs.save({"setup_done": False})
				self.envfile("" if source is None else "QUERY_SOURCE=" + source + "\n")
				api = bridge.Bridge(dependencies={"prefs": prefs, "schedule": SimpleNamespace(ensure=self.ensure_mock)})
				try:
					self.assertTrue(api.complete_setup({})["ok"])
				finally:
					api._close()
				self.assertEqual(dotenv_values(self.root / ".env")["QUERY_SOURCE"], source or "trends")
				self.assertTrue(prefs.load()["setup_done"])

	def test_every_id_write_mapping(self):
		edge = self.root / "msedge.exe"
		driver = self.root / "msedgedriver.exe"
		edge.touch()
		driver.touch()
		changes = {"schedule.mode": "fixed", "schedule.window_start": "08:00", "schedule.window_end": "20:00", "schedule.checks": 3, "schedule.time": "10:00", "schedule.days": ["mon", "fri"], "schedule.random_delay": 5, "schedule.catch_up": False, "schedule.on_battery": False, "schedule.min_points": 25, "run.browser_mode": "hidden", "run.tasks_off": ["quests"], "run.search_source": "openrouter", "run.ollama_url": "http://localhost:11434/v1", "run.ollama_model": "test:4b", "run.openrouter_key": "fake key with 'quote'", "run.openrouter_model": "openrouter/free", "notify.policy": "errors", "notify.webhook_url": "https://example.test/hook", "app.autostart": True, "app.close_to_tray": False, "app.language": "system", "app.update_check": False, "advanced.edge_path": str(edge), "advanced.driver_path": str(driver), "advanced.log_level": "DEBUG", "advanced.data_folder": str(self.root / "profiles")}
		self.assertEqual(set(changes), set(settings.SETTING_IDS))
		self.envfile("# untouched\nUNKNOWN=value\n")
		with patch("run_lock.status", return_value=None):
			result = settings.save(changes)
		for key, value in changes.items():
			if key != "app.autostart":
				self.assertEqual(result["values"][key], value, key)
		self.registry_mock.assert_called_once_with(True)
		self.ensure_mock.assert_called_once()
		self.assertEqual(result["warnings"], [])
		self.assertEqual(dotenv_values(self.root / ".env")["UNKNOWN"], "value")
		self.assertTrue((self.root / ".env").read_text().startswith("# untouched\nUNKNOWN=value\n"))

	def test_validation_errors_for_every_field(self):
		current = settings.load()
		cases = [("schedule.mode", "unknown", "out_of_range"), ("schedule.window_start", "25:00", "out_of_range"), ("schedule.window_end", "01:00", "window_order"), ("schedule.checks", 13, "out_of_range"), ("schedule.time", "9:45", "out_of_range"), ("schedule.days", [], "days_empty"), ("schedule.random_delay", 1, "out_of_range"), ("schedule.catch_up", "true", "out_of_range"), ("schedule.on_battery", 1, "out_of_range"), ("schedule.min_points", 0, "out_of_range"), ("run.browser_mode", "bad", "out_of_range"), ("run.tasks_off", list(settings._TASK_IDS), "tasks_all_off"), ("run.search_source", "bad", "out_of_range"), ("run.ollama_url", "file:///tmp", "url_invalid"), ("run.ollama_model", "has space", "model_required"), ("run.openrouter_key", 1, "key_required"), ("run.openrouter_model", "", "model_required"), ("notify.policy", "bad", "out_of_range"), ("notify.webhook_url", "http://example.test", "https_required"), ("app.autostart", 1, "out_of_range"), ("app.close_to_tray", None, "out_of_range"), ("app.language", "not-a-locale", "out_of_range"), ("app.update_check", "yes", "out_of_range"), ("advanced.edge_path", str(self.root / "missing.exe"), "file_missing"), ("advanced.driver_path", str(self.root / "missing.exe"), "file_missing"), ("advanced.log_level", "CRITICAL", "out_of_range"), ("advanced.data_folder", "relative", "folder_invalid")]
		for key, value, code in cases:
			with self.subTest(key=key):
				self.assertEqual(settings.validate({key: value}, current)[key], code)
		self.assertEqual(settings.validate({"run.search_source": "openrouter"}, current)["run.openrouter_key"], "key_required")
		self.assertEqual(settings.validate({"schedule.window_end": "10:00"}, current)["schedule.window_end"], "window_too_short")
		self.assertEqual(settings.validate({"run.tasks_off": ["not-a-task"]}, current)["run.tasks_off"], "out_of_range")
		file = self.root / "wrong.exe"
		file.touch()
		self.assertEqual(settings.validate({"advanced.edge_path": str(file)}, current)["advanced.edge_path"], "file_wrong_name")

	def test_invalid_save_has_no_side_effects(self):
		self.envfile("# existing\nQUERY_SOURCE=trends\n")
		before = (self.root / ".env").read_bytes()
		with self.assertRaises(ValueError) as caught:
			settings.save({"schedule.checks": 99, "app.autostart": True})
		self.assertEqual(caught.exception.code, "settings.invalid")
		self.assertEqual(caught.exception.fields, {"schedule.checks": "out_of_range"})
		self.assertEqual((self.root / ".env").read_bytes(), before)
		self.assertFalse((self.root / "app-prefs.json").exists())
		self.registry_mock.assert_not_called()
		self.ensure_mock.assert_not_called()

	def test_no_stale_env_copy_and_clear_path_removes_key(self):
		with patch.dict(os.environ, {"QUERY_SOURCE": "llm", "LLM_PROVIDER": "openrouter", "USER_DATA_DIR": "stale"}):
			self.envfile("QUERY_SOURCE=trends\nUSER_DATA_DIR=" + str(self.root / "profiles") + "\n")
			self.assertEqual(settings.load()["run.search_source"], "trends")
			with patch("run_lock.status", return_value=None):
				settings.save({"advanced.data_folder": ""})
			self.assertNotIn("USER_DATA_DIR", dotenv_values(self.root / ".env"))
			self.assertEqual(os.environ["USER_DATA_DIR"], "stale")

	def test_root_account_only_when_local_state_exists(self):
		(self.root / "data-dir").mkdir()
		(self.root / "data-dir" / "Local State").touch()
		self.assertEqual(settings.active_accounts(), ["@root"])
		self.envfile("REWARDS_ACCOUNTS=one,Two,ONE\nREWARDS_PAUSED_ACCOUNTS=three,four\n")
		self.assertEqual(settings.active_accounts(), ["one", "Two"])
		self.assertEqual(settings.paused_accounts(), ["three", "four"])

	def test_active_run_blocks_folder_change(self):
		with patch("run_lock.status", return_value=SimpleNamespace(owner="signin")):
			self.assertEqual(settings.validate({"advanced.data_folder": str(self.root / "new")}, settings.load())["advanced.data_folder"], "run_active")

	def test_pending_signin_also_blocks_folder_change(self):
		with patch("run_lock.status", return_value=None), patch("rfapp.accounts_admin._signin_active", return_value={"state": "starting"}):
			self.assertEqual(settings.validate({"advanced.data_folder": str(self.root / "new")}, settings.load())["advanced.data_folder"], "run_active")

	def test_scheduler_failure_is_typed_after_file_save(self):
		self.ensure_mock.return_value = {"error_code": "schedule.failed"}
		with self.assertRaises(ValueError) as caught:
			settings.save({"schedule.mode": "fixed"})
		self.assertEqual(caught.exception.code, "schedule.failed")
		self.assertEqual(EnvFile.load(self.root / ".env").get("REWARDS_SCHEDULE_MODE"), "fixed")

	def test_locale_catalog_accepts_only_shipped_codes(self):
		with patch("rfapp.settings.paths.ui_dir", return_value=self.root):
			(self.root / "i18n").mkdir()
			(self.root / "i18n" / "index.json").write_text(json.dumps({"locales": [{"code": "en", "name": "English", "dir": "ltr"}, {"code": "de", "name": "Deutsch", "dir": "ltr"}]}))
			self.assertEqual(settings.validate({"app.language": "de"}, settings.load()), {})
			self.assertEqual(settings.validate({"app.language": "fr"}, settings.load())["app.language"], "out_of_range")

	def test_bad_integer_file_value_falls_back_without_loading_environment(self):
		self.envfile("REWARDS_SCHEDULE_CHECKS=garbage\n")
		self.assertEqual(settings.load()["schedule.checks"], 4)

	def test_dollar_brace_rejected_for_every_string_field_without_writes(self):
		current = settings.load()
		for key, value in current.items():
			if isinstance(value, str):
				self.assertEqual(settings.validate({key: "${RF_SECRET}"}, current)[key], "dollar_brace", key)
		self.assertEqual(settings.validate({"run.openrouter_key": "$literal"}, current), {})
		with self.assertRaises(ValueError) as caught:
			settings.save({"run.openrouter_key": "${RF_SECRET}"})
		self.assertEqual(caught.exception.fields, {"run.openrouter_key": "dollar_brace"})
		self.assertFalse((self.root / ".env").exists())
		self.registry_mock.assert_not_called()

	def test_unrelated_save_does_not_register(self):
		settings.save({"notify.policy": "never"})
		self.ensure_mock.assert_not_called()
		self.status_mock.assert_called_once()

	def test_init_config_manage_accounts_reads_app_fixture(self):
		fixture = Path(__file__).parent / "fixtures" / "env" / "all-keys.env"
		self.envfile(fixture.read_text())
		editor = EnvFile.load(self.root / ".env")
		editor.set("REWARDS_HEADLESS", "true")
		editor.set("REWARDS_ACCOUNTS", "account-1,account-2")
		editor.save()
		browser = ModuleType("browser")
		browser.start_driver = Mock(side_effect=AssertionError("No real browser allowed"))
		with patch.dict("sys.modules", {"browser": browser}), patch.dict(os.environ, {}, clear=True), patch("builtins.input", side_effect=[str(self.root / ".env"), "exit"]), patch("builtins.print"):
			config = runpy.run_path(str(Path(__file__).resolve().parents[2] / "src" / "init-config.py"), run_name="rf_init_config_test")
			config["manage_accounts"]()
			self.assertEqual(os.environ["REWARDS_ACCOUNTS"], "account-1,account-2")
			self.assertEqual(os.environ["REWARDS_HEADLESS"], "true")
			self.assertEqual(os.environ["UNKNOWN_USER_KEY"], "leave this alone")
		browser.start_driver.assert_not_called()
		data = dotenv_values(self.root / ".env")
		self.assertEqual(data["REWARDS_ACCOUNTS"], "account-1,account-2")
		self.assertEqual(data["UNKNOWN_USER_KEY"], "leave this alone")


if __name__ == "__main__":
	unittest.main()
