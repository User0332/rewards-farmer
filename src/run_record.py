"""Best-effort JSON-lines evidence, independent of the app and console output."""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import re
import secrets
import threading
import time

import constants

SCHEMA_VERSION = 1
RUN_ID_ENV = "REWARDS_RUN_ID"
TRIGGER_ENV = "REWARDS_RUN_TRIGGER"
APP_VERSION_ENV = "REWARDS_APP_VERSION"
WARNING_LOG_PAIRS = (
	("llm_utils", "LLM is offline or unavailable", "llm_unavailable"),
	("queries", "No query source reachable", "search_terms_unavailable"),
)

logger = logging.getLogger(__name__)
_current = None
_account = ContextVar("rewards_record_account", default=None)
_RUN_ID = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{4}$")


def runs_dir() -> str:
	return constants.RUNS_DIR


def _timestamp() -> str:
	return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _env_pid(name):
	try:
		return int(os.environ[name])
	except (KeyError, ValueError):
		return None


def new_run_id(now: datetime | None = None) -> str:
	now = now or datetime.now(timezone.utc)
	return now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(2)


def _event(type, run_id, fields):
	# Envelope identity cannot be replaced by an unknown future field.
	return {**fields, "v": SCHEMA_VERSION, "ts": _timestamp(), "run": run_id, "type": type}


class _WarningHandler(logging.Handler):
	def __init__(self, recorder):
		super().__init__(logging.WARNING)
		self.recorder = recorder
		self.seen = set()

	def emit(self, record):
		for name, prefix, code in WARNING_LOG_PAIRS:
			if record.name == name and str(record.msg).startswith(prefix):
				key = (_account.get(), code)
				if code == "llm_unavailable" and key in self.seen:
					return
				self.seen.add(key)
				try:
					self.recorder.emit("warning", code=code, detail=record.getMessage())
				except Exception:
					pass
				return


class Recorder:
	def __init__(self, run_id, directory):
		self.run_id = run_id
		self.path = str(Path(directory) / (run_id + ".jsonl"))
		self.stats = dict.fromkeys(("task_fail", "browser_failed", "account_error", "accounts_started", "accounts_ran", "accounts_decided_no"), 0)
		self._mutex = threading.RLock()
		self._start = time.monotonic()
		self._stream = None
		self._disabled = False
		self._closed = False
		self._handler = None
		try:
			Path(directory).mkdir(parents=True, exist_ok=True)
			self._stream = open(self.path, "a", encoding="utf-8", newline="\n")
		except Exception as exc:
			self._disable(exc)

	def _disable(self, exc):
		if not self._disabled:
			self._disabled = True
			logger.debug("Run record disabled for %s: %s", self.run_id, type(exc).__name__)
		if self._stream is not None:
			try:
				self._stream.close()
			except Exception:
				pass
			self._stream = None

	def emit(self, type: str, **fields) -> None:
		with self._mutex:
			if self._closed:
				return
			if "account" not in fields and _account.get() is not None:
				fields["account"] = _account.get()
			elif type == "warning" and "account" not in fields:
				fields["account"] = None
			if type == "task_end" and fields.get("outcome") == "fail":
				self.stats["task_fail"] += 1
			elif type == "browser_failed":
				self.stats["browser_failed"] += 1
			elif type == "account_end":
				self.stats["accounts_started"] += int(bool(fields.get("started")))
				self.stats["accounts_ran"] += int(bool(fields.get("ran_tasks")))
				self.stats["account_error"] += int(fields.get("error_code") == "error")
			elif type == "decision" and fields.get("should_run") is False:
				self.stats["accounts_decided_no"] += 1
			if self._disabled:
				return
			try:
				self._stream.write(json.dumps(_event(type, self.run_id, fields), ensure_ascii=False, separators=(",", ":")) + "\n")
				self._stream.flush()
			except Exception as exc:
				self._disable(exc)

	def close(self, result: str, exit_code: int, detail: str | None = None) -> None:
		with self._mutex:
			if self._closed:
				return
			self.emit("run_end", result=result, exit_code=exit_code,
				accounts_started=self.stats["accounts_started"], accounts_ran=self.stats["accounts_ran"],
				duration_ms=round((time.monotonic() - self._start) * 1000), written_by="farmer", detail=detail)
			self._closed = True
			if self._handler is not None:
				logging.getLogger().removeHandler(self._handler)
				self._handler.close()
			if self._stream is not None:
				try:
					os.fsync(self._stream.fileno())
				except Exception as exc:
					self._disable(exc)
				finally:
					if self._stream is not None:
						try:
							self._stream.close()
						except Exception as exc:
							self._disable(exc)
						self._stream = None


