"""Best-effort JSON-lines evidence, independent of console output."""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
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


def open_run(trigger: str, accounts: list[str], plan: dict, run_id: str | None = None, directory: str | None = None) -> Recorder:
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
	_current = Recorder(run_id or new_run_id(), directory or runs_dir())
	_current._handler = _WarningHandler(_current)
	logging.getLogger().addHandler(_current._handler)
	_current.emit("run_start", trigger=trigger, accounts=accounts, plan=plan, pid=os.getpid())
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
