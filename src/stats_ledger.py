"""Local daily observations retained independently of raw run retention."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, time as day_time, timedelta, timezone
import copy
import json
import io
import logging
import os
from pathlib import Path
import re
import tempfile
import time

import constants
import run_lock
from file_replace import read_file, replace_file
from dotenv import dotenv_values

VERSION = 3
RECORD_VERSION = 1
TASKS = ("daily_set", "explore_on_bing", "visual_search", "misc_cards", "quests", "searches", "bonus")
SOURCES = TASKS + ("other",)
_RUN_ID = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{4}\Z")
_ACCOUNT = re.compile(r"(?:@root|[A-Za-z0-9_.-]+)\Z")
_QUERIES = re.compile(r"sent (\d+) search(?:es)?\Z")
_TASK_REASONS = frozenset(("not_available", "quota_short", "element_missing", "not_ready", "browser_closed", "offline", "error", "stopped", "not_run", "turned_off"))
_RESULTS = frozenset(("ok", "skipped", "partial", "checked", "crashed", "interrupted", "stopped", "failed", "busy", "config_error", "unknown"))
_BROWSER_REASONS = frozenset(("edge_missing", "driver_missing", "driver_mismatch", "profile_in_use", "profile_unwritable", "edge_exited", "unknown"))


@dataclass(frozen=True)
class FoldResult:
	ledger: dict
	ok: bool
	newer: bool = False
	pending: frozenset[str] = frozenset()


def _log(code):
	# CLI console output is frozen; only durable file handlers receive codes.
	record = logging.LogRecord("stats_ledger", logging.WARNING, "", 0, code, (), None)
	seen = set()
	for logger in (logging.getLogger("rfapp"), logging.getLogger()):
		for handler in logger.handlers:
			if isinstance(handler, logging.FileHandler) and id(handler) not in seen:
				seen.add(id(handler))
				try:
					handler.handle(record)
				except Exception:
					pass


def _at(value):
	try:
		return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
	except (AttributeError, TypeError, ValueError):
		return None


def _iso(value):
	return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _number(value):
	return type(value) is int


def account_id(name, profile, user_data_dir):
	if profile and os.path.normcase(os.path.abspath(profile)) == os.path.normcase(os.path.abspath(user_data_dir)):
		return "@root"
	value = Path(profile).name if profile else "@root" if name == "default" else name
	return value if isinstance(value, str) and _ACCOUNT.fullmatch(value) and value not in (".", "..") and not value.endswith(".") else None


def _bounds(at, zone):
	local = at.astimezone(zone) if zone is not None else at.astimezone()
	date = local.date()
	start = datetime.combine(date, day_time.min, tzinfo=zone).astimezone(timezone.utc)
	end = datetime.combine(date + timedelta(days=1), day_time.min, tzinfo=zone).astimezone(timezone.utc)
	return date.isoformat(), _iso(start), _iso(end)


def _day(at, zone, bounds):
	for date, start, end in bounds:
		if start <= at < end:
			return date, _iso(start), _iso(end)
	return _bounds(at, zone)


def _row(date, account, start, end):
	return {"date": date, "account_id": account, "earned": None, "earned_basis": None,
		"balance": None, "balance_at": None, "done": None, "done_at": None,
		"daily_set_open": None, "searches_earned": None, "searches_max": None,
		"batch_queries_sent": None, "query_count_complete": True, "run_count": 0, "check_count": 0,
		"sources": dict.fromkeys(SOURCES), "combined": [], "source_complete": True,
		"parts": [], "open_points": None, "quests_open": None, "runs": [],
		"counted_sources": dict.fromkeys(TASKS, 0), "_counted_runs": [],
		"run_ids": [], "first_at": None, "last_at": None,
		"_day_start": start, "_day_end": end, "_state_at": None, "_page_earned": None,
		"_run_earned": None, "_runs": [], "_checks": [], "_has_tasks": False}


def _add(left, right):
	return right if left is None else left if right is None else left + right


def _finalize(row):
	row["earned"] = row["_page_earned"] if row["_page_earned"] is not None else row["_run_earned"]
	row["earned_basis"] = "page" if row["_page_earned"] is not None else "runs" if row["_run_earned"] is not None else None
	row["run_ids"] = sorted(set(row["run_ids"]))
	row["_runs"] = sorted(set(row["_runs"]))
	row["_checks"] = sorted(set(row["_checks"]))
	row["_counted_runs"] = sorted(set(row["_counted_runs"]))
	row["run_count"], row["check_count"] = len(row["_runs"]), len(row["_checks"])
	row["combined"] = sorted({tuple(group) for group in row["combined"]})
	row["combined"] = [list(group) for group in row["combined"]]
	row["parts"].sort(key=lambda part: (part["started_at"] or "", part["run_id"]))
	if row["runs"] is not None:
		row["runs"].sort(key=lambda run: (run["started_at"] or "", run["run_id"]))
	return row


def _merge(left, right):
	row = copy.deepcopy(left)
	for field in ("_run_earned", "batch_queries_sent"):
		row[field] = _add(row[field], right[field])
	for field in ("query_count_complete", "source_complete"):
		row[field] = row[field] and right[field]
	for task in SOURCES:
		row["sources"][task] = _add(row["sources"][task], right["sources"][task])
	for task in TASKS:
		row["counted_sources"][task] += right["counted_sources"][task]
	for field in ("run_ids", "_runs", "_checks", "combined", "parts", "_counted_runs"):
		row[field] += right[field]
	row["runs"] = (row["runs"] or []) + (right["runs"] or []) if row["runs"] is not None or right["runs"] is not None else None
	for stamp, fields in (("_state_at", ("_page_earned",)), ("balance_at", ("balance",)),
		("done_at", ("done", "daily_set_open", "searches_earned", "searches_max", "open_points", "quests_open"))):
		if right[stamp] is not None and (row[stamp] is None or right[stamp] >= row[stamp]):
			row[stamp] = right[stamp]
			for field in fields:
				row[field] = right[field]
	row["_has_tasks"] |= right["_has_tasks"]
	row["first_at"] = min(value for value in (row["first_at"], right["first_at"]) if value is not None)
	row["last_at"] = max(value for value in (row["last_at"], right["last_at"]) if value is not None)
	return _finalize(row)


def _sources(events, before, after, *, run_id=None, started_at=None, date=None, fallback=None):
	points = dict.fromkeys(SOURCES)
	combined, pending, parts = [], [], []
	anchor = before
	complete = True
	ends = [event for event in events if event.get("type") == "task_end" and event.get("task") in TASKS and event.get("outcome") != "off"]
	ran = list(dict.fromkeys(event["task"] for event in events if event.get("type") in ("task_start", "task_end") and event.get("task") in TASKS and event.get("outcome") != "off"))
	def part(kind, value, group):
		if value > 0:
			parts.append({"kind": kind, "points": value, "tasks": list(dict.fromkeys(group)),
				"date": date, "run_id": run_id, "started_at": started_at})
	def gap(value, group):
		nonlocal complete
		if anchor is None or value < anchor:
			complete = False
		else:
			points["other"] = 0
			part("tasks" if group else "between", value - anchor, group)
			if len(group) > 1:
				combined.append(list(group))
	for event in ends:
		task = event["task"]
		begin, end = event.get("points_before"), event.get("points_after")
		begin = begin if _number(begin) else None
		end = end if _number(end) else None
		if begin is not None:
			if anchor is not None and (begin != anchor or pending):
				gap(begin, pending)
			anchor = begin
			pending = []
		if begin is not None and end is not None:
			if end < begin:
				complete = False
			else:
				points[task] = _add(points[task], end - begin)
			anchor = end
		elif end is not None:
			gap(end, pending + [task])
			pending, anchor = [], end
		else:
			pending.append(task)
	if after is not None and (anchor != after or pending):
		gap(after, pending)
	if ends and (before is None or after is None or after < before):
		complete = False
	if ends and before is not None and after is not None:
		complete &= sum(value for key, value in points.items() if key != "other" and value is not None) + sum(part["points"] for part in parts) == after - before
	if complete and ends and points["other"] is None:
		points["other"] = 0
	# With no individually measured task, a pair of run-level counters gives
	# one honest unsplit part. Never mix a daily counter across local midnight.
	if ran and all(points[task] is None for task in TASKS):
		total = fallback
		if _number(total) and total >= 0:
			points["other"] = total
			parts = []
			part("run", total, ran)
			complete = True
	# Keep the scalar and its explanation in one operation. This also covers
	# zero gaps, whose parts are deliberately omitted.
	if points["other"] is not None:
		points["other"] = sum(part["points"] for part in parts)
	return points, combined, bool(complete), parts


def _counted_sources(events):
	"""Return only a complete task attribution supported by the search counter."""
	before = next((event for event in reversed(events) if event.get("type") == "state" and event.get("phase") == "before"), {})
	after = next((event for event in reversed(events) if event.get("type") == "state" and event.get("phase") == "after"), {})
	first, last = before.get("balance"), after.get("balance")
	if not _number(first) or not _number(last) or first < 0 or last < first:
		return None
	ends = [event for event in events if event.get("type") == "task_end" and event.get("outcome") != "off"]
	started = {event.get("task") for event in events if event.get("type") == "task_start"}
	if not ends or started - {event.get("task") for event in ends}:
		return None
	points = dict.fromkeys(TASKS, 0)
	anchor, outside = first, 0
	for event in ends:
		task = event.get("task")
		begin, end = event.get("points_before"), event.get("points_after")
		if task not in TASKS or not _number(begin) or not _number(end):
			return None
		if begin < anchor or end < begin:
			return None
		outside += begin - anchor
		points[task] += end - begin
		anchor = end
	if last < anchor:
		return None
	outside += last - anchor
	if outside:
		counter_before = before.get("searches_earned")
		counters = [event.get("earned") for event in events if event.get("type") == "search_points"] + [after.get("searches_earned")]
		counters = [value for value in counters if _number(value) and value >= 0]
		if not _number(counter_before) or counter_before < 0 or not counters:
			return None
		# Round zero can already lead the task balance. The baseline is the
		# state before the run, not that later search-task observation.
		coverage = max(counters) - counter_before - points["searches"]
		if coverage < outside:
			return None
	points["searches"] += outside
	return points


def _run_points(events, *, zone=None, bounds=()):
	"""History's delta, with same-day page counters before the old task fallback."""
	before = next((event.get("balance") for event in reversed(events) if event.get("type") == "state" and event.get("phase") == "before"), None)
	after = next((event.get("balance") for event in reversed(events) if event.get("type") == "state" and event.get("phase") == "after"), None)
	if not _number(before) or not _number(after):
		daily = _daily_delta(events, zone=zone, bounds=bounds)
		if daily is not None:
			return daily
	if after is None:
		after = next((event.get("points_after") for event in reversed(events) if event.get("type") == "task_end" and _number(event.get("points_after"))), None)
	return after - before if _number(before) and _number(after) else None


