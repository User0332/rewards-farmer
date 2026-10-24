"""A window onto the pieces that already exist.

The farmer is driven from the command line: `main.py` works through the
accounts named in `.env`, and `init-config.py` writes that file and can sign a
profile in. Nothing here adds a capability the command line does not already
have. It is the same three things with buttons instead of prompts:

    Run       start `main.py` and watch its log while it works
    Accounts  add, remove and sign in to the profile directories
    Settings  read and write the same values `init-config.py` asks about

Tkinter ships with Python, so the window costs no new dependency. Start it
with `python src/gui.py`, or with `pythonw src/gui.py` on Windows to do
without the console window.
"""

import logging
import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import traceback
from dataclasses import dataclass, field as dataclass_field
from tkinter import filedialog, messagebox, ttk

import dotenv
from PIL import Image, ImageDraw, ImageTk

import accounts
import browser
import log_utils
from constants import DEFAULT_ROOT_DATA_DIR, DOTENV_PATH, REPO_ROOT

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_SCRIPT = os.path.join(SRC_DIR, "main.py")

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
LOG_DATE_FORMAT = "%H:%M:%S"

# Two palettes with the same keys. apply_palette() copies one over the module
# names below, so everything built afterwards uses it, and the caller rebuilds
# the widgets already on screen. The chosen name is remembered in .env under
# THEME_ENV_KEY, which the bot itself never reads - it is a window preference
# only, kept with the rest of the window's state so there is no second file.
LIGHT_PALETTE = {
	"APP_BG": "#f5f6f8",
	"CARD_BG": "#ffffff",
	"CARD_BORDER": "#e5e8ec",
	"HEADER_BG": "#ffffff",
	"BORDER": "#e5e8ec",
	"ACCENT": "#0f6cbd",
	"ACCENT_ACTIVE": "#0b5a9e",
	"ACCENT_DISABLED": "#a9c8e6",
	"ACCENT_SOFT": "#e8f1fb",
	"TEXT": "#1b1d21",
	"MUTED": "#5f6673",
	"DANGER": "#b42318",
	"DANGER_SOFT": "#fdecea",
	"SUCCESS": "#0f7b34",
	"IDLE": "#98a0ab",
	"SIDEBAR_BG": "#ffffff",
	"NAV_HOVER": "#f2f4f7",
	"NAV_SELECTED": "#e8f1fb",
	"TRACK_OFF": "#cbd0d6",
	"BTN_HOVER": "#eef0f3",
	"GHOST_HOVER": "#eef4fb",
	"FIELD_BG": "#f2f4f7",
	"HEADING_BG": "#fafbfc",
	"ROW_ALT": "#fafbfc",
	"PILL_BG": "#f2f4f7",
	"SCROLL_THUMB": "#cfd4da",
	"SCROLL_HOVER": "#b6bcc4",
	"CONSOLE_BG": "#ffffff",
	"CONSOLE_BORDER": "#e5e8ec",
	"CONSOLE_FG": "#24292f",
	"CONSOLE_ERROR": "#b3261e",
	"CONSOLE_WARNING": "#8a6100",
	"CONSOLE_SUCCESS": "#107c10",
	"CONSOLE_MUTED": "#6a6f76",
}

DARK_PALETTE = {
	"APP_BG": "#17181b",
	"CARD_BG": "#212327",
	"CARD_BORDER": "#31343a",
	"HEADER_BG": "#1c1e21",
	"BORDER": "#31343a",
	"ACCENT": "#4c9be8",
	"ACCENT_ACTIVE": "#67acef",
	"ACCENT_DISABLED": "#39516b",
	"ACCENT_SOFT": "#26313d",
	"TEXT": "#e9ebee",
	"MUTED": "#99a0aa",
	"DANGER": "#f0857c",
	"DANGER_SOFT": "#3a2624",
	"SUCCESS": "#74d68a",
	"IDLE": "#7f868f",
	"SIDEBAR_BG": "#1c1e21",
	"NAV_HOVER": "#26292e",
	"NAV_SELECTED": "#26313d",
	"TRACK_OFF": "#4a4e55",
	"BTN_HOVER": "#2c2f35",
	"GHOST_HOVER": "#26313d",
	"FIELD_BG": "#26282d",
	"HEADING_BG": "#26282c",
	"ROW_ALT": "#26282c",
	"PILL_BG": "#26292e",
	"SCROLL_THUMB": "#454951",
	"SCROLL_HOVER": "#565b64",
	"CONSOLE_BG": "#101113",
	"CONSOLE_BORDER": "#2b2e33",
	"CONSOLE_FG": "#d7dae0",
	"CONSOLE_ERROR": "#ff9c9c",
	"CONSOLE_WARNING": "#ffd27d",
	"CONSOLE_SUCCESS": "#8ce99a",
	"CONSOLE_MUTED": "#8a8f98",
}

PALETTES = {"Light": LIGHT_PALETTE, "Dark": DARK_PALETTE}

THEME_ENV_KEY = "GUI_THEME"

# Accounts the window leaves out of a run, by name. The bot never reads this;
# the window turns it into the REWARDS_ACCOUNTS it hands the run, so the list
# in .env stays the full set and "exclude" is a window-side view of it.
EXCLUDE_ENV_KEY = "GUI_EXCLUDED_ACCOUNTS"

# The names the rest of this module reads. apply_palette() rebinds them, so
# every widget built after a switch picks the new colours up.
APP_BG = LIGHT_PALETTE["APP_BG"]
CARD_BG = LIGHT_PALETTE["CARD_BG"]
CARD_BORDER = LIGHT_PALETTE["CARD_BORDER"]
HEADER_BG = LIGHT_PALETTE["HEADER_BG"]
BORDER = LIGHT_PALETTE["BORDER"]
ACCENT = LIGHT_PALETTE["ACCENT"]
ACCENT_ACTIVE = LIGHT_PALETTE["ACCENT_ACTIVE"]
ACCENT_DISABLED = LIGHT_PALETTE["ACCENT_DISABLED"]
ACCENT_SOFT = LIGHT_PALETTE["ACCENT_SOFT"]
TEXT = LIGHT_PALETTE["TEXT"]
MUTED = LIGHT_PALETTE["MUTED"]
DANGER = LIGHT_PALETTE["DANGER"]
DANGER_SOFT = LIGHT_PALETTE["DANGER_SOFT"]
SUCCESS = LIGHT_PALETTE["SUCCESS"]
IDLE = LIGHT_PALETTE["IDLE"]
SIDEBAR_BG = LIGHT_PALETTE["SIDEBAR_BG"]
NAV_HOVER = LIGHT_PALETTE["NAV_HOVER"]
NAV_SELECTED = LIGHT_PALETTE["NAV_SELECTED"]
TRACK_OFF = LIGHT_PALETTE["TRACK_OFF"]
BTN_HOVER = LIGHT_PALETTE["BTN_HOVER"]
GHOST_HOVER = LIGHT_PALETTE["GHOST_HOVER"]
FIELD_BG = LIGHT_PALETTE["FIELD_BG"]
HEADING_BG = LIGHT_PALETTE["HEADING_BG"]
ROW_ALT = LIGHT_PALETTE["ROW_ALT"]
PILL_BG = LIGHT_PALETTE["PILL_BG"]
SCROLL_THUMB = LIGHT_PALETTE["SCROLL_THUMB"]
SCROLL_HOVER = LIGHT_PALETTE["SCROLL_HOVER"]
CONSOLE_BG = LIGHT_PALETTE["CONSOLE_BG"]
CONSOLE_BORDER = LIGHT_PALETTE["CONSOLE_BORDER"]
CONSOLE_FG = LIGHT_PALETTE["CONSOLE_FG"]
CONSOLE_ERROR = LIGHT_PALETTE["CONSOLE_ERROR"]
CONSOLE_WARNING = LIGHT_PALETTE["CONSOLE_WARNING"]
CONSOLE_SUCCESS = LIGHT_PALETTE["CONSOLE_SUCCESS"]
CONSOLE_MUTED = LIGHT_PALETTE["CONSOLE_MUTED"]


def apply_palette(name: str) -> str:
	"""Rebind the module colour names to one palette.

	Returns the name actually used, so an unknown or missing value lands on
	Light rather than leaving the colours unset.
	"""

	chosen = name if name in PALETTES else "Light"
	globals().update(PALETTES[chosen])

	return chosen


def detect_system_theme() -> str:
	"""Light or Dark, following Windows when it can be read, else Light."""

	try:
		import winreg

		with winreg.OpenKey(
			winreg.HKEY_CURRENT_USER,
			r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
		) as key:
			value, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")

		return "Light" if value else "Dark"
	except Exception:
		return "Light"

# One family and a small size scale, so the window reads as a single piece
# rather than a pile of default widgets.
FONT_FAMILY = "Segoe UI"
BASE_FONT = (FONT_FAMILY, 10)
SMALL_FONT = (FONT_FAMILY, 9)
BOLD_FONT = (FONT_FAMILY, 10, "bold")
TITLE_FONT = (FONT_FAMILY, 16, "bold")
SUBTITLE_FONT = (FONT_FAMILY, 9)
SECTION_FONT = (FONT_FAMILY, 11, "bold")
HEADLINE_FONT = (FONT_FAMILY, 13, "bold")
EYEBROW_FONT = (FONT_FAMILY, 8, "bold")
MONO_FONT = ("Consolas", 9)

# The spacing scale the layout is built from, so margins line up instead of
# being a fresh guess per widget.
GAP = 12
PAGE_PAD = 20
CARD_PAD = 18
NAV_HEIGHT = 42

# How many console lines to keep before dropping the oldest. The log of a run
# is useful, an unbounded one is a memory leak that also gets slower to draw.
MAX_CONSOLE_LINES = 5000


def _env_value(value: str) -> str:
	"""Format a value the way `.env` can hold it unchanged.

	Unquoted is the point: a Windows path in double quotes has its `\\n` and
	`\\t` read as escapes, which is how a valid path silently becomes a broken
	one. Quote only what would otherwise not survive the trip, and escape the
	backslashes when a quote is forced.
	"""
	value = str(value)

	needs_quotes = (
		value != value.strip()
		or "\n" in value
		or '"' in value
		or value.startswith("'")
		or re.search(r"(^|\s)#", value) is not None
	)

	if not needs_quotes:
		return value

	escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

	return f'"{escaped}"'


class EnvFile:
	"""The `.env` on disk, read and updated line by line.

	Only the keys handed to `update` are touched. Comments, blank lines and
	keys this window does not know about stay exactly where they were, so the
	file stays hand-editable the way the README promises.
	"""

	KEY = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")

	def __init__(self, path: str):
		self.path = path

	def values(self) -> dict[str, str]:
		return {
			key: value
			for key, value in dotenv.dotenv_values(self.path).items()
			if value is not None
		}

	def get(self, key: str, default: str = "") -> str:
		return self.values().get(key, default)

	def update(self, updates: dict[str, str]) -> None:
		"""Write the given keys, remove the blank ones, leave the rest alone."""
		lines: list[str] = []

		if os.path.isfile(self.path):
			with open(self.path, "r", encoding="utf-8") as handle:
				lines = handle.read().splitlines()

		remaining = dict(updates)
		written: list[str] = []

		for line in lines:
			match = self.KEY.match(line)

			if not match or match.group(1) not in remaining:
				written.append(line)
				continue

			key = match.group(1)
			value = remaining.pop(key)

			# An empty field means "unset". Dropping the line is what lets a
			# cleared setting actually clear, rather than leaving the old
			# value sitting there for the code's fallback to never reach.
			if value != "":
				written.append(f"{key}={_env_value(value)}")

		for key, value in remaining.items():
			if value != "":
				written.append(f"{key}={_env_value(value)}")

		text = "\n".join(written)

		if text:
			text += "\n"

		with open(self.path, "w", encoding="utf-8") as handle:
			handle.write(text)


