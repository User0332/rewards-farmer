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

VERSION = 1
TASKS = ("daily_set", "explore_on_bing", "visual_search", "misc_cards", "quests", "searches", "bonus")
SOURCES = TASKS + ("other",)
_RUN_ID = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{4}\Z")
_ACCOUNT = re.compile(r"(?:@root|[A-Za-z0-9_.-]+)\Z")
_QUERIES = re.compile(r"sent (\d+) search(?:es)?\Z")


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
	row["run_count"], row["check_count"] = len(row["_runs"]), len(row["_checks"])
	row["combined"] = sorted({tuple(group) for group in row["combined"]})
	row["combined"] = [list(group) for group in row["combined"]]
	return row


def _merge(left, right):
	row = copy.deepcopy(left)
	for field in ("_run_earned", "batch_queries_sent"):
		row[field] = _add(row[field], right[field])
	for field in ("query_count_complete", "source_complete"):
		row[field] = row[field] and right[field]
	for task in SOURCES:
		row["sources"][task] = _add(row["sources"][task], right["sources"][task])
	for field in ("run_ids", "_runs", "_checks", "combined"):
		row[field] += right[field]
	for stamp, fields in (("_state_at", ("_page_earned",)), ("balance_at", ("balance",)),
		("done_at", ("done", "daily_set_open", "searches_earned", "searches_max"))):
		if right[stamp] is not None and (row[stamp] is None or right[stamp] >= row[stamp]):
			row[stamp] = right[stamp]
			for field in fields:
				row[field] = right[field]
	row["_has_tasks"] |= right["_has_tasks"]
	row["first_at"] = min(value for value in (row["first_at"], right["first_at"]) if value is not None)
	row["last_at"] = max(value for value in (row["last_at"], right["last_at"]) if value is not None)
	return _finalize(row)


def _sources(events, before, after):
	points = dict.fromkeys(SOURCES)
	combined, pending = [], []
	anchor = before
	complete = True
	ends = [event for event in events if event.get("type") == "task_end" and event.get("task") in TASKS]
	def gap(value, group):
		nonlocal complete
		if anchor is None or value < anchor:
			complete = False
		else:
			points["other"] = _add(points["other"], value - anchor)
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
		complete &= sum(value for value in points.values() if value is not None) == after - before
	if complete and ends and points["other"] is None:
		points["other"] = 0
	return points, combined, bool(complete)


def reduce_records(records, *, user_data_dir=None, now=None, zone=None, bounds=()):
	"""Pure daily reduction shared with Overview; no ledger or profile reads."""
	user_data_dir = user_data_dir or os.environ.get("USER_DATA_DIR") or constants.DEFAULT_ROOT_DATA_DIR
	now = now or datetime.now(timezone.utc)
	rows = {}
	for run_id, raw in sorted(records.items()):
		if any(type(event.get("v")) is int and event["v"] > VERSION for event in raw):
			continue
		# Search progress can dominate a record, but daily statistics use states
		# and completed task windows, not each repeated quota observation.
		kinds = {"run_start", "run_end", "account_start", "account_end", "state", "task_end", "decision"}
		events = [{**event, "ts": _iso(at)} for event in raw if event.get("type") in kinds and (at := _at(event.get("ts"))) is not None and at <= now.astimezone(timezone.utc)]
		start = next((event for event in events if event.get("type") == "run_start"), {})
		end = next((event for event in reversed(events) if event.get("type") == "run_end"), None)
		profiles = {event.get("account"): event.get("profile") for event in events if event.get("type") == "account_start"}
		names = list(dict.fromkeys([name for name in start.get("accounts", []) if isinstance(name, str)] + [event["account"] for event in events if isinstance(event.get("account"), str)]))
		decisions = {event.get("account"): event.get("should_run") for event in events if event.get("type") == "decision"}
		check = bool(start.get("plan", {}).get("check_only")) or bool(names and all(decisions.get(name) is False for name in names))
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
						row.update(done=values[0] == 0 and values[2] > 0 and values[1] >= values[2], done_at=event["ts"], daily_set_open=values[0], searches_earned=values[1], searches_max=values[2])
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
				if after is None:
					after = next((event["points_after"] for event in reversed(own) if event.get("type") == "task_end" and _number(event.get("points_after"))), None)
				if _number(before) and _number(after):
					row["_run_earned"] = after - before
				points, combined, complete = _sources(own, before, after)
				row.update(sources=points, combined=combined, source_complete=complete, _has_tasks=any(event.get("type") == "task_end" for event in own))
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
		if not isinstance(value, dict) or type(value.get("v")) is not int or value["v"] != VERSION or value.get("day_policy") != "local" or any(key not in value for key in _empty(now)) or not isinstance(value.get("index"), dict) or not isinstance(value.get("rows"), list):
			raise ValueError("ledger_invalid")
		for row in value["rows"]:
			if not isinstance(row, dict) or any(key not in row for key in _row("", "", "", "")) or not isinstance(row.get("account_id"), str) or not _ACCOUNT.fullmatch(row["account_id"]):
				raise ValueError("ledger_invalid")
			datetime.strptime(row["date"], "%Y-%m-%d")
			if _at(row["_day_start"]) is None or _at(row["_day_end"]) is None or not isinstance(row["run_ids"], list) or any(not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id) for run_id in row["run_ids"]):
				raise ValueError("ledger_invalid")
			if not isinstance(row["sources"], dict) or set(row["sources"]) != set(SOURCES):
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


def _fold(ledger, files, *, user_data_dir, now, zone, pending=None):
	old_rows = {(row["date"], row["account_id"]): row for row in ledger["rows"]}
	bounds = sorted({(row["date"], _at(row["_day_start"]), _at(row["_day_end"])) for row in ledger["rows"]})
	changed = {run_id for run_id, (_, signature) in files.items() if ledger["index"].get(run_id) != signature}
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
			pending = set()
			value = _fold(ledger, files, user_data_dir=user_data_dir, now=now, zone=zone, pending=pending)
			if value != ledger or not path.exists():
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
