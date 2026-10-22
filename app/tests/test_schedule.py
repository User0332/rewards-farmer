import _bootstrap  # noqa: F401

import ctypes
import io
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import datetime, time, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from rfapp import schedule


DEFAULT = schedule.ScheduleSettings("automatic", time(9, 45), time(21, 45), 4, time(9, 45), frozenset(schedule._DAYS), 15, True, True, 15, 1)
COMMAND = [r"C:\Programs\RewardsFarmer\RewardsFarmer.exe", "--scheduled"]
NOW = datetime(2026, 10, 14, 10)
_CURRENT_USER = schedule.current_user_id
_FIND_LEGACY = schedule.find_legacy_task
_REGISTRY_KEY = r"Software\RewardsFarmer"


class _RegistryHandle:
	def __init__(self, path):
		self.path = path

	def __enter__(self):
		return self

	def __exit__(self, *args):
		return False


class FakeWinreg(ModuleType):
	HKEY_CURRENT_USER = "HKCU"
	REG_SZ = 1
	KEY_QUERY_VALUE = 1
	KEY_SET_VALUE = 2

	def __init__(self):
		super().__init__("winreg")
		self.keys = {}
		self.calls = []

	def _path(self, root, path):
		return root.path + "\\" + path if isinstance(root, _RegistryHandle) else path

	def OpenKey(self, root, path, reserved=0, access=KEY_QUERY_VALUE):
		path = self._path(root, path)
		self.calls.append(("open", path, access))
		if path not in self.keys:
			raise FileNotFoundError(path)
		return _RegistryHandle(path)

	def CreateKey(self, root, path):
		path = self._path(root, path)
		self.calls.append(("create", path))
		self.keys.setdefault(path, {})
		return _RegistryHandle(path)

	def QueryValueEx(self, key, name):
		self.calls.append(("query", key.path, name))
		try:
			return self.keys[key.path][name]
		except KeyError:
			raise FileNotFoundError(name)

	def SetValueEx(self, key, name, reserved, kind, value):
		self.calls.append(("set", key.path, name, value, kind))
		self.keys[key.path][name] = (value, kind)

	def DeleteValue(self, key, name):
		self.calls.append(("delete_value", key.path, name))
		try:
			del self.keys[key.path][name]
		except KeyError:
			raise FileNotFoundError(name)

	def QueryInfoKey(self, key):
		self.calls.append(("info", key.path))
		prefix = key.path + "\\"
		subkeys = {path[len(prefix):].split("\\", 1)[0] for path in self.keys if path.startswith(prefix)}
		return len(subkeys), len(self.keys[key.path]), 0

	def DeleteKey(self, root, path):
		path = self._path(root, path)
		self.calls.append(("delete_key", path))
		if path not in self.keys:
			raise FileNotFoundError(path)
		if any(other.startswith(path + "\\") for other in self.keys):
			raise OSError("Key has subkeys")
		del self.keys[path]


def tree(s=DEFAULT, command=COMMAND):
	return ET.fromstring(schedule.build_task_xml(s, command, r"C:\RewardsFarmer", r"DESKTOP-ABC\user", NOW).split("\n", 1)[1])