@dataclass(frozen=True)
class Field:
	"""One editable `.env` value, and how it wants to be shown."""

	key: str
	label: str
	kind: str = "text"
	choices: tuple = ()
	default: str = ""
	browse: str = ""


@dataclass(frozen=True)
class Section:
	title: str
	fields: tuple = dataclass_field(default_factory=tuple)


SETTINGS: tuple = (
	Section("Search", (
		Field("QUERY_SOURCE", "Search backend", "choice", ("trends", "llm"), "llm"),
	)),
	Section("LLM (used when the search backend is llm)", (
		Field("LLM_PROVIDER", "Provider", "choice", ("openrouter", "local"), "local"),
		Field("OPENROUTER_API_KEY", "OpenRouter API key"),
		Field("OPENROUTER_MODEL", "OpenRouter model", default="openrouter/free"),
		Field("OPENROUTER_BASE_URL", "OpenRouter base URL", default="https://openrouter.ai/api/v1"),
		Field("LOCAL_LLM_BASE_URL", "Local base URL", default="http://localhost:11434/v1"),
		Field("LOCAL_LLM_MODEL", "Local model", default="gemma4:cloud"),
		Field("LOCAL_LLM_API_KEY", "Local API key"),
		Field("LLM_REQUEST_TIMEOUT_SECONDS", "Request timeout (seconds)", "int", default="60"),
	)),
	Section("Browser", (
		Field("REWARDS_HEADLESS", "Headless", "bool", default="false"),
		Field("EDGE_BINARY", "Edge executable", browse="file"),
		Field("MSEDGEDRIVER_PATH", "msedgedriver executable", browse="file"),
	)),
	Section("Accounts", (
		Field("USER_DATA_DIR", "Profile data directory", browse="dir", default=DEFAULT_ROOT_DATA_DIR),
	)),
	Section("Logging", (
		Field("REWARDS_FARMER_LOG_LEVEL", "Log level", "choice",
			("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"), "INFO"),
		Field("REWARDS_DRIVER_LOG", "Driver log file", browse="file"),
		Field("REWARDS_FARMER_LOG_FILE", "Farmer log file", browse="file"),
	)),
	Section("Windows virtual desktop", (
		Field("USE_VIRTUAL_DESKTOP", "Run on a separate virtual desktop", "bool", default="false"),
		Field("SWITCH_BACK_TO_MAIN_DESKTOP", "Switch back after launching", "bool", default="true"),
		Field("SWITCH_BACK_DELAY_SECONDS", "Switch-back delay (seconds)", "float", default="1.5"),
		Field("CLEANUP_VIRTUAL_DESKTOP", "Close the worker desktop when done", "bool", default="true"),
	)),
)

ALL_FIELDS: tuple = tuple(field for section in SETTINGS for field in section.fields)


def _to_bool(value: str) -> bool:
	return str(value).strip().lower() in ("1", "true", "yes", "on")


def split_account_names(text: str) -> list[str]:
	"""Profile names from free text, split on commas, semicolons and lines.

	So a copied list - one per line, or comma separated - pastes straight in.
	"""

	return [
		part
		for part in (chunk.strip() for chunk in re.split(r"[,;\s]+", text or ""))
		if part
	]


def partition_accounts(names: list[str], excluded_raw: str) -> tuple[list[str], list[str]]:
	"""Split configured names into (farming, excluded) using the exclude list.

	Everything farms unless it appears in the exclude list. An entry in that
	list which is no longer configured is ignored, so a leftover name can never
	hide an account that is actually present.
	"""

	excluded = {name.lower() for name in split_account_names(excluded_raw)}

	farming = [name for name in names if name.lower() not in excluded]
	skipped = [name for name in names if name.lower() in excluded]

	return farming, skipped


def _signed_in_profiles(profile_dir: str) -> dict:
	"""The user-data-dir's `Local State` info_cache, or an empty dict.

	Keyed by browser profile directory ("Default", "Profile 1", ...). Each entry
	carries the `user_name` signed into that browser profile, which is what the
	bot's `--profile-directory` actually opens.
	"""

	try:
		with open(os.path.join(profile_dir, "Local State"), encoding="utf-8") as handle:
			return json.load(handle).get("profile", {}).get("info_cache", {}) or {}
	except (OSError, ValueError):
		return {}


def _recorded_accounts(profile_dir: str) -> list:
	"""The opened profile's `account_info`, or an empty list.

	Every account ever used on the profile is listed here, in no particular
	order, so the first entry is not necessarily the account signed in now.
	"""

	try:
		with open(os.path.join(profile_dir, accounts.PROFILE_NAME, "Preferences"),
			encoding="utf-8") as handle:
			return json.load(handle).get("account_info") or []
	except (OSError, ValueError):
		return []


def read_profile_email(profile_dir: str) -> str:
	"""Which Microsoft account a browser profile is signed into, for display.

	Read from the profile's own files rather than guessed from its folder name.
	The signed-in account wins over the recorded list: `account_info` keeps
	every account ever added to the profile, so its first entry can be an older,
	personal one that is not the account actually farming. Any failure - the
	profile not being created yet, or a file mid-write - falls back to a note,
	never an error.
	"""

	signed_in = _signed_in_profiles(profile_dir)
	opened = signed_in.get(accounts.PROFILE_NAME) or {}

	if opened.get("user_name"):
		return opened["user_name"]

	for entry in _recorded_accounts(profile_dir):
		if entry.get("email"):
			return entry["email"]

	for entry in signed_in.values():
		if entry.get("user_name"):
			return entry["user_name"]

	return "not signed in" if os.path.isdir(profile_dir) else "no folder yet"


def _round_rect(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float,
	radius: float, **kwargs):
	"""A rounded rectangle, drawn as a smoothed polygon.

	The radius is capped at half the width or height. Past that the corner
	points cross back over each other and the outline kinks, which the
	smoothing then smears into a lopsided wobble.
	"""

	radius = max(0.0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))

	points = (
		x1 + radius, y1,
		x2 - radius, y1,
		x2, y1,
		x2, y1 + radius,
		x2, y2 - radius,
		x2, y2,
		x2 - radius, y2,
		x1 + radius, y2,
		x1, y2,
		x1, y2 - radius,
		x1, y1 + radius,
		x1, y1,
	)

	return canvas.create_polygon(points, smooth=True, **kwargs)


def _pill(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float,
	steps: int = 16, **kwargs):
	"""A capsule: straight sides, semicircular ends.

	`_round_rect` places only a few points per corner, and on a shape as short
	as the toggle's track the spline bows the straight edges until the whole
	thing reads as a blob. Sampling the outline finely keeps it a capsule.
	The ends sit on whichever axis is the shorter one.
	"""

	centre_x = (x1 + x2) / 2
	centre_y = (y1 + y2) / 2
	points = []

	if x2 - x1 <= y2 - y1:
		radius = (x2 - x1) / 2
		caps = ((centre_x, y1 + radius, 180), (centre_x, y2 - radius, 0))
	else:
		radius = (y2 - y1) / 2
		caps = ((x1 + radius, centre_y, 90), (x2 - radius, centre_y, 270))

	for cap_x, cap_y, start in caps:
		for step in range(steps + 1):
			angle = math.radians(start + step * (180 / steps))
			points.append(cap_x + radius * math.cos(angle))
			points.append(cap_y + radius * math.sin(angle))

	return canvas.create_polygon(points, smooth=True, **kwargs)


class Card(tk.Frame):
	"""A panel with a rounded, hairline-bordered face.

	Tk has no rounded frame, so the shape is painted on a canvas laid under the
	content. The frame keeps its `padx`/`pady` inset, which holds the content
	clear of the corners, so callers grid into a card exactly as they would a
	plain frame.
	"""

	RADIUS = 14

	def __init__(self, parent: tk.Misc, padx: int = CARD_PAD, pady: int = CARD_PAD,
		**kwargs):
		super().__init__(parent, bg=APP_BG, bd=0, highlightthickness=0,
			padx=padx, pady=pady, **kwargs)

		self._face = tk.Canvas(self, bg=APP_BG, highlightthickness=0, bd=0)
		# `place` measures from the padded content area, so step back by the
		# padding and grow by twice it to cover the whole frame.
		self._face.place(x=-padx, y=-pady, relwidth=1, relheight=1,
			width=2 * padx, height=2 * pady)
		self._face.bind("<Configure>", self._paint)

	def _paint(self, event) -> None:
		self._face.delete("all")
		_round_rect(self._face, 0.5, 0.5, event.width - 0.5, event.height - 0.5,
			self.RADIUS, fill=CARD_BG, outline=CARD_BORDER, width=1)


def _card(parent: tk.Misc, padx: int = CARD_PAD, pady: int = CARD_PAD) -> Card:
	return Card(parent, padx=padx, pady=pady)


class PillButton(tk.Canvas):
	"""A button with rounded corners and the states this window needs.

	ttk buttons are rectangles and a themed one cannot be rounded, so this
	draws its own face. It keeps the slice of the button API used here -
	`command` and `set_enabled` - so a call site does not have to care which
	kind of button it is holding.
	"""

	HEIGHT = 34
	RADIUS = 10
	PAD_X = 16

	def __init__(self, parent: tk.Misc, text: str = "", command=None,
		variant: str = "default", surround: str | None = None, **kwargs):
		# `surround` is resolved here rather than in the default list: a
		# default is bound once at import, which would freeze the Light
		# palette's white into every button built after a switch to Dark and
		# leave a pale halo showing through the corners of the pill.
		super().__init__(parent, height=self.HEIGHT, highlightthickness=0, bd=0,
			bg=CARD_BG if surround is None else surround, cursor="hand2", **kwargs)

		self.command = command
		self.variant = variant
		self._text = text
		self._enabled = True
		self._hover = False
		self._pressed = False

		self.bind("<Enter>", self._enter)
		self.bind("<Leave>", self._leave)
		self.bind("<Button-1>", self._press)
		self.bind("<ButtonRelease-1>", self._release)

		self._resize()
		self._draw()

	def _colors(self) -> dict:
		# Read at draw time, not import time, so a rebuilt window picks up the
		# palette currently in force.
		if self.variant == "accent":
			return {"fill": ACCENT, "fg": "#ffffff", "hover": ACCENT_ACTIVE,
				"off_fill": ACCENT_DISABLED, "off_fg": "#f0f0f0", "border": ACCENT}
		if self.variant == "ghost":
			return {"fill": CARD_BG, "fg": ACCENT, "hover": GHOST_HOVER,
				"off_fill": CARD_BG, "off_fg": MUTED, "border": BORDER}
		if self.variant == "danger":
			return {"fill": CARD_BG, "fg": DANGER, "hover": DANGER_SOFT,
				"off_fill": CARD_BG, "off_fg": MUTED, "border": BORDER}
		return {"fill": CARD_BG, "fg": TEXT, "hover": BTN_HOVER,
			"off_fill": CARD_BG, "off_fg": MUTED, "border": BORDER}

	def _resize(self) -> None:
		font = tkfont.Font(font=BASE_FONT)
		self._width = font.measure(self._text) + 2 * self.PAD_X
		super().configure(width=self._width)

	def _draw(self) -> None:
		self.delete("all")
		colors = self._colors()

		if not self._enabled:
			fill, fg = colors["off_fill"], colors["off_fg"]
		elif self._pressed or self._hover:
			fill, fg = colors["hover"], colors["fg"]
		else:
			fill, fg = colors["fill"], colors["fg"]

		_round_rect(self, 0.5, 0.5, self._width - 0.5, self.HEIGHT - 0.5,
			self.RADIUS, fill=fill, outline=colors["border"], width=1)
		self.create_text(self._width / 2, self.HEIGHT / 2 + 1, text=self._text,
			fill=fg, font=BASE_FONT)

	def set_enabled(self, enabled: bool) -> None:
		self._enabled = bool(enabled)
		self.configure(cursor="hand2" if enabled else "arrow")
		self._draw()

	def _enter(self, _event) -> None:
		if self._enabled:
			self._hover = True
			self._draw()

	def _leave(self, _event) -> None:
		self._hover = False
		self._pressed = False
		self._draw()

	def _press(self, _event) -> None:
		if self._enabled:
			self._pressed = True
			self._draw()

	def _release(self, _event) -> None:
		was_pressed = self._pressed
		self._pressed = False
		self._draw()

		if was_pressed and self._enabled and self.command is not None:
			self.command()