def _daily_delta(events, *, zone=None, bounds=()):
	states = {phase: next((event for event in reversed(events) if event.get("type") == "state" and event.get("phase") == phase), None) for phase in ("before", "after")}
	before, after = states["before"], states["after"]
	if not before or not after or not _number(before.get("earned_today")) or not _number(after.get("earned_today")):
		return None
	if before["earned_today"] < 0 or after["earned_today"] < 0:
		return None
	first, last = _at(before.get("ts")), _at(after.get("ts"))
	if first is None or last is None or last < first or _day(first, zone, bounds)[0] != _day(last, zone, bounds)[0]:
		return None
	value = after["earned_today"] - before["earned_today"]
	return value if value >= 0 else None


def result_key(result, events) -> str | None:
	"""Describe the whole run without changing its recorded terminal result."""
	if result is None:
		return None
	if result == "skipped":
		unreadable = False
		for event in events:
			kind = event.get("type")
			if (kind == "run_end" and _number(event.get("accounts_ran")) and event["accounts_ran"] > 0
				or kind == "account_end" and event.get("ran_tasks") is True
				or kind in ("task_start", "task_end") and event.get("outcome") != "off"):
				return "errors.run.skipped"
			unreadable |= kind == "decision" and event.get("reason") == "unreadable"
		if unreadable:
			return "errors.run.unreadable"
	return "errors.run." + result


