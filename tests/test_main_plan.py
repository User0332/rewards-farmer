import contextlib
from dataclasses import replace
import logging
import os
from pathlib import Path
import sys
import tempfile
import subprocess
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import accounts
import browser
import constants
import main
import rewards_tasks
import rewards_state
import run_lock
import run_record
from file_replace import read_file


class DecisionTests(unittest.TestCase):
	def test_balance_only_does_not_gate_but_other_codes_stay_strict(self):
		state = rewards_state.RewardsState(unreadable="balance", open_points=15)
		self.assertIsNone(main.decision_state(state).unreadable)
		self.assertEqual(state.unreadable, "balance")
		self.assertEqual(main.decision_state(replace(state, unreadable="searches,balance")).unreadable, "searches")
		for code in main.WHOLE_PAGE_CODES:
			self.assertEqual(main.decision_state(replace(state, unreadable=code)).unreadable, code)

	def test_every_decision_reason_and_threshold_boundary(self):
		plan = main.RunPlan(only_if_open=True, min_points=15)
		for state, expected in (
			(rewards_state.RewardsState(open_points=15), (True, "enough_open")),
			(rewards_state.RewardsState(open_points=14), (False, "below_threshold")),
			(rewards_state.RewardsState(open_points=0), (False, "nothing_open")),
			(rewards_state.RewardsState(quests_open=1), (True, "quest_unlocked")),
			(rewards_state.RewardsState(unreadable="signed_out", open_points=100), (False, "unreadable")),
			(rewards_state.RewardsState(unreadable="balance", open_points=15), (True, "enough_open")),
		):
			with self.subTest(expected=expected):
				self.assertEqual(main.decide(state, plan), expected)
		self.assertEqual(main.decide(rewards_state.RewardsState(unreadable="page_blank"), replace(plan, run_if_unreadable=True)), (True, "unreadable_run_anyway"))


class PartialDecisionTests(unittest.TestCase):
	def setUp(self):
		self.plan = main.RunPlan(only_if_open=True, min_points=15)

	def test_partial_quests_twenty_points_runs_with_enough_open_reason(self):
		state = rewards_state.RewardsState(open_points=20, unreadable="quests")
		self.assertEqual(main.decide(state, self.plan), (True, "enough_open"))
		self.assertEqual(state.unreadable, "quests")

	def test_partial_quests_ten_points_remains_unreadable(self):
		self.assertEqual(main.decide(rewards_state.RewardsState(open_points=10, unreadable="quests"), self.plan), (False, "unreadable"))

	def test_partial_searches_thirty_points_runs_with_enough_open_reason(self):
		self.assertEqual(main.decide(rewards_state.RewardsState(open_points=30, unreadable="searches"), self.plan), (True, "enough_open"))

	def test_partial_with_a_known_unlocked_quest_runs_with_quest_reason(self):
		self.assertEqual(main.decide(rewards_state.RewardsState(quests_open=1, unreadable="searches"), self.plan), (True, "quest_unlocked"))

	def test_combined_quests_cards_with_known_points_runs(self):
		self.assertEqual(main.decide(rewards_state.RewardsState(open_points=20, unreadable="quests,cards"), self.plan), (True, "enough_open"))

	def test_whole_page_codes_keep_unreadable_and_override_behavior(self):
		for code in main.WHOLE_PAGE_CODES:
			with self.subTest(code=code):
				state = rewards_state.RewardsState(open_points=30, quests_open=1, unreadable=code)
				self.assertEqual(main.decide(state, self.plan), (False, "unreadable"))
				self.assertEqual(main.decide(state, replace(self.plan, run_if_unreadable=True)), (True, "unreadable_run_anyway"))

	def test_balance_alone_is_stripped_without_changing_the_recorded_state(self):
		state = rewards_state.RewardsState(open_points=10, unreadable="balance")
		self.assertEqual(main.decide(state, self.plan), (False, "below_threshold"))
		self.assertIsNone(main.decision_state(state).unreadable)
		self.assertEqual(state.unreadable, "balance")
		self.assertEqual(main.decide(replace(state, open_points=20), self.plan), (True, "enough_open"))

	def test_run_if_unreadable_still_overrides_partial_below_threshold(self):
		state = rewards_state.RewardsState(open_points=10, unreadable="quests")
		self.assertEqual(main.decide(state, replace(self.plan, run_if_unreadable=True)), (True, "unreadable_run_anyway"))

	def test_fixed_mode_keeps_one_point_threshold_and_unreadable_fallback(self):
		plan = replace(self.plan, min_points=1, run_if_unreadable=True)
		self.assertEqual(main.decide(rewards_state.RewardsState(open_points=1, unreadable="quests"), plan), (True, "enough_open"))
		self.assertEqual(main.decide(rewards_state.RewardsState(unreadable="quests"), plan), (True, "unreadable_run_anyway"))


