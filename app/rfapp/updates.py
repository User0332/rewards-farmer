from datetime import datetime, timedelta, timezone
import logging
import re
import threading
from urllib.parse import urlsplit

import requests

from rfapp import __version__
from rfapp import prefs

UPDATE_REPO = "User0332/rewards-farmer"
_lock = threading.RLock()
_cached = None
_log = logging.getLogger("rfapp.updates")


class UpdateError(ValueError):
	def __init__(self, code: str, *, fields=None, params=None, detail=None):
		super().__init__(detail or code)
		self.code = code
		self.fields = fields
		self.params = params or {}
		self.detail = detail


def _version(value):
	if not isinstance(value, str) or not re.fullmatch(r"v?\d+(?:\.\d+)*", value):
		raise UpdateError("updates.failed", detail="Release has no supported numeric version")
	parts = [int(part) for part in value.removeprefix("v").split(".")]
	while len(parts) > 1 and parts[-1] == 0:
		parts.pop()
	return tuple(parts)


def _stamp(now):
	return now.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _date(value):
	try:
		parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
		return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
	except (AttributeError, TypeError, ValueError):
		return None


def _store_result(info, mutation_lock):
	global _cached
	with mutation_lock:
		with _lock:
			p = prefs.load()
			# A slower request must not overwrite the result reserved by a newer check.
			if p.get("update_last_checked") == info["checked_at"]:
				_cached = dict(info)
			result = dict(info)
			if result["latest"] == p.get("update_dismissed_version"):
				result["available"] = False
			return result


def check(force: bool = False, mutation_lock=None) -> dict:
	mutation_lock = _lock if mutation_lock is None else mutation_lock
	with mutation_lock:
		with _lock:
			p = prefs.load()
			now = datetime.now(timezone.utc)
			last = _date(p.get("update_last_checked"))
			base = dict(_cached or {"checked_at": p.get("update_last_checked"), "current": __version__, "latest": None, "available": False, "url": None})
			if not force and (not p.get("update_check", True) or (last and now - last < timedelta(hours=24))):
				if base["latest"] == p.get("update_dismissed_version"):
					base["available"] = False
				return base
			# Reserve the daily slot before releasing both locks for the request.
			checked_at = _stamp(now)
			prefs.save({"update_last_checked": checked_at})
	try:
		response = requests.get(
			f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest",
			headers={"Accept": "application/vnd.github+json", "User-Agent": f"RewardsFarmer/{__version__}"},
			timeout=10,
		)
		info = {"checked_at": checked_at, "current": __version__, "latest": None, "available": False, "url": None}
		if response.status_code != 404:
			response.raise_for_status()
			release = response.json()
			if not isinstance(release, dict):
				raise UpdateError("updates.failed", detail="Malformed release response")
			if not release.get("draft") and not release.get("prerelease"):
				latest = release.get("tag_name")
				newer = _version(latest) > _version(__version__)
				url = release.get("html_url")
				parsed = urlsplit(url or "")
				if newer and (parsed.scheme != "https" or parsed.netloc != "github.com" or not parsed.path.startswith(f"/{UPDATE_REPO}/releases/")):
					raise UpdateError("updates.failed", detail="Release URL is outside the configured repository")
				info.update(latest=latest.removeprefix("v"), available=newer, url=url if newer else None)
	except Exception as exc:
		_store_result({"checked_at": checked_at, "current": __version__, "latest": None, "available": False, "url": None}, mutation_lock)
		_log.warning("Update check failed: %s", type(exc).__name__, exc_info=True)
		raise UpdateError("updates.failed", detail=str(exc)) from exc
	return _store_result(info, mutation_lock)