def run_summary(run_id, events, *, active=False, account=None, zone=None, bounds=()):
	"""Compact shared History identity; no labels, paths, text or log reads."""
	start = next((event for event in events if event.get("type") == "run_start"), {})
	end = next((event for event in reversed(events) if event.get("type") == "run_end"), None)
	unknown = any(type(event.get("v")) is int and event["v"] > RECORD_VERSION for event in events)
	active = bool(active and end is None and not unknown)
	result = "unknown" if unknown else end.get("result") if end else None if active else "interrupted"
	if result is not None and result not in _RESULTS:
		result = "unknown"
	names = list(dict.fromkeys([name for name in start.get("accounts", []) if isinstance(name, str)] + [event["account"] for event in events if isinstance(event.get("account"), str)]))
	decisions = {event.get("account"): event.get("should_run") for event in events if event.get("type") == "decision"}
	check = bool(start.get("plan", {}).get("check_only")) or bool(names and all(decisions.get(name) is False for name in names))
	selected = events if account is None else [event for event in events if event.get("account") in (None, account)]
	known = [_run_points([event for event in selected if event.get("account") == name], zone=zone, bounds=bounds) for name in names] if not unknown else []
	known = [value for value in known if value is not None]
	problems = []
	if not unknown:
		for event in selected:
			if event.get("type") == "task_end" and event.get("task") in TASKS and event.get("outcome") == "fail":
				reason = event.get("reason_key")
				problems.append({"task": event["task"], "reason_key": "errors.task." + reason if reason in _TASK_REASONS else "errors.task.error" if reason else None})
	account_error = next((event for event in selected if event.get("type") == "account_end" and event.get("error_code")), None)
	browser_error = next((event for event in selected if event.get("type") == "browser_failed"), None)
	if result not in (None, "ok", "skipped", "partial") or account_error or browser_error:
		reason = "errors.run." + result if result not in (None, "ok", "skipped", "partial") else "errors.run.failed"
		if browser_error and result in (None, "ok", "skipped", "partial"):
			code = browser_error.get("code")
			reason = "errors.browser." + (code if code in _BROWSER_REASONS else "unknown")
		problems.append({"task": None, "reason_key": reason})
	return {"run_id": run_id, "started_at": start.get("ts", events[0].get("ts") if events else None),
		"kind": "check" if check else "run", "trigger": start.get("trigger") if start.get("trigger") in ("scheduled", "app", "cli") else "cli", "active": active,
		"result": result, "result_key": result_key(result, events),
		"points_earned": sum(known) if known else None, "problems": problems}