class RoundedField(tk.Frame):
	"""A rounded surround for a single-line text or choice field.

	A ttk entry is square, which reads as unfinished beside the rounded
	cards and buttons. This paints a soft rounded fill and seats a
	borderless widget on top of it - an entry, or the select control that
	opens a choice. The fill is the whole story: the field looks the same
	focused or not, and the caret says where the typing goes.
	"""

	RADIUS = 8
	PAD_X = 11
	HEIGHT = 34

	def __init__(self, parent: tk.Misc, width: int = 1, **kwargs):
		super().__init__(parent, bg=CARD_BG, width=width, height=self.HEIGHT,
			bd=0, highlightthickness=0, **kwargs)

		self._face = tk.Canvas(self, bg=CARD_BG, highlightthickness=0, bd=0)
		self._face.place(x=0, y=0, relwidth=1, relheight=1)
		self._face.bind("<Configure>", self._paint)

	def attach(self, widget: tk.Widget) -> tk.Widget:
		"""Seat `widget` inside the field, leaving room for the rounded edge."""

		widget.place(relx=0, rely=0.5, anchor="w", x=self.PAD_X, relwidth=1,
			width=-2 * self.PAD_X, height=self.HEIGHT - 12)

		return widget

	def _paint(self, _event=None) -> None:
		width = self._face.winfo_width()
		height = self._face.winfo_height()

		self._face.delete("all")
		# A soft fill and no stroke. An outline here read as heavy, and one
		# that appeared only on focus read as a glitch, so the fill carries
		# the whole shape in every state.
		_round_rect(self._face, 0.5, 0.5, width - 0.5, height - 0.5,
			self.RADIUS, fill=FIELD_BG, outline=FIELD_BG, width=1)


class SelectControl(tk.Canvas):
	"""The control inside a choice field, and the menu it opens.

	ttk's combobox drops a square listbox inside a bezel - tight rows, the
	text flush against the edge, no hover, and past a few items an arrowed
	scrollbar. It was the last control in the window that still looked like
	the platform rather than the window, so its menu is drawn here instead:
	a rounded card of roomy rows that answer the pointer, and the row the
	value already holds shown in the accent.

	It is seated in a RoundedField, which supplies the soft fill of the
	closed control, so a choice reads exactly like the text field beside
	it. The field also does the inset, so the label sits at this canvas's
	left edge and the chevron at its right.
	"""

	RADIUS = 10
	ROW_HEIGHT = 30
	MENU_PAD = 6
	MAX_ROWS = 8
	GAP = 4
	# Only the menu's corners are painted this, and the window is told to
	# treat it as see-through, so the card has no square patch behind it.
	BLANK = "#ff00fe"

	def __init__(self, parent: tk.Misc, variable: tk.StringVar, values=(),
		**kwargs):
		super().__init__(parent, bd=0, highlightthickness=0, bg=FIELD_BG,
			cursor="hand2", **kwargs)

		self.variable = variable
		self.values = [str(value) for value in values]
		self._menu: tk.Toplevel | None = None
		self._canvas: tk.Canvas | None = None
		self._origin = (0, 0)
		self._size = (0, 0)
		self._hover = -1
		self._top = 0
		self._rows = min(len(self.values), self.MAX_ROWS)
		self._chevron: tuple | None = None

		self.bind("<Configure>", self._draw)
		self.bind("<Button-1>", self._open)
		self.bind("<Destroy>", lambda _event: self._close())
		self.variable.trace_add("write", self._changed)

	def _changed(self, *_args) -> None:
		if self.winfo_exists():
			self._draw()

	def _draw(self, _event=None) -> None:
		self.delete("all")
		width = self.winfo_width()
		height = self.winfo_height()

		if width < 2 or height < 2:
			return

		self.create_text(0, height / 2, anchor="w", text=self.variable.get(),
			fill=TEXT if self.values else MUTED, font=BASE_FONT)
		self.create_image(width, height / 2, anchor="e", image=self._arrow())

	def _arrow(self) -> ImageTk.PhotoImage:
		"""A chevron, drawn oversized and shrunk so it does not step."""

		key = (FIELD_BG, MUTED)

		if self._chevron is None or self._chevron[0] != key:
			scale = 4
			width, height = 10, 6
			image = Image.new("RGB", (width * scale, height * scale), FIELD_BG)
			draw = ImageDraw.Draw(image)
			draw.line([(scale / 2, scale / 2),
				(width * scale / 2, height * scale - scale / 2),
				(width * scale - scale / 2, scale / 2)],
				fill=MUTED, width=int(1.5 * scale), joint="curve")
			self._chevron = (key, ImageTk.PhotoImage(
				image.resize((width, height), Image.Resampling.LANCZOS),
				master=self))

		return self._chevron[1]

	# region menu

	def _open(self, _event=None) -> None:
		if self._menu is not None or not self.values:
			return

		menu = tk.Toplevel(self)
		menu.overrideredirect(True)
		menu.configure(bg=self.BLANK)
		menu.attributes("-topmost", True)

		try:
			menu.attributes("-transparentcolor", self.BLANK)
			blank = self.BLANK
		except tk.TclError:
			# Without a see-through colour the corners take the card fill
			# instead; the menu still reads as a card, just a squarer one.
			blank = CARD_BG
			menu.configure(bg=blank)

		width = max(self.master.winfo_width(), 120)
		height = self._rows * self.ROW_HEIGHT + 2 * self.MENU_PAD
		canvas = tk.Canvas(menu, width=width, height=height, bd=0,
			highlightthickness=0, bg=blank)
		canvas.pack()

		value = self.variable.get()
		self._menu = menu
		self._canvas = canvas
		self._hover = self.values.index(value) if value in self.values else -1
		self._top = max(0, self._hover - self._rows + 1)

		# Hang it under the field, or above it when the screen runs out.
		menu.update_idletasks()
		field = self.master
		x = field.winfo_rootx()
		y = field.winfo_rooty() + field.winfo_height() + self.GAP

		if y + height > self.winfo_screenheight():
			y = max(0, field.winfo_rooty() - height - self.GAP)

		self._origin = (x, y)
		self._size = (width, height)
		menu.geometry(f"{width}x{height}+{x}+{y}")
		# An overrideredirect window is not on screen until it is asked for,
		# which is also why Tk's own combobox deiconifies its popdown here.
		menu.deiconify()
		menu.lift()

		self._paint()
		canvas.bind("<Motion>", self._motion)
		canvas.bind("<Button-1>", self._click)
		canvas.bind("<MouseWheel>", self._wheel)
		menu.bind("<Key>", self._key)
		menu.grab_set()
		menu.focus_force()

	def _paint(self) -> None:
		canvas = self._canvas

		if canvas is None:
			return

		canvas.delete("all")
		width, height = self._size
		_round_rect(canvas, 0.5, 0.5, width - 0.5, height - 0.5, self.RADIUS,
			fill=CARD_BG, outline=CARD_BORDER, width=1)

		value = self.variable.get()
		for index in range(self._top, min(self._top + self._rows, len(self.values))):
			top = self.MENU_PAD + (index - self._top) * self.ROW_HEIGHT

			if index == self._hover:
				_round_rect(canvas, self.MENU_PAD, top + 1, width - self.MENU_PAD,
					top + self.ROW_HEIGHT - 1, self.RADIUS - 3,
					fill=ACCENT_SOFT, outline="")

			canvas.create_text(self.MENU_PAD + 6, top + self.ROW_HEIGHT / 2,
				anchor="w", text=self.values[index],
				fill=ACCENT if self.values[index] == value else TEXT,
				font=BASE_FONT)

	def _row_at(self, y: int) -> int:
		index = self._top + int((y - self.MENU_PAD) // self.ROW_HEIGHT)

		if self._top <= index < min(self._top + self._rows, len(self.values)):
			return index

		return -1

	def _highlight(self, index: int) -> None:
		if index >= 0 and index != self._hover:
			self._hover = index
			self._paint()

	def _move(self, step: int) -> None:
		if not self.values:
			return

		if self._hover < 0:
			index = 0 if step > 0 else len(self.values) - 1
		else:
			index = min(max(self._hover + step, 0), len(self.values) - 1)

		self._hover = index
		self._top = min(max(self._top, index - self._rows + 1), index)

		self._paint()

	def _take(self) -> None:
		if 0 <= self._hover < len(self.values):
			self.variable.set(self.values[self._hover])

		self._close()

	def _motion(self, event) -> None:
		self._highlight(self._row_at(event.y))

	def _click(self, event) -> None:
		# The grab sends clicks outside the menu here too, which is how a
		# click anywhere else dismisses it.
		x, y = self._origin
		width, height = self._size

		if not (x <= event.x_root < x + width and y <= event.y_root < y + height):
			self._close()
			return

		self._hover = self._row_at(event.y)
		self._take()

	def _wheel(self, event) -> None:
		self._move(-1 if event.delta > 0 else 1)

	def _key(self, event) -> None:
		if event.keysym == "Up":
			self._move(-1)
		elif event.keysym == "Down":
			self._move(1)
		elif event.keysym == "Home":
			self._move(-len(self.values))
		elif event.keysym == "End":
			self._move(len(self.values))
		elif event.keysym in ("Return", "KP_Enter", "space"):
			self._take()
		elif event.keysym == "Escape":
			self._close()

	def _close(self) -> None:
		menu = self._menu
		self._menu = None
		self._canvas = None

		if menu is None:
			return

		try:
			menu.grab_release()
		except tk.TclError:
			pass

		menu.destroy()

	# endregion


class ToggleSwitch(tk.Canvas):
	"""A small sliding switch bound to a BooleanVar.

	Tkinter has no switch widget, and the default checkbutton renders as a
	setting with no visible label next to it, which is easy to read as broken.
	This draws the familiar track-and-knob, and slides the knob so a change is
	legible at a glance.

	Tk paints canvas shapes with no anti-aliasing, which leaves curves this
	small looking stepped. The switch is drawn with Pillow at a larger scale
	and shrunk back down instead, so its edges soften into the card.
	"""

	WIDTH = 46
	HEIGHT = 26
	KNOB = 20
	PAD = 3
	EASE = 0.35
	SNAP = 0.02
	SAMPLE = 4

	def __init__(self, parent, variable: tk.BooleanVar, **kwargs):
		super().__init__(parent, width=self.WIDTH, height=self.HEIGHT, bd=0,
			highlightthickness=0, bg=CARD_BG, cursor="hand2", **kwargs)
		self.variable = variable
		self._position = 1.0 if variable.get() else 0.0
		self._animation: str | None = None
		self._frames: dict[tuple, ImageTk.PhotoImage] = {}
		self._face = self.create_image(0, 0, anchor="nw")

		self.variable.trace_add("write", self._animate)
		self.bind("<Button-1>", self._toggle)
		self.bind("<Destroy>", self._cancel)
		self._draw()

	def _centre(self, position: float) -> float:
		"""Where the knob sits for a slide position between 0.0 and 1.0."""

		radius = self.KNOB / 2
		left = self.PAD + radius
		right = self.WIDTH - self.PAD - radius

		return left + (right - left) * position

	def _render(self, on: bool, centre: float) -> Image.Image:
		"""Paint the switch oversized, then shrink it to soften the edges."""

		scale = self.SAMPLE
		image = Image.new("RGB", (self.WIDTH * scale, self.HEIGHT * scale),
			CARD_BG)
		draw = ImageDraw.Draw(image)

		draw.rounded_rectangle(
			(0, 0, self.WIDTH * scale - 1, self.HEIGHT * scale - 1),
			radius=self.HEIGHT * scale / 2,
			fill=ACCENT if on else TRACK_OFF)

		# The knob stays a plain white disc: the hairline ring it used to
		# carry read as a wireframe, and the gap to the track edge does the
		# same job more quietly.
		radius = self.KNOB * scale / 2
		centre_x = centre * scale
		centre_y = self.HEIGHT * scale / 2

		draw.ellipse((centre_x - radius, centre_y - radius,
			centre_x + radius, centre_y + radius), fill="#ffffff")

		return image.resize((self.WIDTH, self.HEIGHT),
			Image.Resampling.LANCZOS)

	def _draw(self) -> None:
		on = self.variable.get()

		# The knob follows the animation, not the variable, so a flip looks
		# like a slide rather than a jump. Whole-pixel centres keep the cache
		# to a couple of dozen frames and stop the disc half-landing.
		centre = round(self._centre(self._position))
		key = (on, centre, ACCENT, TRACK_OFF, CARD_BG)

		photo = self._frames.get(key)
		if photo is None:
			photo = ImageTk.PhotoImage(self._render(on, centre), master=self)
			self._frames[key] = photo

		self.itemconfig(self._face, image=photo)

	def _animate(self, *_args) -> None:
		self._cancel()
		target = 1.0 if self.variable.get() else 0.0

		def tick():
			self._animation = None

			# A theme switch rebuilds the window out from under a running
			# animation, so stop quietly rather than drawing on a dead widget.
			if not self.winfo_exists():
				return

			# Ease out: each frame covers a fixed share of what is left, so
			# the knob leaves quickly and settles gently instead of stepping.
			delta = target - self._position

			if abs(delta) <= self.SNAP:
				self._position = target
			else:
				self._position += delta * self.EASE
				self._animation = self.after(14, tick)

			self._draw()

		tick()

	def _cancel(self, _event=None) -> None:
		if self._animation is not None:
			try:
				self.after_cancel(self._animation)
			except Exception:
				pass
			self._animation = None

	def _toggle(self, _event=None) -> None:
		self.variable.set(not self.variable.get())


class PillScrollbar(tk.Canvas):
	"""A slim, floating scrollbar with no arrow buttons.

	ttk's scrollbar keeps its up/down arrows and a square thumb that sits
	flush against the edges, which is the last dated-looking control in the
	window. This draws one rounded thumb that floats in the trough and speaks
	the same `set`/`command` contract as `ttk.Scrollbar`, so it drops straight
	in where one was.
	"""

	WIDTH = 12
	PAD = 3
	MIN_THUMB = 30

	def __init__(self, parent, command=None, **kwargs):
		super().__init__(parent, width=self.WIDTH, highlightthickness=0, bd=0,
			bg=CARD_BG, **kwargs)
		self.command = command
		self._first = 0.0
		self._last = 1.0
		self._hover = False
		self._grabbed = None
		self._grab_first = 0.0

		self.bind("<Configure>", self._redraw)
		self.bind("<Enter>", lambda _event: self._set_hover(True))
		self.bind("<Leave>", lambda _event: self._set_hover(False))
		self.bind("<Button-1>", self._press)
		self.bind("<B1-Motion>", self._drag)
		self.bind("<ButtonRelease-1>", lambda _event: self._release())

	def set(self, first, last) -> None:
		"""Mirror of `ttk.Scrollbar.set`, driven by the widget's yscrollcommand."""

		try:
			self._first = float(first)
			self._last = float(last)
		except (TypeError, ValueError):
			return

		self._redraw()

	def _set_hover(self, value: bool) -> None:
		if self._hover != value:
			self._hover = value
			self._redraw()

	def _thumb(self):
		height = self.winfo_height()
		span = max(self._last - self._first, 0.0)
		size = min(max(self.MIN_THUMB, span * height), height)
		top = min(self._first * height, height - size)

		return height, top, size

	def _redraw(self, _event=None) -> None:
		self.delete("all")
		height = self.winfo_height()

		if height < 2:
			return

		_, top, size = self._thumb()
		x1, x2 = self.PAD, self.WIDTH - self.PAD
		fill = SCROLL_HOVER if self._hover else SCROLL_THUMB

		if size >= height:
			# Nothing to scroll: hide the thumb rather than park a full bar
			# down the side, which would read as a divider.
			return

		_pill(self, x1, top + 1, x2, top + size - 1, fill=fill, outline="")

	def _press(self, event) -> None:
		height, top, size = self._thumb()

		if top <= event.y <= top + size:
			self._grab_first = self._first
		else:
			# Centre the thumb on the click, then keep dragging from there.
			self._grab_first = (event.y - size / 2) / height
			self._move(self._grab_first)

		self._grabbed = event.y

	def _drag(self, event) -> None:
		if self._grabbed is None:
			return

		height = self.winfo_height()

		if height <= 0:
			return

		self._move(self._grab_first + (event.y - self._grabbed) / height)

	def _release(self) -> None:
		self._grabbed = None

	def _move(self, first: float) -> None:
		span = self._last - self._first
		first = min(max(first, 0.0), max(1.0 - span, 0.0))

		if self.command:
			self.command("moveto", first)


class TkLogHandler(logging.Handler):
	"""Send log records to the console widget, from any thread.

	`emit_line` is expected to be thread-safe - here it only queues the line,
	which the main loop then draws.
	"""

	def __init__(self, emit_line):
		super().__init__()
		self.emit_line = emit_line
		self.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))

	def emit(self, record):
		try:
			self.emit_line(self.format(record))
		except Exception:
			self.handleError(record)