class CliPlanTests(unittest.TestCase):
	def test_all_flags_and_implied_reads(self):
		for flags, expected in (
			([], main.RunPlan()),
			(["--read-state"], main.RunPlan(read_state=True)),
			(["--check-only"], main.RunPlan(read_state=True, check_only=True)),
			(["--only-if-open"], main.RunPlan(read_state=True, only_if_open=True, min_points=15)),
			(["--only-if-open", "--min-points", "7", "--run-if-unreadable"], main.RunPlan(True, True, False, 7, True)),
		):
			with self.subTest(flags=flags), mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(main, "main", return_value=8) as runner, mock.patch.object(main.os.path, "isfile", return_value=False):
				self.assertEqual(main.cli(["--unattended", *flags]), 8)
				runner.assert_called_once_with(unattended=True, plan=expected)

	def test_dotenv_loads_after_parse_and_before_environment_default(self):
		def load(*, stream):
			self.assertEqual(stream.read(), "REWARDS_MIN_POINTS=23\n")
			os.environ[main.MIN_POINTS_ENV] = "23"
		with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(main, "read_file", return_value="REWARDS_MIN_POINTS=23\n"), mock.patch.object(main.os.path, "isfile", return_value=True), mock.patch.object(main.dotenv, "load_dotenv", side_effect=load), mock.patch.object(main, "main", return_value=0) as runner:
			main.cli(["--only-if-open"])
			self.assertEqual(runner.call_args.kwargs["plan"].min_points, 23)

	def test_cli_dotenv_retries_read_and_preserves_environment_override(self):
		with tempfile.TemporaryDirectory() as folder:
			path = Path(folder) / ".env"
			path.write_text("REWARDS_MIN_POINTS=23\nF61_DEFAULT=file\n", encoding="utf-8")
			real_open = open
			attempts = []
			def open_file(*args, **kwargs):
				attempts.append(True)
				if len(attempts) == 1:
					raise PermissionError("delete-pending")
				return real_open(*args, **kwargs)
			with mock.patch.dict(os.environ, {main.MIN_POINTS_ENV: "17"}, clear=True), mock.patch.object(main, "DOTENV_PATH", str(path)), mock.patch.object(main, "read_file", wraps=read_file) as reader, mock.patch("file_replace.builtins.open", side_effect=open_file), mock.patch.object(main, "main", return_value=0) as runner:
				self.assertEqual(main.cli(["--only-if-open"]), 0)
				self.assertEqual(runner.call_args.kwargs["plan"].min_points, 17)
				self.assertEqual(os.environ["F61_DEFAULT"], "file")
				reader.assert_called_once_with(str(path))
			self.assertEqual(len(attempts), 2)

	def test_negative_threshold_and_invalid_environment_are_parser_errors(self):
		for flags, env in ((["--min-points", "-1"], {}), (["--only-if-open"], {main.MIN_POINTS_ENV: "bad"})):
			with self.subTest(flags=flags), mock.patch.dict(os.environ, env, clear=True), mock.patch.object(main.os.path, "isfile", return_value=False), self.assertRaises(SystemExit) as error:
				main.cli(flags)
			self.assertEqual(error.exception.code, 2)


class MainRecordTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.home = Path(self.temp.name)
		self.stack = contextlib.ExitStack()
		self.addCleanup(self.stack.close)
		self.stack.enter_context(mock.patch.dict(os.environ, {"USER_DATA_DIR": str(self.home / "profiles")}, clear=True))
		self.stack.enter_context(mock.patch.object(constants, "RUNS_DIR", str(self.home / "runs")))
		self.stack.enter_context(mock.patch.object(main, "HEADLESS", True))
		self.stack.enter_context(mock.patch.object(main.log_utils, "setup_logging", lambda: None))
		self.cleanup_desktop = self.stack.enter_context(mock.patch.object(main.desktop_utils, "cleanup_virtual_desktop"))
		for name, value in (("reset_virtual_desktop_state", None), ("switch_back_after_launch", None), ("prepare_desktop_before_launch", True), ("is_virtual_desktop_enabled", False)):
			self.stack.enter_context(mock.patch.object(main.desktop_utils, name, return_value=value))
		self.driver = mock.Mock()
		self.stack.enter_context(mock.patch.object(browser, "start_driver", return_value=self.driver))
		self.tasks = mock.Mock()
		self.tasks.complete_all_tasks.side_effect = lambda: run_record.emit("task_end", task="daily_set", outcome="ok")
		self.factory = self.stack.enter_context(mock.patch.object(main.rewards_tasks, "RewardsTaskUtils", return_value=self.tasks))
		self.state = rewards_state.RewardsState(balance=100, searches_earned=0, searches_max=60, open_points=60)
		self.reader = self.stack.enter_context(mock.patch.object(rewards_state, "read_state", return_value=self.state))
		old = logging.root.manager.disable
		logging.disable(logging.CRITICAL)
		self.addCleanup(logging.disable, old)

	def events(self):
		ids = run_record.list_run_ids(str(self.home / "runs"))
		self.assertEqual(len(ids), 1)
		return run_record.read_events(str(self.home / "runs" / (ids[0] + ".jsonl")))

	def test_default_keeps_no_extra_browser_reads_and_records_ok(self):
		self.assertEqual(main.main(unattended=True), 0)
		self.reader.assert_not_called()
		self.driver.set_page_load_timeout.assert_not_called()
		self.assertEqual(self.events()[-1]["result"], "ok")
		self.driver.quit.assert_called_once()
		self.assertIsNone(run_lock.status())

	def test_check_signed_out_records_lifecycle_without_tasks_and_quits(self):
		self.reader.return_value = rewards_state.RewardsState(unreadable="signed_out")
		self.assertEqual(main.main(True, main.RunPlan(check_only=True)), 0)
		events = self.events()
		self.assertEqual([e["type"] for e in events], ["run_start", "account_start", "state", "account_end", "run_end"])
		self.assertEqual(events[2]["unreadable"], "signed_out")
		self.assertEqual(events[-1]["result"], "checked")
		self.assertEqual(events[-1]["exit_code"], 0)
		self.factory.assert_not_called()
		self.driver.set_page_load_timeout.assert_called_once_with(60)
		self.driver.set_script_timeout.assert_called_once_with(30)
		self.driver.quit.assert_called_once()

	def test_state_before_task_creation_after_headers_clear_and_today_skip_cards(self):
		calls = []
		def read(*args, **kwargs):
			calls.append(("state", kwargs["skip_cards"]))
			return self.state
		self.reader.side_effect = read
		self.factory.side_effect = lambda driver: calls.append(("tasks", None)) or self.tasks
		self.tasks.complete_all_tasks.side_effect = lambda: run_record.emit("card_left_open", description="Sticky")
		self.driver.execute_cdp_cmd.side_effect = lambda *args: calls.append(("headers", args))
		self.assertEqual(main.main(True, main.RunPlan(read_state=True)), 0)
		self.assertEqual([name for name, value in calls], ["state", "tasks", "headers", "state"])
		self.assertEqual(calls[0][1], frozenset())
		self.assertEqual(calls[-1][1], frozenset({"Sticky"}))
		self.assertTrue(callable(self.tasks.balance_reader))

	def test_skipped_and_partial_result_table(self):
		self.reader.return_value = replace(self.state, open_points=0)
		self.assertEqual(main.main(True, main.RunPlan(only_if_open=True)), 0)
		self.assertEqual(self.events()[-1]["result"], "skipped")
		self.assertFalse(next(e for e in self.events() if e["type"] == "decision")["should_run"])

	def test_task_failure_is_partial_while_browser_started(self):
		self.tasks.complete_all_tasks.side_effect = lambda: run_record.emit("task_end", task="daily_set", outcome="fail")
		self.assertEqual(main.main(True), 0)
		self.assertEqual(self.events()[-1]["result"], "partial")

	def test_no_browser_is_failed_exit_one(self):
		browser.start_driver.return_value = None
		self.assertEqual(main.main(True), 1)
		self.assertEqual(self.events()[-1]["result"], "failed")

	def test_all_started_accounts_error_is_failed_not_partial(self):
		self.factory.side_effect = ValueError("cannot load")
		self.assertEqual(main.main(True), 1)
		self.assertEqual(self.events()[-1]["result"], "failed")
		self.driver.quit.assert_called_once()

	def test_prestart_error_does_not_make_a_good_account_failed(self):
		os.environ["REWARDS_ACCOUNTS"] = "one,two"
		browser.start_driver.side_effect = [OSError("denied"), self.driver]
		self.assertEqual(main.main(True), 0)
		self.assertEqual(self.events()[-1]["result"], "partial")

	def test_config_error_writes_record_and_exit_two(self):
		os.environ["REWARDS_ACCOUNTS"] = "../bad"
		self.assertEqual(main.main(True), 2)
		self.assertEqual(self.events()[-1]["result"], "config_error")

	def test_busy_writes_record_and_exit_three_without_browser(self):
		with run_lock.acquire("signin", account="one"):
			self.assertEqual(main.main(True), 3)
		self.assertEqual(self.events()[-1]["result"], "busy")
		browser.start_driver.assert_not_called()

	def test_stop_file_watcher_sets_flag_interrupts_and_exits_four(self):
		stop = self.home / "run.stop"
		os.environ[main.STOP_FILE_ENV] = str(stop)
		def task():
			stop.write_text('{"by":"app"}', encoding="utf-8")
			# Drive the real watcher synchronously so unittest cannot be interrupted.
			with mock.patch.object(main._thread, "interrupt_main") as interrupt:
				main._watch_stop(str(stop), types.SimpleNamespace(wait=lambda _: False))
				interrupt.assert_called_once()
			self.assertTrue(main._stop_requested)
			raise KeyboardInterrupt
		self.tasks.complete_all_tasks.side_effect = task
		self.assertEqual(main.main(True), 4)
		self.assertEqual(self.events()[-1]["result"], "stopped")
		self.assertIn("stop_requested", [e["type"] for e in self.events()])
		self.assertEqual(self.events()[-1]["accounts_started"], 1)
		self.driver.quit.assert_called_once()
		self.cleanup_desktop.assert_called_once()
		self.assertIsNone(run_lock.status())

	def test_ctrl_c_is_recorded_and_still_reraises(self):
		self.tasks.complete_all_tasks.side_effect = KeyboardInterrupt
		with self.assertRaises(KeyboardInterrupt):
			main.main(True)
		self.assertEqual(self.events()[-1]["result"], "stopped")
		self.assertEqual(self.events()[-1]["exit_code"], 130)
		self.driver.quit.assert_called_once()
		self.assertIsNone(run_lock.status())

	def test_run_unreadable_anyway_runs_tasks_and_records_original_state(self):
		self.reader.return_value = rewards_state.RewardsState(unreadable="page_blank")
		self.assertEqual(main.main(True, main.RunPlan(only_if_open=True, run_if_unreadable=True)), 0)
		events = self.events()
		decision = next(e for e in events if e["type"] == "decision")
		self.assertEqual(decision["reason"], "unreadable_run_anyway")
		self.assertTrue(decision["should_run"])
		self.assertEqual(next(e for e in events if e["type"] == "state")["unreadable"], "page_blank")
		self.tasks.complete_all_tasks.assert_called_once()

	def test_partial_known_work_runs_and_keeps_unreadable_in_recorded_states(self):
		self.reader.return_value = replace(self.state, open_points=20, cards_open=2, unreadable="quests")
		self.assertEqual(main.main(True, main.RunPlan(only_if_open=True, min_points=15)), 0)
		events = self.events()
		decision = next(event for event in events if event["type"] == "decision")
		self.assertTrue(decision["should_run"])
		self.assertEqual(decision["reason"], "enough_open")
		states = [event for event in events if event["type"] == "state"]
		self.assertEqual([event["phase"] for event in states], ["before", "after"])
		self.assertTrue(all(event["unreadable"] == "quests" and event["open_points"] == 20 for event in states))
		self.tasks.complete_all_tasks.assert_called_once()

	def test_multi_account_events_keep_account_identity_and_driver_cleanup(self):
		os.environ["REWARDS_ACCOUNTS"] = "one,two"
		self.factory.side_effect = [ValueError("first account failed"), self.tasks]
		self.assertEqual(main.main(True), 0)
		events = self.events()
		self.assertEqual([e["account"] for e in events if e["type"] == "account_end"], ["one", "two"])
		self.assertEqual(events[-1]["accounts_started"], 2)
		self.assertEqual(events[-1]["result"], "partial")
		self.assertEqual(self.driver.quit.call_count, 2)


