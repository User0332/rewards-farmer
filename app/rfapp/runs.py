"""Reduce versioned farmer records into snapshots; never interpret console text."""

from datetime import datetime, timedelta, timezone
import logging
import math
import os
from pathlib import Path
import threading
from typing import Callable

from rfapp import paths, streaks
import run_lock
import run_record
import stats_ledger

logger = logging.getLogger("rfapp")
_TASK_IDS = ("daily_set", "explore_on_bing", "visual_search", "misc_cards", "quests", "searches", "bonus")
_STATE_FIELDS = ("balance", "earned_today", "searches_earned", "searches_max", "daily_set_open", "cards_open", "quests_open", "open_points", "unreadable")
_EVENT_TYPES = frozenset(("run_start", "account_start", "browser_failed", "state", "decision", "task_start", "search_points", "card_left_open", "task_end", "warning", "account_end", "stop_requested", "run_end", "notified", "deferred"))


def active_run_id() -> str | None:
	from rfapp import settings
	info = run_lock.status(directory=str(settings.user_data_dir()))
	return info.run_id if info is not None and info.owner == "farmer" else None


def _path(run_id, suffix=".jsonl"):
	# File ids come from the record directory, never a bridge-provided path.
	if not run_record._RUN_ID.fullmatch(run_id):
		raise FileNotFoundError(run_id)
	return paths.runs_dir() / (run_id + suffix)


def _at(iso):
	try:
		return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
	except (AttributeError, ValueError, TypeError):
		return None


def _account_id(name, profile, user_data_dir):
	if profile:
		if os.path.normcase(os.path.abspath(profile)) == os.path.normcase(os.path.abspath(user_data_dir)):
			return "@root"
		return Path(profile).name
	return "@root" if name == "default" else name


def _label(account_id, labels, number):
	if account_id in labels:
		return labels[account_id]
	from rfapp import i18n
	return i18n.current().t("accounts.default_label", number=number)


def _task(task_id):
	return {"task": task_id, "name_key": "common.task." + task_id, "status": "pending", "reason_key": None,
		"detail": None, "progress": None, "points_before": None, "points_after": None, "points": None, "duration_ms": None}


def _state(event):
	return {"at": event.get("ts"), **{key: event.get(key) for key in _STATE_FIELDS}}


def _deferred(events):
	event = next((event for event in events if event.get("type") == "deferred"), None)
	if event is None:
		return None
	value = {key: event.get(key) for key in ("state", "waited_s", "gave_up")}
	if type(value["state"]) is not int or value["state"] not in (2, 3, 4) or type(value["waited_s"]) is not int or value["waited_s"] < 0 or type(value["gave_up"]) is not bool:
		return None
	return value


