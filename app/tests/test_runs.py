import _bootstrap  # noqa: F401

import contextlib
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

import rfapp
from rfapp import runs, paths
import rewards_tasks
import run_record


class RunReductionTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.directory = Path(self.temp.name)
		self.stack = contextlib.ExitStack()
		self.addCleanup(self.stack.close)
		self.stack.enter_context(mock.patch.object(paths, "runs_dir", return_value=self.directory))
		self.active_patch = mock.patch.object(runs, "active_run_id", return_value=None)
		self.active = self.active_patch.start()
		self.addCleanup(self.active_patch.stop)
		translator = types.SimpleNamespace(current=lambda: types.SimpleNamespace(t=lambda key, **params: "Account " + str(params.get("number", 1))))
		self.stack.enter_context(mock.patch.dict(sys.modules, {"rfapp.i18n": translator}))
		self.stack.enter_context(mock.patch.object(rfapp, "i18n", translator, create=True))

	def fixture(self, name):
		text = (Path(__file__).parent / "fixtures" / "runs" / (name + ".jsonl")).read_text(encoding="utf-8")
		run_id = json.loads(text.splitlines()[0])["run"]
		(self.directory / (run_id + ".jsonl")).write_text(text, encoding="utf-8")
		return run_id

	def test_multi_account_real_fields_task_order_and_measured_balance(self):
		run_id = self.fixture("multi")
		detail = runs.load_run(run_id, {"one": "Personal"}, "profiles")
		self.assertEqual(detail["result"], "partial")
		self.assertEqual(detail["points_earned"], 65)
		one, two = detail["accounts"]
		self.assertEqual(one["label"], "Personal")
		self.assertEqual(one["points_now"], 165)
		self.assertEqual(one["search"], {"earned": 60, "max": 60})
		self.assertEqual([task["task"] for task in one["tasks"]], list(runs._TASK_IDS))
		self.assertEqual(one["tasks"][5]["status"], "ok")
		self.assertEqual(two["status"], "failed")
		self.assertEqual(two["error"]["message_key"], "errors.browser.driver_missing")
		self.assertIsNone(two["points_earned"])
		self.assertEqual(detail["warnings"][0]["account_id"], "one")

	def test_short_search_quota_reduces_real_task_failure_to_history_reason(self):
		tasks = rewards_tasks.RewardsTaskUtils.__new__(rewards_tasks.RewardsTaskUtils)
		tasks.read_search_points = mock.Mock(side_effect=[(3, 60), (3, 60)])
		def batch(count, already_sent=0):
			tasks.progress = "sent 19 searches"
			return 19
		tasks.run_search_batch = batch
		tasks.restore_main_tab = lambda: None
		tasks.return_to_rewards_home = lambda: None
		for task, _, method in rewards_tasks.TASKS:
			if task != "searches":
				setattr(tasks, method, lambda: None)
		run_id = "20261017T100000Z-0070"
		recorder = run_record.open_run("app", ["one"], {}, [], run_id=run_id, directory=str(self.directory))
		try:
			with mock.patch.dict(os.environ, {rewards_tasks.SKIP_TASKS_ENV: ""}), run_record.account_context("one"), self.assertLogs(rewards_tasks.logger, level="INFO"):
				run_record.emit("account_start", profile="profiles/one")
				tasks.complete_all_tasks()
				run_record.emit("account_end", started=True, ran_tasks=True, error_code=None)
			self.assertEqual(recorder.stats["task_fail"], 1)
		finally:
			run_record.close_run("partial", 0)
		detail = runs.load_run(run_id, {"one": "Synthetic"}, "profiles")
		self.assertEqual((detail["result"], detail["result_key"]), ("partial", "errors.run.partial"))
		search = next(task for task in detail["accounts"][0]["tasks"] if task["task"] == "searches")
		self.assertEqual((search["status"], search["reason_key"]), ("fail", "errors.task.quota_short"))
		self.assertEqual(search["detail"], "sent 19 searches, then the search points stayed at 3/60")
		self.assertEqual(search["progress"], "sent 19 searches")
		self.assertEqual(detail["accounts"][0]["search"], {"earned": 3, "max": 60})
		summary = runs.history(labels={"one": "Synthetic"}, user_data_dir="profiles")["runs"][0]
		self.assertEqual(summary["result_key"], "errors.run.partial")
		self.assertEqual(summary["accounts"][0]["task_counts"]["fail"], 1)

	def test_interrupted_active_and_incomplete_tasks(self):
		run_id = self.fixture("interrupted")
		detail = runs.load_run(run_id, {}, "profiles")
		self.assertEqual(detail["result"], "interrupted")
		self.assertEqual(detail["accounts"][0]["account_id"], "@root")
		self.assertTrue(all(task["status"] == "not_run" for task in detail["accounts"][0]["tasks"]))
		self.active.return_value = run_id
		detail = runs.load_run(run_id, {}, "profiles")
		self.assertIsNone(detail["result"])
		self.assertTrue(detail["active"])
		self.assertEqual(detail["accounts"][0]["tasks"][0]["status"], "running")
		self.assertEqual(detail["accounts"][0]["tasks"][1]["status"], "pending")

	def test_stopped_crashed_skipped_and_check_kind(self):
		for fixture, result in (("stopped", "stopped"), ("crashed", "crashed"), ("skipped", "skipped")):
			with self.subTest(fixture=fixture):
				detail = runs.load_run(self.fixture(fixture), {}, "profiles")
				self.assertEqual(detail["result"], result)
				self.assertFalse(detail["active"])
				if fixture == "stopped":
					self.assertTrue(detail["stop_requested"])
					self.assertEqual(detail["accounts"][0]["tasks"][0]["reason_key"], "errors.task.stopped")
				if fixture == "skipped":
					self.assertEqual(detail["kind"], "check")
					self.assertEqual(detail["accounts"][0]["status"], "skipped")
					self.assertIs(detail["accounts"][0]["decision"]["run"], False)

	def _result_key_record(self, decisions=(("one", "unreadable"),), *, unreadable="page_blank", result="skipped", ran=()):
		run_id = "20261023T210000Z-0088"
		stamp = datetime(2026, 10, 23, 21, tzinfo=timezone.utc)
		events = []
		def emit(kind, **fields):
			events.append({"v": 1, "run": run_id, "ts": (stamp + timedelta(seconds=len(events))).isoformat(), "type": kind, **fields})
		emit("run_start", trigger="scheduled", accounts=[name for name, _ in decisions], plan={"only_if_open": True})
		for name, reason in decisions:
			emit("account_start", account=name, profile="profiles/" + name)
			emit("state", account=name, phase="before", balance=None, earned_today=None, searches_earned=60, searches_max=60,
				daily_set_open=0, cards_open=0, quests_open=0, open_points=0, unreadable=unreadable if reason.startswith("unreadable") else None)
			emit("decision", account=name, should_run=reason == "unreadable_run_anyway", reason=reason)
			if name in ran:
				emit("task_start", account=name, task="quests")
				emit("task_end", account=name, task="quests", outcome="ok")
			emit("account_end", account=name, started=True, ran_tasks=name in ran)
		if result is not None:
			emit("run_end", result=result, accounts_ran=len(ran), exit_code=0, duration_ms=1)
		return run_id, events

	def _assert_result_key_surfaces(self, record, result, key, *, active=False):
		run_id, events = record
		path = self.directory / (run_id + ".jsonl")
		path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
		self.active.return_value = run_id if active else None
		labels = {name: name for name in events[0]["accounts"]}
		detail = runs.load_run(run_id, labels, "profiles")
		history = runs.history(labels=labels, user_data_dir="profiles")["runs"]
		self.assertEqual(len(history), 1)
		last_run = self._overview(datetime(2026, 10, 23, 22, tzinfo=timezone.utc), active=tuple(labels), prefs={"account_labels": labels})["last_run"]
		surfaces = {"detail": detail, "history": history[0], "last_run": last_run,
			"stats": runs.stats_ledger.run_summary(run_id, events, active=active)}
		for name in labels:
			surfaces["stats/" + name] = runs.stats_ledger.run_summary(run_id, events, active=active, account=name)
		pushes = []
		tail = runs.LiveTail(lambda topic, data: pushes.append((topic, data)), labels, "profiles")
		if active:
			tail.poll()
			topic = "run.started"
		else:
			tail._pending_finishes[run_id] = None
			tail.poll()
			topic = "run.finished"
		live = [data for pushed_topic, data in pushes if pushed_topic == topic]
		self.assertEqual(len(live), 1)
		surfaces["live"] = live[0]
		for surface, value in surfaces.items():
			with self.subTest(surface=surface):
				self.assertEqual((value["result"], value["result_key"], value["active"]), (result, key, active))
		return detail

	def test_unreadable_skip_result_key_agrees_across_run_surfaces(self):
		for unreadable in ("page_blank", "signed_out"):
			with self.subTest(unreadable=unreadable):
				detail = self._assert_result_key_surfaces(self._result_key_record(unreadable=unreadable), "skipped", "errors.run.unreadable")
				self.assertEqual(detail["kind"], "check")
				self.assertEqual(detail["accounts"][0]["status"], "skipped")
				self.assertEqual(detail["accounts"][0]["decision"]["reason"], "unreadable")
				self.assertEqual(detail["accounts"][0]["state_before"]["unreadable"], unreadable)

	def test_readable_skip_result_keys_are_unchanged_across_run_surfaces(self):
		for reason in ("nothing_open", "below_threshold"):
			with self.subTest(reason=reason):
				self._assert_result_key_surfaces(self._result_key_record((("one", reason),)), "skipped", "errors.run.skipped")

	def test_two_account_skip_result_key_uses_the_whole_run(self):
		for decisions in ((("one", "unreadable"), ("two", "nothing_open")), (("one", "nothing_open"), ("two", "unreadable"))):
			with self.subTest(decisions=decisions):
				self._assert_result_key_surfaces(self._result_key_record(decisions), "skipped", "errors.run.unreadable")

	def test_unreadable_run_anyway_result_keys_are_unchanged_across_run_surfaces(self):
		for result in ("ok", "partial", "failed"):
			with self.subTest(result=result):
				self._assert_result_key_surfaces(self._result_key_record((("one", "unreadable_run_anyway"),), result=result, ran=("one",)), result, "errors.run." + result)

	def test_other_results_and_active_keys_are_unchanged_across_run_surfaces(self):
		for recorded, active, result in (("checked", False, "checked"), ("crashed", False, "crashed"), (None, False, "interrupted"), (None, True, None)):
			with self.subTest(recorded=recorded, active=active):
				self._assert_result_key_surfaces(self._result_key_record(result=recorded), result, "errors.run." + result if result else None, active=active)

	def test_execution_in_another_account_keeps_the_skipped_key_across_run_surfaces(self):
		markers = ({"type": "run_end", "accounts_ran": 1}, {"type": "account_end", "account": "two", "ran_tasks": True},
			{"type": "task_start", "account": "two", "task": "quests"}, {"type": "task_end", "account": "two", "task": "quests", "outcome": "ok"})
		for marker in markers:
			with self.subTest(marker=marker):
				run_id, events = self._result_key_record((("one", "unreadable"), ("two", "nothing_open")))
				if marker["type"] == "run_end":
					events[-1].update(marker)
				else:
					events.insert(-1, {**events[-1], **marker})
				self._assert_result_key_surfaces((run_id, events), "skipped", "errors.run.skipped")

	def test_off_task_does_not_hide_an_unreadable_skip_across_run_surfaces(self):
		run_id, events = self._result_key_record()
		events.insert(-1, {**events[-2], "type": "task_end", "task": "quests", "outcome": "off"})
		self._assert_result_key_surfaces((run_id, events), "skipped", "errors.run.unreadable")

	def test_newer_schema_unknown_ignores_reduction_and_unknown_events(self):
		run_id = self.fixture("multi")
		path = self.directory / (run_id + ".jsonl")
		with path.open("a", encoding="utf-8") as stream:
			stream.write('{"v":2,"type":"future"}\n')
		detail = runs.load_run(run_id, {}, "profiles")
		self.assertEqual(detail["result"], "unknown")
		self.assertEqual(detail["accounts"], [])

	def test_no_after_uses_last_known_task_measurement(self):
		run_id = self.fixture("multi")
		path = self.directory / (run_id + ".jsonl")
		events = [e for e in run_record.read_events(str(path)) if not (e.get("type") == "state" and e.get("phase") == "after")]
		path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
		detail = runs.load_run(run_id, {}, "profiles")
		self.assertEqual(detail["points_earned"], 60)
		self.assertEqual(detail["accounts"][0]["points_now"], 160)

	def test_history_filters_checks_paginates_and_removes_detail_fields(self):
		for name in ("multi", "interrupted", "stopped", "skipped", "crashed"):
			self.fixture(name)
		page = runs.history(limit=2, labels={}, user_data_dir="profiles")
		self.assertEqual(len(page["runs"]), 2)
		self.assertIsNotNone(page["next_before"])
		next_page = runs.history(before=page["next_before"], labels={}, user_data_dir="profiles")
		self.assertEqual(len(next_page["runs"]), 3)
		self.assertIsNone(next_page["next_before"])
		filtered = runs.history(include_checks=False, labels={}, user_data_dir="profiles")
		self.assertEqual(len(filtered["runs"]), 4)
		row = next(item for item in filtered["runs"] if item["result"] == "partial")
		self.assertNotIn("warnings", row)
		self.assertNotIn("tasks", row["accounts"][0])
		self.assertNotIn("state_before", row["accounts"][0])
		self.assertEqual(row["accounts"][0]["task_counts"]["ok"], 1)

	def test_deferred_record_has_exact_shape_in_detail_and_history(self):
		run_id = "20261016T010000Z-0001"
		path = self.directory / (run_id + ".jsonl")
		run_record.append_event(str(path), "deferred", run_id, state=3, waited_s=180, gave_up=False, future_field="ignored")
		run_record.append_event(str(path), "run_start", run_id, trigger="scheduled", accounts=[], plan={})
		run_record.append_event(str(path), "run_end", run_id, result="checked", exit_code=0, duration_ms=1)
		expected = {"state": 3, "waited_s": 180, "gave_up": False}
		self.assertEqual(runs.load_run(run_id, {}, "profiles")["deferred"], expected)
		self.assertEqual(runs.history(labels={}, user_data_dir="profiles")["runs"][0]["deferred"], expected)
		self.assertEqual(set(runs.load_run(run_id, {}, "profiles")["deferred"]), {"state", "waited_s", "gave_up"})

	def test_deferred_is_null_when_absent_or_malformed(self):
		run_id = self.fixture("multi")
		self.assertIsNone(runs.load_run(run_id, {}, "profiles")["deferred"])
		self.assertIsNone(runs.history(labels={}, user_data_dir="profiles")["runs"][0]["deferred"])
		for state, waited, gave_up in ((5, 1, False), (True, 1, False), (2, -1, False), (2, "60", False), (2, 60, "false")):
			with self.subTest(state=state, waited=waited, gave_up=gave_up):
				path = self.directory / (run_id + ".jsonl")
				path.write_text("", encoding="utf-8")
				run_record.append_event(str(path), "deferred", run_id, state=state, waited_s=waited, gave_up=gave_up)
				self.assertIsNone(runs.load_run(run_id, {}, "profiles")["deferred"])

	def test_record_path_rejects_traversal_and_missing_ids(self):
		for value in ("../file", "unknown", "20261014T090000Z-ffff"):
			with self.subTest(value=value), self.assertRaises(FileNotFoundError):
				runs.load_run(value, {}, "profiles")

	def _state_run(self, day, balance, earned, unreadable=None, profile="profiles/one", account="one"):
		stamp = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).replace(hour=12)
		run_id = stamp.strftime("%Y%m%dT%H%M%SZ-0001")
		path = self.directory / (run_id + ".jsonl")
		def event(type, **fields):
			return {"v": 1, "run": run_id, "ts": stamp.isoformat().replace("+00:00", "Z"), "type": type, **fields}
		events = [event("run_start", accounts=[account], plan={"check_only": True}, trigger="app"), event("account_start", account=account, profile=profile),
			event("state", account=account, phase="before", balance=balance, earned_today=earned, searches_earned=60, searches_max=60,
				daily_set_open=0, cards_open=1, quests_open=0, open_points=10, unreadable=unreadable),
			event("task_end", account=account, task="misc_cards", outcome="ok", reason_key=None, points_before=balance, points_after=balance),
			event("run_end", result="checked", exit_code=0)]
		path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
		return run_id

	def _overview(self, now, active=("one",), paused=(), prefs=None):
		settings = types.SimpleNamespace(active_accounts=lambda: list(active), paused_accounts=lambda: list(paused), user_data_dir=lambda: Path("profiles"))
		with mock.patch.dict(sys.modules, {"rfapp.settings": settings}), mock.patch.object(rfapp, "settings", settings, create=True), mock.patch.object(runs.run_lock, "status", return_value=None):
			return runs.overview({}, prefs or {}, now)

	def test_chart_unknown_days_and_goal_use_only_measured_samples(self):
		now = datetime(2026, 10, 14, 20, tzinfo=timezone.utc)
		today = now.astimezone().date()
		for days_ago, balance, earned in ((4, 100, 10), (2, 130, 30), (0, 150, 20)):
			self._state_run(today - timedelta(days=days_ago), balance, earned)
		data = self._overview(now, prefs={"goals": {"one": 210}})
		row = data["accounts"][0]
		self.assertEqual(len(row["chart"]), 30)
		self.assertEqual(row["chart"][-1], {"date": today.isoformat(), "balance": 150, "earned": 20})
		self.assertEqual(row["chart"][-2]["balance"], None)
		self.assertEqual(row["chart"][-2]["earned"], None)
		self.assertEqual(row["goal"], {"target": 210, "remaining": 60, "avg_per_day": 20.0, "days": 3})
		self.assertTrue(row["state_is_today"])
		misc = next(task for task in row["tasks_today"] if task["task"] == "misc_cards")
		self.assertEqual(misc["status"], "ok")
		self.assertTrue(misc["still_open"])
		self.assertEqual(data["totals"]["balance"], 150)

	def test_stale_unreadable_missing_accounts_and_insufficient_goal_days(self):
		now = datetime(2026, 10, 14, 20, tzinfo=timezone.utc)
		self._state_run(now.astimezone().date() - timedelta(days=1), 100, 10, unreadable="cards")
		data = self._overview(now, active=("one", "two"), paused=("three",), prefs={"goals": {"one": 200}})
		one = data["accounts"][0]
		self.assertFalse(one["state_is_today"])
		self.assertTrue(all(task["still_open"] is None for task in one["tasks_today"]))
		self.assertIsNone(one["goal"]["avg_per_day"])
		self.assertIsNone(data["totals"]["balance"])
		self.assertIsNone(data["accounts"][1]["state"])
		self.assertTrue(data["accounts"][2]["paused"])

	def test_check_without_after_or_task_measurement_does_not_fabricate_earned_zero(self):
		run_id = self.fixture("multi")
		path = self.directory / (run_id + ".jsonl")
		events = [event for event in run_record.read_events(str(path)) if event.get("type") not in ("task_start", "task_end") and not (event.get("type") == "state" and event.get("phase") == "after")]
		path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
		detail = runs.load_run(run_id, {}, "profiles")
		self.assertEqual(detail["accounts"][0]["points_now"], 100)
		self.assertIsNone(detail["accounts"][0]["points_earned"])
		self.assertIsNone(detail["points_earned"])

	def test_overview_uses_daily_run_earnings_only_when_state_earnings_are_missing(self):
		self.fixture("multi")
		now = datetime(2026, 10, 14, 20, tzinfo=timezone.utc)
		row = self._overview(now)["accounts"][0]
		self.assertEqual(row["chart"][-1]["earned"], 65)
		path = next(self.directory.glob("*.jsonl"))
		events = run_record.read_events(str(path))
		for event in events:
			if event.get("type") == "state":
				event["earned_today"] = None
		path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
		row = self._overview(now)["accounts"][0]
		self.assertEqual(row["chart"][-1]["earned"], 65)

	def test_overview_no_day_measurement_stays_unknown(self):
		now = datetime(2026, 10, 14, 20, tzinfo=timezone.utc)
		run_id = self._state_run(now.astimezone().date(), None, None)
		path = self.directory / (run_id + ".jsonl")
		events = [event for event in run_record.read_events(str(path)) if event.get("type") != "task_end"]
		path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
		row = self._overview(now)["accounts"][0]
		self.assertIsNone(row["chart"][-1]["balance"])
		self.assertIsNone(row["chart"][-1]["earned"])

	def test_chart_uses_measurement_timestamp_not_file_sort_order(self):
		first = self.fixture("multi")
		original = self.directory / (first + ".jsonl")
		events = run_record.read_events(str(original))
		later = "20261014T080000Z-0000"
		for event in events:
			event["run"] = later
			event["ts"] = event["ts"].replace("09:00:", "14:00:")
			if event.get("type") == "state" and event.get("phase") == "after":
				event["balance"] = 200
				event["earned_today"] = 100
		(self.directory / (later + ".jsonl")).write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
		row = self._overview(datetime(2026, 10, 14, 20, tzinfo=timezone.utc))["accounts"][0]
		self.assertEqual(row["chart"][-1]["balance"], 200)
		self.assertEqual(row["chart"][-1]["earned"], 100)

	def test_live_tail_orders_pushes_and_waits_for_complete_record_and_log_lines(self):
		run_id = self.fixture("interrupted")
		self.active.return_value = run_id
		pushes = []
		tail = runs.LiveTail(lambda topic, data: pushes.append((topic, data)), {}, "profiles")
		log = self.directory / (run_id + ".log")
		log.write_bytes(b"first\npartial")
		tail.poll()
		self.assertEqual([topic for topic, data in pushes], ["run.started", "run.log"])
		self.assertEqual(pushes[1][1]["text"], "first\n")
		pushes.clear()
		tail.poll()
		self.assertEqual(pushes, [])
		path = self.directory / (run_id + ".jsonl")
		event = {"v": 1, "run": run_id, "type": "task_end", "ts": "2026-10-14T10:00:03.000Z", "account": "default", "task": "daily_set", "outcome": "ok"}
		with path.open("ab") as stream:
			stream.write(json.dumps(event).encode("utf-8"))
		tail.poll()
		self.assertEqual(pushes, [])
		with path.open("ab") as stream:
			stream.write(b"\n")
		with log.open("ab") as stream:
			stream.write(b" finished\n")
		tail.poll()
		self.assertEqual([topic for topic, data in pushes], ["run.snapshot", "run.log"])
		self.assertEqual(pushes[1][1]["text"], "partial finished\n")
		pushes.clear()
		run_record.append_event(str(path), "run_end", run_id, result="ok", duration_ms=3, exit_code=0)
		self.active.return_value = None
		tail.poll()
		self.assertEqual([topic for topic, data in pushes], ["run.finished", "overview.changed"])
		self.assertEqual(pushes[0][1]["result"], "ok")
		self.assertNotIn("tasks", pushes[0][1]["accounts"][0])

	def test_live_log_pushes_are_capped_and_live_tail_owns_no_thread(self):
		run_id = self.fixture("interrupted")
		self.active.return_value = run_id
		push = mock.Mock()
		tail = runs.LiveTail(push, {}, "profiles")
		(self.directory / (run_id + ".log")).write_bytes(b"x\n" * 40000)
		tail.poll()
		logs = [call.args[1]["text"] for call in push.call_args_list if call.args[0] == "run.log"]
		self.assertEqual(len(logs[0].encode("utf-8")), 65536)
		push.reset_mock()
		tail.poll()
		self.assertEqual(len(push.call_args.args[1]["text"].encode("utf-8")), 14464)
		self.assertFalse(hasattr(tail, "start"))
		self.assertFalse(hasattr(tail, "stop"))
		self.assertFalse(hasattr(tail, "_thread"))

	def test_long_unterminated_log_waits_and_utf8_survives_chunk_boundaries(self):
		run_id = self.fixture("interrupted")
		self.active.return_value = run_id
		push = mock.Mock()
		tail = runs.LiveTail(push, {}, "profiles")
		log = self.directory / (run_id + ".log")
		text = "漢" * 24000
		log.write_bytes(text.encode("utf-8"))
		tail.poll()
		self.assertFalse(any(call.args[0] == "run.log" for call in push.call_args_list))
		with log.open("ab") as stream:
			stream.write(b"\n")
		tail.poll()
		tail.poll()
		chunks = [call.args[1]["text"] for call in push.call_args_list if call.args[0] == "run.log"]
		self.assertEqual("".join(chunks), text + "\n")
		self.assertTrue(all(len(chunk.encode("utf-8")) <= 65536 for chunk in chunks))

	def _assert_log_pushes(self, pushes, run_id, expected):
		logs = [data for topic, data in pushes if topic == "run.log"]
		offset = 0
		for data in logs:
			self.assertEqual(set(data), {"run_id", "text", "offset", "next_offset"})
			self.assertEqual(data["run_id"], run_id)
			self.assertEqual(data["offset"], offset)
			chunk = data["text"].encode("utf-8")
			self.assertGreater(len(chunk), 0)
			self.assertLessEqual(len(chunk), 65536)
			self.assertEqual(data["next_offset"] - data["offset"], len(chunk))
			self.assertEqual(expected[data["offset"]:data["next_offset"]], chunk)
			expected[:data["offset"]].decode("utf-8")
			expected[:data["next_offset"]].decode("utf-8")
			offset = data["next_offset"]
		self.assertEqual(offset, len(expected))
		self.assertEqual(b"".join(data["text"].encode("utf-8") for data in logs), expected)
		return logs

	def test_log_offsets_are_contiguous_and_reproduce_bytes_across_polls(self):
		run_id = self.fixture("interrupted")
		self.active.return_value = run_id
		pushes = []
		tail = runs.LiveTail(lambda topic, data: pushes.append((topic, data)), {}, "profiles")
		data = ("first\r\n" + "漢" * 24000 + "\n" + "\U00020000" * 24000 + "\nlast\n").encode("utf-8")
		(self.directory / (run_id + ".log")).write_bytes(data)
		for _ in range(5):
			tail.poll()
		logs = self._assert_log_pushes(pushes, run_id, data)
		self.assertEqual(len(logs), 3)
		count = len(pushes)
		tail.poll()
		self.assertEqual(len(pushes), count)

	def test_three_and_four_byte_characters_straddling_cap_stay_whole(self):
		run_id = self.fixture("interrupted")
		self.active.return_value = run_id
		for character in ("漢", "\U00020000"):
			for split in range(1, len(character.encode("utf-8"))):
				with self.subTest(character=character, split=split):
					pushes = []
					tail = runs.LiveTail(lambda topic, data: pushes.append((topic, data)), {}, "profiles")
					prefix = b"a" * (65536 - split)
					data = prefix + character.encode("utf-8") + b"last\n"
					(self.directory / (run_id + ".log")).write_bytes(data)
					tail.poll()
					tail.poll()
					logs = self._assert_log_pushes(pushes, run_id, data)
					self.assertEqual(logs[0]["next_offset"], len(prefix))
					self.assertEqual(logs[1]["offset"], len(prefix))
					self.assertEqual(logs[1]["text"], character + "last\n")

	def test_growing_log_continues_from_last_offset_after_context_refresh(self):
		run_id = self.fixture("interrupted")
		self.active.return_value = run_id
		pushes = []
		tail = runs.LiveTail(lambda topic, data: pushes.append((topic, data)), {}, "profiles")
		log = self.directory / (run_id + ".log")
		first = "first 漢\n".encode("utf-8")
		partial = "\U00020000".encode("utf-8")
		log.write_bytes(first + partial[:2])
		tail.poll()
		self.assertEqual(tail._offsets[(run_id, ".log")], len(first))
		tail.set_context({"@root": "Renamed"}, "profiles")
		tail.poll()
		self.assertEqual(tail._offsets[(run_id, ".log")], len(first))
		with log.open("ab") as stream:
			stream.write(partial[2:] + b" next\n")
		tail.poll()
		last = "last 漢\U00020000\n".encode("utf-8")
		with log.open("ab") as stream:
			stream.write(last)
		tail.poll()
		logs = self._assert_log_pushes(pushes, run_id, first + partial + b" next\n" + last)
		self.assertEqual(len(logs), 3)
		self.assertEqual(logs[1]["offset"], len(first))

	def test_resumed_log_offset_advances_past_continuation_bytes(self):
		run_id = self.fixture("interrupted")
		self.active.return_value = run_id
		data = "漢\U00020000last\n".encode("utf-8")
		(self.directory / (run_id + ".log")).write_bytes(data)
		for offset, expected_start in ((1, 3), (2, 3), (4, 7), (5, 7), (6, 7)):
			with self.subTest(offset=offset):
				push = mock.Mock()
				tail = runs.LiveTail(push, {}, "profiles")
				tail._offsets[(run_id, ".log")] = offset
				tail.poll()
				log = next(call.args[1] for call in push.call_args_list if call.args[0] == "run.log")
				self.assertEqual(log["offset"], expected_start)
				self.assertEqual(log["next_offset"], len(data))
				self.assertEqual(log["text"], data[expected_start:].decode("utf-8"))

	def test_context_refresh_keeps_offsets_and_emits_changed_label_next_poll(self):
		run_id = self.fixture("interrupted")
		self.active.return_value = run_id
		push = mock.Mock()
		tail = runs.LiveTail(push, {}, "profiles")
		(self.directory / (run_id + ".log")).write_text("first\n", encoding="utf-8")
		tail.poll()
		offsets = tail._offsets.copy()
		push.reset_mock()
		tail.set_context({"@root": "Renamed"}, "profiles")
		self.assertEqual(tail._snapshot["accounts"][0]["label"], "Account 1")
		tail.poll()
		self.assertEqual(tail._offsets, offsets)
		push.assert_called_once()
		self.assertEqual(push.call_args.args[0], "run.snapshot")
		self.assertEqual(push.call_args.args[1]["accounts"][0]["label"], "Renamed")

	def test_active_run_resolves_the_settings_profile_folder_explicitly(self):
		settings = types.SimpleNamespace(user_data_dir=lambda: Path("custom"))
		with mock.patch.dict(sys.modules, {"rfapp.settings": settings}), mock.patch.object(rfapp, "settings", settings, create=True), mock.patch.object(runs.run_lock, "status", return_value=types.SimpleNamespace(owner="farmer", run_id="synthetic")) as status:
			self.active_patch.stop()
			self.assertEqual(runs.active_run_id(), "synthetic")
			status.assert_called_once_with(directory="custom")

	def _supervised_fixture(self):
		run_id = self.fixture("interrupted")
		path = self.directory / (run_id + ".jsonl")
		events = run_record.read_events(str(path))
		events[0].update(supervisor_pid=os.getpid(), ts=datetime.now(timezone.utc).isoformat())
		path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
		self.active.return_value = run_id
		pushes = []
		tail = runs.LiveTail(lambda topic, data: pushes.append((topic, data)), {}, "profiles")
		return run_id, path, tail, pushes

	def test_alive_supervisor_without_run_end_never_pushes_finished(self):
		run_id, path, tail, pushes = self._supervised_fixture()
		tail.poll()
		self.active.return_value = None
		for _ in range(3):
			tail.poll()
		self.assertEqual([topic for topic, data in pushes], ["run.started"])
		self.assertIn(run_id, tail._pending_finishes)
		self.assertEqual(tail._supervisors[run_id], (os.getpid(), runs.run_lock._process_created(os.getpid())))
		self.assertFalse(any(event.get("type") == "run_end" for event in run_record.read_events(str(path))))

	def test_supervisor_terminal_event_wins_after_lock_is_released(self):
		for result in ("crashed", "stopped"):
			with self.subTest(result=result):
				run_id, path, tail, pushes = self._supervised_fixture()
				tail.poll()
				self.active.return_value = None
				tail.poll()
				self.assertFalse(any(topic == "run.finished" for topic, data in pushes))
				run_record.append_event(str(path), "run_end", run_id, result=result, exit_code=9, duration_ms=3, written_by="supervisor")
				tail.poll()
				finished = [data for topic, data in pushes if topic == "run.finished"]
				self.assertEqual(len(finished), 1)
				self.assertEqual(finished[0]["result"], result)
				self.assertEqual([topic for topic, data in pushes][-2:], ["run.finished", "overview.changed"])
				self.assertNotIn(run_id, tail._pending_finishes)
				self.assertNotIn(run_id, tail._supervisors)
				tail.poll()
				self.assertEqual(len([topic for topic, data in pushes if topic == "run.finished"]), 1)

	def test_dead_supervisor_without_terminal_event_is_interrupted(self):
		run_id, path, tail, pushes = self._supervised_fixture()
		with mock.patch.object(runs.run_lock, "_process_created", return_value=100) as created:
			tail.poll()
			self.active.return_value = None
			tail.poll()
			self.assertFalse(any(topic == "run.finished" for topic, data in pushes))
			created.return_value = None
			tail.poll()
		self.assertEqual([data["result"] for topic, data in pushes if topic == "run.finished"], ["interrupted"])
		self.assertNotIn(run_id, tail._pending_finishes)
		self.assertFalse(any(event.get("type") == "run_end" for event in run_record.read_events(str(path))))

	def test_reused_supervisor_pid_does_not_hold_old_run_open(self):
		_, _, tail, pushes = self._supervised_fixture()
		with mock.patch.object(runs.run_lock, "_process_created", return_value=100) as created:
			tail.poll()
			self.active.return_value = None
			created.return_value = 101
			tail.poll()
		self.assertEqual([data["result"] for topic, data in pushes if topic == "run.finished"], ["interrupted"])

	def test_first_seen_supervisor_created_after_run_start_is_not_its_owner(self):
		_, path, tail, pushes = self._supervised_fixture()
		start = run_record.read_events(str(path))[0]
		at = datetime.fromisoformat(start["ts"]).astimezone(timezone.utc) + timedelta(seconds=1)
		elapsed = at - datetime(1601, 1, 1, tzinfo=timezone.utc)
		created = (elapsed.days * 86400 + elapsed.seconds) * 10_000_000 + elapsed.microseconds * 10
		with mock.patch.object(runs.run_lock, "_process_created", return_value=created):
			tail.poll()
			self.active.return_value = None
			tail.poll()
		self.assertEqual([data["result"] for topic, data in pushes if topic == "run.finished"], ["interrupted"])

	def test_pending_previous_run_finishes_while_a_new_run_is_active(self):
		run_id, path, tail, pushes = self._supervised_fixture()
		tail.poll()
		new_id = self.fixture("multi")
		self.active.return_value = new_id
		tail.poll()
		self.assertFalse(any(topic == "run.finished" for topic, data in pushes))
		self.assertEqual([data["run_id"] for topic, data in pushes if topic == "run.started"], [run_id, new_id])
		run_record.append_event(str(path), "run_end", run_id, result="crashed", exit_code=9, duration_ms=3, written_by="supervisor")
		tail.poll()
		self.assertEqual([(data["run_id"], data["result"]) for topic, data in pushes if topic == "run.finished"], [(run_id, "crashed")])
		self.assertEqual(tail._active, new_id)