def reduce_records(records, *, user_data_dir=None, now=None, zone=None, bounds=()):
	"""Pure daily reduction shared with Overview; no ledger or profile reads."""
	user_data_dir = user_data_dir or os.environ.get("USER_DATA_DIR") or constants.DEFAULT_ROOT_DATA_DIR
	now = now or datetime.now(timezone.utc)
	rows = {}
	for run_id, raw in sorted(records.items()):
		if any(type(event.get("v")) is int and event["v"] > RECORD_VERSION for event in raw):
			continue
		# Search progress can dominate a record, but daily statistics use states
		# and completed task windows, not each repeated quota observation.
		kinds = {"run_start", "run_end", "account_start", "account_end", "browser_failed", "state", "task_start", "task_end", "search_points", "decision"}
		events = [{**event, "ts": _iso(at)} for event in raw if event.get("type") in kinds and (at := _at(event.get("ts"))) is not None and at <= now.astimezone(timezone.utc)]
		start = next((event for event in events if event.get("type") == "run_start"), {})
		end = next((event for event in reversed(events) if event.get("type") == "run_end"), None)
		# A supervisor can fail before any account starts. Keep its run link
		# without assigning invented progress to a configured account.
		profiles = {event.get("account"): event.get("profile") for event in events if event.get("type") == "account_start"}
		names = list(dict.fromkeys([name for name in start.get("accounts", []) if isinstance(name, str)] + [event["account"] for event in events if isinstance(event.get("account"), str)]))
		decisions = {event.get("account"): event.get("should_run") for event in events if event.get("type") == "decision"}
		check = bool(start.get("plan", {}).get("check_only")) or bool(names and all(decisions.get(name) is False for name in names))
		attributions = {}
		if not check:
			for name in names:
				account_events = [event for event in events if event.get("account") == name]
				if any(event.get("type") in ("task_start", "task_end") and event.get("outcome") != "off" for event in account_events):
					attributions[name] = _counted_sources(account_events)
		counted = bool(attributions) and all(value is not None for value in attributions.values())
		if not names:
			stamp = start.get("ts") or (events[0]["ts"] if events else None)
			if stamp is not None and start:
				date, first, last = _day(_at(stamp), zone, bounds)
				row = _row(date, "@run", first, last)
				row.update(first_at=stamp, last_at=(end or start).get("ts") or stamp, run_ids=[run_id])
				row["runs"].append(run_summary(run_id, events, zone=zone, bounds=bounds))
				key = (date, "@run")
				rows[key] = _merge(rows[key], row) if key in rows else _finalize(row)
		for name in names:
			account = account_id(name, profiles.get(name), user_data_dir)
			if account is None:
				continue
			own = [event for event in events if event.get("account") == name]
			local_rows = {}
			def row_for(stamp):
				at = _at(stamp)
				if at is None:
					return None
				date, first, last = _day(at, zone, bounds)
				if date not in local_rows:
					local_rows[date] = _row(date, account, first, last)
				row = local_rows[date]
				row["run_ids"].append(run_id)
				row["first_at"] = min(row["first_at"] or stamp, stamp)
				row["last_at"] = max(row["last_at"] or stamp, stamp)
				return row
			for event in own:
				row = row_for(event["ts"])
				kind = event.get("type")
				if kind == "state":
					if row["_state_at"] is None or event["ts"] >= row["_state_at"]:
						earned = event.get("earned_today")
						row["_state_at"], row["_page_earned"] = event["ts"], earned if _number(earned) else None
					codes = set((event.get("unreadable") or "").split(","))
					readable = not codes.intersection(("daily_set", "searches", "signed_out", "terms_of_use", "page_blank"))
					values = [event.get(key) for key in ("daily_set_open", "searches_earned", "searches_max")]
					if readable and all(_number(value) for value in values) and (row["done_at"] is None or event["ts"] >= row["done_at"]):
						row.update(done=values[0] == 0 and values[2] > 0 and values[1] >= values[2], done_at=event["ts"], daily_set_open=values[0], searches_earned=values[1], searches_max=values[2],
							open_points=event.get("open_points") if _number(event.get("open_points")) else None,
							quests_open=event.get("quests_open") if _number(event.get("quests_open")) else None)
				balance = event.get("balance") if kind == "state" else event.get("points_after") if kind == "task_end" else None
				if _number(balance) and (row["balance_at"] is None or event["ts"] >= row["balance_at"]):
					row.update(balance=balance, balance_at=event["ts"])
				if kind == "task_end" and event.get("task") == "searches":
					progress = event.get("progress")
					count = _QUERIES.fullmatch(progress if isinstance(progress, str) else "")
					row["query_count_complete"] &= count is not None
					if count:
						row["batch_queries_sent"] = _add(row["batch_queries_sent"], int(count[1]))
			terminal = (end or start).get("ts") or (own[-1]["ts"] if own else None)
			row = row_for(terminal)
			if row is not None:
				row["_checks" if check else "_runs"].append(run_id)
				before = next((event.get("balance") for event in reversed(own) if event.get("type") == "state" and event.get("phase") == "before"), None)
				after = next((event.get("balance") for event in reversed(own) if event.get("type") == "state" and event.get("phase") == "after"), None)
				before = before if _number(before) else None
				after = after if _number(after) else None
				# The run part needs genuine run endpoints. F67's last-task fallback
				# can still measure a shorter interval, never the unread tail.
				fallback = None
				if before is not None and after is not None:
					if after >= before:
						fallback = after - before
				else:
					fallback = _daily_delta(own, zone=zone, bounds=bounds)
				if after is None:
					after = next((event["points_after"] for event in reversed(own) if event.get("type") == "task_end" and _number(event.get("points_after"))), None)
				if _number(before) and _number(after):
					row["_run_earned"] = after - before
				points, combined, complete, parts = _sources(own, before, after, run_id=run_id,
					started_at=start.get("ts"), date=row["date"], fallback=fallback)
				if row["_run_earned"] is None and any(part["kind"] == "run" for part in parts):
					row["_run_earned"] = points["other"]
				row.update(sources=points, combined=combined, parts=parts, source_complete=complete, _has_tasks=any(event.get("type") == "task_end" for event in own))
				if counted and name in attributions:
					row["counted_sources"] = attributions[name]
					row["_counted_runs"].append(run_id)
			# The run is listed on its local start day. A cross-midnight state
			# still contributes to the state observation day, not the run list.
			summary_row = row_for(start.get("ts") or terminal)
			if summary_row is not None:
				summary_row["runs"].append(run_summary(run_id, events, account=name, zone=zone, bounds=bounds))
			for date, row in local_rows.items():
				key = (date, account)
				rows[key] = _merge(rows[key], row) if key in rows else _finalize(row)
	return rows


