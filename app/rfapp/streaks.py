"""Microsoft's recorded English streak panel, without estimating missing values."""

from datetime import datetime, timezone
import re

from rfapp import paths
import run_record
import stats_ledger

_IDS = {"bing search streak": "bing_search", "daily set streak": "daily_set", "bing app streak": "bing_app", "visual search streak": "visual_search"}
_NAME = re.compile(r"\b(?:[A-Z][A-Za-z]*[ -]){1,5}Streak\b")
_NUMBER = r"(?:\d{1,3}(?:[,\.\u00a0\u202f]\d{3})+|\d+)"
_REWARD = rf"Complete the next day to earn ({_NUMBER}) points\."
_TODAY = r"(?:Search|Activity|Check-in):\s*(\d+)\s*/\s*(\d+)"
_PAIR = re.compile(_REWARD + rf"\s+(\+?{_NUMBER})\s+({_NUMBER})\s+" + _TODAY, re.IGNORECASE)


def _integer(value):
	if not isinstance(value, str) or not re.fullmatch(_NUMBER, value.strip()):
		return None
	return int(re.sub(r"[,\.\u00a0\u202f]", "", value.strip()))


def _match(pattern, text):
	match = re.search(pattern, text, re.IGNORECASE)
	return match.groups() if match else ()


def _blocks(text):
	names = list(_NAME.finditer(text))
	return [(match.group().lower(), text[match.end():names[index + 1].start() if index + 1 < len(names) else len(text)]) for index, match in enumerate(names)]


def parse(capture, account_id):
	"""B14 text remains useful when its depth-limited nodes omit reward digits."""
	sections = capture.get("sections")
	section = next((section for section in sections if isinstance(section, dict) and section.get("id") == "streaks"), None) if isinstance(sections, list) else None
	if section is None or not isinstance(section.get("text"), str):
		return None
	text = section["text"]
	nodes = [node for node in section.get("nodes", []) if isinstance(node, dict) and isinstance(node.get("text"), str)] if isinstance(section.get("nodes"), list) else []
	node_blocks = dict(_blocks(" ".join(node["text"] for node in nodes)))
	text_blocks = dict(_blocks(text))
	items = []
	for name in dict.fromkeys([*text_blocks, *node_blocks]):
		if name not in _IDS:
			continue
		body = text_blocks.get(name, "")
		own_nodes = node_blocks.get(name, "")
		day = _match(r"Day (\d+) of (\d+) streak completed\.", own_nodes) or _match(r"Day (\d+) of (\d+) streak completed\.", body)
		today = _match(_TODAY, own_nodes) or _match(_TODAY, body)
		next_points = _match(_REWARD, own_nodes) or _match(_REWARD, body)
		# Newer leaves own the reward pair when present. B14 has the sentence
		# and label but no intervening leaves, so only then use its section text.
		node_reward = re.search(_REWARD + r"(.*?)" + _TODAY, own_nodes, re.IGNORECASE)
		pair_body = own_nodes if node_reward and node_reward.group(2).strip() else body
		pair = _PAIR.search(pair_body)
		items.append({"id": _IDS[name], "day": int(day[0]) if day else None, "length": int(day[1]) if day else None,
			"today_done": int(today[0]) if today else None, "today_total": int(today[1]) if today else None,
			"next_points": _integer(next_points[0]) if next_points else None,
			"day_points": _integer(pair[2].lstrip("+")) if pair else None,
			"day_points_earned": not pair[2].startswith("+") if pair else None,
			"end_points": _integer(pair[3]) if pair else None})
	node_text = " ".join(node["text"] for node in nodes)
	bonus = _match(rf"({_NUMBER})\s+BONUS\b", node_text) or _match(rf"({_NUMBER})\s+BONUS\b", text)
	stamps = _match(r"Earn (\d+) stamps\b", node_text) or _match(r"Earn (\d+) stamps\b", text)
	if not items and not stamps:
		return None
	collected = None
	for node in nodes:
		if node.get("role") == "progressbar" and str(node.get("aria_label", "")).lower() == "stamps collected":
			collected = _integer(node.get("aria_valuenow"))
			if collected is None:
				own = _match(r"^\s*(\d+)\s+(?:of|/)\s+\d+(?:\s+stamps)?\s*$", str(node.get("aria_valuetext", "")))
				collected = int(own[0]) if own else None
			break
	return {"captured_at": capture["ts"], "account_id": account_id,
		"big_bonus": {"points": _integer(bonus[0]) if bonus else None, "stamps_total": int(stamps[0]) if stamps else None, "stamps_collected": collected}, "items": items}


def latest(account_ids, user_data_dir, now):
	"""Read backward and stop at the first successful selected-account capture."""
	selected = set(account_ids)
	if not selected:
		return None
	for run_id in run_record.list_run_ids(str(paths.runs_dir())):
		events = run_record.read_events(str(paths.runs_dir() / (run_id + ".jsonl")))
		profiles = {event.get("account"): event.get("profile") for event in events if event.get("type") == "account_start"}
		captures = []
		for event in events:
			if event.get("type") != "streak_capture" or event.get("status") != "ok" or event.get("v") != run_record.SCHEMA_VERSION:
				continue
			account = stats_ledger.account_id(event.get("account"), profiles.get(event.get("account")), user_data_dir)
			if account not in selected:
				continue
			try:
				at = datetime.fromisoformat(event["ts"].replace("Z", "+00:00"))
				if at.tzinfo is not None and at <= now.astimezone(timezone.utc):
					captures.append((at, event, account))
			except (KeyError, TypeError, ValueError, AttributeError):
				continue
		if captures:
			# Timestamp owns freshness; after wins only an equal-millisecond tie.
			_, event, account = max(captures, key=lambda value: (value[0], value[1].get("phase") == "after"))
			return parse(event, account)
	return None
