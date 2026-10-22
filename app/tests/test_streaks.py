import _bootstrap  # noqa: F401

from datetime import datetime, timezone
import json
from pathlib import Path
import unittest
from unittest import mock

from rfapp import runs, streaks
import run_record
from test_stats import StatsTests

# Verbatim section text and observed nodes from CONTRACT 2026-10-20T20:33:30Z.
# Only the account and envelope are synthetic; no live account data is read.
B14_TEXT = "Streaks Daily streaks reset in 3h 1,000 BONUS Earn 12 stamps for big bonus points Learn more Bing Search Streak Day 6 of 7 streak completed. Complete the next day to earn 100 points. 3 100 Search: 1/1 Daily Set Streak Day 6 of 7 streak completed. Complete the next day to earn 100 points. 30 100 Activity: 3/3 Bing App Streak Day 0 of 7 streak completed. Complete the next day to earn 5 points. +5 50 Check-in: 0/1 Visual Search Streak Day 2 of 7 streak completed. Complete the next day to earn 5 points. +5 100 Activity: 0/1"
PANELS = (
	("Bing Search Streak", 6, 100, "3", "100", "Search: 1/1"),
	("Daily Set Streak", 6, 100, "30", "100", "Activity: 3/3"),
	("Bing App Streak", 0, 5, "+5", "50", "Check-in: 0/1"),
	("Visual Search Streak", 2, 5, "+5", "100", "Activity: 0/1"),
)


def node(text="", *, tag="p", role="", aria_label="", classes=(), depth=4, **extra):
	return {"tag": tag, "role": role, "aria_label": aria_label, "data_attributes": [], "classes": list(classes), "text": text, "depth": depth, **extra}


def capture(*, deeper=False, stamps=None):
	nodes = [node("Streaks", tag="h2"), node("Daily streaks reset in 3h", tag="button", aria_label="About Streaks"),
		node("1,000 BONUS", classes=("bg-rewardsBgBonusGradient", "text-pageHeader")), node("Earn 12 stamps for big bonus points"),
		node(tag="div", role="progressbar", aria_label="Stamps collected", classes=("grid", "grid-cols-6"))]
	if deeper:
		nodes[-1].update(aria_valuenow="" if stamps is None else str(stamps), aria_valuemax="12", aria_valuetext="",
			children_count=12, children=[{"classes": ["stamp"], "aria_label": ""} for _ in range(12)])
	for name, day, next_points, daily, end, today in PANELS:
		nodes.extend([node(name, classes=("text-globalBody2Strong",)), node(f"Day {day} of 7 streak completed.", tag="div"),
			node(f"Complete the next day to earn {next_points} points.", tag="div")])
		if deeper:
			nodes.extend([node(daily, tag="span", depth=16), node(end, tag="span", depth=17)])
		nodes.append(node(today, tag="div", classes=("text-end",)))
	return {"v": 1, "type": "streak_capture", "run": "20261020T195534Z-abcd", "ts": "2026-10-20T19:55:41.000Z", "account": "sample",
		"status": "ok", "reason": None, "truncated": not deeper,
		"sections": [{"id": "streaks", "text": B14_TEXT, "nodes": nodes}, {"id": "moreactivities", "text": "Keep earning Bonuspunkte", "nodes": []}]}


def expected():
	return {"captured_at": "2026-10-20T19:55:41.000Z", "account_id": "sample",
		"big_bonus": {"points": 1000, "stamps_total": 12, "stamps_collected": None},
		"items": [
			{"id": "bing_search", "day": 6, "length": 7, "today_done": 1, "today_total": 1, "next_points": 100, "day_points": 3, "day_points_earned": True, "end_points": 100},
			{"id": "daily_set", "day": 6, "length": 7, "today_done": 3, "today_total": 3, "next_points": 100, "day_points": 30, "day_points_earned": True, "end_points": 100},
			{"id": "bing_app", "day": 0, "length": 7, "today_done": 0, "today_total": 1, "next_points": 5, "day_points": 5, "day_points_earned": False, "end_points": 50},
			{"id": "visual_search", "day": 2, "length": 7, "today_done": 0, "today_total": 1, "next_points": 5, "day_points": 5, "day_points_earned": False, "end_points": 100}
		]}