def load_run(run_id: str, labels: dict[str, str], user_data_dir: str) -> dict:
	path = _path(run_id)
	if not path.is_file():
		raise FileNotFoundError(run_id)
	events = run_record.read_events(str(path))
	start = next((event for event in events if event.get("type") == "run_start"), {})
	end = next((event for event in reversed(events) if event.get("type") == "run_end"), None)
	deferred = _deferred(events)
	unknown = any(isinstance(event.get("v"), int) and event["v"] > run_record.SCHEMA_VERSION for event in events)
	active = not unknown and end is None and active_run_id() == run_id
	result = "unknown" if unknown else end.get("result") if end else None if active else "interrupted"
	try:
		log_size = _path(run_id, ".log").stat().st_size
	except OSError:
		log_size = 0
	detail = {"run_id": run_id, "trigger": start.get("trigger", "cli"), "kind": "check" if start.get("plan", {}).get("check_only") else "run",
		"started_at": start.get("ts", events[0].get("ts") if events else None), "ended_at": end.get("ts") if end and not unknown else None,
		"duration_ms": end.get("duration_ms") if end and not unknown else None, "active": active, "result": result,
		"result_key": stats_ledger.result_key(result, events), "points_earned": None, "stop_requested": False,
		"accounts": [], "warnings": [], "log_size": log_size, "deferred": deferred if not unknown else None}
	if unknown:
		return detail
	profiles = {event.get("account"): event.get("profile") for event in events if event.get("type") == "account_start"}
	by_name = {}
	last_balance = {}
	last_task_balance = {}
	def account(name):
		if name not in by_name:
			account_id = _account_id(name, profiles.get(name), user_data_dir)
			row = {"account_id": account_id, "label": _label(account_id, labels, len(by_name) + 1), "status": "pending", "decision": None,
				"state_before": None, "state_after": None, "points_now": None, "points_earned": None, "search": None, "error": None,
				"tasks": [_task(task_id) for task_id in _TASK_IDS]}
			by_name[name] = row
			detail["accounts"].append(row)
		return by_name[name]
	for name in start.get("accounts", []):
		account(name)
	for event in events:
		type = event.get("type")
		if type not in _EVENT_TYPES:
			continue
		if type == "stop_requested":
			detail["stop_requested"] = True
		name = event.get("account")
		row = account(name) if isinstance(name, str) else None
		if type == "warning":
			code = event.get("code")
			if isinstance(code, str):
				detail["warnings"].append({"code": code, "message_key": "errors.warning." + code, "account_id": row["account_id"] if row else None})
		if row is None:
			continue
		if type == "account_start":
			row["status"] = "running"
		elif type == "browser_failed":
			row["error"] = {"code": event.get("code", "unknown"), "message_key": "errors.browser." + event.get("code", "unknown"), "detail": event.get("detail")}
			row["status"] = "failed"
		elif type == "state" and event.get("phase") in ("before", "after"):
			row["state_" + event["phase"]] = _state(event)
			if event.get("balance") is not None:
				last_balance[name] = event["balance"]
			if event.get("searches_earned") is not None and event.get("searches_max") is not None:
				row["search"] = {"earned": event["searches_earned"], "max": event["searches_max"]}
		elif type == "decision":
			reason = event.get("reason", "unreadable")
			row["decision"] = {"run": event.get("should_run"), "reason": reason, "reason_key": "errors.decision." + reason,
				"open_points": event.get("open_points"), "min_points": event.get("min_points")}
		elif type in ("task_start", "task_end") and event.get("task") in _TASK_IDS:
			task = row["tasks"][_TASK_IDS.index(event["task"])]
			if type == "task_start":
				task.update(status="running", points_before=event.get("points_before"))
			else:
				task.update(status=event.get("outcome", "fail"), reason_key="errors.task." + event["reason_key"] if event.get("reason_key") else None,
					detail=event.get("detail"), progress=event.get("progress"), points_before=event.get("points_before"), points_after=event.get("points_after"), duration_ms=event.get("duration_ms"))
				if event.get("points_after") is not None:
					last_balance[name] = event["points_after"]
					last_task_balance[name] = event["points_after"]
		elif type == "search_points":
			row["search"] = {"earned": event.get("earned"), "max": event.get("max")}
		elif type == "account_end":
			if event.get("error_code"):
				row["status"] = "failed"
				if row["error"] is None:
					code = event["error_code"]
					row["error"] = {"code": code, "message_key": "errors.run.failed", "detail": event.get("detail")}
			elif row["decision"] and row["decision"]["run"] is False:
				row["status"] = "skipped"
			else:
				row["status"] = "done"
	for name, row in by_name.items():
		own = [event for event in events if event.get("account") == name]
		# Stats owns counter-covered credit; History must not invent a second
		# attribution rule or add that credit to an incompletely measured run.
		counted = stats_ledger._counted_sources(own)
		for task in row["tasks"]:
			if task["status"] not in ("ok", "skip", "fail"):
				continue
			if counted is not None:
				task["points"] = counted[task["task"]]
			else:
				begin, end = task["points_before"], task["points_after"]
				if stats_ledger._number(begin) and stats_ledger._number(end) and end >= begin:
					task["points"] = end - begin
		before = row["state_before"]
		after = row["state_after"]
		row["points_now"] = after["balance"] if after and after["balance"] is not None else last_balance.get(name)
		final_balance = after["balance"] if after and after["balance"] is not None else last_task_balance.get(name)
		if before and before["balance"] is not None and final_balance is not None:
			row["points_earned"] = final_balance - before["balance"]
		if not before or before["balance"] is None or not after or after["balance"] is None:
			daily = stats_ledger._daily_delta([event for event in events if event.get("account") == name])
			if daily is not None:
				row["points_earned"] = daily
		if not active:
			for task in row["tasks"]:
				if task["status"] in ("pending", "running"):
					task.update(status="not_run", reason_key="errors.task.stopped" if result == "stopped" else "errors.task.not_run")
			if row["status"] in ("pending", "running"):
				row["status"] = "failed"
	if by_name and all(row["decision"] and row["decision"]["run"] is False for row in by_name.values()):
		detail["kind"] = "check"
	known = [row["points_earned"] for row in by_name.values() if row["points_earned"] is not None]
	detail["points_earned"] = sum(known) if known else None
	return detail


