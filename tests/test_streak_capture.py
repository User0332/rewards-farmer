"""Passive streak evidence, with synthetic pages and no browser launch."""

from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app/tests"))

import rewards_state
import run_record
import stats_ledger
from element_selectors import ElementSelectionUtils
from test_rewards_state import StateDriver
from test_stats_progress import record, write_record


def packet(text="Daily Set Streak Day 2 of 7"):
	return {"status": "ok", "reason": None, "sections": [{"id": "streaks", "text": text,
		"nodes": [{"tag": "button", "role": "button", "aria_label": "Daily set streak",
			"data_attributes": ["data-state"], "classes": ["tile"], "text": text, "depth": 2}]}], "truncated": False}


class Clock:
	def __init__(self):
		self.now = 0
	def __call__(self):
		return self.now
	def sleep(self, seconds):
		self.now += seconds


class CaptureDriver:
	def __init__(self, values):
		self.values = list(values)
		self.calls = 0
		self.clicks = []
	def execute_script(self, script, *args):
		if ".click(" in script or "scrollIntoView" in script:
			self.clicks.append(script)
			return None
		assert script == rewards_state._STREAK_SNAPSHOT
		self.calls += 1
		value = self.values[min(self.calls - 1, len(self.values) - 1)]
		if isinstance(value, Exception):
			raise value
		return value