class RealWatcherProcessTests(unittest.TestCase):
	def test_real_interrupt_main_unwinds_driver_and_returns_exit_four(self):
		code = """
import main,run_record,time,os
from pathlib import Path
from unittest import mock
driver=mock.Mock()
driver.quit.side_effect=lambda: Path(os.environ['REWARDS_FARMER_DATA_HOME'],'quit.txt').write_text('quit')
tasks=mock.Mock()
def work():
 Path(os.environ[main.STOP_FILE_ENV]).write_text('{"by":"app"}')
 while True: time.sleep(.05)
tasks.complete_all_tasks.side_effect=work
with mock.patch.object(main.browser,'start_driver',return_value=driver), mock.patch.object(main.rewards_tasks,'RewardsTaskUtils',return_value=tasks), mock.patch.object(main.desktop_utils,'prepare_desktop_before_launch',return_value=True), mock.patch.object(main.desktop_utils,'switch_back_after_launch'), mock.patch.object(main.desktop_utils,'cleanup_virtual_desktop'):
 raise SystemExit(main.main(unattended=True))
"""
		with tempfile.TemporaryDirectory() as folder:
			env = {key: value for key, value in os.environ.items() if not key.startswith("REWARDS_") and key not in ("USER_DATA_DIR", "USE_VIRTUAL_DESKTOP")}
			env.update(REWARDS_FARMER_DATA_HOME=folder, USER_DATA_DIR=str(Path(folder, "profile")), REWARDS_STOP_FILE=str(Path(folder, "run.stop")),
				REWARDS_HEADLESS="1", USE_VIRTUAL_DESKTOP="0", PYTHONPATH=str(Path(main.__file__).parent))
			result = subprocess.run([sys.executable, "-c", code], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15)
			self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
			ids = run_record.list_run_ids(str(Path(folder, "runs")))
			self.assertEqual(len(ids), 1)
			events = run_record.read_events(str(Path(folder, "runs", ids[0] + ".jsonl")))
			self.assertIn("stop_requested", [event["type"] for event in events])
			self.assertEqual(events[-1]["result"], "stopped")
			self.assertEqual(events[-1]["exit_code"], 4)
			self.assertTrue(Path(folder, "quit.txt").exists())
			self.assertFalse(Path(folder, "profile", run_lock.INFO_FILE_NAME).exists())
			with run_lock.acquire("farmer", directory=str(Path(folder, "profile"))) as lock:
				self.assertIsNotNone(lock)