def _summary(detail):
	summary = {key: value for key, value in detail.items() if key not in ("accounts", "warnings")}
	summary["accounts"] = []
	for account in detail["accounts"]:
		row = {key: value for key, value in account.items() if key not in ("tasks", "state_before", "state_after")}
		row["task_counts"] = {status: sum(task["status"] == status for task in account["tasks"]) for status in ("ok", "skip", "fail", "off")}
		summary["accounts"].append(row)
	return summary


def history(limit: int = 50, before: str | None = None, include_checks: bool = True, labels=None, user_data_dir=None) -> dict:
	labels = labels or {}
	if user_data_dir is None:
		from rfapp import settings
		user_data_dir = str(settings.user_data_dir())
	items = []
	for run_id in run_record.list_run_ids(str(paths.runs_dir())):
		if before is not None and run_id >= before:
			continue
		try:
			detail = load_run(run_id, labels, user_data_dir)
		except FileNotFoundError:
			continue
		if include_checks or detail["kind"] != "check":
			items.append(_summary(detail))
		if len(items) > limit:
			break
	page = items[:limit]
	return {"runs": page, "next_before": page[-1]["run_id"] if len(items) > limit and page else None}


def overview(settings: dict, prefs: dict, now: datetime) -> dict:
	from rfapp import settings as settings_module
	active_ids = settings_module.active_accounts()
	paused_ids = settings_module.paused_accounts()
	user_data_dir = str(settings_module.user_data_dir())
	labels = prefs.get("account_labels", {})
	today = now.astimezone().date()
	dates = [today - timedelta(days=29 - index) for index in range(30)]
	data = {account_id: {"states": [], "balances": {}, "daily_states": {}, "earned": {}, "tasks": {}} for account_id in active_ids + paused_ids}
	records = {}
	last_run = None
	for run_id in reversed(run_record.list_run_ids(str(paths.runs_dir()))):
		try:
			detail = load_run(run_id, labels, user_data_dir)
		except FileNotFoundError:
			continue
		last_run = _summary(detail)
		if detail["result"] == "unknown":
			continue
		events = run_record.read_events(str(_path(run_id)))
		records[run_id] = events
		profiles = {event.get("account"): event.get("profile") for event in events if event.get("type") == "account_start"}
		for event in events:
			name = event.get("account")
			if not isinstance(name, str):
				continue
			account_id = _account_id(name, profiles.get(name), user_data_dir)
			at = _at(event.get("ts"))
			if account_id not in data or at is None or at > now.astimezone():
				continue
			row, day = data[account_id], at.date()
			if event.get("type") == "state":
				snapshot = _state(event)
				row["states"].append(snapshot)
				if day not in row["daily_states"] or snapshot["at"] >= row["daily_states"][day]["at"]:
					row["daily_states"][day] = snapshot
				if event.get("balance") is not None:
					previous = row["balances"].get(day)
					if previous is None or event["ts"] >= previous[0]:
						row["balances"][day] = (event["ts"], event["balance"])
			elif event.get("type") == "task_end":
				if event.get("points_after") is not None:
					previous = row["balances"].get(day)
					if previous is None or event["ts"] >= previous[0]:
						row["balances"][day] = (event["ts"], event["points_after"])
				if day == today and event.get("task") in _TASK_IDS:
					previous = row["tasks"].get(event["task"])
					if previous is None or event["ts"] >= previous["ts"]:
						row["tasks"][event["task"]] = event
		day_at = _at(detail["ended_at"] or detail["started_at"])
		if day_at and day_at <= now.astimezone():
			for account in detail["accounts"]:
				if account["account_id"] in data and account["points_earned"] is not None:
					day = day_at.date()
					row = data[account["account_id"]]
					row["earned"][day] = row["earned"].get(day, 0) + account["points_earned"]
	daily = stats_ledger.reduce_records(records, user_data_dir=user_data_dir, now=now)
	rows = []
	for number, account_id in enumerate(active_ids + paused_ids, 1):
		data_row = data[account_id]
		state = max(data_row["states"], key=lambda value: value["at"]) if data_row["states"] else None
		is_today = bool(state and _at(state["at"]).date() == today)
		readable = bool(is_today and state["unreadable"] is None)
		still_open = {"searches": state["searches_earned"] < state["searches_max"] if readable and state["searches_earned"] is not None and state["searches_max"] is not None else None,
			"daily_set": state["daily_set_open"] > 0 if readable else None, "misc_cards": state["cards_open"] > 0 if readable else None,
			"quests": state["quests_open"] > 0 if readable else None}
		chart = []
		for day in dates:
			observed = daily.get((day.isoformat(), account_id))
			chart.append({"date": day.isoformat(), "balance": observed["balance"] if observed else None, "earned": observed["earned"] if observed else None})
		tasks_today = []
		for task_id in _TASK_IDS:
			event = data_row["tasks"].get(task_id, {})
			tasks_today.append({"task": task_id, "status": event.get("outcome", "none"), "reason_key": "errors.task." + event["reason_key"] if event.get("reason_key") else None,
				"still_open": still_open.get(task_id), "at": event.get("ts")})
		goal = None
		target = prefs.get("goals", {}).get(account_id)
		if target is not None and state and state["balance"] is not None:
			known = [day["earned"] for day in chart[-14:] if day["earned"] is not None]
			avg = sum(known) / len(known) if len(known) >= 3 else None
			remaining = max(0, target - state["balance"])
			goal = {"target": target, "remaining": remaining, "avg_per_day": avg, "days": math.ceil(remaining / avg) if avg is not None and avg > 0 else None}
		rows.append({"account_id": account_id, "label": _label(account_id, labels, number), "paused": account_id in paused_ids, "state": state,
			"state_is_today": is_today, "tasks_today": tasks_today, "chart": chart, "goal": goal})
	totals = {}
	for field in ("balance", "earned_today", "open_points"):
		values = [row["state"].get(field) if row["state"] else None for row in rows if not row["paused"]]
		totals[field] = sum(values) if values and all(value is not None for value in values) else None
	info = run_lock.status(directory=user_data_dir)
	return {"accounts": rows, "totals": totals, "active_run": {"run_id": info.run_id, "since": info.since} if info and info.owner == "farmer" else None, "last_run": last_run,
		"streaks": streaks.latest(active_ids + paused_ids, user_data_dir, now)}


