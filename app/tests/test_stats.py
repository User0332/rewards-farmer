import _bootstrap  # noqa: F401

import contextlib
from datetime import datetime, time as day_time, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
from test_stats_ledger import synthetic_record
import rfapp
from rfapp import diagnostics, paths, runs, stats
import constants
import run_record
import stats_ledger


TOP_KEYS = {"generated_at", "today", "range", "from", "account_id", "accounts", "coverage_from", "days", "totals", "streak", "sources", "goal", "warnings"}
TOTAL_KEYS = {"week_earned", "month_earned", "earned", "measured_earned", "known_days", "expected_days", "average_per_day", "best_day", "balance", "balance_at", "batch_queries_sent", "query_count_complete", "run_count", "check_count"}
STREAK_KEYS = {"current", "through", "today_done", "best", "best_from", "best_to", "missed", "not_checked"}


class StatsTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.home = Path(self.temp.name)
		self.folder = self.home / "runs"
		self.folder.mkdir()
		self.now = datetime(2026, 10, 16, 20, tzinfo=timezone.utc)
		self.active, self.paused = ["account-1"], []
		self.prefs = {"account_labels": {"account-1": "Synthetic Account"}, "goals": {}}
		self.settings = {"schedule.mode": "automatic"}
		self.stack = contextlib.ExitStack()
		self.addCleanup(self.stack.close)
		settings_module = types.SimpleNamespace(active_accounts=lambda: self.active, paused_accounts=lambda: self.paused, user_data_dir=lambda: self.home / "profiles")
		i18n = types.SimpleNamespace(current=lambda: types.SimpleNamespace(t=lambda key, **params: "Account " + str(params.get("number", 1))))
		self.stack.enter_context(mock.patch.dict(sys.modules, {"rfapp.settings": settings_module, "rfapp.i18n": i18n}))
		self.stack.enter_context(mock.patch.object(rfapp, "settings", settings_module, create=True))
		self.stack.enter_context(mock.patch.object(rfapp, "i18n", i18n, create=True))
		self.stack.enter_context(mock.patch.object(paths, "data_home", return_value=self.home))
		self.stack.enter_context(mock.patch.object(paths, "runs_dir", return_value=self.folder))
		self.stack.enter_context(mock.patch.object(constants, "RUNS_DIR", str(self.folder)))
		self.stack.enter_context(mock.patch.dict(os.environ, {"REWARDS_FARMER_DATA_HOME": str(self.home), "USER_DATA_DIR": str(self.home / "profiles")}))
		self.stack.enter_context(mock.patch.object(runs.run_lock, "status", return_value=None))
		case = self
		class FixedDateTime(datetime):
			@classmethod
			def now(cls, tz=None):
				return case.now.astimezone(tz) if tz else case.now.replace(tzinfo=None)
		self.stack.enter_context(mock.patch.object(stats_ledger, "datetime", FixedDateTime))
		self.stack.enter_context(mock.patch.object(run_record, "datetime", FixedDateTime))
		self.stack.enter_context(mock.patch.object(run_record, "_timestamp", side_effect=lambda: stats_ledger._iso(self.now)))
		new_id = run_record.new_run_id
		self.stack.enter_context(mock.patch.object(run_record, "new_run_id", side_effect=lambda now=None: new_id(now or self.now)))

	def get(self, request=None):
		return stats.get_stats(request or {}, settings=self.settings, prefs=self.prefs, now=self.now)

	def record(self, date, **kwargs):
		return synthetic_record(self.folder, date + "T12:00:00Z", **kwargs)

	def test_exact_empty_shape_and_unknown_values(self):
		data = self.get()
		self.assertEqual(set(data), TOP_KEYS)
		self.assertEqual(set(data["totals"]), TOTAL_KEYS)
		self.assertEqual(set(data["streak"]), STREAK_KEYS)
		self.assertEqual(set(data["sources"]), {"points", "runs"})
		self.assertEqual(set(data["sources"]["points"]), set(stats_ledger.TASKS))
		self.assertTrue(all(type(value) is int and value >= 0 for value in data["sources"]["points"].values()))
		self.assertEqual(len(data["days"]), 90)
		self.assertTrue(all(set(day) == {"date", "state", "earned", "balance", "open_points", "quests_open", "possible", "runs", "sources"} for day in data["days"]))
		self.assertTrue(all(day["sources"] == {"points": dict.fromkeys(stats_ledger.TASKS, 0), "runs": 0} for day in data["days"]))
		self.assertTrue(all(day["state"] == "before_tracking" and day["earned"] is None for day in data["days"]))
		self.assertIsNone(data["totals"]["earned"])
		self.assertIsNone(data["totals"]["average_per_day"])
		self.assertEqual((data["totals"]["run_count"], data["totals"]["check_count"]), (0, 0))

	def test_five_day_states_and_nothing_open_check_completion(self):
		self.record("2026-10-12", complete=True, unreadable="quests")
		self.record("2026-10-13", complete=False)
		self.record("2026-10-15", check=True, gain=0, earned=0)
		data = self.get()
		states = {day["date"]: day["state"] for day in data["days"]}
		self.assertEqual([states[day] for day in ("2026-10-11", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16")],
			["before_tracking", "done", "missed", "no_check", "done", "today_open"])
		self.assertEqual(data["streak"], {"current": 1, "through": "2026-10-15", "today_done": False, "best": 1, "best_from": "2026-10-15", "best_to": "2026-10-15", "missed": 1, "not_checked": 1})

	def test_streak_today_yesterday_and_best_dates_from_all_rows(self):
		for date in ("2026-09-01", "2026-09-02", "2026-09-03", "2026-10-14", "2026-10-15"):
			self.record(date)
		data = self.get({"range": 7})
		self.assertEqual((data["streak"]["current"], data["streak"]["through"]), (2, "2026-10-15"))
		self.assertEqual((data["streak"]["best"], data["streak"]["best_from"], data["streak"]["best_to"]), (3, "2026-09-01", "2026-09-03"))
		self.record("2026-10-16")
		data = self.get({"range": 7})
		self.assertEqual((data["streak"]["current"], data["streak"]["through"], data["streak"]["today_done"]), (3, "2026-10-16", True))
		self.assertEqual(data["streak"]["not_checked"], 4)

	def test_two_accounts_strict_daily_sum_states_and_default_goal_null(self):
		self.active = ["account-1", "account-2"]
		self.record("2026-10-14", gain=20, earned=20)
		self.record("2026-10-14", account="account-2", suffix="0002", gain=10, earned=10)
		self.record("2026-10-15", complete=False, gain=15, earned=15)
		self.record("2026-10-16", gain=10, earned=10)
		data = self.get()
		self.assertEqual(set(data), TOP_KEYS)
		self.assertEqual(len(data["accounts"]), 2)
		self.assertTrue(all(set(account) == {"account_id", "label", "paused"} for account in data["accounts"]))
		days = {day["date"]: day for day in data["days"]}
		self.assertEqual((days["2026-10-14"]["earned"], days["2026-10-14"]["state"]), (30, "done"))
		self.assertEqual((days["2026-10-15"]["earned"], days["2026-10-15"]["state"]), (None, "missed"))
		self.assertEqual(days["2026-10-16"]["state"], "today_open")
		self.assertIsNone(data["totals"]["earned"])
		self.assertEqual((data["totals"]["measured_earned"], data["totals"]["known_days"], data["totals"]["expected_days"]), (30, 1, 3))
		self.assertIsNone(data["goal"])

	def test_multi_account_run_is_counted_once_globally(self):
		run_id, one = self.record("2026-10-16")
		_, two = self.record("2026-10-16", account="account-2", suffix="0002")
		other = self.folder / (run_id[:-4] + "0002.jsonl")
		other.unlink()
		one[0]["accounts"] = ["account-1", "account-2"]
		one = [event for event in one if event["type"] != "run_end"] + [event for event in two if event["type"] != "run_start"]
		(self.folder / (run_id + ".jsonl")).write_text("".join(json.dumps(event) + "\n" for event in one), encoding="utf-8")
		self.active.append("account-2")
		self.assertEqual(self.get()["totals"]["run_count"], 1)

	def test_week_start_one_seven_month_boundary_and_null_gaps(self):
		self.now = datetime(2026, 11, 3, 20, tzinfo=timezone.utc)
		for date, earned in (("2026-10-31", 10), ("2026-11-01", 20), ("2026-11-02", 30), ("2026-11-03", 40)):
			self.record(date, gain=earned, earned=earned)
		monday = self.get({"range": 7, "week_start": 1})
		sunday = self.get({"range": 7, "week_start": 7})
		self.assertEqual((monday["totals"]["week_earned"], sunday["totals"]["week_earned"]), (70, 90))
		self.assertEqual(monday["totals"]["month_earned"], 90)
		self.assertEqual(monday["totals"]["earned"], 100)
		self.assertEqual(monday["totals"]["best_day"], {"date": "2026-11-03", "earned": 40})
		self.assertEqual(monday["totals"]["average_per_day"], 25)

	def test_earned_measured_zero_missing_day_and_best_day_tie(self):
		self.record("2026-10-13", gain=10, earned=10)
		self.record("2026-10-15", gain=10, earned=10)
		self.record("2026-10-16", gain=0, earned=0)
		data = self.get({"range": 7})
		self.assertIsNone(data["totals"]["earned"])
		self.assertEqual((data["totals"]["measured_earned"], data["totals"]["known_days"], data["totals"]["expected_days"]), (20, 3, 4))
		self.assertEqual(data["totals"]["best_day"], {"date": "2026-10-15", "earned": 10})
		self.assertEqual(data["days"][-1]["earned"], 0)

	def test_default_single_and_explicit_goal_equal_actual_overview(self):
		for date in ("2026-10-14", "2026-10-15", "2026-10-16"):
			self.record(date)
		self.prefs["goals"] = {"account-1": 1500}
		expected = runs.overview(self.settings, self.prefs, self.now)["accounts"][0]["goal"]
		self.assertIsNotNone(expected)
		self.assertEqual(self.get()["goal"], expected)
		self.assertEqual(self.get({"account_id": "account-1"})["goal"], expected)
		self.paused = ["account-2"]
		self.assertIsNone(self.get()["goal"])
		self.assertEqual(self.get({"account_id": "account-1"})["goal"], expected)

	def test_days_and_existing_overview_chart_agree_on_every_retained_date(self):
		for date, earned in (("2026-10-13", 10), ("2026-10-15", 20), ("2026-10-16", 0)):
			self.record(date, gain=earned, earned=earned, unreadable="quests")
		chart = runs.overview(self.settings, self.prefs, self.now)["accounts"][0]["chart"]
		data = self.get()
		self.assertEqual([{key: day[key] for key in ("date", "balance", "earned")} for day in data["days"][-30:]], chart)

	def test_sealed_date_preserves_pruned_gain_and_keeps_latest_reading(self):
		old, _ = self.record("2026-10-16", earned=None)
		self.get()
		(self.folder / (old + ".jsonl")).unlink()
		synthetic_record(self.folder, "2026-10-16T14:00:00Z", gain=10, before=1030, earned=None)
		data = self.get()
		self.assertEqual(data["days"][-1]["earned"], 40)
		self.assertEqual(data["days"][-1]["balance"], 1040)

	def test_rebuilt_warning_is_range_relative_and_survives_next_read(self):
		self.record("2026-10-16")
		(self.home / "stats-ledger.json").write_text("broken", encoding="utf-8")
		self.assertIn({"code": "ledger_rebuilt"}, self.get()["warnings"])
		self.assertIn({"code": "ledger_rebuilt"}, self.get()["warnings"])
		self.now += timedelta(days=40)
		self.assertNotIn({"code": "ledger_rebuilt"}, self.get({"range": 7})["warnings"])

	def test_newer_version_readonly_raw_fallback_and_warning(self):
		self.record("2026-10-16")
		path = self.home / "stats-ledger.json"
		path.write_text('{"v":5,"future":"preserve"}', encoding="utf-8")
		before = path.read_bytes()
		data = self.get()
		self.assertEqual(data["days"][-1]["earned"], 30)
		self.assertIn({"code": "ledger_newer"}, data["warnings"])
		self.assertEqual(path.read_bytes(), before)

	def test_sources_exclude_missing_pair_and_uncovered_runs_without_partial_warning(self):
		self.record("2026-10-16", partial_pair=True)
		data = self.get()
		self.assertEqual(data["sources"], {"points": dict.fromkeys(stats_ledger.TASKS, 0), "runs": 0})
		synthetic_record(self.folder, "2026-10-16T14:00:00Z", gain=-10)
		data = self.get()
		self.assertEqual(data["sources"]["runs"], 0)
		self.assertNotIn({"code": "sources_partial"}, data["warnings"])

	def test_bad_request_and_unknown_accounts_precede_ledger_effects(self):
		for request in (None, [], {"range": True}, {"range": "all"}, {"range": 8}, {"week_start": 0}, {"week_start": True}, {"week_start": 8}, {"account_id": []}, {"account_id": "../private"}, {"extra": 1}):
			with self.subTest(request=request), mock.patch.object(stats_ledger, "fold", side_effect=AssertionError("invalid request folded")), self.assertRaises(stats._Error) as error:
				stats.get_stats(request, settings={}, prefs={}, now=self.now)
			self.assertEqual(error.exception.code, "bad_request")
		with self.assertRaises(stats._Error) as error:
			self.get({"account_id": "account-999"})
		self.assertEqual(error.exception.code, "accounts.unknown")
		self.assertFalse((self.home / "stats-ledger.json").exists())

	def test_fold_failure_is_not_returned_as_empty_stats(self):
		with mock.patch.object(stats_ledger, "fold", return_value=stats_ledger.FoldResult({}, False)), self.assertRaises(stats._Error) as error:
			self.get()
		self.assertEqual(error.exception.code, "internal")

	def test_crash_without_final_state_keeps_known_gain_and_incomplete_query(self):
		run_id, events = self.record("2026-10-16", result=False, queries=None)
		data = self.get()
		self.assertEqual(data["totals"]["earned"], 0)
		self.assertFalse(data["totals"]["query_count_complete"])
		self.assertEqual(data["totals"]["run_count"], 1)

	def test_local_midnight_uses_the_observation_date_not_filename(self):
		# Pin the calendar, including the European autumn transition and a month
		# boundary. Naive midnight asks the host for that date's actual offset.
		for year, month, day in ((2026, 10, 16), (2026, 10, 25), (2026, 11, 1)):
			with self.subTest(date=(year, month, day)):
				self.now = datetime(year, month, day, 12, tzinfo=timezone.utc)
				date = self.now.astimezone().date()
				midnight = datetime.combine(date, day_time.min).astimezone(timezone.utc)
				synthetic_record(self.folder, (midnight - timedelta(seconds=4)).isoformat(), gain=20, earned=20)
				data = self.get()
				self.assertEqual(data["days"][-2]["earned"], 0)
				self.assertEqual(data["days"][-1]["earned"], 20)
				self.assertEqual(data["today"], date.isoformat())

	def test_paused_account_is_available_and_specific_account_does_not_borrow_data(self):
		self.paused = ["account-2"]
		self.record("2026-10-16", account="account-2", earned=0, gain=0)
		data = self.get({"account_id": "account-2"})
		self.assertEqual(data["totals"]["earned"], 0)
		self.assertTrue(data["streak"]["today_done"])
		self.assertEqual(data["accounts"][1], {"account_id": "account-2", "label": "Account 2", "paused": True})
		self.assertIsNone(self.get({"account_id": "account-1"})["totals"]["earned"])

	def test_sources_partial_is_not_added_just_because_page_gain_differs(self):
		self.record("2026-10-16", gain=30, earned=80)
		data = self.get()
		self.assertEqual(data["totals"]["earned"], 80)
		self.assertEqual(data["sources"]["points"]["searches"], 30)
		self.assertEqual(data["sources"]["points"]["visual_search"], 0)
		self.assertEqual(data["sources"]["runs"], 1)
		self.assertNotIn({"code": "sources_partial"}, data["warnings"])

	def test_no_known_date_never_returns_a_zero_average_or_best_day(self):
		self.record("2026-10-16", earned=None, check=True)
		path = next(self.folder.glob("*.jsonl"))
		events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
		for event in events:
			if event.get("type") == "state":
				event["balance"] = None
		path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
		data = self.get()
		self.assertIsNone(data["totals"]["measured_earned"])
		self.assertIsNone(data["totals"]["earned"])
		self.assertIsNone(data["totals"]["average_per_day"])
		self.assertIsNone(data["totals"]["best_day"])

	def test_all_three_ranges_echo_selection_but_always_return_ninety_dates(self):
		self.record("2026-10-16")
		for days in (7, 30, 90):
			with self.subTest(days=days):
				data = self.get({"range": days})
				self.assertEqual(data["range"], days)
				self.assertEqual(data["from"], (self.now.astimezone().date() - timedelta(days=days - 1)).isoformat())
				self.assertEqual(len(data["days"]), 90)
				self.assertEqual(data["days"][-1]["date"], data["today"])

	def test_farmer_prune_callsite_seals_expired_record_before_deletion(self):
		import main
		import rewards_state
		old, _ = self.record("2026-07-01", gain=17, earned=17)
		driver = mock.Mock()
		with contextlib.ExitStack() as stack:
			stack.enter_context(mock.patch.dict(os.environ, {"REWARDS_ACCOUNTS": "account-1", "USE_VIRTUAL_DESKTOP": "0"}))
			stack.enter_context(mock.patch.object(main, "HEADLESS", True))
			stack.enter_context(mock.patch.object(main.log_utils, "setup_logging"))
			for method in ("reset_virtual_desktop_state", "switch_back_after_launch", "cleanup_virtual_desktop"):
				stack.enter_context(mock.patch.object(main.desktop_utils, method))
			stack.enter_context(mock.patch.object(main.desktop_utils, "prepare_desktop_before_launch", return_value=True))
			stack.enter_context(mock.patch.object(main.browser, "start_driver", return_value=driver))
			stack.enter_context(mock.patch.object(main.rewards_state, "read_state", return_value=rewards_state.RewardsState(balance=1200, earned_today=0)))
			self.assertEqual(main.main(True, main.RunPlan(check_only=True)), 0)
		self.assertFalse((self.folder / (old + ".jsonl")).exists())
		value = json.loads((self.home / "stats-ledger.json").read_text(encoding="utf-8"))
		self.assertEqual(next(row for row in value["rows"] if row["date"] == "2026-07-01")["earned"], 17)
		driver.quit.assert_called_once()

	def test_supervisor_prune_callsite_seals_expired_record_before_deletion(self):
		from rfapp import supervisor
		import run_record
		old, _ = self.record("2026-07-01", gain=19, earned=19)
		proc = mock.Mock(pid=123, returncode=0)
		proc.poll.return_value = 0
		def spawn(command, **kwargs):
			run_id = kwargs["env"]["REWARDS_RUN_ID"]
			path = self.folder / (run_id + ".jsonl")
			run_record.append_event(str(path), "run_start", run_id, accounts=[], plan={"check_only": True})
			run_record.append_event(str(path), "run_end", run_id, result="checked", exit_code=0, duration_ms=1)
			return proc
		with mock.patch.object(rfapp.settings, "load", return_value=self.settings, create=True), mock.patch.object(paths, "farmer_command", return_value=["synthetic-farmer"]), mock.patch.object(supervisor.subprocess, "Popen", side_effect=spawn), mock.patch.object(supervisor, "Job"), mock.patch.object(supervisor, "_set_execution_state"):
			self.assertEqual(supervisor.supervise("check_now", ["account-1"]), 0)
		self.assertFalse((self.folder / (old + ".jsonl")).exists())
		value = json.loads((self.home / "stats-ledger.json").read_text(encoding="utf-8"))
		self.assertEqual(next(row for row in value["rows"] if row["date"] == "2026-07-01")["earned"], 19)

	def test_unknown_account_shape_and_root_label_mapping_do_not_expose_profiles(self):
		self.active = ["@root"]
		run_id, events = self.record("2026-10-16", account="default")
		for event in events:
			if event.get("type") == "account_start":
				event["profile"] = str(self.home / "profiles")
		(self.folder / (run_id + ".jsonl")).write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
		data = self.get({"account_id": "@root"})
		self.assertEqual(data["accounts"], [{"account_id": "@root", "label": "Account 1", "paused": False}])
		self.assertEqual(data["totals"]["earned"], 30)
		self.assertNotIn(str(self.home), json.dumps(data))

	def test_today_with_latest_known_open_state_is_open_and_not_a_missed_day(self):
		self.record("2026-10-16", complete=True)
		synthetic_record(self.folder, "2026-10-16T14:00:00Z", complete=False)
		data = self.get()
		self.assertEqual(data["days"][-1]["state"], "today_open")
		self.assertFalse(data["streak"]["today_done"])
		self.assertEqual(data["streak"]["missed"], 0)
		self.assertEqual(data["streak"]["not_checked"], 0)

	def test_day_before_coverage_does_not_raise_expected_days_or_not_checked(self):
		self.record("2026-10-16", gain=0, earned=0)
		data = self.get({"range": 90})
		self.assertEqual(data["coverage_from"], "2026-10-16")
		self.assertEqual(data["totals"]["expected_days"], 1)
		self.assertEqual(data["totals"]["earned"], 0)
		self.assertEqual(data["streak"]["not_checked"], 0)
		self.assertTrue(all(day["state"] == "before_tracking" for day in data["days"][:-1]))

	def test_legacy_valid_account_ids_are_not_rejected_by_a_new_syntax(self):
		for account in ("_legacy", "-legacy", ".legacy"):
			with self.subTest(account=account):
				self.active = [account]
				self.assertEqual(self.get({"account_id": account})["account_id"], account)

	def test_active_record_is_absent_until_terminal_without_storing_partial_snapshot(self):
		run_id, events = self.record("2026-10-16", result=False)
		events[0]["supervisor_pid"] = 123
		path = self.folder / (run_id + ".jsonl")
		path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
		with mock.patch.object(stats_ledger, "_created", side_effect=lambda pid: 100 if pid == 123 else None):
			before = self.get()
			self.assertIsNone(before["coverage_from"])
			self.assertIsNone(before["totals"]["earned"])
			self.assertEqual(before["totals"]["run_count"], 0)
			self.assertNotIn(run_id, json.loads((self.home / "stats-ledger.json").read_text())["index"])
			events.append({"v": 1, "type": "run_end", "run": run_id, "ts": "2026-10-16T12:00:09.000Z", "result": "crashed"})
			path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
			after = self.get()
		self.assertEqual(after["totals"]["run_count"], 1)
		self.assertEqual(after["coverage_from"], "2026-10-16")

	def test_profile_identity_never_read_and_export_never_contains_ledger(self):
		self.record("2026-10-16")
		profile = self.home / "profiles" / "account-1"
		profile.mkdir(parents=True)
		email, name = "ledger-person@example.invalid", "Synthetic Ledger Person"
		(profile / "Local State").write_text(json.dumps({"email": email, "name": name}), encoding="utf-8")
		read = stats_ledger.read_file
		def no_profile(path, *args, **kwargs):
			self.assertNotEqual(Path(path).name, "Local State")
			return read(path, *args, **kwargs)
		with mock.patch.object(stats_ledger, "read_file", side_effect=no_profile):
			data = self.get()
		ledger = (self.home / "stats-ledger.json").read_text(encoding="utf-8")
		self.assertNotIn(email, ledger)
		self.assertNotIn(name, ledger)
		self.assertNotIn(str(profile), ledger)
		(self.home / "app-prefs.json").write_text('{"v":1}', encoding="utf-8")
		(self.home / "index.json").write_text('{"locales":[{"code":"en"}]}', encoding="utf-8")
		fake_paths = types.SimpleNamespace(resource=lambda *parts: self.home / "index.json", data_home=lambda: self.home, runs_dir=lambda: self.folder,
			env_path=lambda: self.home / ".env", prefs_path=lambda: self.home / "app-prefs.json", task_name=lambda: "RewardsFarmerApp_synthetic", is_frozen=lambda: False)
		p = types.SimpleNamespace(load=lambda: {"language": "system"})
		i18n = types.SimpleNamespace(resolve=lambda *args: ("en", "ltr", "en-US"))
		with mock.patch.object(diagnostics, "paths", fake_paths), mock.patch.dict(sys.modules, {"rfapp.prefs": p, "rfapp.i18n": i18n}), mock.patch.object(rfapp, "prefs", p, create=True), mock.patch.object(rfapp, "i18n", i18n), mock.patch.object(diagnostics, "_run", return_value=types.SimpleNamespace(returncode=0, stdout=b"<Task/>")), mock.patch.object(diagnostics, "_edge_version", return_value="synthetic"):
			diagnostics.export(self.home / "diagnostics.zip")
		with zipfile.ZipFile(self.home / "diagnostics.zip") as archive:
			self.assertFalse(any("stats-ledger" in entry for entry in archive.namelist()))
			for entry in archive.namelist():
				self.assertNotIn(email.encode(), archive.read(entry))
				self.assertNotIn(name.encode(), archive.read(entry))


def measure_synthetic_history():
	"""Explicit benchmark entrypoint, never selected by unittest discovery."""
	case = StatsTests()
	case.setUp()
	try:
		for index in range(1000):
			date = case.now - timedelta(days=index % 90)
			at = date.replace(hour=8, minute=index // 90, second=0)
			run_id, events = synthetic_record(case.folder, at.isoformat(), suffix=f"{index:04x}", gain=30, earned=(index // 90 + 1) * 30)
			account = "account-1"
			extra = [{"v": 1, "type": "search_points", "run": run_id, "ts": (at + timedelta(seconds=4, milliseconds=round)).isoformat().replace("+00:00", "Z"),
				"account": account, "earned": min(60, round), "max": 60, "round": round} for round in range(192)]
			events[4:4] = extra
			(case.folder / (run_id + ".jsonl")).write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
		files = list(case.folder.glob("*.jsonl"))
		assert len(files) == 1000
		began = time.perf_counter()
		cold = case.get({"range": 90})
		cold_ms = (time.perf_counter() - began) * 1000
		warm = []
		for _ in range(5):
			began = time.perf_counter()
			with mock.patch.object(stats_ledger, "_read_record", side_effect=AssertionError("unchanged warm record parsed")):
				result = case.get({"range": 90})
			warm.append((time.perf_counter() - began) * 1000)
			assert result == cold
		return {"records": len(files), "bytes": sum(path.stat().st_size for path in files), "events_per_record": len(events),
			"cold_ms": round(cold_ms, 3), "warm_ms": [round(value, 3) for value in warm],
			"ledger_bytes": (case.home / "stats-ledger.json").stat().st_size, "warm_record_reads": 0,
			"range": 90, "accounts": 1, "goal": None}
	finally:
		case.doCleanups()


if __name__ == "__main__":
	unittest.main()
