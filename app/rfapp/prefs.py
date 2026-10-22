"""Atomic app-only preferences, retaining keys from later app versions."""

import copy
import json
import logging
import os
import tempfile
import threading
from pathlib import Path

from file_replace import read_file, replace_file
from rfapp import paths


_DEFAULTS = {
	"v": 1,
	"language": "system",
	"close_to_tray": True,
	"update_check": True,
	"update_last_checked": None,
	"update_dismissed_version": None,
	"setup_done": False,
	"tray_hint_shown": False,
	"account_labels": {},
	"goals": {},
	"window": {"width": 1100, "height": 760, "x": None, "y": None, "maximized": False},
	"last_route": "#/overview",
	"log_panel_open": False,
}
_lock = threading.RLock()
_logger = logging.getLogger("rfapp")


def load() -> dict:
	values = copy.deepcopy(_DEFAULTS)
	try:
		stored = json.loads(read_file(paths.prefs_path()))
		if not isinstance(stored, dict):
			raise ValueError("Preferences must be an object")
	except FileNotFoundError:
		return values
	except (ValueError, OSError):
		_logger.exception("Could not read app preferences")
		return values
	values.update(stored)
	return values


def save(changes: dict) -> dict:
	with _lock:
		values = load()
		for key, value in changes.items():
			values[key] = copy.deepcopy(value)
		path = paths.prefs_path()
		path.parent.mkdir(parents=True, exist_ok=True)
		name = None
		try:
			with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent, prefix=".prefs-", delete=False) as stream:
				name = stream.name
				json.dump(values, stream, ensure_ascii=False, indent=2)
				stream.write("\n")
				stream.flush()
				os.fsync(stream.fileno())
			replace_file(name, path)
		finally:
			if name is not None:
				Path(name).unlink(missing_ok=True)
		return values
