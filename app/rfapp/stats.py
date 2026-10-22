"""Statistics from durable local observations, with unknown days left unknown."""

from datetime import datetime, timedelta, timezone

from rfapp import paths, runs
import stats_ledger


class _Error(ValueError):
	def __init__(self, code):
		super().__init__(code)
		self.code = code
		self.fields, self.params, self.detail = None, {}, None


def _request(request):
	if not isinstance(request, dict) or set(request) - {"range", "account_id", "week_start"}:
		raise _Error("bad_request")
	period = request.get("range", 30)
	week_start = request.get("week_start", 1)
	account = request.get("account_id")
	if type(period) is not int or period not in (7, 30, 90) or type(week_start) is not int or week_start not in range(1, 8):
		raise _Error("bad_request")
	if account is not None and (not isinstance(account, str) or not account or not stats_ledger._ACCOUNT.fullmatch(account) or account in (".", "..") or account.endswith(".")):
		raise _Error("bad_request")
	return period, account, week_start


def _sum(values):
	known = [value for value in values if value is not None]
	return sum(known) if known else None


def _sources(rows):
	return {"points": {task: sum(row["counted_sources"][task] for row in rows) for task in stats_ledger.TASKS},
		"runs": len({run_id for row in rows for run_id in row["_counted_runs"]})}


def _state(row, date, today, coverage):
	if coverage is None or date < coverage:
		return "before_tracking"
	if row is not None and row["done"] is True:
		return "done"
	if date == today:
		return "today_open"
	return "missed" if row is not None and row["done"] is False else "no_check"


def _days(rows, ids, today, coverage, first):
	result = []
	date = first
	while date <= today:
		values = [rows.get((date.isoformat(), account)) for account in ids]
		states = [_state(row, date, today, coverage) for row in values]
		if coverage is None or date < coverage:
			state = "before_tracking"
		elif states and all(state == "done" for state in states):
			state = "done"
		elif date == today:
			state = "today_open"
		else:
			state = "missed" if "missed" in states else "no_check"
		earned = [row["earned"] if row else None for row in values]
		balance = [row["balance"] if row else None for row in values]
		day_earned = sum(earned) if earned and all(value is not None for value in earned) else None
		progress = {}
		for field in ("open_points", "quests_open"):
			known = [row.get(field) if row else None for row in values]
			progress[field] = sum(known) if known and all(value is not None for value in known) else None
		summaries = {}
		for row in values:
			if row is None:
				continue
			for summary in row.get("runs") or []:
				if summary["run_id"] not in summaries:
					summaries[summary["run_id"]] = dict(summary, problems=list(summary["problems"]))
				else:
					current = summaries[summary["run_id"]]
					current["points_earned"] = _sum([current["points_earned"], summary["points_earned"]])
					for problem in summary["problems"]:
						if problem["task"] is not None or not any(item["task"] is None for item in current["problems"]):
							current["problems"].append(problem)
		result.append({"date": date.isoformat(), "state": state, "earned": day_earned,
			"balance": sum(balance) if balance and all(value is not None for value in balance) else None,
			**progress, "possible": day_earned + progress["open_points"] if day_earned is not None and progress["open_points"] is not None else None,
			"runs": sorted(summaries.values(), key=lambda summary: (summary["started_at"] or "", summary["run_id"])) if state != "before_tracking" else [],
			"sources": _sources([row for row in values if row is not None]) if state != "before_tracking" else _sources([])})
		date += timedelta(days=1)
	return result


def _streak(days, today, first):
	by_date = {day["date"]: day["state"] for day in days}
	today_done = by_date.get(today.isoformat()) == "done"
	through = today if today_done else today - timedelta(days=1)
	current, cursor = 0, through
	while by_date.get(cursor.isoformat()) == "done":
		current += 1
		cursor -= timedelta(days=1)
	best, start, end, length, began = 0, None, None, 0, None
	for day in days:
		if day["state"] == "done":
			began = day["date"] if length == 0 else began
			length += 1
			if length >= best:
				best, start, end = length, began, day["date"]
		else:
			length = 0
	closed = [day for day in days if first <= day["date"] < today.isoformat()]
	return {"current": current, "through": through.isoformat() if current else None, "today_done": today_done,
		"best": best, "best_from": start, "best_to": end,
		"missed": sum(day["state"] == "missed" for day in closed), "not_checked": sum(day["state"] == "no_check" for day in closed)}