class ParseTests(unittest.TestCase):
	def test_b14_verbatim_text_and_depth_limited_nodes_preserve_unknown_stamps(self):
		self.assertEqual(streaks.parse(capture(), "sample"), expected())

	def test_deeper_reward_nodes_and_progressbar_use_own_values(self):
		value = expected()
		value["big_bonus"]["stamps_collected"] = 5
		self.assertEqual(streaks.parse(capture(deeper=True, stamps=5), "sample"), value)

	def test_missing_progress_value_never_infers_count_from_total_or_child_classes(self):
		value = capture(deeper=True)
		self.assertEqual(streaks.parse(value, "sample")["big_bonus"], expected()["big_bonus"])
		value["sections"][0]["nodes"][4]["aria_valuetext"] = "4 of 12 stamps"
		self.assertEqual(streaks.parse(value, "sample")["big_bonus"]["stamps_collected"], 4)

	def test_unknown_streak_is_dropped_but_bing_app_is_preserved_for_ui_to_filter(self):
		value = capture()
		value["sections"][0]["text"] += " Unknown Streak Day 4 of 7 streak completed. Complete the next day to earn 5 points. 5 100 Activity: 1/1"
		self.assertEqual(streaks.parse(value, "sample"), expected())
		self.assertIn("bing_app", [item["id"] for item in streaks.parse(value, "sample")["items"]])

	def test_german_page_has_no_guessed_values_and_known_name_without_fields_has_nulls(self):
		value = capture()
		value["sections"][0].update(text="Serien 1.000 BONUS Sammle 12 Stempel Tägliche Aufgaben Tag 6 von 7", nodes=[])
		# BONUS alone exists in German too; no English stamp or streak evidence
		# may turn that into an English panel.
		self.assertIsNone(streaks.parse(value, "sample"))
		value["sections"][0]["text"] = "Daily Set Streak Unbekannt"
		item = streaks.parse(value, "sample")["items"][0]
		self.assertEqual(item, {"id": "daily_set", **dict.fromkeys(("day", "length", "today_done", "today_total", "next_points", "day_points", "day_points_earned", "end_points"))})

	def test_truncated_capture_keeps_only_complete_matches_and_never_borrows_other_section(self):
		value = capture()
		value["sections"][0]["text"] = B14_TEXT.split(" Bing App Streak")[0]
		value["sections"][0]["nodes"] = value["sections"][0]["nodes"][:13]
		self.assertEqual(streaks.parse(value, "sample")["items"], expected()["items"][:2])

	def test_nodes_own_reward_pair_and_invalid_order_stays_null(self):
		value = capture(deeper=True)
		first = next(node for node in value["sections"][0]["nodes"] if node["text"] == "3")
		first["text"] = "+9"
		item = streaks.parse(value, "sample")["items"][0]
		self.assertEqual((item["day_points"], item["day_points_earned"], item["end_points"]), (9, False, 100))
		first["text"] = "not a number"
		item = streaks.parse(value, "sample")["items"][0]
		self.assertEqual((item["day_points"], item["day_points_earned"], item["end_points"]), (None, None, None))
		value = capture()
		value["sections"][0].update(text=B14_TEXT.replace("3 100 Search", "Search"), nodes=[])
		self.assertIsNone(streaks.parse(value, "sample")["items"][0]["day_points"])

	def test_page_thousands_separators_are_parsed_without_changing_other_fields(self):
		for number in ("1,000", "1.000", "1\u00a0000", "1\u202f000"):
			value = capture()
			value["sections"][0]["text"] = B14_TEXT.replace("1,000", number)
			self.assertEqual(streaks.parse(value, "sample")["big_bonus"]["points"], 1000)

	def test_truncated_text_still_uses_complete_deep_nodes(self):
		value = capture(deeper=True, stamps=3)
		value["sections"][0]["text"] = B14_TEXT[:100]
		result = expected()
		result["big_bonus"]["stamps_collected"] = 3
		self.assertEqual(streaks.parse(value, "sample"), result)


