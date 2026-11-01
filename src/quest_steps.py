"""Today's clicked quest links, used only by the scheduling state reader."""

from datetime import datetime
import json
import logging
import os
from pathlib import Path
import tempfile

import constants
import run_record
from file_replace import read_file, replace_file

logger = logging.getLogger(__name__)
FILENAME = "quest-steps-today.json"
MAX_BYTES = 128 * 1024
MAX_STEPS = 512


def _today():
	return datetime.now().astimezone().date().isoformat()


def _path():
	return Path(constants.RUNS_DIR) / FILENAME


def _identity(value):
	return isinstance(value, (list, tuple)) and len(value) == 3 and all(
		isinstance(part, str) and 0 < len(part) <= 4096 for part in value)


def _load(today):
	try:
		path = _path()
		if path.stat().st_size > MAX_BYTES:
			return {}
		text = read_file(path)
		if len(text.encode("utf-8")) > MAX_BYTES:
			return {}
		value = json.loads(text)
		if not isinstance(value, dict) or set(value) != {"v", "date", "accounts"}:
			return {}
		if type(value["v"]) is not int or value["v"] != 1 or value["date"] != today or not isinstance(value["accounts"], dict):
			return {}
		accounts, total = {}, 0
		for account, rows in value["accounts"].items():
			if not isinstance(account, str) or not account or len(account) > 256 or not isinstance(rows, list):
				return {}
			total += len(rows)
			if total > MAX_STEPS or not all(_identity(row) for row in rows):
				return {}
			accounts[account] = {tuple(row) for row in rows}
		return accounts
	except Exception:
		# A damaged, unreadable or differently dated store must never hide work.
		return {}


def clicked_today():
	account = run_record._account.get()
	if not isinstance(account, str) or not account:
		return frozenset()
	try:
		return frozenset(_load(_today()).get(account, ()))
	except Exception:
		return frozenset()


def record_click(card, href, text):
	"""Persist a completed click, without making storage a task prerequisite."""
	account = run_record._account.get()
	if not isinstance(account, str) or not account:
		return
	temp = None
	try:
		row = (card, href, text)
		if len(account) > 256 or not _identity(row):
			raise ValueError("invalid quest step identity")
		today = _today()
		accounts = _load(today)
		accounts.setdefault(account, set()).add(row)
		if sum(map(len, accounts.values())) > MAX_STEPS:
			raise ValueError("quest step limit reached")
		value = {"v": 1, "date": today, "accounts": {name: sorted(rows) for name, rows in accounts.items()}}
		encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
		if len(encoded.encode("utf-8")) > MAX_BYTES:
			raise ValueError("quest step record too large")
		path = _path()
		path.parent.mkdir(parents=True, exist_ok=True)
		with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
			temp = stream.name
			stream.write(encoded)
			stream.flush()
			os.fsync(stream.fileno())
		replace_file(temp, path)
	except Exception as exc:
		logger.debug("Could not save quest steps clicked today (%s)", type(exc).__name__)
	finally:
		if temp is not None:
			try:
				Path(temp).unlink(missing_ok=True)
			except OSError:
				pass