def _read_record(path):
	events = []
	for line in read_file(path, errors="replace").splitlines():
		try:
			event = json.loads(line)
			if isinstance(event, dict):
				events.append(event)
		except ValueError:
			continue
	return events


def _created(pid):
	"""Return a live process's creation FILETIME using read-only OS observations."""
	if type(pid) is not int or pid <= 0:
		return None
	if os.name == "nt":
		return run_lock._process_created(pid)
	try:
		# /proc supplies creation ticks; unlike kill(pid, 0), this sends no signal.
		fields = read_file(f"/proc/{pid}/stat").rsplit(")", 1)[1].split()
		if fields[0] == "Z":
			return None
		ticks = int(fields[19]) / os.sysconf("SC_CLK_TCK")
		epoch = time.time() - time.clock_gettime(time.CLOCK_BOOTTIME) + ticks
		return round((epoch + 11644473600) * 10_000_000)
	except (OSError, ValueError, AttributeError, IndexError):
		return None


def _before(pid, at):
	created = _created(pid)
	if created is None or at is None:
		return False
	elapsed = at - datetime(1601, 1, 1, tzinfo=timezone.utc)
	upper = (elapsed.days * 86400 + elapsed.seconds) * 10_000_000 + elapsed.microseconds * 10 + 10_000
	return created < upper


def _unfinished_live(events, holder):
	if any(event.get("type") == "run_end" for event in events):
		return False
	start = next((event for event in events if event.get("type") == "run_start"), {})
	at = _at(start.get("ts"))
	if holder is not None and holder.run_id == start.get("run") and _before(holder.pid, at):
		created = _created(holder.pid)
		if holder.pid_created is None or holder.pid_created == created:
			return True
	pids = [start.get("pid"), start.get("supervisor_pid")]
	if holder is not None and holder.run_id == start.get("run"):
		pids += [holder.pid, holder.supervisor_pid]
	return any(_before(pid, at) for pid in pids)