class LatestTests(unittest.TestCase):
	setUp = StatsTests.setUp

	def write(self, stamp, value=None, account="sample", result="ok"):
		run_id = stamp + "-abcd"
		start = {"v": 1, "type": "account_start", "run": run_id, "ts": "2026-10-20T19:55:30Z", "account": account, "profile": str(self.home / "profiles" / account)}
		events = [start]
		if value is not None:
			events.append({**value, "run": run_id, "account": account, "status": result})
		(self.folder / (run_id + ".jsonl")).write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
		return run_id

	def latest(self, accounts=("sample",)):
		return streaks.latest(accounts, str(self.home / "profiles"), datetime(2026, 10, 20, 21, tzinfo=timezone.utc))

	def test_no_capture_is_null_and_old_records_without_it_are_compatible(self):
		self.assertIsNone(self.latest())
		self.write("20261020T195534Z")
		self.assertIsNone(self.latest())

	def test_newer_absent_or_unreadable_is_skipped_and_read_stops_at_first_ok(self):
		self.write("20261020T180000Z", capture())
		ok_id = self.write("20261020T190000Z", capture())
		self.write("20261020T200000Z", capture(), result="absent")
		self.write("20261020T201000Z", capture(), result="unreadable")
		with mock.patch.object(run_record, "read_events", wraps=run_record.read_events) as read:
			self.assertEqual(self.latest(), expected())
		self.assertEqual(len(read.call_args_list), 3)
		self.assertEqual(Path(read.call_args_list[-1].args[0]).stem, ok_id)

	def test_two_accounts_selected_account_only_and_latest_timestamp_inside_one_run(self):
		self.write("20261020T190000Z", capture(), account="sample")
		self.write("20261020T200000Z", capture(deeper=True, stamps=7), account="second")
		self.assertEqual(self.latest()["account_id"], "sample")
		self.assertEqual(self.latest(("second",))["big_bonus"]["stamps_collected"], 7)
		self.assertEqual(self.latest(("sample", "second"))["account_id"], "second")
		path = self.folder / "20261020T200000Z-abcd.jsonl"
		events = run_record.read_events(str(path))
		events.append({**capture(), "run": "20261020T200000Z-abcd", "ts": "2026-10-20T20:00:00Z"})
		path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
		self.assertEqual(self.latest(("sample", "second"))["account_id"], "sample")

	def test_overview_calls_new_producer_and_never_changes_existing_detail(self):
		self.active, self.paused = ["sample"], ["second"]
		self.now = datetime(2026, 10, 20, 21, tzinfo=timezone.utc)
		self.write("20261020T190000Z", capture())
		value = runs.overview(self.settings, self.prefs, self.now)
		self.assertEqual(value["streaks"], expected())
		self.active, self.paused = ["second"], []
		self.assertIsNone(runs.overview(self.settings, self.prefs, self.now)["streaks"])

	def phase_capture(self, phase, *, done, stamp, status="ok"):
		value = capture(deeper=True, stamps=9)
		if not done:
			value = json.loads(json.dumps(value).replace("Search: 1/1", "Search: 0/1").replace("Activity: 3/3", "Activity: 0/3"))
		value.update(phase=phase, ts=stamp, status=status)
		return value

	def append_capture(self, run_id, value):
		path = self.folder / (run_id + ".jsonl")
		events = run_record.read_events(str(path)) + [{**value, "run": run_id}]
		path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")

	def test_after_capture_updates_card_today_counts_by_timestamp_even_when_event_order_reversed(self):
		before = self.phase_capture("before", done=False, stamp="2026-10-20T19:00:00Z")
		after = self.phase_capture("after", done=True, stamp="2026-10-20T19:05:00Z")
		run_id = self.write("20261020T190000Z", after)
		self.append_capture(run_id, before)
		self.active, self.paused = ["sample"], []
		self.now = datetime(2026, 10, 20, 21, tzinfo=timezone.utc)
		value = runs.overview(self.settings, self.prefs, self.now)["streaks"]
		self.assertEqual(value["captured_at"], after["ts"])
		by_id = {item["id"]: item for item in value["items"]}
		self.assertEqual((by_id["bing_search"]["today_done"], by_id["bing_search"]["today_total"]), (1, 1))
		self.assertEqual((by_id["daily_set"]["today_done"], by_id["daily_set"]["today_total"]), (3, 3))

	def test_absent_or_unreadable_after_keeps_before_and_phase_less_b16_remains_compatible(self):
		for status in ("absent", "unreadable"):
			with self.subTest(status=status):
				before = self.phase_capture("before", done=False, stamp="2026-10-20T19:00:00Z")
				after = self.phase_capture("after", done=True, stamp="2026-10-20T19:05:00Z", status=status)
				run_id = self.write("20261020T190000Z", before)
				self.append_capture(run_id, after)
				self.assertEqual(self.latest(), streaks.parse(before, "sample"))
		before.pop("phase")
		self.write("20261020T190000Z", before)
		self.assertEqual(self.latest(), streaks.parse(before, "sample"))

	def test_older_run_never_overrides_newer_run_and_after_wins_only_timestamp_ties(self):
		old = self.phase_capture("after", done=False, stamp="2026-10-20T20:30:00Z")
		new = self.phase_capture("before", done=True, stamp="2026-10-20T20:00:00Z")
		self.write("20261020T190000Z", old)
		run_id = self.write("20261020T200000Z", new)
		self.assertEqual(self.latest(), streaks.parse(new, "sample"))
		after = self.phase_capture("after", done=False, stamp=new["ts"])
		self.append_capture(run_id, after)
		self.assertEqual(self.latest(), streaks.parse(after, "sample"))


del StatsTests
