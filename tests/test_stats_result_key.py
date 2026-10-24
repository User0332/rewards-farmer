"""F88 shares unreadable-check wording; F89 refreshes unmarked old ledgers."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import stats_ledger as ledger

NOW = datetime(2026, 10, 23, 22, tzinfo=timezone.utc)
RUN_ID = "20261023T210000Z-0088"


def record(decisions=(("one", "unreadable"),), *, code="page_blank", result="skipped", ran=()):
	"""Raw protocol with invented accounts and observations only."""
	start = datetime(2026, 10, 23, 21, tzinfo=timezone.utc)
	events = []
	def emit(kind, **values):
		events.append({"v": 1, "type": kind, "run": RUN_ID,
			"ts": ledger._iso(start + timedelta(seconds=len(events))), **values})
	emit("run_start", trigger="scheduled", accounts=[account for account, _ in decisions], plan={"only_if_open": True})
	for account, reason in decisions:
		emit("account_start", account=account, profile="synthetic-profiles/" + account)
		emit("state", account=account, phase="before", balance=None, open_points=0, quests_open=0,
			daily_set_open=0, searches_earned=60, searches_max=60, unreadable=code if reason.startswith("unreadable") else None)
		emit("decision", account=account, should_run=reason == "unreadable_run_anyway", reason=reason)
		if account in ran:
			emit("task_start", account=account, task="quests")
			emit("task_end", account=account, task="quests", outcome="ok", points_before=1000, points_after=1000)
		emit("account_end", account=account, started=True, ran_tasks=account in ran)
	if result is not None:
		emit("run_end", result=result, accounts_ran=len(ran))
	return events


class ResultKeyTests(unittest.TestCase):
	def test_unreadable_skipped_check_uses_the_unreadable_key(self):
		for code in ("page_blank", "signed_out"):
			with self.subTest(code=code):
				summary = ledger.run_summary(RUN_ID, record(code=code))
				self.assertEqual((summary["result"], summary["result_key"]), ("skipped", "errors.run.unreadable"))
				self.assertEqual((summary["kind"], summary["problems"]), ("check", []))

	def test_nothing_open_and_below_threshold_keep_the_skipped_key(self):
		for reason in ("nothing_open", "below_threshold"):
			with self.subTest(reason=reason):
				summary = ledger.run_summary(RUN_ID, record((("one", reason),)))
				self.assertEqual((summary["result"], summary["result_key"]), ("skipped", "errors.run.skipped"))

	def test_two_account_skip_uses_full_run_decisions_in_every_account_summary(self):
		events = record((("one", "nothing_open"), ("two", "unreadable")))
		for account in (None, "one", "two"):
			with self.subTest(account=account):
				summary = ledger.run_summary(RUN_ID, events, account=account)
				self.assertEqual(summary["result_key"], "errors.run.unreadable")

	def test_unreadable_run_anyway_is_not_the_unreadable_skip_reason(self):
		for result in ("ok", "partial", "failed", "skipped"):
			with self.subTest(result=result):
				events = record((("one", "unreadable_run_anyway"),), result=result, ran=("one",))
				self.assertEqual(ledger.run_summary(RUN_ID, events)["result_key"], "errors.run." + result)
		self.assertEqual(ledger.run_summary(RUN_ID, record((("one", "unreadable_run_anyway"),)))["result_key"], "errors.run.skipped")

	def test_checked_crashed_interrupted_and_other_terminal_results_keep_their_keys(self):
		for result in ("ok", "partial", "checked", "crashed", "interrupted", "failed", "stopped", "busy", "config_error", "unknown"):
			with self.subTest(result=result):
				self.assertEqual(ledger.run_summary(RUN_ID, record(result=result))["result_key"], "errors.run." + result)

	def test_active_result_none_and_unfinished_interruption_keep_current_keys(self):
		events = record(result=None)
		active = ledger.run_summary(RUN_ID, events, active=True)
		self.assertEqual((active["result"], active["result_key"], active["active"]), (None, None, True))
		interrupted = ledger.run_summary(RUN_ID, events)
		self.assertEqual((interrupted["result"], interrupted["result_key"]), ("interrupted", "errors.run.interrupted"))
		self.assertIsNone(ledger.result_key(None, events))

	def test_any_account_that_ran_prevents_the_unreadable_override(self):
		events = record((("one", "unreadable"), ("two", "unreadable_run_anyway")), ran=("two",))
		self.assertEqual(ledger.run_summary(RUN_ID, events)["result_key"], "errors.run.skipped")
		self.assertEqual(ledger.run_summary(RUN_ID, events, account="one")["result_key"], "errors.run.skipped")

	def test_each_actual_execution_marker_prevents_override_even_without_the_others(self):
		markers = ({"type": "run_end", "accounts_ran": 1}, {"type": "account_end", "account": "two", "ran_tasks": True},
			{"type": "task_start", "account": "two", "task": "quests"},
			{"type": "task_end", "account": "two", "task": "quests", "outcome": "ok"})
		decision = {"type": "decision", "account": "one", "should_run": False, "reason": "unreadable"}
		for marker in markers:
			with self.subTest(marker=marker):
				self.assertEqual(ledger.result_key("skipped", [decision, marker]), "errors.run.skipped")

	def test_off_tasks_do_not_mean_an_account_executed_work(self):
		events = [{"type": "decision", "account": "one", "should_run": False, "reason": "unreadable"},
			{"type": "task_end", "account": "one", "task": "quests", "outcome": "off"}]
		self.assertEqual(ledger.result_key("skipped", events), "errors.run.unreadable")

	def test_state_code_without_an_unreadable_decision_cannot_override_the_key(self):
		events = [event for event in record() if event["type"] != "decision"]
		self.assertEqual(ledger.run_summary(RUN_ID, events)["result_key"], "errors.run.skipped")
		self.assertEqual(ledger.result_key("skipped", []), "errors.run.skipped")

	def test_shared_helper_does_not_modify_events(self):
		events = record()
		before = json.dumps(events, sort_keys=True)
		self.assertEqual(ledger.result_key("skipped", events), "errors.run.unreadable")
		self.assertEqual(json.dumps(events, sort_keys=True), before)


class ResultKeyFoldTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.home = Path(self.temp.name)
		self.folder = self.home / "runs"
		self.folder.mkdir()
		self.path = self.home / "stats-ledger.json"
		self.record_path = self.folder / (RUN_ID + ".jsonl")

	def write(self, events):
		self.record_path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")

	def fold(self):
		return ledger.fold(self.folder, data_home=self.home, user_data_dir=str(self.home / "profiles"), now=NOW, zone=timezone.utc)

	def old_ledger(self):
		self.write(record())
		value = self.fold().ledger
		for row in value["rows"]:
			for summary in row["runs"]:
				summary["result_key"] = "errors.run.skipped"
		self.path.write_text(json.dumps(value), encoding="utf-8")
		return value

	def test_fresh_fold_persists_new_key_without_a_schema_change(self):
		self.write(record((("one", "nothing_open"), ("two", "unreadable"))))
		result = self.fold()
		self.assertTrue(result.ok)
		self.assertFalse(result.newer)
		self.assertEqual(result.ledger["v"], 3)
		self.assertEqual(len(result.ledger["rows"]), 2)
		persisted = json.loads(self.path.read_text(encoding="utf-8"))
		self.assertEqual(persisted, result.ledger)
		for row in persisted["rows"]:
			self.assertEqual((row["run_count"], row["check_count"]), (0, 1))
			self.assertEqual((row["runs"][0]["result"], row["runs"][0]["result_key"]), ("skipped", "errors.run.unreadable"))
			self.assertEqual(set(row["runs"][0]), {"run_id", "started_at", "kind", "trigger", "active", "result", "result_key", "points_earned", "problems"})

	def test_refreshed_v3_ledger_keeps_its_stored_summary_and_warm_bytes(self):
		previous = self.old_ledger()
		self.assertEqual(previous["result_key_refresh"], 1)
		before = self.path.read_bytes()
		with mock.patch.object(ledger, "_read_record", side_effect=AssertionError("unchanged record reread")), \
			mock.patch.object(ledger, "replace_file", side_effect=AssertionError("unexpected refresh")), \
			mock.patch.object(ledger, "_migrate", side_effect=AssertionError("v3 must not migrate")):
			result = self.fold()
		self.assertTrue(result.ok)
		self.assertEqual(result.ledger, previous)
		self.assertEqual(self.path.read_bytes(), before)

	def test_changed_record_gets_new_key_through_normal_incremental_fold(self):
		previous = self.old_ledger()
		self.write(record(code="signed_out"))
		with mock.patch.object(ledger, "_read_record", wraps=ledger._read_record) as read:
			result = self.fold()
		self.assertTrue(result.ok)
		self.assertEqual(read.call_count, 1)
		self.assertEqual(result.ledger["v"], previous["v"])
		self.assertEqual(result.ledger["rows"][0]["runs"][0]["result_key"], "errors.run.unreadable")
		self.assertNotEqual(result.ledger["index"][RUN_ID], previous["index"][RUN_ID])
		self.assertEqual(json.loads(self.path.read_text())["rows"][0]["runs"][0]["result_key"], "errors.run.unreadable")


if __name__ == "__main__":
	unittest.main()