def _holder(user_data_dir):
	try:
		return run_lock.LockInfo(**json.loads(read_file(Path(user_data_dir, run_lock.INFO_FILE_NAME))))
	except (OSError, TypeError, ValueError):
		return None


def _empty(now):
	return {"v": VERSION, "day_policy": "local", "coverage_from": None, "built_at": _iso(now), "rebuilt_at": None, "index": {}, "rows": []}


def _lock_file(fd, release=False):
	if os.name == "nt":
		import msvcrt
		os.lseek(fd, 0, os.SEEK_SET)
		msvcrt.locking(fd, msvcrt.LK_UNLCK if release else msvcrt.LK_NBLCK, 1)
	else:
		import fcntl
		fcntl.flock(fd, fcntl.LOCK_UN if release else fcntl.LOCK_EX | fcntl.LOCK_NB)


@contextmanager
def _writer(path, *, clock=None, sleep=None):
	clock, sleep = clock or time.monotonic, sleep or time.sleep
	fd = os.open(path, os.O_RDWR | os.O_CREAT)
	locked = False
	try:
		deadline = clock() + 5
		while not locked:
			try:
				_lock_file(fd)
				locked = True
			except OSError:
				remaining = deadline - clock()
				if remaining <= 0:
					raise TimeoutError("ledger_lock_timeout") from None
				sleep(min(0.05, remaining))
		yield
	finally:
		try:
			if locked:
				_lock_file(fd, release=True)
		finally:
			os.close(fd)


def _load(path, now):
	try:
		value = json.loads(read_file(path))
		if isinstance(value, dict) and type(value.get("v")) is int and value["v"] > VERSION:
			return value, True
		if not isinstance(value, dict) or type(value.get("v")) is not int or value["v"] not in (1, 2, VERSION) or value.get("day_policy") != "local" or any(key not in value for key in _empty(now)) or not isinstance(value.get("index"), dict) or not isinstance(value.get("rows"), list):
			raise ValueError("ledger_invalid")
		fields = set(_row("", "", "", ""))
		if value["v"] < 3:
			fields -= {"counted_sources", "_counted_runs"}
		if value["v"] == 1:
			fields -= {"parts", "open_points", "quests_open", "runs"}
		for row in value["rows"]:
			if not isinstance(row, dict) or any(key not in row for key in fields) or not isinstance(row.get("account_id"), str) or not (_ACCOUNT.fullmatch(row["account_id"]) or row["account_id"] == "@run"):
				raise ValueError("ledger_invalid")
			datetime.strptime(row["date"], "%Y-%m-%d")
			if _at(row["_day_start"]) is None or _at(row["_day_end"]) is None or not isinstance(row["run_ids"], list) or any(not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id) for run_id in row["run_ids"]):
				raise ValueError("ledger_invalid")
			if not isinstance(row["sources"], dict) or set(row["sources"]) != set(SOURCES):
				raise ValueError("ledger_invalid")
			if value["v"] >= 2:
				if any(row[field] is not None and not _number(row[field]) for field in ("open_points", "quests_open")) or not isinstance(row["parts"], list) or row["runs"] is not None and not isinstance(row["runs"], list):
					raise ValueError("ledger_invalid")
				for part in row["parts"]:
					if not isinstance(part, dict) or set(part) != {"kind", "points", "tasks", "date", "run_id", "started_at"} or part["kind"] not in ("tasks", "between", "run") or not _number(part["points"]) or part["points"] <= 0:
						raise ValueError("ledger_invalid")
					if not isinstance(part["tasks"], list) or any(task not in TASKS for task in part["tasks"]) or part["kind"] == "between" and part["tasks"] or part["date"] != row["date"] or part["run_id"] not in row["run_ids"] or _at(part["started_at"]) is None:
						raise ValueError("ledger_invalid")
				for summary in row["runs"] or []:
					if not isinstance(summary, dict) or set(summary) != {"run_id", "started_at", "kind", "trigger", "active", "result", "result_key", "points_earned", "problems"} or summary["run_id"] not in row["run_ids"] or _at(summary["started_at"]) is None or summary["kind"] not in ("check", "run") or summary["active"] is not False or not isinstance(summary["problems"], list):
						raise ValueError("ledger_invalid")
					if any(not isinstance(problem, dict) or set(problem) != {"task", "reason_key"} or problem["task"] is not None and problem["task"] not in TASKS for problem in summary["problems"]):
						raise ValueError("ledger_invalid")
			if value["v"] >= 3:
				if not isinstance(row["counted_sources"], dict) or set(row["counted_sources"]) != set(TASKS) or any(not _number(points) or points < 0 for points in row["counted_sources"].values()):
					raise ValueError("ledger_invalid")
				if not isinstance(row["_counted_runs"], list) or any(run_id not in row["run_ids"] for run_id in row["_counted_runs"]):
					raise ValueError("ledger_invalid")
		for run_id, signature in value["index"].items():
			if not _RUN_ID.fullmatch(run_id) or not isinstance(signature, dict) or set(signature) != {"size", "mtime_ns"} or any(not _number(value) or value < 0 for value in signature.values()):
				raise ValueError("ledger_invalid")
		return value, False
	except FileNotFoundError:
		return _empty(now), False
	except (ValueError, TypeError, KeyError):
		stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
		bad = path.with_name("stats-ledger.bad-" + stamp + ".json")
		index = 1
		while bad.exists():
			bad = path.with_name("stats-ledger.bad-" + stamp + f"-{index}.json")
			index += 1
		replace_file(path, bad)
		value = _empty(now)
		value["rebuilt_at"] = _iso(now)
		_log("ledger_rebuilt")
		return value, False