class CaptureTests(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.TemporaryDirectory()
		self.addCleanup(self.tmp.cleanup)
		self.addCleanup(run_record.close_run, "checked", 0)
		self.recorder = run_record.open_run("cli", ["sample"], {}, [], run_id="20261020T080000Z-abcd", directory=self.tmp.name)

	def capture(self, driver, timeout=0, phase="before"):
		clock = Clock()
		with run_record.account_context("sample"), mock.patch.object(rewards_state.time, "monotonic", clock), mock.patch.object(rewards_state.time, "sleep", clock.sleep):
			rewards_state._streak_capture_once(driver, timeout, phase=phase)
		self.assertEqual(driver.clicks, [])
		return [event for event in run_record.read_events(self.recorder.path) if event["type"] == "streak_capture"][-1], clock

	def test_capture_masks_visible_and_accessible_text_before_real_record_write(self):
		event, _ = self.capture(CaptureDriver([packet("Streak for synthetic.user@example.test Day 2")]))
		self.assertEqual(event["status"], "ok")
		self.assertEqual(event["account"], "sample")
		self.assertEqual(event["phase"], "before")
		self.assertNotIn("example.test", Path(self.recorder.path).read_text(encoding="utf-8"))
		self.assertIn("[email]", event["sections"][0]["text"])
		self.assertLessEqual(len(json.dumps({key: event[key] for key in ("status", "reason", "sections", "truncated")}, ensure_ascii=False, separators=(",", ":")).encode()), rewards_state._STREAK_BYTES)

	def test_delayed_section_appears_inside_one_bounded_poll(self):
		driver = CaptureDriver([{"status": "absent", "reason": "section_absent", "sections": [], "truncated": False}, packet()])
		event, clock = self.capture(driver, 30)
		self.assertEqual(event["status"], "ok")
		self.assertEqual(driver.calls, 2)
		self.assertEqual(clock.now, 0.25)

	def test_empty_section_is_recorded_as_unreadable_after_bounded_poll(self):
		event, clock = self.capture(CaptureDriver([{"status": "unreadable", "reason": "section_empty", "sections": [], "truncated": False}]), 1)
		self.assertEqual((event["status"], event["reason"]), ("unreadable", "section_empty"))
		self.assertEqual(clock.now, 1)

	def test_record_failure_never_changes_the_state_read(self):
		driver = StateDriver()
		with run_record.account_context("sample"), mock.patch.object(run_record, "emit", side_effect=OSError("unwritable record")), mock.patch.object(rewards_state.time, "sleep", return_value=None):
			state = rewards_state.read_state(driver, ElementSelectionUtils(driver), timeout=0)
		self.assertIsNone(state.unreadable)
		self.assertEqual(state.balance, 12345)

	def test_capture_command_uses_remaining_deadline_and_restores_driver_transport(self):
		class Driver(CaptureDriver):
			def __init__(self):
				super().__init__([packet()])
				self.observed = []
				self.command_executor = types.SimpleNamespace(_client_config=types.SimpleNamespace(timeout=30))
				self.command_executor.execute = self.command
			def command(self, *args):
				self.observed.append(self.command_executor._client_config.timeout.total)
				return packet()
			def execute_script(self, script, *args):
				return self.command_executor.execute(script, args)
		driver = Driver()
		self.capture(driver, 30)
		self.assertEqual(driver.observed, [2.95])
		self.assertEqual(driver.command_executor._client_config.timeout, 30)
		self.assertNotIn("_balance_deadline", vars(driver.command_executor))

	def test_absent_and_never_appearing_section_record_reason_with_three_second_ceiling(self):
		driver = CaptureDriver([{"status": "absent", "reason": "section_absent", "sections": [], "truncated": False}])
		event, clock = self.capture(driver, 30)
		self.assertEqual((event["status"], event["reason"]), ("absent", "section_absent"))
		self.assertEqual(clock.now, 3)
		self.assertEqual(driver.calls, 12)

	def test_driver_exception_is_a_reason_without_exception_text_or_console_output(self):
		output = io.StringIO()
		with redirect_stdout(output):
			event, _ = self.capture(CaptureDriver([RuntimeError("secret@example.test")]))
		self.assertEqual((event["status"], event["reason"]), ("unreadable", "driver_error"))
		self.assertEqual(output.getvalue(), "")
		self.assertNotIn("secret", Path(self.recorder.path).read_text())

	def test_oversize_response_fails_closed_and_does_not_break_outcome(self):
		event, _ = self.capture(CaptureDriver([packet("x" * 20000)]))
		self.assertEqual((event["status"], event["reason"], event["truncated"]), ("unreadable", "size_limit", True))
		run_record.close_run("checked", 0)
		self.assertEqual(run_record.read_events(self.recorder.path)[-1]["result"], "checked")

	def test_once_per_account_phase_and_recorder_not_once_per_process(self):
		driver = CaptureDriver([packet()])
		self.capture(driver)
		self.capture(driver)
		self.assertEqual(driver.calls, 1)
		self.capture(driver, phase="after")
		self.capture(driver, phase="after")
		self.assertEqual(driver.calls, 2)
		with run_record.account_context("second"):
			rewards_state._streak_capture_once(driver, 0)
		self.assertEqual(driver.calls, 3)
		run_record.close_run("checked", 0)
		self.recorder = run_record.open_run("cli", ["sample"], {}, [], run_id="20261020T080100Z-abcd", directory=self.tmp.name)
		self.capture(driver)
		self.assertEqual(driver.calls, 4)

	def test_capture_hook_runs_before_existing_information_clicks_in_both_phases(self):
		class Driver(StateDriver):
			captures = 0
			def execute_script(self, script, *args):
				if script == rewards_state._STREAK_SNAPSHOT:
					assert len(self.clicks) == self.captures * 4
					self.captures += 1
					return packet()
				return super().execute_script(script, *args)
		driver = Driver()
		with run_record.account_context("sample"), mock.patch.object(rewards_state.time, "sleep", return_value=None):
			state = rewards_state.read_state(driver, ElementSelectionUtils(driver), timeout=0, phase="before")
			rewards_state.read_state(driver, ElementSelectionUtils(driver), timeout=0, phase="after")
		self.assertEqual(driver.captures, 2)
		self.assertEqual([event["phase"] for event in run_record.read_events(self.recorder.path) if event["type"] == "streak_capture"], ["before", "after"])
		self.assertEqual(state, rewards_state.RewardsState(balance=12345, searches_earned=60, searches_max=60, earned_today=2345))
		self.assertEqual(driver.earn_loads, 8)
		self.assertEqual(driver.clicks, [driver.breakdown, driver.close, driver.daily_opener, driver.close] * 2)

	def test_failed_page_records_unreadable_without_reading_stale_document(self):
		driver = StateDriver(redirect="https://login.live.com/login")
		with run_record.account_context("sample"):
			state = rewards_state.read_state(driver, ElementSelectionUtils(driver), timeout=0)
		event = [event for event in run_record.read_events(self.recorder.path) if event["type"] == "streak_capture"][0]
		self.assertEqual((state.unreadable, event["reason"]), ("signed_out", "signed_out"))
		self.assertEqual(driver.scripts, [])
		self.assertEqual(event["phase"], "before")

	def test_real_account_path_records_after_tasks_and_card_data_uses_after_values(self):
		import accounts
		import main
		from rfapp import paths, streaks
		from test_streaks import capture
		class Driver(StateDriver):
			done = False
			def execute_script(self, script, *args):
				if script == rewards_state._STREAK_SNAPSHOT:
					value = capture(deeper=True, stamps=9)
					if not self.done:
						value = json.loads(json.dumps(value).replace("Search: 1/1", "Search: 0/1").replace("Activity: 3/3", "Activity: 0/3"))
					return {key: value[key] for key in ("status", "reason", "sections", "truncated")}
				return super().execute_script(script, *args)
		driver = Driver()
		for name in ("set_page_load_timeout", "set_script_timeout", "execute_cdp_cmd", "quit"):
			setattr(driver, name, mock.Mock())
		stamp = ["2026-10-21T08:00:00Z"]
		def tasks_done():
			driver.done = True
			stamp[0] = "2026-10-21T08:05:00Z"
		tasks = types.SimpleNamespace(elements=ElementSelectionUtils(driver), complete_all_tasks=tasks_done)
		account = accounts.Account(name="sample", user_data_dir=os.path.join(self.tmp.name, "profiles", "sample"), profile_name="Default")
		with run_record.account_context("sample"), mock.patch.object(main, "_plan", main.RunPlan(read_state=True)), \
			mock.patch.object(main.browser, "start_driver", return_value=driver), \
			mock.patch.object(main.desktop_utils, "prepare_desktop_before_launch", return_value=True), \
			mock.patch.object(main.desktop_utils, "switch_back_after_launch"), \
			mock.patch.object(main.rewards_tasks, "RewardsTaskUtils", return_value=tasks), \
			mock.patch.object(run_record, "_timestamp", side_effect=lambda: stamp[0]), \
			mock.patch.object(rewards_state.time, "sleep", return_value=None):
			self.assertTrue(main.run_account(account))
		events = [event for event in run_record.read_events(self.recorder.path) if event["type"] == "streak_capture"]
		self.assertEqual([event["phase"] for event in events], ["before", "after"])
		self.assertEqual([event["ts"] for event in events], ["2026-10-21T08:00:00Z", "2026-10-21T08:05:00Z"])
		with mock.patch.object(paths, "runs_dir", return_value=Path(self.tmp.name)):
			value = streaks.latest(["sample"], os.path.join(self.tmp.name, "profiles"), datetime(2026, 10, 21, 9, tzinfo=timezone.utc))
		by_id = {item["id"]: item for item in value["items"]}
		self.assertEqual((by_id["bing_search"]["today_done"], by_id["bing_search"]["today_total"]), (1, 1))
		self.assertEqual((by_id["daily_set"]["today_done"], by_id["daily_set"]["today_total"]), (3, 3))
		driver.quit.assert_called_once()


@unittest.skipUnless(shutil.which("node"), "native synthetic DOM proof needs Node")
class NativeSnapshotTests(unittest.TestCase):
	def snapshot(self):
		fixture = Path(__file__).with_name("fixtures") / "streak_dom.js"
		value = subprocess.run([shutil.which("node"), str(fixture)], input=json.dumps({"script": rewards_state._STREAK_SNAPSHOT, "limit": rewards_state._STREAK_BYTES - 128}), text=True, encoding="utf-8", capture_output=True, timeout=30)
		self.assertEqual(value.returncode, 0, value.stderr)
		return json.loads(value.stdout)

	def test_real_extraction_script_records_visible_tiles_masks_and_is_bounded_without_effects(self):
		result = self.snapshot()
		self.assertEqual(result["clicks"], 0)
		self.assertEqual(result["navigation"], 0)
		self.assertEqual(result["scrolls"], 0)
		self.assertEqual(result["small"]["status"], "ok")
		self.assertEqual([section["id"] for section in result["small"]["sections"]], ["streaks", "bonus"])
		encoded = json.dumps(result)
		self.assertNotIn("example.test", encoded)
		self.assertNotIn("hidden address", encoded)
		self.assertNotIn("secret-value", encoded)
		self.assertIn("[email]", encoded)
		nodes = result["small"]["sections"][0]["nodes"]
		self.assertEqual([node["text"] for node in nodes[:3]], ["Daily set streak", "Day 2 of 7", "Visual search streak"])
		self.assertEqual(nodes[0], {"tag": "button", "role": "button", "aria_label": "Daily set streak [email]", "data_attributes": ["data-state"], "classes": ["tile", "daily"], "text": "Daily set streak", "depth": 1})
		self.assertEqual(nodes[-1]["aria_label"], "Bing search streak 4 of 7")
		self.assertEqual(nodes[-1]["text"], "")
		self.assertTrue(result["large"]["truncated"])
		self.assertTrue(all(len(section["nodes"]) <= 40 for section in result["large"]["sections"]))
		self.assertLessEqual(len(json.dumps(result["large"], ensure_ascii=False, separators=(",", ":")).encode()), rewards_state._STREAK_BYTES)
		self.assertEqual((result["absent"]["status"], result["absent"]["reason"]), ("absent", "section_absent"))
		self.assertEqual((result["empty"]["status"], result["empty"]["reason"]), ("unreadable", "section_empty"))
		bar = next(node for node in result["expanded"]["sections"][0]["nodes"] if node["role"] == "progressbar")
		self.assertEqual((bar["aria_valuenow"], bar["aria_valuemax"], bar["aria_valuetext"]), ("5", "12", "5 of 12 stamps"))
		self.assertEqual(bar["children_count"], 15)
		self.assertNotIn("children", bar)
		self.assertFalse(result["expanded"]["truncated"])
		self.assertIn(("30", 16), [(node["text"], node["depth"]) for node in result["expanded"]["sections"][0]["nodes"]])
		self.assertLessEqual(len(json.dumps(result["expanded"], ensure_ascii=False, separators=(",", ":")).encode()), rewards_state._STREAK_BYTES)
		self.assertFalse(result["full6"]["truncated"])
		self.assertEqual(len(result["full6"]["sections"][0]["nodes"]), 29)
		self.assertLessEqual(len(json.dumps(result["full6"], ensure_ascii=False, separators=(",", ":")).encode()), 6144)
		rewards = [node["text"] for node in result["full6"]["sections"][0]["nodes"] if node["depth"] > 12]
		self.assertEqual(rewards, ["3", "100", "30", "100", "+5", "50", "+5", "100"])

	def test_real_shape_captures_all_four_streaks_without_metadata_overflow(self):
		from rfapp import streaks
		result = self.snapshot()
		capture = result["realShape"]
		size = len(json.dumps(capture, ensure_ascii=False, separators=(",", ":")).encode())
		print(f"F78 S2 real-shape payload: {size} bytes; truncated={capture['truncated']}")
		self.assertFalse(capture["truncated"])
		self.assertLessEqual(size, rewards_state._STREAK_BYTES)
		nodes = capture["sections"][0]["nodes"]
		self.assertTrue(all(len(node["classes"]) <= 4 for node in nodes))
		self.assertEqual([node["text"] for node in nodes if node["text"].endswith(" Streak")], ["Bing Search Streak", "Daily Set Streak", "Bing App Streak", "Visual Search Streak"])
		bar = next(node for node in nodes if node["role"] == "progressbar")
		self.assertEqual((bar["aria_valuenow"], bar["aria_valuemax"], bar["children_count"]), ("9", "12", 12))
		self.assertNotIn("children", bar)
		parsed = streaks.parse({**capture, "ts": "2026-10-21T08:00:00Z"}, "sample")
		self.assertEqual(len(parsed["items"]), 4)
		self.assertEqual(parsed["big_bonus"]["stamps_collected"], 9)
		self.assertEqual((result["clicks"], result["navigation"], result["scrolls"]), (0, 0, 0))


class ConsumerTests(unittest.TestCase):
	def test_ledger_runs_supervisor_and_mock_checker_ignore_raw_capture(self):
		from rfapp import paths, runs, supervisor
		from test_mock_shapes import run_task_points_problems
		with tempfile.TemporaryDirectory() as folder:
			home = Path(folder)
			run_id, events = record("2026-10-20T08:00:00Z", after=1009, earned_after=9, windows=(("searches", 1000, 1009),))
			write_record(home, run_id, events)
			with mock.patch.object(paths, "runs_dir", return_value=home):
				baseline = runs.load_run(run_id, {}, str(home / "profiles"))
				rows = stats_ledger.reduce_records({run_id: events}, user_data_dir=str(home / "profiles"), now=datetime(2026, 10, 20, 12, tzinfo=timezone.utc))
				before_fold = stats_ledger.fold(home, data_home=home / "ledger", user_data_dir=str(home / "profiles"), now=datetime(2026, 10, 20, 12, tzinfo=timezone.utc))
				extra = {**events[1], "type": "streak_capture", **packet()}
				changed = events[:3] + [extra] + events[3:]
				write_record(home, run_id, changed)
				self.assertEqual(runs.load_run(run_id, {}, str(home / "profiles")), baseline)
				self.assertEqual(stats_ledger.reduce_records({run_id: changed}, user_data_dir=str(home / "profiles"), now=datetime(2026, 10, 20, 12, tzinfo=timezone.utc)), rows)
				after_fold = stats_ledger.fold(home, data_home=home / "ledger", user_data_dir=str(home / "profiles"), now=datetime(2026, 10, 20, 12, tzinfo=timezone.utc))
				self.assertTrue(before_fold.ok and after_fold.ok)
				self.assertEqual(before_fold.ledger["rows"], after_fold.ledger["rows"])
				self.assertEqual(run_task_points_problems(baseline), [])
				self.assertNotIn("streak_capture", json.dumps(baseline))
				pushed = []
				with mock.patch.object(runs, "active_run_id", return_value=run_id):
					tail = runs.LiveTail(lambda topic, data: pushed.append((topic, data)), {}, str(home / "profiles"))
					tail.poll()
					run_record.append_event(str(home / (run_id + ".jsonl")), "streak_capture", run_id, account="sample", **packet())
					tail.poll()
				self.assertEqual(len(pushed), 1)
				self.assertEqual(pushed[0], ("run.started", baseline))
				self.assertNotIn("streak_capture", json.dumps(pushed))
			path = home / (run_id + ".jsonl")
			before = path.read_bytes()
			supervisor._end(path, run_id, "crashed", 1, 0)
			self.assertEqual(path.read_bytes(), before)
			path.write_text("\n".join(json.dumps(event) for event in changed if event["type"] != "run_end") + "\n", encoding="utf-8")
			supervisor._end(path, run_id, "crashed", 1, 0)
			self.assertEqual(run_record.read_events(str(path))[-1]["result"], "crashed")
