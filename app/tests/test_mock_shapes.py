import _bootstrap  # noqa: F401

import json
import base64
import struct
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import date, datetime, timedelta

from rfapp import signin


MOCK_ROOT = Path(__file__).resolve().parents[1] / "ui/mock"
SCREEN_ROOT = MOCK_ROOT.parent / "js/screens"
SOURCE_CATALOGS = MOCK_ROOT.parent / "i18n/en"
# Literal success objects from CONTRACT 6.3. Named response types are excluded.
SUCCESS_KEYS = {
	"ui_ready": set(),
	"open_external": set(),
	"get_live": {"run", "log"},
	"get_history": {"runs", "next_before"},
	"get_run_log": {"text", "next_offset", "eof", "size"},
	"start_run": {"started"},
	"stop_run": {"stopping"},
	"set_goal": {"goal"},
	"get_settings": {"values", "schedule", "secrets_set"},
	"save_settings": {"values", "schedule", "warnings", "secrets_set"},
	"pick_path": {"path"},
	"list_ollama_models": {"models"},
	"export_diagnostics": {"path"},
	"dismiss_update": set(),
	"get_legacy_install": {"found", "path", "accounts"},
	"import_legacy_install": {"accounts"},
	"complete_setup": set(),
	"list_accounts": {"accounts"},
	"get_account_picture": {"account_id", "rev", "data_url"},
	"add_account": {"account_id", "signin"},
	"sign_in_again": {"signin"},
	"cancel_signin": set(),
	"rename_account": {"account"},
	"set_account_paused": {"account"},
	"remove_account": {"accounts", "warnings"},
}

STATS_KEYS = {"generated_at", "today", "range", "from", "account_id", "accounts", "coverage_from", "days", "totals", "streak", "sources", "goal", "warnings"}
STATS_TOTALS = {"week_earned", "month_earned", "earned", "measured_earned", "known_days", "expected_days", "average_per_day", "best_day", "balance", "balance_at", "batch_queries_sent", "query_count_complete", "run_count", "check_count"}
STATS_STREAK = {"current", "through", "today_done", "best", "best_from", "best_to", "missed", "not_checked"}
STATS_SOURCE_IDS = {"daily_set", "explore_on_bing", "visual_search", "misc_cards", "quests", "searches", "bonus"}
STATS_STATES = {"done", "missed", "no_check", "today_open", "before_tracking"}
ACCOUNT_KEYS = {"id", "label", "dir", "paused", "is_root", "signed_in", "balance", "balance_at", "last_run_at", "last_result", "email", "name", "picture_rev"}


def overview_streaks_problems(value):
	if "streaks" not in value:
		return ["overview.streaks: missing F78 field"]
	streaks = value["streaks"]
	if streaks is None:
		return []
	problems = []
	def keys(node, expected, location):
		if not isinstance(node, dict) or set(node) != expected:
			problems.append(f"{location}: expected exactly {sorted(expected)}")
			return False
		return True
	def integer(node, field, location):
		if node[field] is not None and (type(node[field]) is not int or node[field] < 0):
			problems.append(f"{location}.{field}: expected a nonnegative integer or null")
	if not keys(streaks, {"captured_at", "account_id", "big_bonus", "items"}, "overview.streaks"):
		return problems
	try:
		if not isinstance(streaks["captured_at"], str) or "T" not in streaks["captured_at"]:
			raise ValueError("not an ISO timestamp")
		datetime.fromisoformat(streaks["captured_at"].replace("Z", "+00:00"))
	except ValueError:
		problems.append("overview.streaks.captured_at: expected an ISO timestamp")
	if not isinstance(streaks["account_id"], str) or not streaks["account_id"]:
		problems.append("overview.streaks.account_id: expected a nonempty string")
	bonus = streaks["big_bonus"]
	if keys(bonus, {"points", "stamps_total", "stamps_collected"}, "overview.streaks.big_bonus"):
		for field in bonus:
			integer(bonus, field, "overview.streaks.big_bonus")
	if not isinstance(streaks["items"], list):
		problems.append("overview.streaks.items: expected a list")
		return problems
	seen = set()
	fields = {"id", "day", "length", "today_done", "today_total", "next_points", "day_points", "day_points_earned", "end_points"}
	for index, item in enumerate(streaks["items"]):
		location = f"overview.streaks.items[{index}]"
		if not keys(item, fields, location):
			continue
		identity = item["id"]
		if not isinstance(identity, str) or identity not in ("bing_search", "daily_set", "bing_app", "visual_search") or identity in seen:
			problems.append(f"{location}.id: expected one of the four unique F78 ids")
		else:
			seen.add(identity)
		for field in fields - {"id", "day_points_earned"}:
			integer(item, field, location)
		if item["day_points_earned"] is not None and type(item["day_points_earned"]) is not bool:
			problems.append(f"{location}.day_points_earned: expected boolean or null")
		if type(item["day"]) is int and type(item["length"]) is int and item["day"] > item["length"]:
			problems.append(f"{location}.day: exceeds length")
	return problems