def _migrate(ledger, files, *, user_data_dir=None, now=None, zone=None):
	"""Rebuild retained dates; a sealed legacy date cannot be reconstructed."""
	rows = []
	retained_ids = set()
	sealed_records = {run_id for row in ledger["rows"] if any(run_id not in files for run_id in row["run_ids"])
		for run_id in row["run_ids"] if run_id in files}
	known = reduce_records({run_id: _read_record(files[run_id][0]) for run_id in sealed_records},
		user_data_dir=user_data_dir, now=now, zone=zone,
		bounds=sorted({(row["date"], _at(row["_day_start"]), _at(row["_day_end"])) for row in ledger["rows"]})) if sealed_records else {}
	for previous in ledger["rows"]:
		row = dict(previous, counted_sources=dict.fromkeys(TASKS, 0), _counted_runs=[])
		if ledger["v"] == 1:
			row.update(parts=[], open_points=None, quests_open=None, runs=None)
		rows.append(row)
		if all(run_id in files for run_id in previous["run_ids"]):
			retained_ids.update(previous["run_ids"])
		elif (measured := known.get((row["date"], row["account_id"]))) is not None:
			# Retained neighbors can prove their own eligibility even when the
			# day's old totals are sealed by a pruned run. Never replay totals.
			row["counted_sources"] = measured["counted_sources"]
			row["_counted_runs"] = measured["_counted_runs"]
			if ledger["v"] == 2 and row["runs"] is not None:
				by_id = {summary["run_id"]: summary for summary in measured["runs"] or []}
				row["runs"] = [by_id.get(summary["run_id"], summary) for summary in row["runs"]]
	# Invalidate only recoverable dates. Sealed dates keep their old index so
	# retained neighbors cannot replay a contribution already in the v1 row.
	index = {run_id: signature for run_id, signature in ledger["index"].items() if run_id not in retained_ids}
	return dict(ledger, v=VERSION, rows=rows, index=index)


def _refresh_result_keys(ledger, files):
	# Keep the v3 rows intact for rollback readers. Only this top-level
	# marker is new; never replay a sealed day's accounting for a sentence.
	if ledger.get("result_key_refresh") == 1:
		return ledger
	value = dict(ledger, rows=copy.deepcopy(ledger["rows"]))
	read = {}
	for row in value["rows"]:
		for summary in row["runs"] or []:
			run_id = summary["run_id"]
			if run_id not in files:
				continue
			if run_id not in read:
				try:
					read[run_id] = _read_record(files[run_id][0])
				except (OSError, ValueError, RecursionError):
					read[run_id] = []
				if not read[run_id]:
					_log("ledger_result_key_refresh_unreadable:" + run_id)
			if read[run_id]:
				summary["result_key"] = result_key(summary["result"], read[run_id])
	value["result_key_refresh"] = 1
	return value