class LiveTail:
	def __init__(self, push: Callable[[str, dict], None], labels: dict[str, str], user_data_dir: str):
		self._push = push
		self._labels = labels
		self._user_data_dir = user_data_dir
		self._active = None
		self._snapshot = None
		self._offsets = {}
		self._pending_finishes = {}
		self._supervisors = {}
		self._context = None
		self._mutex = threading.RLock()

	def set_context(self, labels: dict[str, str], user_data_dir: str) -> None:
		with self._mutex:
			self._context = (dict(labels), user_data_dir)

	def _read_lines(self, run_id, suffix, cap=None):
		key = (run_id, suffix)
		offset = self._offsets.get(key, 0)
		try:
			with _path(run_id, suffix).open("rb") as stream:
				if stream.seek(0, 2) < offset:
					offset = 0
				stream.seek(offset)
				data = stream.read()
		except OSError:
			return b""
		if suffix == ".log":
			start = 0
			# A resumed offset must not decode the rest of an already split character.
			while start < len(data) and data[start] & 0xC0 == 0x80:
				start += 1
			offset += start
			data = data[start:]
		complete = data.rfind(b"\n") + 1
		if cap is not None and complete > cap:
			complete = cap
			# A complete long line can span pushes, but a UTF-8 code point cannot.
			while complete > 0 and data[complete] & 0xC0 == 0x80:
				complete -= 1
		self._offsets[key] = offset + complete
		return data[:complete]

	def poll(self) -> None:
		with self._mutex:
			self._poll()

	def _supervisor_alive(self, run_id, events):
		if run_id not in self._supervisors:
			start = next((event for event in events if event.get("type") == "run_start"), None)
			if start is None:
				return False
			pid = start.get("supervisor_pid")
			created = run_lock._process_created(pid) if type(pid) is int and pid > 0 else None
			at = _at(start.get("ts"))
			if created is not None and created > 0 and at is not None:
				elapsed = at.astimezone(timezone.utc) - datetime(1601, 1, 1, tzinfo=timezone.utc)
				start_filetime = (elapsed.days * 86400 + elapsed.seconds) * 10_000_000 + elapsed.microseconds * 10
				# A newer process with a recycled pid cannot own this older run.
				if created >= start_filetime + 10_000:
					created = None
			self._supervisors[run_id] = (pid, created) if created is not None else None
		identity = self._supervisors[run_id]
		return identity is not None and run_lock._process_created(identity[0]) == identity[1]

	def _finish_pending(self):
		for run_id in list(self._pending_finishes):
			self._log(run_id)
			events = run_record.read_events(str(_path(run_id)))
			# The farmer releases its lock before the supervisor appends a crash outcome.
			if not any(event.get("type") == "run_end" for event in events) and self._supervisor_alive(run_id, events):
				continue
			try:
				final = load_run(run_id, self._labels, self._user_data_dir)
				self._push("run.finished", _summary(final))
			except FileNotFoundError:
				pass
			self._pending_finishes.pop(run_id)
			self._supervisors.pop(run_id, None)
			self._push("overview.changed", {})

	def _poll(self):
		context_changed = self._context is not None
		if context_changed:
			self._labels, self._user_data_dir = self._context
			self._context = None
		run_id = active_run_id()
		if run_id != self._active:
			if self._active is not None:
				self._pending_finishes[self._active] = None
			self._active = run_id
			self._snapshot = None
		self._finish_pending()
		if run_id is None:
			return
		self._supervisor_alive(run_id, run_record.read_events(str(_path(run_id))))
		new_events = self._read_lines(run_id, ".jsonl")
		if new_events or self._snapshot is None or context_changed:
			try:
				snapshot = load_run(run_id, self._labels, self._user_data_dir)
			except FileNotFoundError:
				return
			if self._snapshot is None:
				self._push("run.started", snapshot)
			elif snapshot != self._snapshot:
				self._push("run.snapshot", snapshot)
			self._snapshot = snapshot
		self._log(run_id)

	def _log(self, run_id):
		text = self._read_lines(run_id, ".log", 65536)
		if text:
			next_offset = self._offsets[(run_id, ".log")]
			self._push("run.log", {"run_id": run_id, "text": text.decode("utf-8", errors="replace"),
				"offset": next_offset - len(text), "next_offset": next_offset})
