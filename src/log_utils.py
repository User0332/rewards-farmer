"""Central logging configuration.

`setup_logging` is called once from `main.py`. Every other module just does
`logger = logging.getLogger(__name__)` at import time, which is safe to do
before this runs, so import order does not matter.
"""

import logging
import os
import sys

LEVEL_ENV_VAR = "REWARDS_FARMER_LOG_LEVEL"
FILE_ENV_VAR = "REWARDS_FARMER_LOG_FILE"

DEFAULT_LEVEL = "INFO"

# CRITICAL is the longest level name at 8 characters, so pad to that and the
# message column stays aligned no matter what is being logged.
LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
DATE_FORMAT = "%H:%M:%S"

_configured = False


def _resolve_level(level: str | int | None) -> int:
	"""Turn a level name, a level number or None into a level number.

	An unusable value falls back to the default rather than raising. A typo in
	an environment variable must not be able to take down an unattended run.
	"""
	if level is None:
		level = os.environ.get(LEVEL_ENV_VAR, DEFAULT_LEVEL)

	if isinstance(level, int):
		return level

	resolved = logging.getLevelNamesMapping().get(str(level).strip().upper())

	if resolved is None:
		logging.getLogger(__name__).warning(
			"Unknown log level %r, falling back to %s", level, DEFAULT_LEVEL
		)

		return logging.getLevelNamesMapping()[DEFAULT_LEVEL]

	return resolved


def setup_logging(level: str | int | None = None, log_file: str | None = None) -> None:
	"""Configure the root logger. Calling this more than once is a no-op.

	`level` defaults to $REWARDS_FARMER_LOG_LEVEL, then to INFO.
	`log_file` defaults to $REWARDS_FARMER_LOG_FILE, and no file is written
	when neither is set.
	"""
	global _configured

	if _configured:
		return

	root = logging.getLogger()
	root.setLevel(_resolve_level(level))

	formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

	# Card descriptions are scraped from the page and are not ASCII outside the
	# en-US market, which the Windows console encoding cannot represent. Replace
	# those characters instead of letting the write raise.
	if hasattr(sys.stdout, "reconfigure"):
		sys.stdout.reconfigure(errors="replace")

	# stdout rather than the StreamHandler default of stderr, because this
	# replaces print and anyone already redirecting stdout to a file should
	# keep getting the same output there.
	console = logging.StreamHandler(sys.stdout)
	console.setFormatter(formatter)
	root.addHandler(console)

	if log_file is None:
		log_file = os.environ.get(FILE_ENV_VAR)

	if log_file:
		# utf-8 explicitly. Card descriptions are scraped from the page and are
		# not ASCII outside the en-US market, and the Windows default encoding
		# would raise on them.
		file_handler = logging.FileHandler(log_file, encoding="utf-8")
		file_handler.setFormatter(formatter)
		root.addHandler(file_handler)

	_configured = True