class SchedulePureTests(unittest.TestCase):
	def setUp(self):
		item = patch("rfapp.schedule.subprocess.run", side_effect=AssertionError("Unpatched schtasks in pure test"))
		item.start()
		self.addCleanup(item.stop)
		item = patch("rfapp.schedule.paths.task_name", return_value="RewardsFarmerApp_test0000")
		item.start()
		self.addCleanup(item.stop)

	def test_golden_xml_modes_and_day_sets(self):
		cases = {"automatic": DEFAULT, "fixed": replace(DEFAULT, mode="fixed"), "manual": replace(DEFAULT, mode="manual"), "weekdays": replace(DEFAULT, mode="fixed", days=frozenset(("mon", "wed", "fri")), random_delay=0)}
		for filename, configuration in cases.items():
			with self.subTest(fixture=filename):
				expected = (Path(__file__).parent / "fixtures" / "schedule" / (filename + ".xml")).read_text(encoding="utf-8")
				self.assertEqual(schedule.build_task_xml(configuration, COMMAND, r"C:\RewardsFarmer", r"DESKTOP-ABC\user", NOW), expected)

	def test_default_trigger_times(self):
		self.assertEqual(schedule.trigger_times(DEFAULT), [time(9, 45), time(13, 45), time(17, 45), time(21, 45)])

	def test_modes_single_and_floor_spacing(self):
		self.assertEqual(schedule.trigger_times(replace(DEFAULT, checks=1)), [time(9, 45)])
		self.assertEqual(schedule.trigger_times(replace(DEFAULT, checks=6)), [time(9, 45), time(12, 9), time(14, 33), time(16, 57), time(19, 21), time(21, 45)])
		self.assertEqual(schedule.trigger_times(replace(DEFAULT, mode="fixed", fixed_time=time(12, 3))), [time(12, 3)])
		self.assertEqual(schedule.trigger_times(replace(DEFAULT, mode="manual")), [])

	def test_strict_future_boundaries(self):
		root = tree()
		boundaries = [node.text for node in root.findall(".//{" + schedule._NS + "}StartBoundary")]
		self.assertEqual(boundaries, ["2026-10-15T09:45:00", "2026-10-14T13:45:00", "2026-10-14T17:45:00", "2026-10-14T21:45:00"])
		xml = schedule.build_task_xml(DEFAULT, COMMAND, "dir", "user", datetime(2026, 10, 14, 9, 45))
		self.assertIn("2026-10-15T09:45:00", xml)

	def test_all_task_flags_and_order(self):
		root = tree()
		expected = {"MultipleInstancesPolicy": "IgnoreNew", "DisallowStartIfOnBatteries": "false", "StopIfGoingOnBatteries": "false", "AllowHardTerminate": "true", "StartWhenAvailable": "true", "RunOnlyIfNetworkAvailable": "true", "AllowStartOnDemand": "true", "Enabled": "true", "Hidden": "false", "RunOnlyIfIdle": "false", "WakeToRun": "false", "ExecutionTimeLimit": "PT1H", "Priority": "5"}
		for key, value in expected.items():
			self.assertEqual(schedule._text(root, "Settings/" + key), value)
		self.assertEqual([child.tag.rsplit("}", 1)[-1] for child in root], ["RegistrationInfo", "Triggers", "Principals", "Settings", "Actions"])
		self.assertEqual(schedule._text(root, "Principals/Principal/LogonType"), "InteractiveToken")
		self.assertEqual(schedule._text(root, "Principals/Principal/RunLevel"), "LeastPrivilege")
		self.assertEqual(schedule._text(root, "Principals/Principal/UserId"), r"DESKTOP-ABC\user")
		self.assertEqual(schedule._text(root, "Settings/IdleSettings/StopOnIdleEnd"), "false")
		self.assertEqual(schedule._text(root, "Settings/IdleSettings/RestartOnIdle"), "false")

	def test_weekday_order_random_delay_and_battery(self):
		root = tree(replace(DEFAULT, days=frozenset(("sun", "mon", "fri")), random_delay=0, on_battery=False, catch_up=False, active_accounts=8))
		self.assertEqual(schedule._text(root, "Settings/ExecutionTimeLimit"), "PT4H")
		self.assertEqual(schedule._text(root, "Settings/DisallowStartIfOnBatteries"), "true")
		self.assertEqual(schedule._text(root, "Settings/StopIfGoingOnBatteries"), "true")
		self.assertEqual(schedule._text(root, "Settings/StartWhenAvailable"), "false")
		trigger = root.find("{" + schedule._NS + "}Triggers")[0]
		self.assertIsNone(schedule._text(trigger, "RandomDelay"))
		days = trigger.find("{" + schedule._NS + "}ScheduleByWeek/{" + schedule._NS + "}DaysOfWeek")
		self.assertEqual([child.tag.rsplit("}", 1)[-1] for child in days], ["Monday", "Friday", "Sunday"])
		for count, limit in [(0, "PT1H"), (1, "PT1H"), (2, "PT2H"), (4, "PT4H")]:
			self.assertEqual(schedule._text(tree(replace(DEFAULT, active_accounts=count)), "Settings/ExecutionTimeLimit"), limit)

	def test_escaping_and_windows_quoting(self):
		command = [r"C:\A & B\farmer.exe", r"C:\has spaces\launcher.py", '--value="<x>"']
		xml = schedule.build_task_xml(DEFAULT, command, "C:\\A & B", "DOMAIN\\A<B", NOW)
		root = ET.fromstring(xml.split("\n", 1)[1])
		self.assertEqual(schedule._text(root, "Actions/Exec/Command"), command[0])
		self.assertEqual(schedule._text(root, "Actions/Exec/Arguments"), subprocess.list2cmdline(command[1:]))
		self.assertEqual(schedule._text(root, "Principals/Principal/UserId"), "DOMAIN\\A<B")

	def test_next_check_strict_and_enabled_days(self):
		self.assertEqual(schedule.next_check(DEFAULT, NOW), datetime(2026, 10, 14, 13, 45))
		self.assertEqual(schedule.next_check(DEFAULT, datetime(2026, 10, 14, 21, 45)), datetime(2026, 10, 15, 9, 45))
		self.assertEqual(schedule.next_check(replace(DEFAULT, mode="manual"), NOW), None)
		self.assertEqual(schedule.next_check(replace(DEFAULT, days=frozenset(("mon",))), NOW), datetime(2026, 10, 19, 9, 45))
		aware = NOW.replace(tzinfo=timezone.utc)
		self.assertEqual(schedule.next_check(DEFAULT, aware).tzinfo, timezone.utc)

	def test_config_hash_is_canonical_and_sensitive_to_every_field(self):
		base = schedule.config_hash(DEFAULT, COMMAND)
		self.assertRegex(base, r"^[a-f0-9]{12}$")
		self.assertEqual(base, schedule.config_hash(replace(DEFAULT, days=frozenset(reversed(schedule._DAYS))), COMMAND))
		for changes in [{"mode": "fixed"}, {"window_start": time(9, 30)}, {"window_end": time(22)}, {"checks": 3}, {"fixed_time": time(10)}, {"days": frozenset(("mon",))}, {"random_delay": 0}, {"catch_up": False}, {"on_battery": False}, {"min_points": 16}, {"active_accounts": 2}]:
			with self.subTest(changes=changes):
				self.assertNotEqual(base, schedule.config_hash(replace(DEFAULT, **changes), COMMAND))
		self.assertNotEqual(base, schedule.config_hash(DEFAULT, [*COMMAND, "extra"]))

	def test_output_encodings_and_declaration(self):
		text = "<?xml version='1.0' encoding='UTF-16'?><Task>ü</Task>"
		for codec in ("utf-16", "utf-16-le", "utf-8"):
			self.assertEqual(schedule._decode_output(text.encode(codec)), text)
		with patch.object(ctypes, "windll", SimpleNamespace(kernel32=SimpleNamespace(GetOEMCP=lambda: 850)), create=True):
			self.assertEqual(schedule._decode_output("ä".encode("cp850")), "ä")


class ScheduleWrapperTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.registry = FakeWinreg()
		item = patch.dict("sys.modules", {"winreg": self.registry})
		item.start()
		self.addCleanup(item.stop)
		item = patch("rfapp.schedule.subprocess.run", side_effect=AssertionError("Unpatched schtasks in wrapper test"))
		item.start()
		self.addCleanup(item.stop)
		for target, value in [("paths.data_home", Path(self.temp.name)), ("paths.task_name", "RewardsFarmerApp_test0000"), ("paths.launcher_command", COMMAND), ("paths.is_frozen", False), ("_settings", DEFAULT), ("current_user_id", "DOMAIN\\user"), ("find_legacy_task", None)]:
			mock = patch("rfapp.schedule." + target, return_value=value)
			mock.start()
			self.addCleanup(mock.stop)

	def test_low_level_command_flags_and_readback_decoding(self):
		xml = schedule.build_task_xml(DEFAULT, COMMAND, "dir", "user", NOW)
		with patch("rfapp.schedule.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=xml.encode("utf-16"))) as run:
			self.assertIsNotNone(schedule._read_task("RewardsFarmerApp_test0000"))
			args, kwargs = run.call_args
			self.assertEqual(args[0], ["schtasks", "/Query", "/TN", "RewardsFarmerApp_test0000", "/XML"])
			self.assertEqual(kwargs["timeout"], 30)
			self.assertTrue(kwargs["capture_output"])
			self.assertEqual(kwargs["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))
			self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)

	def test_create_utf16_bom_and_temp_cleanup(self):
		def inspect(args, **kwargs):
			file = Path(args[args.index("/XML") + 1])
			self.assertTrue(file.read_bytes().startswith(b"\xff\xfe"))
			self.assertEqual(file.read_text(encoding="utf-16"), "<Task/>")
			return SimpleNamespace(returncode=0)
		with patch("rfapp.schedule.subprocess.run", side_effect=inspect):
			schedule._create_task("RewardsFarmerApp_test0000", "<Task/>")
		self.assertEqual(list(Path(self.temp.name).iterdir()), [])

	def test_missing_and_failed_create_cleanup(self):
		with patch("rfapp.schedule.subprocess.run", return_value=SimpleNamespace(returncode=1, stdout=b"failed", stderr=b"failed")):
			self.assertIsNone(schedule._read_task("RewardsFarmerApp_test0000"))
			with self.assertRaises(OSError):
				schedule._create_task("RewardsFarmerApp_test0000", "<Task/>")
		self.assertEqual(list(Path(self.temp.name).iterdir()), [])

	def test_ensure_in_sync_never_re_registers(self):
		with patch("rfapp.schedule._read_task", return_value=tree()), patch("rfapp.schedule._create_task") as create:
			status = schedule.ensure()
			self.assertTrue(status["in_sync"])
			self.assertTrue(status["task_exists"])
			create.assert_not_called()
			self.assertEqual(set(status), {"mode", "task_name", "task_exists", "in_sync", "next_at", "next_is_check", "legacy_task", "error_code"})

	def test_ensure_registers_missing_changed_hash_or_command(self):
		for root in (None, tree(replace(DEFAULT, checks=3)), tree(command=["other.exe", "--scheduled"])):
			with self.subTest(root=root), patch("rfapp.schedule._read_task", return_value=root), patch("rfapp.schedule._create_task") as create:
				self.assertTrue(schedule.ensure()["in_sync"])
				create.assert_called_once()

	def test_manual_or_no_accounts_removes(self):
		for s in (replace(DEFAULT, mode="manual"), replace(DEFAULT, active_accounts=0)):
			with patch("rfapp.schedule._settings", return_value=s), patch("rfapp.schedule._read_task", return_value=tree()), patch("rfapp.schedule._delete_task") as delete:
				status = schedule.ensure()
				self.assertTrue(status["in_sync"])
				self.assertIsNone(status["next_at"])
				self.assertFalse(status["task_exists"])
				delete.assert_called_once_with("RewardsFarmerApp_test0000")

	def test_name_guard_refuses_task_not_created_by_app_before_schtasks(self):
		# A task the app did not create must fail at every entrypoint, even
		# when its name comes from paths.task_name rather than an argument.
		with patch("rfapp.schedule.subprocess.run") as run:
			functions = [(schedule.status, ()), (schedule.ensure, ()), (schedule.remove, ()), (schedule.replace_legacy_task, ()), (_FIND_LEGACY, ()), (schedule.from_settings, ({},)), (schedule.trigger_times, (DEFAULT,)), (schedule.config_hash, (DEFAULT, COMMAND)), (schedule.build_task_xml, (DEFAULT, COMMAND, "dir", "user", NOW)), (schedule.next_check, (DEFAULT, NOW)), (_CURRENT_USER, ())]
			for name in ("RewardsFarmer", "rewardsfarmer", "REWARDSFARMER"):
				with patch("rfapp.schedule.paths.task_name", return_value=name):
					for function, args in functions:
						with self.subTest(function=function.__name__, name=name), self.assertRaises(ValueError):
							function(*args)
			for name in ("RewardsFarmer", "rewardsfarmer", "REWARDSFARMER", "not-an-app", "RewardsFarmerApp_bad\\path"):
				for function, args in [(schedule._read_task, (name,)), (schedule._create_task, (name, "<Task/>")), (schedule._delete_task, (name,)), (schedule._schtasks, (["/Query", "/TN", name], name)), (schedule._guard_name, (name,))]:
					with self.subTest(function=function.__name__, name=name), self.assertRaises(ValueError):
						function(*args)
			run.assert_not_called()

	def test_source_never_writes_uninstaller_registry(self):
		with patch("rfapp.schedule.paths.is_frozen", return_value=False):
			schedule._record_task_name("RewardsFarmerApp_test0000")
			schedule._record_task_name(None)
		self.assertEqual(self.registry.calls, [])
		self.assertEqual(self.registry.keys, {})

	def test_status_query_failure_keeps_correlated_error(self):
		with patch("rfapp.schedule._read_task", side_effect=OSError("not available")), self.assertLogs("rfapp", "ERROR"):
			status = schedule.status()
		self.assertEqual(status["error_code"], "schedule.failed")

	def test_legacy_query_failure_does_not_break_status_error(self):
		with patch("rfapp.schedule._read_task", return_value=None), patch("rfapp.schedule.find_legacy_task", side_effect=OSError("not available")), self.assertLogs("rfapp", "ERROR"):
			status = schedule.status()
		self.assertIsNone(status["legacy_task"])
		self.assertEqual(status["error_code"], "schedule.failed")

	def test_frozen_registry_written_only_after_registration(self):
		def create(name, xml):
			self.assertEqual(self.registry.keys, {})
			self.registry.calls.append(("task_created", name))
		with patch("rfapp.schedule.paths.is_frozen", return_value=True), patch("rfapp.schedule._read_task", return_value=None), patch("rfapp.schedule._create_task", side_effect=create):
			self.assertTrue(schedule.ensure()["in_sync"])
		self.assertEqual(self.registry.keys[_REGISTRY_KEY], {"TaskName": ("RewardsFarmerApp_test0000", self.registry.REG_SZ)})
		self.assertEqual([call[0] for call in self.registry.calls].count("set"), 1)
		self.assertLess(next(index for index, call in enumerate(self.registry.calls) if call[0] == "task_created"), next(index for index, call in enumerate(self.registry.calls) if call[0] == "set"))
		self.registry.keys.clear()
		self.registry.calls.clear()
		with patch("rfapp.schedule.paths.is_frozen", return_value=True), patch("rfapp.schedule._read_task", return_value=None), patch("rfapp.schedule._create_task", side_effect=OSError("denied")), self.assertLogs("rfapp", "ERROR"):
			self.assertEqual(schedule.ensure()["error_code"], "schedule.failed")
		self.assertEqual(self.registry.keys, {})
		self.assertEqual(self.registry.calls, [])

	def test_frozen_remove_clears_uninstaller_value(self):
		self.registry.keys[_REGISTRY_KEY] = {"TaskName": ("RewardsFarmerApp_test0000", self.registry.REG_SZ)}
		with patch("rfapp.schedule.paths.is_frozen", return_value=True), patch("rfapp.schedule._read_task", return_value=tree()), patch("rfapp.schedule._delete_task") as delete:
			schedule.remove()
			delete.assert_called_once_with("RewardsFarmerApp_test0000")
		self.assertNotIn(_REGISTRY_KEY, self.registry.keys)
		self.assertIn(("delete_value", _REGISTRY_KEY, "TaskName"), self.registry.calls)
		self.assertIn(("delete_key", _REGISTRY_KEY), self.registry.calls)

	def test_record_none_absent_key_never_creates(self):
		with patch("rfapp.schedule.paths.is_frozen", return_value=True):
			schedule._record_task_name(None)
		self.assertEqual(self.registry.keys, {})
		self.assertEqual([call[0] for call in self.registry.calls], ["open"])

	def test_record_none_keeps_other_values_and_subkeys(self):
		for values, subkeys in [({"TaskName": ("RewardsFarmerApp_test0000", 1), "Other": ("keep", 1)}, []), ({"TaskName": ("RewardsFarmerApp_test0000", 1)}, [_REGISTRY_KEY + "\\Child"]), ({"Other": ("keep", 1)}, [])]:
			with self.subTest(values=values, subkeys=subkeys):
				self.registry.keys = {_REGISTRY_KEY: dict(values), **{path: {} for path in subkeys}}
				self.registry.calls.clear()
				with patch("rfapp.schedule.paths.is_frozen", return_value=True):
					schedule._record_task_name(None)
				self.assertIn(_REGISTRY_KEY, self.registry.keys)
				self.assertNotIn("TaskName", self.registry.keys[_REGISTRY_KEY])
				self.assertTrue(all(path in self.registry.keys for path in subkeys))
				if "Other" in values:
					self.assertEqual(self.registry.keys[_REGISTRY_KEY]["Other"], values["Other"])
				self.assertFalse(any(call[0] in ("create", "delete_key") for call in self.registry.calls))

	def test_record_none_deletes_preexisting_empty_key(self):
		self.registry.keys[_REGISTRY_KEY] = {}
		with patch("rfapp.schedule.paths.is_frozen", return_value=True):
			schedule._record_task_name(None)
		self.assertNotIn(_REGISTRY_KEY, self.registry.keys)
		self.assertFalse(any(call[0] == "create" for call in self.registry.calls))

	def test_in_sync_task_repairs_only_missing_or_wrong_registry_value(self):
		for stored in (None, {}, {"TaskName": ("RewardsFarmerApp_wrong000", 1)}, {"TaskName": ("RewardsFarmerApp_test0000", 1)}):
			with self.subTest(stored=stored):
				self.registry.keys = {} if stored is None else {_REGISTRY_KEY: dict(stored)}
				self.registry.calls.clear()
				with patch("rfapp.schedule.paths.is_frozen", return_value=True), patch("rfapp.schedule._read_task", return_value=tree()), patch("rfapp.schedule._create_task") as create:
					self.assertTrue(schedule.ensure()["in_sync"])
					self.assertTrue(schedule.ensure()["in_sync"])
					create.assert_not_called()
				self.assertEqual(self.registry.keys[_REGISTRY_KEY]["TaskName"], ("RewardsFarmerApp_test0000", self.registry.REG_SZ))
				self.assertEqual(sum(call[0] == "set" for call in self.registry.calls), 0 if stored and stored.get("TaskName") == ("RewardsFarmerApp_test0000", 1) else 1)

	def test_manual_without_task_or_registry_creates_no_key(self):
		with patch("rfapp.schedule.paths.is_frozen", return_value=True), patch("rfapp.schedule._settings", return_value=replace(DEFAULT, mode="manual")), patch("rfapp.schedule._read_task", return_value=None):
			self.assertTrue(schedule.ensure()["in_sync"])
		self.assertEqual(self.registry.keys, {})
		self.assertFalse(any(call[0] == "create" for call in self.registry.calls))


class LegacyTaskTests(unittest.TestCase):
	def setUp(self):
		item = patch("rfapp.schedule.subprocess.run", side_effect=AssertionError("Unpatched legacy schtasks in test"))
		item.start()
		self.addCleanup(item.stop)
		item = patch("rfapp.schedule.paths.task_name", return_value="RewardsFarmerApp_test0000")
		item.start()
		self.addCleanup(item.stop)
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.root = Path(self.temp.name)

	def test_find_only_own_legacy_install(self):
		for install, home, profile, expected in [(self.root / "ours", self.root / "ours", self.root / "other", True), (self.root / "old", self.root / "ours", self.root / "old" / "data-dir", True), (self.root / "foreign", self.root / "ours", self.root / "ours" / "data-dir", False)]:
			root = tree(command=["python.exe", str(install / "src" / "main.py"), "--unattended"])
			with self.subTest(install=install), patch("rfapp.schedule._read_task", return_value=root), patch("rfapp.schedule.paths.data_home", return_value=Path(home)), patch("rfapp.settings.user_data_dir", return_value=Path(profile)):
				found = schedule.find_legacy_task()
				self.assertEqual(found is not None, expected)
				if found:
					self.assertEqual(found, {"name": "rewards-farmer", "time": "09:45", "command": "python.exe"})

	def test_find_refuses_task_not_created_by_app_without_any_query(self):
		with patch("rfapp.schedule.paths.task_name", return_value="RewardsFarmer"), patch("rfapp.schedule.subprocess.run") as run:
			with self.assertRaises(ValueError):
				schedule.find_legacy_task()
			run.assert_not_called()

	def test_replace_only_verified_legacy(self):
		with patch("rfapp.schedule.find_legacy_task", return_value={"name": "rewards-farmer"}), patch("rfapp.schedule._delete_task") as delete, patch("rfapp.schedule.ensure", return_value={"in_sync": True}):
			self.assertEqual(schedule.replace_legacy_task(), {"in_sync": True})
			delete.assert_called_once_with("rewards-farmer", legacy=True)
		with patch("rfapp.schedule.find_legacy_task", return_value=None), patch("rfapp.schedule._delete_task") as delete:
			with self.assertRaises(ValueError) as caught:
				schedule.replace_legacy_task()
			self.assertEqual(caught.exception.code, "schedule.legacy_not_found")
			delete.assert_not_called()

	def test_replace_read_failure_is_typed_without_deletion(self):
		with patch("rfapp.schedule.find_legacy_task", side_effect=OSError("failed")), patch("rfapp.schedule._delete_task") as delete:
			with self.assertRaises(ValueError) as caught:
				schedule.replace_legacy_task()
			self.assertEqual(caught.exception.code, "schedule.failed")
			self.assertEqual(caught.exception.detail, "failed")
			delete.assert_not_called()


if __name__ == "__main__":
	unittest.main()