def get_stats(request, *, settings, prefs, now):
	period, selected, week_start = _request(request)
	from rfapp import settings as settings_module
	active, paused = settings_module.active_accounts(), settings_module.paused_accounts()
	ids = list(dict.fromkeys(active + paused))
	if selected is not None and selected not in ids:
		raise _Error("accounts.unknown")
	selected_ids = [selected] if selected is not None else ids
	labels = prefs.get("account_labels", {})
	accounts = [{"account_id": account, "label": runs._label(account, labels, number), "paused": account in paused} for number, account in enumerate(ids, 1)]
	result = stats_ledger.fold(str(paths.runs_dir()), data_home=paths.data_home(), user_data_dir=str(settings_module.user_data_dir()), now=now)
	if not result.ok:
		# A failed fold is not evidence of empty statistics.
		raise _Error("internal")
	ledger = result.ledger
	today = now.astimezone().date()
	first = today - timedelta(days=period - 1)
	coverage = datetime.fromisoformat(ledger["coverage_from"]).date() if ledger["coverage_from"] else None
	rows = {(row["date"], row["account_id"]): row for row in ledger["rows"] if row["account_id"] in selected_ids and row["date"] <= today.isoformat()}
	begin = min(coverage or today, today - timedelta(days=89), first, today.replace(day=1), today - timedelta(days=(today.isoweekday() - week_start) % 7))
	days = _days(rows, selected_ids, today, coverage, begin)
	if selected is None:
		unassigned = {row["date"]: row["runs"] for row in ledger["rows"] if row["account_id"] == "@run"}
		for day in days:
			if day["state"] != "before_tracking":
				day["runs"].extend(unassigned.get(day["date"]) or [])
	# Active records stay out of the durable fold and its totals. Only their
	# lightweight list item is overlaid, so no growing record is sealed early.
	by_date = {day["date"]: day for day in days}
	for run_id in result.pending:
		try:
			events = stats_ledger._read_record(paths.runs_dir() / (run_id + ".jsonl"))
		except FileNotFoundError:
			continue
		start = next((event for event in events if event.get("type") == "run_start"), {})
		at = stats_ledger._at(start.get("ts"))
		profiles = {event.get("account"): event.get("profile") for event in events if event.get("type") == "account_start"}
		names = [name for name in start.get("accounts", []) if stats_ledger.account_id(name, profiles.get(name), str(settings_module.user_data_dir())) in selected_ids]
		if at is not None and at <= now and (selected is None or names):
			day = by_date.get(at.astimezone().date().isoformat())
			if day is not None and (coverage is None or at.astimezone().date() >= coverage):
				day["runs"].append(stats_ledger.run_summary(run_id, events, active=True, account=names[0] if selected is not None else None))
	for day in days:
		day["runs"].sort(key=lambda summary: (summary["started_at"] or "", summary["run_id"]))
	ranged = [day for day in days if day["date"] >= first.isoformat()]
	known = [day for day in ranged if day["earned"] is not None]
	expected = sum(coverage is not None and day["date"] >= coverage.isoformat() for day in ranged)
	measured = _sum([day["earned"] for day in known])
	best = max(known, key=lambda day: (day["earned"], day["date"])) if known else None
	week = today - timedelta(days=(today.isoweekday() - week_start) % 7)
	part = [row for row in rows.values() if first.isoformat() <= row["date"] <= today.isoformat()]
	last = [max((row for row in rows.values() if row["account_id"] == account and row["balance"] is not None), key=lambda row: row["balance_at"], default=None) for account in selected_ids]
	balance_known = bool(last) and all(row is not None for row in last)
	sources = _sources(part)
	run_ids = {run_id for row in part for run_id in row["_runs"]}
	check_ids = {run_id for row in part for run_id in row["_checks"]}
	warnings = []
	if ledger.get("rebuilt_at") and first < stats_ledger._at(ledger["rebuilt_at"]).astimezone().date():
		warnings.append({"code": "ledger_rebuilt"})
	if result.newer:
		warnings.append({"code": "ledger_newer"})
	goal = None
	if len(selected_ids) == 1 and prefs.get("goals", {}).get(selected_ids[0]) is not None:
		overview = runs.overview(settings, prefs, now)
		goal = next((row["goal"] for row in overview["accounts"] if row["account_id"] == selected_ids[0]), None)
	return {"generated_at": stats_ledger._iso(now), "today": today.isoformat(), "range": period, "from": first.isoformat(),
		"account_id": selected, "accounts": accounts, "coverage_from": coverage.isoformat() if coverage else None,
		"days": [day for day in days if day["date"] >= (today - timedelta(days=89)).isoformat()],
		"totals": {"week_earned": _sum([day["earned"] for day in days if day["date"] >= week.isoformat()]),
			"month_earned": _sum([day["earned"] for day in days if day["date"] >= today.replace(day=1).isoformat()]),
			"earned": measured if known and len(known) == expected else None, "measured_earned": measured,
			"known_days": len(known), "expected_days": expected, "average_per_day": measured / len(known) if known else None,
			"best_day": {"date": best["date"], "earned": best["earned"]} if best else None,
			"balance": sum(row["balance"] for row in last) if balance_known else None,
			"balance_at": max(row["balance_at"] for row in last) if balance_known else None,
			"batch_queries_sent": _sum([row["batch_queries_sent"] for row in part]),
			"query_count_complete": all(row["query_count_complete"] for row in part), "run_count": len(run_ids), "check_count": len(check_ids)},
		"streak": _streak(days, today, first.isoformat()), "sources": sources,
		"goal": goal, "warnings": warnings}