def _fold(ledger, files, *, user_data_dir, now, zone, pending=None):
	old_rows = {(row["date"], row["account_id"]): row for row in ledger["rows"]}
	bounds = sorted({(row["date"], _at(row["_day_start"]), _at(row["_day_end"])) for row in ledger["rows"]})
	changed = {run_id for run_id, (_, signature) in files.items() if ledger["index"].get(run_id) != signature}
	if not changed:
		# Active records never enter the index, so this cannot hide an active
		# writer. Retention can remove index entries without re-reading rows or
		# probing a missing lock-info file on every warm Stats refresh.
		return dict(ledger, index={run_id: signature for run_id, (_, signature) in files.items()})
	sealed = {key for key, row in old_rows.items() if any(run_id not in files for run_id in row["run_ids"])}
	sealed_ids = {run_id for key in sealed for run_id in old_rows[key]["run_ids"]}
	open_ids = {run_id for key, row in old_rows.items() if key not in sealed for run_id in row["run_ids"]}
	# A run crossing midnight can belong to both a sealed and an open date.
	read = {run_id: _read_record(files[run_id][0]) for run_id in changed - (sealed_ids - open_ids)}
	holder = _holder(user_data_dir)
	active = {run_id for run_id, events in read.items() if _unfinished_live(events, holder)}
	if pending is not None:
		pending.update(active)
	read = {run_id: events for run_id, events in read.items() if run_id not in active}
	kwargs = {"user_data_dir": user_data_dir, "now": now, "zone": zone, "bounds": bounds}
	new_rows = reduce_records(read, **kwargs)
	affected = set(new_rows) | {key for key, row in old_rows.items() if set(row["run_ids"]) & (changed - active)}
	needed = {run_id for key in affected - sealed if key in old_rows for run_id in old_rows[key]["run_ids"] if run_id in files}
	missing = needed - read.keys()
	for run_id in missing:
		read[run_id] = _read_record(files[run_id][0])
	reduced = reduce_records(read, **kwargs) if missing else new_rows
	for key in affected - sealed:
		if key in reduced:
			old_rows[key] = reduced[key]
		else:
			old_rows.pop(key, None)
	for key in sealed & affected:
		unseen = {run_id: events for run_id, events in read.items() if run_id not in old_rows[key]["run_ids"]}
		addition = reduce_records(unseen, **kwargs).get(key)
		if addition:
			old_rows[key] = _merge(old_rows[key], addition)
	for key, row in reduced.items():
		if key not in old_rows and key not in affected:
			old_rows[key] = row
	value = dict(ledger, index={run_id: signature for run_id, (_, signature) in files.items() if run_id not in active}, rows=[old_rows[key] for key in sorted(old_rows)])
	dates = [row["date"] for row in value["rows"]]
	if value["coverage_from"] is not None:
		dates.append(value["coverage_from"])
	for events in read.values():
		observed = [at for event in events if (at := _at(event.get("ts"))) is not None and at <= now.astimezone(timezone.utc)]
		if observed:
			dates.append(_day(min(observed), zone, bounds)[0])
	value["coverage_from"] = min(dates) if dates else None
	return value


def fold(directory=None, *, data_home=None, user_data_dir=None, now=None, zone=None, clock=None, sleep=None):
	"""Return a committed fold or an explicit refusal; callers never prune on refusal."""
	now = now or datetime.now(timezone.utc)
	folder = Path(directory or constants.RUNS_DIR)
	home = Path(data_home) if data_home is not None else folder.parent if folder.name == "runs" else folder
	newer = False
	try:
		home.mkdir(parents=True, exist_ok=True)
		if user_data_dir is None:
			user_data_dir = os.environ.get("USER_DATA_DIR")
			if not user_data_dir:
				try:
					user_data_dir = dotenv_values(stream=io.StringIO(read_file(home / ".env"))).get("USER_DATA_DIR")
				except FileNotFoundError:
					pass
			user_data_dir = user_data_dir or str(home / "data-dir")
		with _writer(home / "stats-ledger.lock", clock=clock, sleep=sleep):
			path = home / "stats-ledger.json"
			ledger, newer = _load(path, now)
			files = {}
			for item in folder.glob("*.jsonl"):
				if _RUN_ID.fullmatch(item.stem) and item.is_file() and not item.is_symlink():
					stat = item.stat()
					files[item.stem] = (item, {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
			if newer:
				value = _empty(now)
				pending = set()
				value = _fold(value, files, user_data_dir=user_data_dir, now=now, zone=zone, pending=pending)
				return FoldResult(value, True, True, frozenset(pending))
			if ledger["v"] < VERSION:
				ledger = _migrate(ledger, files, user_data_dir=user_data_dir, now=now, zone=zone)
				migrated = True
			else:
				migrated = False
			pending = set()
			refreshed = _refresh_result_keys(ledger, files)
			value = _fold(refreshed, files, user_data_dir=user_data_dir, now=now, zone=zone, pending=pending)
			folded = migrated or value != refreshed or not path.exists()
			if folded or value != ledger:
				if folded:
					value["built_at"] = _iso(now)
				temp = None
				try:
					with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=home, delete=False) as stream:
						temp = stream.name
						json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
						stream.flush()
						os.fsync(stream.fileno())
					replace_file(temp, path)
				finally:
					if temp is not None:
						Path(temp).unlink(missing_ok=True)
			return FoldResult(value, True, pending=frozenset(pending))
	except Exception as exc:
		_log("ledger_lock_timeout" if isinstance(exc, TimeoutError) else "ledger_fold_failed")
		return FoldResult(_empty(now), False, newer)