def picture_problems(value):
	if not isinstance(value, dict) or set(value) != SUCCESS_KEYS["get_account_picture"]:
		return ["get_account_picture: expected exactly account_id, rev and data_url"]
	problems = []
	if not isinstance(value["account_id"], str) or not value["account_id"]:
		problems.append("account_id: expected a nonempty string")
	if value["rev"] is not None and (not isinstance(value["rev"], str) or not value["rev"]):
		problems.append("rev: expected a nonempty string or null")
	url = value["data_url"]
	if url is not None:
		try:
			if not isinstance(url, str) or not url.startswith("data:image/png;base64,") or value["rev"] is None:
				raise ValueError("picture requires a PNG data URL and revision")
			payload = base64.b64decode(url.split(",", 1)[1], validate=True)
			if payload[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR":
				raise ValueError("not a PNG header")
			width, height = struct.unpack(">II", payload[16:24])
			if not 0 < width <= 128 or not 0 < height <= 128:
				raise ValueError("dimensions exceed 128px")
		except (ValueError, TypeError, struct.error):
			problems.append("data_url: expected a PNG data URL of at most 128x128 with a revision")
	return problems


def stats_problems(value):
	problems = []
	def keys(node, expected, location):
		if not isinstance(node, dict) or set(node) != expected:
			problems.append(f"{location}: expected keys {sorted(expected)}, got {sorted(node) if isinstance(node, dict) else type(node).__name__}")
			return False
		return True
	if not keys(value, STATS_KEYS, "StatsData"):
		return problems
	if value["range"] not in (7, 30, 90):
		problems.append("range: expected 7, 30 or 90")
	if not isinstance(value["days"], list) or len(value["days"]) != 90:
		problems.append("days: expected all 90 calendar dates independently of range")
	else:
		today = date.fromisoformat(value["today"])
		for index, day in enumerate(value["days"]):
			day_keys = {"date", "state", "earned", "balance", "sources"}
			# F73 is additive. Old F67 fixtures remain deliberate compatibility
			# samples; a fixture using any F73 field must carry the whole shape.
			progress = {"open_points", "quests_open", "possible", "runs"}
			if isinstance(day, dict) and progress.intersection(day):
				day_keys |= progress
			if not keys(day, day_keys, f"days[{index}]"):
				continue
			if day["date"] != (today - timedelta(days=89-index)).isoformat():
				problems.append(f"days[{index}].date: not dense oldest-first local dates")
			if day["state"] not in STATS_STATES:
				problems.append(f"days[{index}].state: {day['state']!r} is not in F67")
			if index == 89 and day["state"] not in ("done", "today_open", "before_tracking"):
				problems.append("today: must never be a past missed or no_check date")
			if index < 89 and day["state"] == "today_open":
				problems.append(f"days[{index}]: today_open on an earlier date")
			for field in ("earned", "balance"):
				if day[field] is not None and type(day[field]) is not int:
					problems.append(f"days[{index}].{field}: expected integer or null")
			if day["state"] == "before_tracking" and (day["earned"] is not None or day["balance"] is not None):
				problems.append(f"days[{index}]: before_tracking has invented values")
			if "sources" in day and keys(day["sources"], {"points", "runs"}, f"days[{index}].sources"):
				sources = day["sources"]
				valid_points = keys(sources["points"], STATS_SOURCE_IDS, f"days[{index}].sources.points")
				if not isinstance(sources["points"], dict) or any(type(points) is not int or points < 0 for points in sources["points"].values()):
					problems.append(f"days[{index}].sources.points: expected seven nonnegative integers")
					valid_points = False
				if type(sources["runs"]) is not int or sources["runs"] < 0:
					problems.append(f"days[{index}].sources.runs: expected a nonnegative counted-run integer")
				if valid_points:
					total = sum(sources["points"].values())
					if sources["runs"] == 0 and total != 0:
						problems.append(f"days[{index}].sources: zero counted runs require zero task points")
					if type(day["earned"]) is int and total > day["earned"]:
						problems.append(f"days[{index}].sources: counted task sum exceeds known day earned")
			if "possible" in day:
				for field in ("open_points", "quests_open", "possible"):
					if day[field] is not None and type(day[field]) is not int:
						problems.append(f"days[{index}].{field}: expected integer or null")
				expected = day["earned"] + day["open_points"] if type(day["earned"]) is int and type(day["open_points"]) is int else None
				if day["possible"] != expected:
					problems.append("possible: expected earned + open_points or null")
				if day["runs"] is not None and not isinstance(day["runs"], list):
					problems.append("runs: expected list or legacy null")
				elif isinstance(day["runs"], list):
					for summary in day["runs"]:
						if keys(summary, {"run_id", "started_at", "kind", "trigger", "active", "result", "result_key", "points_earned", "problems"}, "days.runs[]"):
							if summary["kind"] not in ("check", "run") or type(summary["active"]) is not bool:
								problems.append("run: invalid kind or active")
							if not isinstance(summary["problems"], list):
								problems.append("run.problems: expected list")
							else:
								for problem in summary["problems"]:
									if keys(problem, {"task", "reason_key"}, "problems[]") and problem["task"] is not None and problem["task"] not in STATS_SOURCE_IDS - {"other"}:
										problems.append("problem.task: unknown task")
	for account in value["accounts"]:
		keys(account, {"account_id", "label", "paused"}, "accounts[]")
	if value["account_id"] is not None and value["account_id"] not in [account["account_id"] for account in value["accounts"]]:
		problems.append("account_id: selected id absent from current accounts")
	keys(value["totals"], STATS_TOTALS, "totals")
	keys(value["streak"], STATS_STREAK, "streak")
	if keys(value["sources"], {"points", "runs"}, "sources"):
		keys(value["sources"]["points"], STATS_SOURCE_IDS, "sources.points")
		if not isinstance(value["sources"]["points"], dict) or any(type(points) is not int or points < 0 for points in value["sources"]["points"].values()):
			problems.append("sources.points: expected nonnegative integers, never null")
		if type(value["sources"]["runs"]) is not int or value["sources"]["runs"] < 0:
			problems.append("sources.runs: expected a nonnegative counted-run integer")
	if value["goal"] is not None:
		keys(value["goal"], {"target", "remaining", "avg_per_day", "days"}, "goal")
		if value["account_id"] is None and len(value["accounts"]) > 1:
			problems.append("goal: aggregate multi-account goal must be null")
	for warning in value["warnings"]:
		if keys(warning, {"code"}, "warnings[]") and warning["code"] not in ("ledger_rebuilt", "ledger_newer"):
			problems.append("warnings[].code: not in F67")
	return problems


def successful_answers(answer):
	answers = answer if isinstance(answer, list) else [answer]
	return [(index, entry) for index, entry in enumerate(answers) if isinstance(entry, dict) and entry.get("ok") is True]


def run_task_points_problems(value):
	problems = []
	for account_index, account in enumerate(value.get("accounts", [])):
		for task_index, task in enumerate(account.get("tasks", [])):
			location = f"accounts[{account_index}].tasks[{task_index}].points"
			if "points" not in task:
				problems.append(f"{location}: missing F75 H1 task field")
			elif task["points"] is not None and (type(task["points"]) is not int or task["points"] < 0):
				problems.append(f"{location}: expected a nonnegative integer or null")
	return problems


def list_problems(answer):
	if not isinstance(answer, list):
		return []
	problems = []
	defaults = []
	for index, entry in enumerate(answer):
		if not isinstance(entry, dict) or not isinstance(entry.get("ok"), bool):
			problems.append(f"answer {index} is not an envelope with boolean ok")
			continue
		if "when" in entry:
			if not isinstance(entry["when"], dict):
				problems.append(f"answer {index} when must be an object")
		else:
			defaults.append(index)
	if len(defaults) != 1:
		problems.append(f"exactly one default without when required, found {len(defaults)}")
	elif defaults[0] != len(answer) - 1:
		problems.append("default without when must be last")
	return problems


def field_error_consumers(screen_root=SCREEN_ROOT):
	"""Read the field-code namespace from the consumers, not mock vocabulary."""
	consumers = {}
	for path in sorted(Path(screen_root).glob("*.js")):
		source = path.read_text(encoding="utf-8")
		if not re.search(r"\b\w+\??\.fields\b", source):
			continue
		# Setup interpolates the Object.entries code; Settings interpolates its
		# error.fields alias fieldErrors[id]. A new/ambiguous reader fails closed.
		prefixes = set(re.findall(r"`([a-z][a-z0-9_.]*\.)\$\{(?:code|fieldErrors\[[^\]]+\])\}", source))
		if len(prefixes) != 1:
			raise ValueError(f"{path.name}: error.fields reader needs one identifiable field-code catalog prefix, found {sorted(prefixes)}")
		consumers[path.stem] = prefixes.pop()
	return consumers


def message_key_references(value, location):
	if isinstance(value, dict):
		for name, item in value.items():
			child = f"{location}.{name}"
			if name == "message_key" and item is not None:
				yield child, item
			elif name != "when":
				yield from message_key_references(item, child)
	elif isinstance(value, list):
		for index, item in enumerate(value):
			yield from message_key_references(item, f"{location}[{index}]")


def validate_error_references(mock_root, catalog_root=SOURCE_CATALOGS, screen_root=SCREEN_ROOT):
	mock_root = Path(mock_root)
	keys = set()
	for path in sorted(Path(catalog_root).glob("*.json")):
		keys.update(json.loads(path.read_text(encoding="utf-8"))["messages"])
	if not keys:
		raise ValueError("No English catalog messages found")
	consumers = field_error_consumers(screen_root)
	problems = []
	for path in sorted(mock_root.rglob("*.json")):
		relative = path.relative_to(mock_root)
		fixture = json.loads(path.read_text(encoding="utf-8"))
		for method, answer in fixture.get("methods", {}).items():
			for index, entry in enumerate(answer if isinstance(answer, list) else [answer]):
				if not isinstance(entry, dict):
					continue  # Envelope shape is checked separately.
				location = f"{relative.as_posix()}: {method}[{index}]"
				for reference, key in message_key_references(entry, location):
					if not isinstance(key, str) or key not in keys:
						problems.append(f"{reference}: missing English catalog entry {key!r}")
				error = entry.get("error")
				if not isinstance(error, dict):
					continue
				if entry.get("ok") is False and "message_key" not in error:
					problems.append(f"{location}.error.message_key: missing envelope error message key")
				fields = error.get("fields")
				if fields is None:
					continue
				if not isinstance(fields, dict):
					problems.append(f"{location}.error.fields: expected an object or null")
					continue
				prefix = consumers.get(relative.parts[0])
				for field, code in fields.items():
					label = f"{location}.error.fields[{field}]"
					if prefix is None:
						problems.append(f"{label}: no field-error consumer mapping for {relative.parts[0]!r}")
					elif not isinstance(code, str) or prefix + code not in keys:
						problems.append(f"{label}: missing English catalog entry {prefix + str(code)!r}, consumed by {relative.parts[0]}.js")
	return problems


def named_records(value, location):
	if isinstance(value, dict):
		if {"account_id", "state", "error_code", "message_key", "at"}.issubset(value):
			yield "signin", location, value
		if {"id", "label", "dir", "paused", "is_root", "signed_in"}.issubset(value):
			yield "account", location, value
		for key, item in value.items():
			if key != "when":
				yield from named_records(item, f"{location}.{key}")
	elif isinstance(value, list):
		for index, item in enumerate(value):
			yield from named_records(item, f"{location}[{index}]")


def validate_account_and_signin_records(mock_root):
	problems = []
	with tempfile.TemporaryDirectory(prefix="mock-signin-producer-") as directory, patch.object(signin.paths, "runs_dir", return_value=Path(directory)):
		for path in sorted(Path(mock_root).rglob("*.json")):
			fixture = json.loads(path.read_text(encoding="utf-8"))
			for kind, location, record in named_records(fixture, path.relative_to(mock_root).as_posix()):
				if kind == "signin":
					produced = signin._write_status(record["account_id"], record["state"], record["error_code"], record.get("detail"))
					if record["message_key"] != produced["message_key"]:
						problems.append(f"{location}.message_key: {record['message_key']!r} differs from signin._write_status {produced['message_key']!r}")
				else:
					for key in ("email", "name", "picture_rev"):
						if key not in record or record[key] is not None and not isinstance(record[key], str):
							problems.append(f"{location}.{key}: Account requires string or null")
	return problems


def run_records(value, location):
	if isinstance(value, dict):
		if {"run_id", "trigger", "kind", "started_at"}.issubset(value):
			yield location, value
		for key, item in value.items():
			if key != "when":
				yield from run_records(item, f"{location}.{key}")
	elif isinstance(value, list):
		for index, item in enumerate(value):
			yield from run_records(item, f"{location}[{index}]")


def validate_deferred_records(mock_root):
	problems = []
	for path in sorted(Path(mock_root).rglob("*.json")):
		fixture = json.loads(path.read_text(encoding="utf-8"))
		for location, run in run_records(fixture, path.relative_to(mock_root).as_posix()):
			if ".get_stats." in location and ".days[" in location and ".runs[" in location:
				# F73's compact Stats item is not the F64 History/Live summary;
				# its exact frozen fields are checked by stats_problems above.
				continue
			label = f"{location}.deferred"
			if "deferred" not in run:
				problems.append(f"{label}: missing F64 run field")
				continue
			value = run["deferred"]
			if value is None:
				continue
			if not isinstance(value, dict) or set(value) != {"state", "waited_s", "gave_up"}:
				problems.append(f"{label}: expected null or exactly state, waited_s, gave_up")
				continue
			if type(value["state"]) is not int or type(value["waited_s"]) is not int or value["waited_s"] < 0 or type(value["gave_up"]) is not bool:
				problems.append(f"{label}: state and waited_s must be integers, waited_s nonnegative, gave_up boolean")
	return problems


class MockShapeTests(unittest.TestCase):
	def test_f78_overview_streaks_shapes(self):
		checked = 0
		for path in sorted(MOCK_ROOT.rglob("*.json")):
			fixture = json.loads(path.read_text(encoding="utf-8"))
			for index, answer in successful_answers(fixture.get("methods", {}).get("get_overview")):
				value = answer["data"]
				if path.parent.name != "overview" and path != MOCK_ROOT / "shell/defaults.json" and "streaks" not in value:
					continue  # Other lanes' older no-account answers are outside this F78 write set.
				with self.subTest(fixture=path.relative_to(MOCK_ROOT).as_posix(), answer=index):
					self.assertEqual([], overview_streaks_problems(value))
				checked += 1
		self.assertGreaterEqual(checked, 9)
		print(f"F78 Overview mock streak answers checked: {checked}", flush=True)

	def test_f78_streaks_checker_rejects_aliases_negative_bool_and_overlong_day(self):
		value = json.loads((MOCK_ROOT / "overview/ready.json").read_text(encoding="utf-8"))["methods"]["get_overview"]["data"]
		self.assertEqual([], overview_streaks_problems(value))
		self.assertEqual([], overview_streaks_problems({"streaks": None}))
		self.assertTrue(overview_streaks_problems({}))
		for replacement in ({}, [], False, {**value["streaks"], "at": value["streaks"]["captured_at"]},
			{**value["streaks"], "captured_at": "yesterday"}, {**value["streaks"], "captured_at": None},
			{**value["streaks"], "items": None}):
			with self.subTest(replacement=replacement):
				self.assertTrue(overview_streaks_problems({"streaks": replacement}))
		for field in ("points", "stamps_total", "stamps_collected"):
			for invalid in (-1, True, 1.5, "12"):
				wrong = json.loads(json.dumps(value))
				wrong["streaks"]["big_bonus"][field] = invalid
				self.assertTrue(overview_streaks_problems(wrong), (field, invalid))
		for field in ("day", "length", "today_done", "today_total", "next_points", "day_points", "end_points"):
			for invalid in (-1, True, 1.5, "7"):
				wrong = json.loads(json.dumps(value))
				wrong["streaks"]["items"][0][field] = invalid
				self.assertTrue(overview_streaks_problems(wrong), (field, invalid))
		for fields in ({"id": "searches"}, {"day": 8}, {"day_points_earned": 1}):
			wrong = json.loads(json.dumps(value))
			wrong["streaks"]["items"][0].update(fields)
			self.assertTrue(overview_streaks_problems(wrong), fields)
		wrong = json.loads(json.dumps(value))
		wrong["streaks"]["items"][0].pop("next_points")
		self.assertTrue(overview_streaks_problems(wrong))
		for bonus in ({"points": None, "stamps_total": None, "stamps_collected": None}, {"points": 0, "stamps_total": 0, "stamps_collected": 0}):
			self.assertEqual([], overview_streaks_problems({"streaks": {**value["streaks"], "big_bonus": bonus, "items": []}}))

	def test_f78_overview_streak_shape_matches_real_producer(self):
		import sys
		import types
		from datetime import timezone
		import rfapp
		from rfapp import paths, runs
		import run_record
		with tempfile.TemporaryDirectory(prefix="f78-overview-shape-") as directory:
			root = Path(directory)
			settings = types.SimpleNamespace(active_accounts=lambda: ["one"], paused_accounts=lambda: [], user_data_dir=lambda: root / "profiles")
			with patch.object(paths, "runs_dir", return_value=root), patch.dict(sys.modules, {"rfapp.settings": settings}), patch.object(rfapp, "settings", settings, create=True), patch.object(runs.run_lock, "status", return_value=None):
				before = runs.overview({}, {}, datetime(2026, 10, 20, 21, tzinfo=timezone.utc))
				self.assertIn("streaks", before, "F78 W2-1 producer must land overview.streaks")
				self.assertIsNone(before["streaks"])
				run_id = "20261020T195534Z-0001"
				record = root / (run_id + ".jsonl")
				run_record.append_event(str(record), "run_start", run_id, accounts=["one"], trigger="app", plan={"check_only": True})
				run_record.append_event(str(record), "account_start", run_id, account="one", profile=str(root / "profiles/one"))
				run_record.append_event(str(record), "streak_capture", run_id, account="one", status="ok", reason=None, truncated=False, sections=[{"id":"streaks", "text":"Streaks 1,000 BONUS Earn 12 stamps for big bonus points Bing Search Streak Day 6 of 7 streak completed. Complete the next day to earn 100 points. 3 100 Search: 1/1 Daily Set Streak Day 6 of 7 streak completed. Complete the next day to earn 100 points. 30 100 Activity: 3/3 Bing App Streak Day 0 of 7 streak completed. Complete the next day to earn 5 points. +5 50 Check-in: 0/1 Visual Search Streak Day 2 of 7 streak completed. Complete the next day to earn 5 points. +5 100 Activity: 0/1", "nodes":[]}])
				run_record.append_event(str(record), "run_end", run_id, result="checked", exit_code=0)
				produced = runs.overview({}, {}, datetime(2030, 1, 1, tzinfo=timezone.utc))
				self.assertEqual([], overview_streaks_problems(produced))
				self.assertIsNotNone(produced["streaks"])
				self.assertEqual({"bing_search", "daily_set", "bing_app", "visual_search"}, {item["id"] for item in produced["streaks"]["items"]})

	def test_f77_day_sources_reject_missing_aliases_negative_and_overcount(self):
		value = json.loads((MOCK_ROOT / "stats/progress.json").read_text(encoding="utf-8"))["methods"]["get_stats"]["data"]
		self.assertEqual([], stats_problems(value))
		for source in (None, {}, {"points": {}, "runs": 0}, {"points": dict.fromkeys(STATS_SOURCE_IDS, 0), "count": 0},
			{"points": {**dict.fromkeys(STATS_SOURCE_IDS, 0), "other": 0}, "runs": 0},
			{"points": {**dict.fromkeys(STATS_SOURCE_IDS, 0), "searches": -1}, "runs": 1},
			{"points": {**dict.fromkeys(STATS_SOURCE_IDS, 0), "searches": True}, "runs": 1},
			{"points": {**dict.fromkeys(STATS_SOURCE_IDS, 0), "searches": None}, "runs": 1},
			{"points": dict.fromkeys(STATS_SOURCE_IDS, 0), "runs": -1},
			{"points": dict.fromkeys(STATS_SOURCE_IDS, 0), "runs": True},
			{"points": {**dict.fromkeys(STATS_SOURCE_IDS, 0), "searches": 1}, "runs": 0},
			{"points": {**dict.fromkeys(STATS_SOURCE_IDS, 0), "searches": 66}, "runs": 1}):
			wrong = json.loads(json.dumps(value))
			if source is None:
				wrong["days"][-1].pop("sources")
			else:
				wrong["days"][-1]["sources"] = source
			with self.subTest(source=source):
				self.assertTrue(any("sources" in problem for problem in stats_problems(wrong)))

	def test_f75_history_detail_task_points_match_contract(self):
		checked = []
		for path in sorted((MOCK_ROOT / "history").glob("*.json")):
			fixture = json.loads(path.read_text(encoding="utf-8"))
			for index, entry in successful_answers(fixture.get("methods", {}).get("get_run")):
				location = f"{path.relative_to(MOCK_ROOT).as_posix()}: get_run[{index}]"
				with self.subTest(fixture=location):
					self.assertEqual([], run_task_points_problems(entry["data"]))
				checked.append(location)
		self.assertTrue(checked, "No successful get_run fixtures found")
		print("F75 H1 task points fixtures: " + ", ".join(checked), flush=True)

	def test_f75_history_task_points_checker_rejects_missing_negative_and_noninteger(self):
		def detail(task):
			return {"accounts": [{"tasks": [task]}]}
		for points in (None, 0, 60):
			self.assertEqual([], run_task_points_problems(detail({"points": points})))
		for task in ({}, {"points": -1}, {"points": True}, {"points": False}, {"points": 1.0}, {"points": "60"}):
			with self.subTest(task=task):
				self.assertEqual(1, len(run_task_points_problems(detail(task))))
		value = {"accounts": [{"tasks": [{"points": 0}, {}]}, {"tasks": [{"points": None}, {}]}]}
		self.assertEqual(run_task_points_problems(value), ["accounts[0].tasks[1].points: missing F75 H1 task field",
			"accounts[1].tasks[1].points: missing F75 H1 task field"])

	def test_f68_account_and_picture_shapes(self):
		accounts = pictures = 0
		for path in sorted(MOCK_ROOT.rglob("*.json")):
			fixture = json.loads(path.read_text(encoding="utf-8"))
			for kind, location, value in named_records(fixture, path.relative_to(MOCK_ROOT).as_posix()):
				if kind == "account":
					with self.subTest(location=location):
						self.assertEqual(ACCOUNT_KEYS, set(value))
						self.assertTrue(value["picture_rev"] is None or isinstance(value["picture_rev"], str))
					accounts += 1
			for _, entry in successful_answers(fixture.get("methods", {}).get("get_account_picture")):
				with self.subTest(fixture=path.name):
					self.assertEqual([], picture_problems(entry["data"]))
				pictures += 1
		self.assertGreater(accounts, 0)
		self.assertGreater(pictures, 0)

	def test_f68_checker_rejects_picture_aliases_external_urls_and_oversize(self):
		valid = {"account_id":"synthetic", "rev":None, "data_url":None}
		self.assertEqual([], picture_problems(valid))
		for wrong in ({**valid,"revision":None}, {**valid,"rev":1}, {**valid,"data_url":"https://example.com/photo.png"}, {**valid,"rev":"r1","data_url":"data:image/jpeg;base64,AAAA"}):
			self.assertTrue(picture_problems(wrong))
		payload = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", 129, 128)
		self.assertTrue(picture_problems({**valid,"rev":"r1","data_url":"data:image/png;base64,"+base64.b64encode(payload).decode("ascii")}))

	def test_stats_mock_names_and_calendar_match_f67(self):
		checked = 0
		for path in sorted(MOCK_ROOT.rglob("*.json")):
			fixture = json.loads(path.read_text(encoding="utf-8"))
			answer = fixture.get("methods", {}).get("get_stats")
			if answer is None:
				continue
			if path.parent.name == "stats":
				self.assertIs(fixture.get("synthetic"), True, path.name)
			for index, entry in successful_answers(answer):
				with self.subTest(fixture=path.relative_to(MOCK_ROOT).as_posix(), answer=index):
					self.assertEqual([], stats_problems(entry["data"]))
				checked += 1
		self.assertGreater(checked, 0)
		print(f"F67 Stats mock success responses checked: {checked}", flush=True)

	def test_stats_checker_rejects_aliases_and_false_today_failure(self):
		answer = json.loads((MOCK_ROOT / "stats/young.json").read_text(encoding="utf-8"))["methods"]["get_stats"]
		value = (answer[0] if isinstance(answer, list) else answer)["data"]
		self.assertEqual([], stats_problems(value))
		wrong = json.loads(json.dumps(value))
		wrong["metrics"] = wrong.pop("totals")
		self.assertTrue(stats_problems(wrong))
		wrong = json.loads(json.dumps(value))
		wrong["days"][-1]["state"] = "missed"
		self.assertTrue(any("today" in problem for problem in stats_problems(wrong)))
		wrong = json.loads(json.dumps(value))
		wrong["days"][-1]["core_state"] = wrong["days"][-1].pop("state")
		self.assertTrue(stats_problems(wrong))
		wrong = json.loads(json.dumps(value))
		wrong["warnings"] = [{"code": "ledger_rebuilt", "message_key": "stats.warning.ledger_rebuilt"}]
		self.assertTrue(stats_problems(wrong))

	def test_f75_sources_checker_rejects_nulls_old_fields_bool_and_aliases(self):
		answer = json.loads((MOCK_ROOT / "stats/young.json").read_text(encoding="utf-8"))["methods"]["get_stats"]
		value = (answer[0] if isinstance(answer, list) else answer)["data"]
		value["sources"] = {"points": dict.fromkeys(STATS_SOURCE_IDS, 0), "runs": 0}
		self.assertEqual(stats_problems(value), [])
		for wrong_source in ({"points": {**value["sources"]["points"], "other": 0}, "runs": 0},
			{"points": {**value["sources"]["points"], "searches": None}, "runs": 1},
			{"points": {**value["sources"]["points"], "searches": True}, "runs": 1},
			{"points": value["sources"]["points"], "runs": True},
			{"points": value["sources"]["points"], "runs": 0, "parts": []},
			{"points": value["sources"]["points"], "count": 0}):
			with self.subTest(source=wrong_source):
				self.assertTrue(stats_problems({**value, "sources": wrong_source}))
	def test_f64_all_mock_run_summaries_and_rows_have_exact_deferred_shape(self):
		problems = validate_deferred_records(MOCK_ROOT)
		self.assertEqual([], problems, "Mock run records differ from CONTRACT F64:\n" + "\n".join(problems))
		self.assertTrue(any(list(run_records(json.loads(path.read_text(encoding="utf-8")), str(path))) for path in MOCK_ROOT.rglob("*.json")))

	def test_f64_deferred_shape_rejects_missing_extra_and_wrong_types(self):
		with tempfile.TemporaryDirectory(prefix="mock-deferred-negative-") as directory:
			path = Path(directory) / "history.json"
			run = {"run_id":"synthetic", "trigger":"scheduled", "kind":"run", "started_at":"2026-10-16T00:00:00Z"}
			for value in (None, {"state":3, "waited_s":61, "gave_up":False}, {"state":2, "waited_s":2400, "gave_up":True}):
				path.write_text(json.dumps({"methods":{"get_history":{"ok":True,"data":{"runs":[{**run,"deferred":value}]}}}}), encoding="utf-8")
				self.assertEqual([], validate_deferred_records(directory))
			invalid = ({}, {"deferred":{}}, {"deferred":{"state":3,"waited_s":61,"gave_up":False,"reason":"extra"}},
				{"deferred":{"state":True,"waited_s":61,"gave_up":False}}, {"deferred":{"state":3,"waited_s":True,"gave_up":False}},
				{"deferred":{"state":3,"waited_s":-1,"gave_up":False}}, {"deferred":{"state":3,"waited_s":61,"gave_up":1}},
				{"deferred":{"state":"3","waited_s":61,"gave_up":False}})
			for fields in invalid:
				with self.subTest(fields=fields):
					path.write_text(json.dumps({"pushes":[{"topic":"run.snapshot","data":{**run,**fields}}]}), encoding="utf-8")
					self.assertEqual(1, len(validate_deferred_records(directory)))

	def test_signin_messages_match_real_producer_and_account_identity_is_present(self):
		problems = validate_account_and_signin_records(MOCK_ROOT)
		self.assertEqual([], problems, "Mock named records differ from the real producer or Account contract:\n" + "\n".join(problems))
		kinds = {kind for path in MOCK_ROOT.rglob("*.json") for kind, _, _ in named_records(json.loads(path.read_text(encoding="utf-8")), str(path))}
		self.assertEqual({"account", "signin"}, kinds)

	def test_null_signin_message_and_missing_account_identity_are_rejected(self):
		with tempfile.TemporaryDirectory(prefix="mock-record-negative-") as directory:
			root = Path(directory)
			with patch.object(signin.paths, "runs_dir", return_value=root):
				status = signin._write_status("synthetic", "waiting")
			status["message_key"] = None
			account = {"id": "synthetic", "label": "Synthetic", "dir": "Synthetic", "paused": False, "is_root": False, "signed_in": None, "picture_rev": None}
			path = root / "fixture.json"
			path.write_text(json.dumps({"signin": status, "account": account}), encoding="utf-8")
			problems = validate_account_and_signin_records(root)
			self.assertEqual(3, len(problems), problems)
			self.assertTrue(any("signin._write_status" in problem for problem in problems))
			self.assertTrue(any(".account.email" in problem for problem in problems))
			self.assertTrue(any(".account.name" in problem for problem in problems))
			with patch.object(signin.paths, "runs_dir", return_value=root):
				status = signin._write_status("synthetic", "waiting")
			account.update(email=None, name=None)
			path.write_text(json.dumps({"signin": status, "account": account}), encoding="utf-8")
			self.assertEqual([], validate_account_and_signin_records(root))

	def test_field_error_mapping_matches_current_consumers(self):
		self.assertEqual({"setup":"errors.settings.", "settings":"errors.settings."}, field_error_consumers())

	def test_all_mock_error_references_have_english_catalog_entries(self):
		problems = validate_error_references(MOCK_ROOT)
		self.assertEqual([], problems, "Mock error references do not match their consumer catalogs:\n" + "\n".join(problems))

	def test_unknown_field_code_mutation_is_rejected_without_editing_fixtures(self):
		with tempfile.TemporaryDirectory(prefix="mock-field-code-") as directory:
			root = Path(directory)
			folder = root / "setup"
			folder.mkdir()
			path = folder / "edge-save-error.json"
			fixture = {"methods":{"save_settings":{"ok":False,"error":{"code":"settings.invalid","message_key":"errors.app.settings_invalid","params":{},"detail":"Synthetic mutation only","fields":{"advanced.edge_path":"path_missing"}}}}}
			path.write_text(json.dumps(fixture), encoding="utf-8")
			problems = validate_error_references(root)
			self.assertEqual(1, len(problems))
			self.assertIn("setup/edge-save-error.json: save_settings[0].error.fields[advanced.edge_path]", problems[0])
			self.assertIn("errors.settings.path_missing", problems[0])
			with self.assertRaises(AssertionError):
				self.assertEqual([], problems)
			print("mock field-code mutation: expected rejection: " + problems[0], flush=True)
			fixture["methods"]["save_settings"]["error"]["fields"]["advanced.edge_path"] = "file_missing"
			path.write_text(json.dumps(fixture), encoding="utf-8")
			self.assertEqual([], validate_error_references(root))

	def test_unknown_message_key_is_rejected_in_every_list_answer(self):
		with tempfile.TemporaryDirectory(prefix="mock-message-key-") as directory:
			path = Path(directory) / "settings.json"
			path.write_text(json.dumps({"methods":{"get_history":[{"when":{"before":"next"},"ok":True,"data":{"warnings":[{"message_key":"errors.app.synthetic_unknown"}]}},{"ok":False,"error":{"message_key":"errors.app.synthetic_unknown","fields":None}}]}}), encoding="utf-8")
			problems = validate_error_references(directory)
			self.assertEqual(2, len(problems))
			self.assertIn("get_history[0].data.warnings[0].message_key", problems[0])
			self.assertIn("get_history[1].error.message_key", problems[1])

	def test_lists_are_request_matched_with_one_final_default(self):
		for path in sorted(MOCK_ROOT.rglob("*.json")):
			fixture = json.loads(path.read_text(encoding="utf-8"))
			for method, answer in fixture.get("methods", {}).items():
				with self.subTest(fixture=path.relative_to(MOCK_ROOT).as_posix(), method=method):
					self.assertEqual([], list_problems(answer), "Response list differs from CONTRACT 6.5 F33")

	def test_all_literal_success_objects_match_contract(self):
		fixtures = sorted(MOCK_ROOT.rglob("*.json"))
		self.assertTrue(fixtures, "No mock fixtures were found")
		checked = 0
		for path in fixtures:
			fixture = json.loads(path.read_text(encoding="utf-8"))
			for method, answer in fixture.get("methods", {}).items():
				if method not in SUCCESS_KEYS:
					continue
				for index, entry in successful_answers(answer):
					with self.subTest(fixture=path.relative_to(MOCK_ROOT).as_posix(), method=method, answer=index):
						self.assertIsInstance(entry.get("data"), dict, "Successful literal response must have object data")
						self.assertEqual(SUCCESS_KEYS[method], set(entry["data"]), "Success response keys differ from CONTRACT 6.3")
						if method in ("get_settings", "save_settings"):
							secrets = {"run.openrouter_key", "notify.webhook_url"}
							self.assertIsInstance(entry["data"]["values"], dict)
							self.assertFalse(secrets.intersection(entry["data"]["values"]), "F42 secrets must not enter public values")
							flags = entry["data"]["secrets_set"]
							self.assertIsInstance(flags, dict)
							self.assertEqual(secrets, set(flags))
							self.assertTrue(all(type(value) is bool for value in flags.values()), "F42 secret flags must be booleans")
					checked += 1
		self.assertGreater(checked, 0)
		print(f"mock_shapes: {len(fixtures)} fixtures, {checked} successful literal answers checked", flush=True)

	def test_list_includes_every_success_and_skips_failures(self):
		first = {"when": {"before": "next"}, "ok": True, "data": {"runs": [], "next_before": "next"}}
		second = {"ok": True, "data": {"runs": [], "next_before": None}}
		failure = {"when": {"before": "missing"}, "ok": False, "error": {}}
		self.assertEqual([(0, first), (2, second)], successful_answers([first, failure, second]))
		self.assertEqual([(0, first)], successful_answers(first))
		self.assertEqual([], list_problems([first, failure, second]))

	def test_invalid_list_shapes_are_detected(self):
		envelope = {"ok": True, "data": {}}
		for answer in ([], [envelope, envelope], [envelope, {"when": {}, **envelope}], [{"when": [], **envelope}, envelope], [{"when": None, **envelope}, envelope], [1, envelope]):
			with self.subTest(answer=answer):
				self.assertTrue(list_problems(answer))


if __name__ == "__main__":
	unittest.main()