class ManagerApp:
	def __init__(self, root: tk.Tk):
		self.root = root
		self.env = EnvFile(DOTENV_PATH)
		self.queue: queue.Queue = queue.Queue()

		self.process: subprocess.Popen | None = None
		self.signin_active = False
		self.stopping = False
		self.fields: dict[str, tuple] = {}

		self.views: dict[str, ttk.Frame] = {}
		self.nav: dict[str, tuple] = {}
		self.current_view = "Run"
		self._status: tuple | None = None

		# Filled by refresh_accounts: the configured names, the ones left out of
		# a run, and those left out in the account list's own order.
		self.accounts: list[str] = []
		self.excluded: set[str] = set()
		self.farming: list[str] = []
		self._skipped: list[str] = []

		self.theme = apply_palette(self._saved_theme())

		self._configure_window()
		self._build_chrome()
		self._install_log_handler()

		self.load_settings()
		self.refresh_accounts()

		self.root.protocol("WM_DELETE_WINDOW", self.on_close)
		self.root.after(80, self._drain_queue)

	# region window chrome

	def _configure_window(self) -> None:
		self.root.title("Rewards Farmer")
		self.root.configure(bg=APP_BG)
		self.root.minsize(860, 560)

		width, height = 1000, 720
		screen_w = self.root.winfo_screenwidth()
		screen_h = self.root.winfo_screenheight()

		# Never open larger than the screen, so the window is never born with
		# its buttons off the edge.
		width = min(width, max(860, screen_w - 80))
		height = min(height, max(560, screen_h - 80))
		x = max(0, (screen_w - width) // 2)
		y = max(0, (screen_h - height) // 3)

		self.root.geometry(f"{width}x{height}+{x}+{y}")

	def _configure_style(self) -> None:
		style = ttk.Style(self.root)

		try:
			style.theme_use("clam")
		except tk.TclError:
			pass

		style.configure(".", background=APP_BG, foreground=TEXT, font=BASE_FONT,
			borderwidth=0, focuscolor=APP_BG)
		style.configure("TFrame", background=APP_BG)
		style.configure("Card.TFrame", background=CARD_BG)
		style.configure("TLabel", background=APP_BG, foreground=TEXT)
		style.configure("Card.TLabel", background=CARD_BG, foreground=TEXT)
		style.configure("Muted.TLabel", background=APP_BG, foreground=MUTED)
		style.configure("CardMuted.TLabel", background=CARD_BG, foreground=MUTED)
		style.configure("Small.TLabel", background=APP_BG, foreground=MUTED,
			font=SMALL_FONT)
		style.configure("CardSmall.TLabel", background=CARD_BG, foreground=MUTED,
			font=SMALL_FONT)
		style.configure("H1.TLabel", background=CARD_BG, foreground=TEXT,
			font=SECTION_FONT)
		style.configure("Headline.TLabel", background=CARD_BG, foreground=TEXT,
			font=HEADLINE_FONT)
		style.configure("Value.TLabel", background=CARD_BG, foreground=TEXT,
			font=BOLD_FONT)

		# Inside a RoundedField the entry draws no chrome of its own; the field
		# paints the whole surface, so the text sits on one clean fill.
		style.configure("Field.TEntry", borderwidth=0, relief="flat",
			fieldbackground=FIELD_BG, background=FIELD_BG, foreground=TEXT,
			insertcolor=TEXT, padding=0, selectbackground=ACCENT,
			selectforeground="#ffffff", bordercolor=FIELD_BG,
			lightcolor=FIELD_BG, darkcolor=FIELD_BG)
		# The clam theme rings a focused entry with a blue border of its own
		# (bordercolor/lightcolor/darkcolor all shift on focus). Those are
		# pinned back to the fill so the field stays one clean surface.
		style.map("Field.TEntry",
			fieldbackground=[("disabled", FIELD_BG)],
			foreground=[("disabled", MUTED)],
			bordercolor=[("focus", FIELD_BG)],
			lightcolor=[("focus", FIELD_BG)],
			darkcolor=[("focus", FIELD_BG)])

		# The treeview and its headings default to the platform's own colours,
		# which stay light in the Dark palette unless they are set here.
		style.configure("Treeview", background=CARD_BG, fieldbackground=CARD_BG,
			foreground=TEXT, bordercolor=CARD_BORDER, lightcolor=CARD_BG,
			darkcolor=CARD_BG, borderwidth=1, relief="solid", rowheight=32,
			font=BASE_FONT)
		style.configure("Treeview.Heading", background=HEADING_BG,
			foreground=MUTED, font=BOLD_FONT, relief="flat", borderwidth=0,
			bordercolor=CARD_BORDER, lightcolor=HEADING_BG, darkcolor=HEADING_BG,
			padding=(12, 9))
		style.map("Treeview.Heading", background=[("active", HEADING_BG)])
		style.map("Treeview",
			background=[("selected", ACCENT_SOFT)],
			foreground=[("selected", TEXT)])

	def _build_chrome(self) -> None:
		"""Draw the whole window. Called again, from scratch, on a theme switch.

		The widgets are recreated rather than restyled because almost every
		colour here is a per-widget option; rebuilding is shorter and leaves no
		stale colours behind.
		"""

		self.views = {}
		self.nav = {}
		self.fields = {}
		self._nav_hovered = None
		self._hovered_row = None

		self.root.configure(bg=APP_BG)
		self._configure_style()
		self._set_window_icon()
		self._build_header()
		self._build_body()
		self._build_status_bar()

		if getattr(self, "_status", None):
			self.set_status(*self._status)

	def _saved_theme(self) -> str:
		stored = self.env.get(THEME_ENV_KEY, "").strip()
		return stored if stored in PALETTES else detect_system_theme()

	def set_theme(self, name: str) -> None:
		if name not in PALETTES:
			name = "Light"

		if name == self.theme:
			return

		# A switch redraws everything, so keep what the user would notice
		# losing: the log so far, the visible page, and any edits not yet saved.
		console_text = self.console.get("1.0", "end-1c")
		view = self.current_view
		values = {key: getter() for key, (getter, _setter, _spec) in self.fields.items()}

		self.theme = apply_palette(name)
		self.env.update({THEME_ENV_KEY: self.theme})

		for child in self.root.winfo_children():
			child.destroy()

		self._build_chrome()
		self.load_settings()

		for key, value in values.items():
			if key in self.fields:
				self.fields[key][1](value)

		self._show_view(view)
		self.refresh_accounts()

		if console_text:
			self.console.configure(state="normal")
			self.console.insert("1.0", console_text)
			self.console.configure(state="disabled")
			self.console.see("end")

		self.set_footer(f"{self.theme} theme.")

	def _build_header(self) -> None:
		header = tk.Frame(self.root, bg=HEADER_BG)
		header.pack(fill="x", side="top")

		inner = tk.Frame(header, bg=HEADER_BG, padx=24, pady=16)
		inner.pack(fill="x")

		mark = tk.Canvas(inner, width=40, height=40, bg=HEADER_BG,
			highlightthickness=0, bd=0)
		mark.pack(side="left", anchor="w")
		self._draw_brand(mark)

		titles = tk.Frame(inner, bg=HEADER_BG)
		titles.pack(side="left", anchor="w", padx=(14, 0))

		tk.Label(titles, text="Rewards Farmer", bg=HEADER_BG, fg=TEXT,
			font=TITLE_FONT).pack(anchor="w")
		tk.Label(titles, text="Microsoft Rewards automation manager", bg=HEADER_BG,
			fg=MUTED, font=SUBTITLE_FONT).pack(anchor="w", pady=(3, 0))

		self.status_canvas = tk.Canvas(inner, height=28, bg=HEADER_BG,
			highlightthickness=0, bd=0)
		self.status_canvas.pack(side="right", anchor="e", padx=(0, 4))
		self._draw_status()

		tk.Frame(self.root, bg=BORDER, height=1).pack(fill="x", side="top")

	def _draw_brand(self, canvas: tk.Canvas) -> None:
		"""The app mark: a rounded accent tile with a run glyph on it."""

		_round_rect(canvas, 0, 0, 40, 40, 11, fill=ACCENT, outline="")
		canvas.create_polygon(16, 12, 16, 28, 29, 20, fill="#ffffff", outline="")

	def _set_window_icon(self) -> None:
		"""Put the same mark on the title bar and the taskbar.

		Tk falls back to its own feather, which makes the window look
		unfinished, and this way the icon ships with no binary asset. The tile
		is painted a pixel at a time into a PhotoImage, then the corners
		outside the rounded edge are cleared so they come out transparent.
		"""

		size, radius = 32, 9
		image = tk.PhotoImage(width=size, height=size)
		image.put(ACCENT, to=(0, 0, size, size))

		def in_tile(x: float, y: float) -> bool:
			width = size
			if radius <= x <= width - radius or radius <= y <= width - radius:
				return True

			corner_x = radius if x < radius else width - radius
			corner_y = radius if y < radius else width - radius

			return ((x - corner_x) ** 2 + (y - corner_y) ** 2) <= radius ** 2

		def in_glyph(x: float, y: float) -> bool:
			# The play triangle from _draw_brand, scaled to this tile.
			triangle = ((13, 10), (13, 22), (23, 16))

			def side(a, b):
				return (x - b[0]) * (a[1] - b[1]) - (a[0] - b[0]) * (y - b[1])

			edges = [side(triangle[i], triangle[(i + 1) % 3]) for i in range(3)]

			return all(edge >= 0 for edge in edges) or all(edge <= 0 for edge in edges)

		for y in range(size):
			for x in range(size):
				center = (x + 0.5, y + 0.5)

				if in_glyph(*center):
					image.put("#ffffff", to=(x, y, x + 1, y + 1))
				elif not in_tile(*center):
					image.transparency_set(x, y, True)

		self._icon = image

		try:
			self.root.iconphoto(True, image)
		except tk.TclError:
			pass

	def _draw_status(self) -> None:
		"""The dot-and-label pill in the header.

		Drawn on a canvas because a rounded background is the point, and a
		`tk.Label` has no rounded corners. The width follows the text so the
		pill hugs it whatever the status says.
		"""

		canvas = self.status_canvas
		canvas.delete("all")

		kind, text = self._status or ("idle", "Idle")
		colors = {"running": ACCENT, "success": SUCCESS, "error": DANGER,
			"idle": IDLE}
		color = colors.get(kind, IDLE)

		font = tkfont.Font(font=BASE_FONT)
		padding, dot, gap = 12, 8, 8
		width = padding + dot + gap + font.measure(text) + padding
		canvas.configure(width=width)

		_round_rect(canvas, 1, 2, width - 1, 26, 12, fill=PILL_BG, outline="")
		canvas.create_oval(padding, 11, padding + dot, 11 + dot, fill=color,
			outline="")
		canvas.create_text(padding + dot + gap, 14, text=text, anchor="w",
			fill=TEXT, font=BASE_FONT)

	def _build_status_bar(self) -> None:
		tk.Frame(self.root, bg=BORDER, height=1).pack(fill="x", side="bottom")

		bar = tk.Frame(self.root, bg=HEADER_BG)
		bar.pack(fill="x", side="bottom")

		self.footer_label = tk.Label(bar, text="Ready", bg=HEADER_BG, fg=MUTED,
			font=SMALL_FONT, padx=24, pady=7, anchor="w")
		self.footer_label.pack(side="left")

		tk.Label(bar, text=".env", bg=HEADER_BG, fg=IDLE, font=SMALL_FONT,
			padx=24, pady=7).pack(side="right")

	def _build_body(self) -> None:
		body = tk.Frame(self.root, bg=APP_BG)
		body.pack(fill="both", expand=True)

		sidebar = tk.Frame(body, bg=SIDEBAR_BG, width=216)
		sidebar.pack(side="left", fill="y")
		sidebar.pack_propagate(False)

		tk.Frame(body, bg=BORDER, width=1).pack(side="left", fill="y")

		tk.Label(sidebar, text="MENU", bg=SIDEBAR_BG, fg=IDLE, anchor="w",
			font=EYEBROW_FONT, padx=24).pack(fill="x", pady=(20, 8))

		nav = tk.Frame(sidebar, bg=SIDEBAR_BG)
		nav.pack(fill="x")

		content = tk.Frame(body, bg=APP_BG)
		content.pack(side="left", fill="both", expand=True)
		content.rowconfigure(0, weight=1)
		content.columnconfigure(0, weight=1)

		for name in ("Run", "Accounts", "Settings"):
			view = ttk.Frame(content)
			view.grid(row=0, column=0, sticky="nsew")
			self.views[name] = view

		for name in ("Run", "Accounts", "Settings"):
			self._add_nav_item(nav, name)

		self._build_run_view(self.views["Run"])
		self._build_accounts_view(self.views["Accounts"])
		self._build_settings_view(self.views["Settings"])

		self._show_view("Run")

	# endregion

	# region navigation

	def _add_nav_item(self, parent: tk.Frame, name: str) -> None:
		# A canvas per entry, so the highlight can be a rounded pill rather
		# than the square block a coloured frame gives.
		item = tk.Canvas(parent, bg=SIDEBAR_BG, height=NAV_HEIGHT,
			highlightthickness=0, bd=0, cursor="hand2")
		item.pack(fill="x", padx=12, pady=(0, 2))

		self.nav[name] = item

		item.bind("<Configure>", lambda _event, n=name: self._draw_nav_item(n))
		item.bind("<Button-1>", lambda _event, n=name: self._show_view(n))
		item.bind("<Enter>", lambda _event, n=name: self._nav_hover(n, True))
		item.bind("<Leave>", lambda _event, n=name: self._nav_hover(n, False))

	def _draw_nav_item(self, name: str) -> None:
		canvas = self.nav[name]
		canvas.delete("all")

		width = canvas.winfo_width()
		if width < 2:
			width = 192

		height = NAV_HEIGHT
		selected = name == self.current_view
		hovered = self._nav_hovered == name

		if selected or hovered:
			_round_rect(canvas, 0, 2, width, height - 2, 9,
				fill=NAV_SELECTED if selected else NAV_HOVER, outline="")

		if selected:
			# A short accent bar, the way a real app flags the current page.
			_round_rect(canvas, 4, height / 2 - 9, 7, height / 2 + 9, 1.5,
				fill=ACCENT, outline="")

		color = ACCENT if selected else (TEXT if hovered else MUTED)
		self._draw_nav_icon(canvas, name, 30, height / 2, color)
		canvas.create_text(54, height / 2, text=name, anchor="w", fill=color,
			font=BOLD_FONT if selected else BASE_FONT)

	def _draw_nav_icon(self, canvas: tk.Canvas, name: str, cx: float, cy: float,
		color: str) -> None:
		"""A small glyph per page, drawn rather than shipped as an image."""

		if name == "Run":
			canvas.create_polygon(cx - 5, cy - 7, cx - 5, cy + 7, cx + 6, cy,
				fill=color, outline="")
		elif name == "Accounts":
			canvas.create_oval(cx - 4, cy - 8, cx + 4, cy, fill=color, outline="")
			canvas.create_arc(cx - 8, cy - 1, cx + 8, cy + 15, start=0, extent=180,
				style="chord", fill=color, outline="")
		else:
			# Three sliders, the usual shorthand for settings.
			for offset, knob in ((-6, -3), (1, 4), (8, -1)):
				canvas.create_line(cx - 8, cy + offset, cx + 8, cy + offset,
					fill=color, width=2)
				canvas.create_oval(cx + knob - 3, cy + offset - 3, cx + knob + 3,
					cy + offset + 3, fill=color, outline="")

	def _nav_hover(self, name: str, entering: bool) -> None:
		self._nav_hovered = name if entering else None
		self._draw_nav_item(name)

	def _show_view(self, name: str) -> None:
		self.current_view = name
		self.views[name].tkraise()

		for other in self.nav:
			self._draw_nav_item(other)

	# endregion

	# region run view

	def _build_run_view(self, tab: ttk.Frame) -> None:
		tab.columnconfigure(0, weight=1)
		tab.rowconfigure(1, weight=1)

		summary = _card(tab)
		summary.grid(row=0, column=0, sticky="ew", pady=(PAGE_PAD, 0), padx=PAGE_PAD)
		summary.columnconfigure(0, weight=1)

		left = tk.Frame(summary, bg=CARD_BG)
		left.grid(row=0, column=0, sticky="w")

		ttk.Label(left, text="Ready to run", style="H1.TLabel").grid(
			row=0, column=0, sticky="w")
		self.run_accounts_label = ttk.Label(left, text="-", style="Headline.TLabel",
			wraplength=560, justify="left")
		self.run_accounts_label.grid(row=1, column=0, sticky="w", pady=(6, 0))
		self.run_excluded_label = ttk.Label(left, text="", style="CardSmall.TLabel",
			wraplength=560, justify="left")
		self.run_excluded_label.grid(row=2, column=0, sticky="w", pady=(4, 0))

		actions = tk.Frame(summary, bg=CARD_BG)
		actions.grid(row=0, column=1, sticky="e", padx=(GAP, 0))

		self.run_button = PillButton(actions, text="Run", variant="accent",
			command=self.start_run)
		self.run_button.grid(row=0, column=0, padx=(0, 8))

		self.stop_button = PillButton(actions, text="Stop", command=self.stop_run)
		self.stop_button.grid(row=0, column=1)
		self.stop_button.set_enabled(False)

		console_card = _card(tab, pady=16)
		console_card.grid(row=1, column=0, sticky="nsew",
			pady=(GAP, PAGE_PAD), padx=PAGE_PAD)
		console_card.columnconfigure(0, weight=1)
		console_card.rowconfigure(1, weight=1)

		head = tk.Frame(console_card, bg=CARD_BG)
		head.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
		head.columnconfigure(0, weight=1)

		ttk.Label(head, text="Output", style="H1.TLabel").grid(row=0, column=0, sticky="w")
		PillButton(head, text="Clear", variant="ghost",
			command=self.clear_console).grid(row=0, column=1, sticky="e")

		self.console = tk.Text(console_card, bg=CONSOLE_BG, fg=CONSOLE_FG, bd=0,
			relief="flat", wrap="word", font=MONO_FONT, padx=12, pady=10,
			highlightthickness=1, highlightbackground=CONSOLE_BORDER,
			highlightcolor=CONSOLE_BORDER, insertbackground=CONSOLE_FG,
			state="disabled", spacing1=1, spacing3=1)

		scrollbar = PillScrollbar(console_card, command=self.console.yview)
		self.console.configure(yscrollcommand=scrollbar.set)

		self.console.grid(row=1, column=0, sticky="nsew")
		scrollbar.grid(row=1, column=1, sticky="ns", padx=(8, 0))

		self.console.tag_configure("error", foreground=CONSOLE_ERROR)
		self.console.tag_configure("warning", foreground=CONSOLE_WARNING)
		self.console.tag_configure("success", foreground=CONSOLE_SUCCESS)
		self.console.tag_configure("muted", foreground=CONSOLE_MUTED)

	# endregion

	# region accounts view

	def _build_accounts_view(self, tab: ttk.Frame) -> None:
		tab.columnconfigure(0, weight=1)
		tab.rowconfigure(0, weight=1)

		card = _card(tab)
		card.grid(row=0, column=0, sticky="nsew", pady=PAGE_PAD, padx=PAGE_PAD)
		card.columnconfigure(0, weight=1)
		card.rowconfigure(2, weight=1)

		head = tk.Frame(card, bg=CARD_BG)
		head.grid(row=0, column=0, columnspan=2, sticky="ew")
		head.columnconfigure(0, weight=1)

		ttk.Label(head, text="Accounts", style="H1.TLabel").grid(row=0, column=0, sticky="w")
		self.accounts_count_label = ttk.Label(head, text="", style="CardMuted.TLabel")
		self.accounts_count_label.grid(row=0, column=1, sticky="e")

		ttk.Label(card, style="CardSmall.TLabel", wraplength=680, justify="left",
			text="Each account is its own browser profile under the data directory. "
			     "The second column is the Microsoft account that profile is signed "
			     "into. Every account runs by default; select rows and Exclude the "
			     "ones you want left out.").grid(
			row=1, column=0, columnspan=2, sticky="w", pady=(6, 10))

		self.accounts_tree = ttk.Treeview(card, columns=("name", "email", "run"),
			show="headings", selectmode="extended", height=10)
		self.accounts_tree.heading("name", text="Profile directory")
		self.accounts_tree.heading("email", text="Signed in as")
		self.accounts_tree.heading("run", text="Run")
		self.accounts_tree.column("name", anchor="w", width=200, stretch=False)
		self.accounts_tree.column("email", anchor="w", width=340)
		self.accounts_tree.column("run", anchor="center", width=90, stretch=False)
		# Zebra stripes keep a long list readable, and an excluded row recedes
		# so the farming set is obvious at a glance.
		self.accounts_tree.tag_configure("odd", background=ROW_ALT)
		self.accounts_tree.tag_configure("even", background=CARD_BG)
		self.accounts_tree.tag_configure("skipped", foreground=MUTED)
		# Configured last, so its background wins over the stripe while the
		# pointer rests on a row. It only sets a background, so an excluded
		# row still keeps the muted text underneath.
		self.accounts_tree.tag_configure("hover", background=NAV_HOVER)
		self.accounts_tree.grid(row=2, column=0, sticky="nsew")
		self.accounts_tree.bind("<Double-1>", self._toggle_row)
		self.accounts_tree.bind("<space>", self._toggle_selected_rows)
		self.accounts_tree.bind("<Motion>", self._hover_row)
		self.accounts_tree.bind("<Leave>", lambda _event: self._set_row_hover(None))

		tree_scroll = PillScrollbar(card, command=self.accounts_tree.yview)
		self.accounts_tree.configure(yscrollcommand=tree_scroll.set)
		tree_scroll.grid(row=2, column=1, sticky="ns", padx=(8, 0))

		ttk.Label(card, style="CardSmall.TLabel",
			text="Tip: double-click a row to include or exclude it.").grid(
			row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))

		add_row = tk.Frame(card, bg=CARD_BG)
		add_row.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(12, 0))
		add_row.columnconfigure(1, weight=1)

		ttk.Label(add_row, text="New profile", style="Card.TLabel").grid(
			row=0, column=0, sticky="w", padx=(0, 10))
		self.new_account_var = tk.StringVar()

		field = RoundedField(add_row)
		field.grid(row=0, column=1, sticky="ew")

		entry = ttk.Entry(field, textvariable=self.new_account_var,
			style="Field.TEntry")
		field.attach(entry)
		entry.bind("<Return>", lambda _event: self.add_account())
		PillButton(add_row, text="Add", variant="accent",
			command=self.add_account).grid(row=0, column=2, padx=(8, 0))
		PillButton(add_row, text="Add many...", variant="ghost",
			command=self.add_accounts_dialog).grid(row=0, column=3, padx=(8, 0))

		action_row = tk.Frame(card, bg=CARD_BG)
		action_row.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(12, 0))
		action_row.columnconfigure(3, weight=1)

		PillButton(action_row, text="Select all", variant="ghost",
			command=self.select_all_accounts).grid(row=0, column=0, padx=(0, 8))
		PillButton(action_row, text="Include", variant="ghost",
			command=self.include_accounts).grid(row=0, column=1, padx=(0, 8))
		PillButton(action_row, text="Exclude", variant="ghost",
			command=self.exclude_accounts).grid(row=0, column=2)

		self.signin_button = PillButton(action_row, text="Sign in", variant="ghost",
			command=self.sign_in)
		self.signin_button.grid(row=0, column=4, padx=(8, 8))
		self.remove_button = PillButton(action_row, text="Remove", variant="danger",
			command=self.remove_accounts)
		self.remove_button.grid(row=0, column=5)

	# endregion

	# region settings view

	def _build_settings_view(self, tab: ttk.Frame) -> None:
		tab.columnconfigure(0, weight=1)
		tab.rowconfigure(0, weight=1)

		card = _card(tab)
		card.grid(row=0, column=0, sticky="nsew", pady=PAGE_PAD, padx=PAGE_PAD)
		card.columnconfigure(0, weight=1)
		card.rowconfigure(1, weight=1)

		head = tk.Frame(card, bg=CARD_BG)
		head.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
		ttk.Label(head, text="Settings", style="H1.TLabel").pack(side="left")
		ttk.Label(head, text="stored in .env", style="CardSmall.TLabel").pack(
			side="left", padx=(8, 0))

		body = self._scrollable(card)

		self._build_appearance_section(body)

		for section in SETTINGS:
			self._build_section(body, section)

		actions = tk.Frame(card, bg=CARD_BG)
		actions.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(14, 0))
		actions.columnconfigure(0, weight=1)

		PillButton(actions, text="Reload from .env", variant="ghost",
			command=self.load_settings).grid(row=0, column=0, sticky="w")
		PillButton(actions, text="Save", variant="accent",
			command=self.save_settings).grid(row=0, column=1, sticky="e")

	def _build_appearance_section(self, parent: tk.Frame) -> None:
		"""The theme switch, kept with the other preferences.

		It applies at once rather than on Save, because a colour scheme is
		something you want to see before committing to it, and it is stored in
		`.env` the moment it changes.
		"""

		block = tk.Frame(parent, bg=CARD_BG)
		block.pack(fill="x")

		ttk.Label(block, text="Appearance", style="H1.TLabel").pack(
			anchor="w", pady=(12, 6))

		row = tk.Frame(block, bg=CARD_BG)
		row.pack(fill="x")

		ttk.Label(row, text="Dark mode", style="Card.TLabel").pack(side="left")

		self.theme_var = tk.BooleanVar(value=self.theme == "Dark")
		ToggleSwitch(row, self.theme_var).pack(side="left", padx=(20, 0))

		state_label = ttk.Label(row, text="", style="CardMuted.TLabel", width=6)
		state_label.pack(side="left", padx=(10, 0))

		def sync_label(*_args):
			dark = self.theme_var.get()
			state_label.configure(text="Dark" if dark else "Light",
				foreground=SUCCESS if dark else MUTED)

		def apply_theme(*_args):
			self.set_theme("Dark" if self.theme_var.get() else "Light")

		self.theme_var.trace_add("write", apply_theme)
		sync_label()

		tk.Frame(block, bg=BORDER, height=1).pack(fill="x", pady=(12, 0))

	def _scrollable(self, parent: tk.Frame) -> tk.Frame:
		canvas = tk.Canvas(parent, bg=CARD_BG, highlightthickness=0, bd=0)
		scrollbar = PillScrollbar(parent, command=canvas.yview)
		canvas.configure(yscrollcommand=scrollbar.set)

		canvas.grid(row=1, column=0, sticky="nsew")
		scrollbar.grid(row=1, column=1, sticky="ns", padx=(8, 0))

		inner = tk.Frame(canvas, bg=CARD_BG)
		window = canvas.create_window((0, 0), window=inner, anchor="nw")

		inner.bind("<Configure>",
			lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
		canvas.bind("<Configure>",
			lambda event: canvas.itemconfigure(window, width=event.width))

		def on_wheel(event):
			canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

		# Bound only while the pointer is over the settings pane, so it does
		# not swallow the wheel elsewhere in the window.
		canvas.bind("<Enter>", lambda _event: self.root.bind_all("<MouseWheel>", on_wheel))
		canvas.bind("<Leave>", lambda _event: self.root.unbind_all("<MouseWheel>"))

		return inner

	def _build_section(self, parent: tk.Frame, section: Section) -> None:
		block = tk.Frame(parent, bg=CARD_BG)
		block.pack(fill="x")
		block.columnconfigure(1, weight=1)

		ttk.Label(block, text=section.title, style="H1.TLabel").grid(
			row=0, column=0, columnspan=3, sticky="w", pady=(12, 4))

		for index, spec in enumerate(section.fields, start=1):
			self._build_field(block, index, spec)

		separator = tk.Frame(block, bg=BORDER, height=1)
		separator.grid(row=len(section.fields) + 1, column=0, columnspan=3,
			sticky="ew", pady=(12, 0))

	def _build_field(self, parent: tk.Frame, row: int, spec: Field) -> None:
		ttk.Label(parent, text=spec.label, style="Card.TLabel").grid(
			row=row, column=0, sticky="w", padx=(0, 20), pady=7)

		current = self.env.get(spec.key, spec.default)

		if spec.kind == "bool":
			var = tk.BooleanVar(value=_to_bool(current))

			holder = tk.Frame(parent, bg=CARD_BG)
			holder.grid(row=row, column=1, sticky="w", pady=7)

			ToggleSwitch(holder, var).pack(side="left")

			state_label = ttk.Label(holder, text="", style="CardMuted.TLabel", width=3)
			state_label.pack(side="left", padx=(10, 0))

			def sync_state(*_args, var=var, state_label=state_label):
				on = var.get()
				state_label.configure(text="On" if on else "Off",
					foreground=SUCCESS if on else MUTED)

			var.trace_add("write", sync_state)
			sync_state()

			getter = lambda var=var: "true" if var.get() else "false"
			setter = lambda value, var=var: var.set(_to_bool(value))

		elif spec.kind == "choice":
			var = tk.StringVar(value=current if current in spec.choices else spec.default)

			field = RoundedField(parent, width=228)
			field.grid(row=row, column=1, sticky="w", pady=7)
			field.attach(SelectControl(field, var, spec.choices))

			getter = lambda var=var, default=spec.default: var.get() or default
			setter = lambda value, var=var, choices=spec.choices, default=spec.default: var.set(
				value if value in choices else default)

		else:
			var = tk.StringVar(value=current)

			field = RoundedField(parent)
			field.grid(row=row, column=1, sticky="ew", pady=7)

			widget = ttk.Entry(field, textvariable=var, style="Field.TEntry")
			field.attach(widget)

			getter = lambda var=var: var.get().strip()
			setter = lambda value, var=var: var.set(value)

			if spec.browse:
				mode = spec.browse
				button = PillButton(parent, text="Browse", variant="ghost",
					command=lambda var=var, mode=mode: self._browse(var, mode))
				button.grid(row=row, column=2, sticky="e", padx=(8, 0))

		self.fields[spec.key] = (getter, setter, spec)

	def _browse(self, var: tk.StringVar, mode: str) -> None:
		if mode == "dir":
			chosen = filedialog.askdirectory(initialdir=var.get() or REPO_ROOT)
		else:
			chosen = filedialog.askopenfilename(initialdir=var.get() or REPO_ROOT)

		if chosen:
			var.set(chosen)

	# endregion

	# region settings load/save

	def load_settings(self) -> None:
		for field in ALL_FIELDS:
			getter, setter, spec = self.fields[field.key]
			setter(self.env.get(spec.key, spec.default))

		self.set_footer("Settings loaded from .env")

	def save_settings(self) -> None:
		updates: dict[str, str] = {}
		problems: list[str] = []

		for field in ALL_FIELDS:
			getter, _setter, spec = self.fields[field.key]
			value = getter()

			if value and spec.kind in ("int", "float"):
				try:
					int(value) if spec.kind == "int" else float(value)
				except ValueError:
					problems.append(f"{spec.label} must be a number.")

			updates[field.key] = value

		if problems:
			messagebox.showerror("Cannot save", "\n".join(problems), parent=self.root)
			return

		try:
			self.env.update(updates)
		except OSError as exc:
			messagebox.showerror("Cannot save", f"Could not write .env:\n{exc}",
				parent=self.root)
			return

		self.reload_environment()
		self.refresh_accounts()
		self.set_footer("Settings saved to .env")
		messagebox.showinfo("Saved", "Settings saved to .env.", parent=self.root)

	def reload_environment(self) -> None:
		"""Make the just-saved file the values this process hands to a browser."""
		dotenv.load_dotenv(self.env.path, override=True)

	# endregion

	# region accounts

	def refresh_accounts(self) -> None:
		self.accounts = split_account_names(self.env.get("REWARDS_ACCOUNTS", ""))
		self.excluded, self._skipped = self._read_exclusions()

		for item in self.accounts_tree.get_children():
			self.accounts_tree.delete(item)

		self._hovered_row = None

		for index, name in enumerate(self.accounts):
			skipped = name in self.excluded
			tags = ["odd" if index % 2 else "even"]
			if skipped:
				tags.append("skipped")

			self.accounts_tree.insert("", "end",
				values=(name, self._profile_email(name), "Skipped" if skipped else "Farms"),
				tags=tuple(tags))

		self.farming = [name for name in self.accounts if name not in self.excluded]
		self._refresh_run_label()
		self._refresh_accounts_count()

		self._update_controls()

	def _read_exclusions(self) -> tuple[set[str], list[str]]:
		"""The excluded names, plus the subset of them that is still configured.

		The second value is the prune list, so a name that was removed from the
		accounts is also dropped from the exclude list the next time this runs.
		"""
		configured = {name.lower(): name for name in self.accounts}
		raw = split_account_names(self.env.get(EXCLUDE_ENV_KEY, ""))

		# Store each exclusion under the account list's own spelling, so the
		# two lists cannot drift apart in case.
		excluded = {configured[name.lower()] for name in raw if name.lower() in configured}
		skipped = [name for name in self.accounts if name in excluded]

		return excluded, skipped

	def _refresh_run_label(self) -> None:
		if not self.accounts:
			self.run_accounts_label.configure(text="(default profile)")
			self.run_excluded_label.configure(
				text="No accounts configured; the default profile will run.")
			return

		total = len(self.accounts)
		running = len(self.farming)
		plural = "" if total == 1 else "s"

		if running:
			self.run_accounts_label.configure(
				text=f"{running} of {total} account{plural} will run")
		else:
			self.run_accounts_label.configure(text="Nothing selected to run")

		lines: list[str] = []

		if self.farming:
			lines.append("Farming: " + ", ".join(self.farming))
		if self._skipped:
			lines.append("Excluded: " + ", ".join(self._skipped))
		if not self.farming:
			lines.append("Every account is excluded. Include one to run.")

		self.run_excluded_label.configure(text="\n".join(lines))

	def _refresh_accounts_count(self) -> None:
		count = len(self.accounts)
		skipped = len(self._skipped)
		if not count:
			text = "0 accounts"
		elif skipped:
			text = f"{count} accounts, {skipped} excluded"
		else:
			text = f"{count} account{'s' if count != 1 else ''}"
		self.accounts_count_label.configure(text=text)

	def _profile_email(self, name: str) -> str:
		root = os.environ.get(accounts.DATA_DIR_ENV_VAR, DEFAULT_ROOT_DATA_DIR)
		return read_profile_email(os.path.join(root, name))

	def add_account(self) -> None:
		names = split_account_names(self.new_account_var.get())

		if not names:
			messagebox.showwarning("Add account", "Enter a name for the profile.",
				parent=self.root)
			return

		if self._add_names(names):
			self.new_account_var.set("")

	def add_accounts_dialog(self) -> None:
		"""A multi-line box for pasting a whole list of profile names at once."""

		dialog = tk.Toplevel(self.root)
		dialog.title("Add accounts")
		dialog.configure(bg=CARD_BG)
		dialog.transient(self.root)

		wrapper = tk.Frame(dialog, bg=CARD_BG, padx=20, pady=18)
		wrapper.pack(fill="both", expand=True)

		tk.Label(wrapper, text="One profile name per line", bg=CARD_BG, fg=TEXT,
			font=BOLD_FONT).pack(anchor="w")
		tk.Label(wrapper, bg=CARD_BG, fg=MUTED, font=SMALL_FONT, justify="left",
			text="Letters, digits, dot, dash or underscore. Commas and blank "
			     "lines are fine too; duplicates are skipped.").pack(anchor="w", pady=(4, 10))

		text = tk.Text(wrapper, width=44, height=12, bg=FIELD_BG, fg=TEXT,
			insertbackground=TEXT, bd=0, highlightthickness=1,
			highlightbackground=FIELD_BG, highlightcolor=FIELD_BG,
			font=BASE_FONT, padx=10, pady=8, wrap="none")
		text.pack(fill="both", expand=True)
		text.focus_set()

		buttons = tk.Frame(wrapper, bg=CARD_BG)
		buttons.pack(fill="x", pady=(12, 0))

		def submit():
			names = split_account_names(text.get("1.0", "end"))
			dialog.destroy()
			if names:
				self._add_names(names)

		PillButton(buttons, text="Add", variant="accent",
			command=submit).pack(side="right")
		PillButton(buttons, text="Cancel", variant="ghost",
			command=dialog.destroy).pack(side="right", padx=(0, 8))

		dialog.bind("<Escape>", lambda _event: dialog.destroy())
		dialog.update_idletasks()
		x = self.root.winfo_rootx() + (self.root.winfo_width() - dialog.winfo_width()) // 2
		y = self.root.winfo_rooty() + (self.root.winfo_height() - dialog.winfo_height()) // 3
		dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
		dialog.grab_set()

	def _add_names(self, names: list[str]) -> list[str]:
		"""Add the valid, not-already-present names. Returns the ones added."""

		existing = {name.lower() for name in self.accounts}
		added: list[str] = []
		rejected: list[str] = []
		duplicates: list[str] = []

		for name in names:
			if not accounts.directory_name_is_valid(name):
				rejected.append(name)
			elif name.lower() in existing:
				duplicates.append(name)
			else:
				existing.add(name.lower())
				self.accounts.append(name)
				added.append(name)

		if added:
			self._write_accounts()
			self.set_footer(
				f"Added {len(added)} account{'s' if len(added) != 1 else ''}. "
				"Sign in to each before a run.")

		if rejected:
			messagebox.showwarning("Add account",
				"Skipped names that cannot be folder names:\n\n" + "\n".join(rejected),
				parent=self.root)
		elif duplicates:
			messagebox.showinfo("Add account",
				"Already configured, so left alone:\n\n" + "\n".join(duplicates),
				parent=self.root)

		return added

	def remove_accounts(self) -> None:
		selected = self._selected_accounts()

		if not selected:
			messagebox.showinfo("Remove account", "Select an account to remove.",
				parent=self.root)
			return

		what = f"{len(selected)} accounts" if len(selected) > 1 else f"'{selected[0]}'"
		if not messagebox.askyesno("Remove account",
			f"Remove {what} from the list?\n\nThe profile director"
			f"{'ies' if len(selected) > 1 else 'y'} on disk "
			f"{'are' if len(selected) > 1 else 'is'} left where "
			f"{'they are' if len(selected) > 1 else 'it is'}.",
			parent=self.root):
			return

		self.accounts = [name for name in self.accounts if name not in selected]
		self._write_accounts()
		self.set_footer(f"Removed {what}.")

	def select_all_accounts(self) -> None:
		self.accounts_tree.selection_set(self.accounts_tree.get_children())

	def include_accounts(self) -> None:
		self._set_excluded(self._selected_accounts(), False)

	def exclude_accounts(self) -> None:
		self._set_excluded(self._selected_accounts(), True)

	def _toggle_row(self, event) -> None:
		"""Double-click: flip one row between farming and skipped."""
		item = self.accounts_tree.identify_row(event.y)

		if item:
			self._toggle_items([item])

	def _hover_row(self, event) -> None:
		"""Light the row under the pointer, the way a real list behaves."""
		self._set_row_hover(self.accounts_tree.identify_row(event.y) or None)

	def _set_row_hover(self, row: str | None) -> None:
		if row == self._hovered_row:
			return

		previous = self._hovered_row
		self._hovered_row = row

		if previous and self.accounts_tree.exists(previous):
			tags = [tag for tag in self.accounts_tree.item(previous, "tags")
				if tag != "hover"]
			self.accounts_tree.item(previous, tags=tags)

		if row and self.accounts_tree.exists(row):
			tags = list(self.accounts_tree.item(row, "tags"))
			if "hover" not in tags:
				self.accounts_tree.item(row, tags=tags + ["hover"])

	def _toggle_selected_rows(self, _event) -> str:
		"""Space: flip every selected row, then stop Tk's default handling."""
		self._toggle_items(self.accounts_tree.selection())
		return "break"

	def _toggle_items(self, items) -> None:
		names = [self.accounts_tree.item(item, "values")[0] for item in items]

		if not names:
			return

		# Flip each row on its own, so a mixed selection ends up mixed the
		# other way rather than all collapsing to one state.
		for name in names:
			if name in self.excluded:
				self.excluded.discard(name)
			else:
				self.excluded.add(name)

		self._save_exclusions()
		self.refresh_accounts()

	def _set_excluded(self, names: list[str], excluded: bool) -> None:
		"""Move the named accounts into or out of the exclude set, and save it."""
		if not names:
			messagebox.showinfo("Accounts",
				"Select one or more accounts first.", parent=self.root)
			return

		if excluded:
			self.excluded.update(names)
		else:
			self.excluded.difference_update(names)

		self._save_exclusions()
		self.refresh_accounts()

		verb = "Excluded" if excluded else "Included"
		count = len(names)
		self.set_footer(f"{verb} {count} account{'s' if count != 1 else ''}.")

	def _save_exclusions(self) -> None:
		# Written in the account list's order so the file stays readable.
		ordered = [name for name in self.accounts if name in self.excluded]
		self.env.update({EXCLUDE_ENV_KEY: ",".join(ordered)})

	def _write_accounts(self) -> None:
		self.env.update({"REWARDS_ACCOUNTS": ",".join(self.accounts)})
		# Keep the exclude list from keeping names that just left the accounts.
		self._save_exclusions()
		self.refresh_accounts()

	def _selected_accounts(self) -> list[str]:
		# selection() gives the tree's own item ids, not the names in them.
		return [
			self.accounts_tree.item(item, "values")[0]
			for item in self.accounts_tree.selection()
		]

	# endregion

	# region sign in

	def sign_in(self) -> None:
		selected = self._selected_accounts()

		if len(selected) != 1:
			messagebox.showinfo("Sign in", "Select exactly one account to sign in.",
				parent=self.root)
			return

		self.signin_active = True
		self._update_controls()
		self.set_status("running", f"Signing in to {selected[0]}...")
		self._show_view("Run")

		threading.Thread(target=self._sign_in_worker, args=(selected[0],),
			daemon=True).start()

	def _sign_in_worker(self, name: str) -> None:
		self.post(self.append_console, f"[gui] Opening Edge for '{name}' ...", "muted")

		try:
			self.reload_environment()
			account = self._account_object(name)
			driver = browser.start_driver(account)

			if driver is None:
				self.post(self.append_console,
					f"[gui] Could not start Edge for '{name}'. See the message above.",
					"error")
				return

			self.post(self.append_console,
				f"[gui] Sign in to '{name}' in the Edge window, then close it.", "muted")

			while True:
				try:
					driver.title
					time.sleep(0.5)
				except Exception:
					break

			try:
				driver.quit()
			except Exception:
				pass

			self.post(self.append_console, f"[gui] '{name}': browser closed.", "success")
		except Exception as exc:
			self.post(self.append_console, f"[gui] Sign in failed: {exc}", "error")
		finally:
			self.post(self._sign_in_finished)

	def _sign_in_finished(self) -> None:
		self.signin_active = False
		# The profile may have picked up a sign-in, so re-read the column.
		self.refresh_accounts()
		self._update_controls()
		self.set_status("idle", "Idle")

	def _account_object(self, name: str) -> accounts.Account:
		root = os.environ.get(accounts.DATA_DIR_ENV_VAR, DEFAULT_ROOT_DATA_DIR)

		return accounts.Account(
			name=name,
			user_data_dir=os.path.join(root, name),
			profile_name=accounts.PROFILE_NAME,
		)

	# endregion

	# region running

	def _run_environment(self) -> dict[str, str]:
		"""The environment for main.py: everything, with REWARDS_ACCOUNTS pinned.

		main.py loads `.env` without overriding, so the value set here is the
		one it works through, and the full list in the file is left alone. With
		no named accounts the variable is not set, so the default profile runs.
		"""
		environment = os.environ.copy()

		if self.accounts:
			environment[accounts.ACCOUNTS_ENV_VAR] = ",".join(self.farming)

		return environment

	def start_run(self) -> None:
		if self.process is not None:
			return

		if self.signin_active:
			messagebox.showinfo("Run", "A sign-in is in progress. Wait for it to finish.",
				parent=self.root)
			return

		# Pick up the file as it is now, including edits made outside this
		# window since it opened. main.py loads .env too, but not over values
		# already in the environment, and a sign-in may have put older ones
		# there.
		self.reload_environment()
		self.refresh_accounts()

		enabled = self.farming

		if self.accounts and not enabled:
			messagebox.showinfo("Run",
				"Every account is excluded. Include at least one on the Accounts tab.",
				parent=self.root)
			return

		self.clear_console()
		self.append_console("[gui] Starting rewards-farmer ...", "muted")
		self.stopping = False

		if self.accounts:
			self.append_console(
				f"[gui] Farming {len(enabled)} of {len(self.accounts)}: "
				f"{', '.join(enabled)}", "muted")
		else:
			self.append_console("[gui] Farming the default profile.", "muted")

		# main.py loads .env without overriding, so a REWARDS_ACCOUNTS put here
		# is the set it actually works through. The full list stays in the file.
		run_env = self._run_environment()

		command = [sys.executable, "-u", MAIN_SCRIPT]
		creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

		try:
			self.process = subprocess.Popen(
				command,
				env=run_env,
				cwd=REPO_ROOT,
				stdin=subprocess.DEVNULL,
				stdout=subprocess.PIPE,
				stderr=subprocess.STDOUT,
				text=True,
				encoding="utf-8",
				errors="replace",
				bufsize=1,
				creationflags=creationflags,
			)
		except OSError as exc:
			self.process = None
			self.append_console(f"[gui] Could not start main.py: {exc}", "error")
			messagebox.showerror("Run", f"Could not start the bot:\n{exc}", parent=self.root)
			return

		self.set_status("running", "Running...")
		self._update_controls()

		threading.Thread(target=self._read_output, args=(self.process,),
			daemon=True).start()

	def _read_output(self, process: subprocess.Popen) -> None:
		try:
			if process.stdout is not None:
				for line in process.stdout:
					self.post(self.append_console, line.rstrip("\n"))
		finally:
			code = process.wait()
			self.post(self._run_finished, code)

	def stop_run(self) -> None:
		if self.process is None:
			return

		self.stopping = True
		self.set_status("running", "Stopping...")
		self.stop_button.set_enabled(False)

		try:
			self._terminate(self.process)
		except Exception as exc:
			self.append_console(f"[gui] Could not stop the bot cleanly: {exc}", "error")

	def _terminate(self, process: subprocess.Popen) -> None:
		if os.name == "nt":
			subprocess.run(
				["taskkill", "/F", "/T", "/PID", str(process.pid)],
				stdout=subprocess.DEVNULL,
				stderr=subprocess.DEVNULL,
				creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
			)
		else:
			process.terminate()

	def _run_finished(self, code: int) -> None:
		self.process = None

		if self.stopping:
			self.stopping = False
			self.append_console("[gui] Stopped.", "muted")
			self.set_status("idle", "Stopped")
		elif code == 0:
			self.append_console("[gui] Finished.", "success")
			self.set_status("success", "Finished")
		else:
			self.append_console(f"[gui] Exited with code {code}.", "error")
			self.set_status("error", f"Exited with code {code}")

		self._update_controls()

	# endregion

	# region console and status

	def append_console(self, line: str, tag: str | None = None) -> None:
		if tag is None:
			tag = self._tag_for(line)

		self.console.configure(state="normal")
		self.console.insert("end", line + "\n", tag)

		# Trim the oldest lines so a long or chatty run cannot grow without end.
		total = int(self.console.index("end-1c").split(".")[0])
		if total > MAX_CONSOLE_LINES:
			self.console.delete("1.0", f"{total - MAX_CONSOLE_LINES}.0")

		self.console.see("end")
		self.console.configure(state="disabled")

	@staticmethod
	def _tag_for(line: str) -> str | None:
		if "[gui]" in line:
			return "muted"
		if "[FAIL]" in line or " ERROR " in f" {line} " or " CRITICAL " in f" {line} ":
			return "error"
		if " WARNING " in f" {line} ":
			return "warning"
		return None

	def clear_console(self) -> None:
		self.console.configure(state="normal")
		self.console.delete("1.0", "end")
		self.console.configure(state="disabled")

	def set_status(self, kind: str, text: str) -> None:
		self._status = (kind, text)
		self._draw_status()

	def set_footer(self, text: str) -> None:
		self.footer_label.configure(text=text)

	def _update_controls(self) -> None:
		running = self.process is not None

		self.run_button.set_enabled(not (running or self.signin_active))
		self.stop_button.set_enabled(running)
		self.signin_button.set_enabled(not (running or self.signin_active))

	# endregion

	# region plumbing

	def _install_log_handler(self) -> None:
		handler = TkLogHandler(lambda line: self.post(self.append_console, line))
		handler.setLevel(logging.INFO)

		root_logger = logging.getLogger()
		root_logger.setLevel(logging.INFO)
		root_logger.addHandler(handler)

		for name in log_utils.NOISY_LIBRARIES:
			logging.getLogger(name).setLevel(logging.WARNING)

	def post(self, callback, *args) -> None:
		"""Queue work for the main thread; tkinter is not safe to touch off it."""
		self.queue.put((callback, args))

	def _drain_queue(self) -> None:
		while True:
			try:
				callback, args = self.queue.get_nowait()
			except queue.Empty:
				break

			try:
				callback(*args)
			except Exception:
				logging.getLogger(__name__).exception("GUI callback failed")

		self.root.after(80, self._drain_queue)

	def on_close(self) -> None:
		if self.process is not None:
			if not messagebox.askyesno("Quit", "A run is in progress. Stop it and quit?",
				parent=self.root):
				return

			try:
				self._terminate(self.process)
			except Exception:
				pass

		self.root.destroy()

	# endregion


def _report_failure(message: str) -> None:
	"""Surface a crash that `pythonw` would otherwise swallow.

	The launcher starts this file with no console, so an exception on the way
	up looks like nothing happening at all. A dialog says what went wrong
	where a traceback would have gone, and the same text goes to a log beside
	the project for when even the dialog cannot open.
	"""

	try:
		messagebox.showerror("Rewards Farmer", message)
	except Exception:
		pass

	try:
		with open(os.path.join(REPO_ROOT, "gui-error.log"), "a",
			encoding="utf-8") as handle:
			handle.write(message.rstrip() + "\n")
	except OSError:
		pass


def _enable_dpi_awareness() -> None:
	"""Ask Windows for crisp text; harmless where the call is missing."""
	if sys.platform != "win32":
		return
	try:
		import ctypes
		ctypes.windll.shcore.SetProcessDpiAwareness(1)
	except Exception:
		pass


def main() -> int:
	_enable_dpi_awareness()

	try:
		root = tk.Tk()
	except Exception:
		_report_failure("Could not open a window:\n\n" + traceback.format_exc())
		return 1

	# An exception inside a callback is otherwise invisible too: pythonw has no
	# console for Tk's default handler to print to.
	root.report_callback_exception = lambda *exc: _report_failure(
		"".join(traceback.format_exception(*exc)))

	try:
		ManagerApp(root)
	except Exception:
		_report_failure("Rewards Farmer could not start:\n\n" + traceback.format_exc())
		return 1

	root.mainloop()

	return 0


if __name__ == "__main__":
	sys.exit(main())