def open_run(trigger: str, accounts: list[str], plan: dict, skip_tasks: list[str], run_id: str | None = None, directory: str | None = None) -> Recorder:
	global _current
	if _current is not None:
		# Repeated in-process callers must not leave a writer and handler behind.
		with _current._mutex:
			if _current._handler is not None:
				logging.getLogger().removeHandler(_current._handler)
				_current._handler.close()
			if _current._stream is not None:
				try:
					_current._stream.close()
				except Exception as exc:
					_current._disable(exc)
			_current._stream = None
			_current._closed = True
	_current = Recorder(run_id or os.environ.get(RUN_ID_ENV) or new_run_id(), directory or runs_dir())
	_current._handler = _WarningHandler(_current)
	logging.getLogger().addHandler(_current._handler)
	_current.emit("run_start", trigger=trigger, accounts=accounts, plan=plan, skip_tasks=skip_tasks,
		pid=os.getpid(), supervisor_pid=_env_pid("REWARDS_SUPERVISOR_PID"), app_version=os.environ.get(APP_VERSION_ENV))
	return _current


def current() -> Recorder | None:
	return _current


def emit(type: str, **fields) -> None:
	try:
		if _current is not None:
			_current.emit(type, **fields)
	except Exception:
		pass


@contextmanager
def account_context(name: str):
	token = _account.set(name)
	try:
		yield
	finally:
		_account.reset(token)


def close_run(result: str, exit_code: int, detail: str | None = None) -> None:
	global _current
	if _current is not None:
		try:
			_current.close(result, exit_code, detail)
		except Exception as exc:
			logger.debug("Run record close failed for %s: %s", _current.run_id, _exception_name(exc))
		finally:
			_current = None


def append_event(path: str, type: str, run_id: str, **fields) -> None:
	try:
		Path(path).parent.mkdir(parents=True, exist_ok=True)
		separator = ""
		if Path(path).exists():
			with open(path, "rb") as existing:
				if existing.seek(0, os.SEEK_END):
					existing.seek(-1, os.SEEK_END)
					if existing.read(1) != b"\n":
						# A killed writer's fragment must not consume the crash outcome.
						separator = "\n"
		with open(path, "a", encoding="utf-8", newline="\n") as stream:
			stream.write(separator + json.dumps(_event(type, run_id, fields), ensure_ascii=False, separators=(",", ":")) + "\n")
			stream.flush()
			os.fsync(stream.fileno())
	except Exception as exc:
		logger.debug("Could not append run record for %s: %s", run_id, _exception_name(exc))


def _exception_name(exc):
	return exc.__class__.__name__


def read_events(path: str) -> list[dict]:
	try:
		with open(path, encoding="utf-8", errors="replace") as stream:
			lines = stream.readlines()
	except OSError:
		return []
	events = []
	for line in lines:
		try:
			event = json.loads(line)
			if isinstance(event, dict):
				events.append(event)
		except (ValueError, TypeError):
			continue
	return events


def list_run_ids(directory: str | None = None) -> list[str]:
	try:
		return sorted((path.stem for path in Path(directory or runs_dir()).glob("*.jsonl") if _RUN_ID.fullmatch(path.stem)), reverse=True)
	except OSError:
		return []


def prune(directory: str | None = None, keep_days: int = 90, keep_max: int = 1000) -> int:
	import run_lock
	import stats_ledger
	try:
		folded = stats_ledger.fold(directory or runs_dir())
		if not folded.ok and not folded.newer:
			return 0
	except Exception:
		stats_ledger._log("ledger_fold_failed")
		return 0
	info = run_lock.status()
	protected = {info.run_id if info else None, _current.run_id if _current else None} | set(folded.pending)
	cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
	folder = Path(directory or runs_dir())
	removed = 0
	for index, run_id in enumerate(list_run_ids(str(folder))):
		try:
			at = datetime.strptime(run_id[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
		except ValueError:
			continue
		if run_id in protected or (index < keep_max and at >= cutoff):
			continue
		try:
			for suffix in (".jsonl", ".log", ".stop"):
				(folder / (run_id + suffix)).unlink(missing_ok=True)
			removed += 1
		except OSError as exc:
			logger.debug("Could not prune run %s: %s", run_id, _exception_name(exc))
	return removed


def cards_left_open_today(account: str, directory: str | None = None, today: date | None = None) -> frozenset[str]:
	today = today or date.today()
	cards = set()
	for run_id in list_run_ids(directory):
		for event in read_events(str(Path(directory or runs_dir()) / (run_id + ".jsonl"))):
			if event.get("type") != "card_left_open" or event.get("account") != account or event.get("v", 1) > SCHEMA_VERSION:
				continue
			try:
				at = datetime.fromisoformat(event["ts"].replace("Z", "+00:00")).astimezone().date()
			except (KeyError, TypeError, ValueError):
				continue
			if at == today and isinstance(event.get("description"), str):
				cards.add(event["description"])
	return frozenset(cards)
