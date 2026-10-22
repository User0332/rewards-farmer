"""Catalog translation, Windows language selection and CLDR cardinal rules."""

import ctypes
import json
import logging
import math
import re
import sys
import threading
from ctypes import wintypes
from datetime import datetime
from decimal import Decimal
from functools import lru_cache

from rfapp import paths, winfmt


_logger = logging.getLogger("rfapp")
_placeholder = re.compile(r"\{([a-z][a-z0-9_]*)\}")
_rtl_languages = frozenset(("ar", "he", "fa", "ur", "ps", "yi", "dv"))
_categories = ("zero", "one", "two", "few", "many", "other")
_lock = threading.RLock()
_current = None


def _read_index() -> dict:
	return json.loads((paths.ui_dir() / "i18n" / "index.json").read_text(encoding="utf-8"))


def _direction(lang: str, index: dict) -> str:
	for locale in index["locales"]:
		if locale["code"].lower() == lang.lower():
			return locale["dir"]
	return "rtl" if lang.split("-")[0].lower() in _rtl_languages else "ltr"


def _preferred_languages() -> list[str]:
	if sys.platform != "win32":
		return ["en"]
	try:
		function = ctypes.WinDLL("kernel32", use_last_error=True).GetUserPreferredUILanguages
		function.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.ULONG), wintypes.LPWSTR, ctypes.POINTER(wintypes.ULONG)]
		function.restype = wintypes.BOOL
		count, size = wintypes.ULONG(), wintypes.ULONG()
		if not function(8, ctypes.byref(count), None, ctypes.byref(size)):
			raise ctypes.WinError(ctypes.get_last_error())
		buffer = ctypes.create_unicode_buffer(size.value)
		if not function(8, ctypes.byref(count), buffer, ctypes.byref(size)):
			raise ctypes.WinError(ctypes.get_last_error())
		return [tag for tag in buffer[:size.value].split("\0") if tag]
	except OSError:
		_logger.warning("Could not read Windows UI languages", exc_info=True)
		return ["en"]


def _format_locale() -> str:
	if sys.platform != "win32":
		return "en-US"
	try:
		function = ctypes.WinDLL("kernel32", use_last_error=True).GetUserDefaultLocaleName
		function.argtypes = [wintypes.LPWSTR, ctypes.c_int]
		function.restype = ctypes.c_int
		buffer = ctypes.create_unicode_buffer(85)
		if not function(buffer, len(buffer)):
			raise ctypes.WinError(ctypes.get_last_error())
		return buffer.value
	except OSError:
		_logger.warning("Could not read Windows regional format", exc_info=True)
		return "en-US"


def resolve(pref: str, available: list[str]) -> tuple[str, str, str]:
	index = _read_index()
	choices = {code.lower(): code for code in available}
	selected = pref if pref != "system" and pref in available else None
	if selected is None:
		for tag in _preferred_languages():
			tag = tag.replace("_", "-").lower()
			base = tag.split("-")[0]
			candidates = [tag]
			if base == "zh":
				traditional = tag in ("zh-tw", "zh-hk", "zh-mo") or tag.startswith("zh-hant")
				candidates.append("zh-hant" if traditional else "zh-hans")
			candidates.append(base)
			selected = next((choices[candidate] for candidate in candidates if candidate in choices), None)
			if selected is None and base != "zh":
				# Keep Windows preference order, but never substitute another Chinese script.
				selected = next((code for code in available if code.lower().split("-")[0] == base), None)
			if selected is not None:
				break
	selected = selected or "en"
	return selected, _direction(selected, index), _format_locale()


@lru_cache(maxsize=1)
def _plural_data() -> dict:
	return json.loads(paths.resource("rfapp", "cldr_plurals.json").read_text(encoding="utf-8"))["supplemental"]["plurals-type-cardinal"]


def _rules_for(lang: str) -> dict:
	data = _plural_data()
	lookup = {key.lower(): value for key, value in data.items()}
	tag = lang.replace("_", "-").lower()
	return lookup.get(tag, lookup.get(tag.split("-")[0], lookup.get("root", lookup.get("und", {"pluralRule-count-other": ""}))))


def _operands(value) -> dict:
	compact = re.fullmatch(r"(-?\d+(?:\.\d+)?)[ce](\d+)", str(value))
	exponent = int(compact[2]) if compact else 0
	if compact:
		number = abs(Decimal(compact[1]).scaleb(exponent))
	elif isinstance(value, float):
		# JS numbers have no visible trailing zeros; Decimal retains CLDR sample precision.
		number = Decimal(str(int(value))) if math.isfinite(value) and value.is_integer() else Decimal(str(value))
	else:
		number = abs(Decimal(str(value)))
	number = abs(number)
	if not number.is_finite():
		raise ValueError("A plural count must be finite")
	whole, _, fraction = format(number, "f").partition(".")
	trimmed = fraction.rstrip("0")
	return {"n": number, "i": int(whole), "v": len(fraction), "w": len(trimmed), "f": int(fraction or "0"), "t": int(trimmed or "0"), "e": exponent, "c": exponent}


@lru_cache(maxsize=256)
def _compile_rule(rule: str) -> tuple:
	expression = rule.split("@", 1)[0].strip()
	if not expression:
		return ()
	alternatives = []
	for alternative in re.split(r"\s+or\s+", expression):
		relations = []
		for relation in re.split(r"\s+and\s+", alternative):
			match = re.fullmatch(r"([nivwftec])(?:\s*(?:%|mod)\s*(\d+))?\s*(not\s+within|not\s+in|within|in|!=|=|is\s+not|is)\s*(.+)", relation.strip())
			if match is None:
				raise ValueError(f"Invalid CLDR relation: {relation}")
			operand, modulus, operator, values = match.groups()
			ranges = []
			for item in values.split(","):
				bounds = item.strip().split("..")
				if len(bounds) not in (1, 2) or not all(re.fullmatch(r"\d+(?:\.\d+)?", bound) for bound in bounds):
					raise ValueError(f"Invalid CLDR range: {item}")
				ranges.append((Decimal(bounds[0]), Decimal(bounds[-1])))
			relations.append((operand, int(modulus) if modulus else None, " ".join(operator.split()), tuple(ranges)))
		alternatives.append(tuple(relations))
	return tuple(alternatives)


def _rule_matches(rule: str, operands: dict) -> bool:
	compiled = _compile_rule(rule)
	if not compiled:
		return True
	for alternative in compiled:
		matched = True
		for operand, modulus, operator, ranges in alternative:
			value = Decimal(operands[operand])
			if modulus is not None:
				value %= modulus
			within = "within" in operator
			included = (within or value == value.to_integral_value()) and any(low <= value <= high for low, high in ranges)
			if operator in ("!=", "not in", "not within", "is not"):
				included = not included
			if not included:
				matched = False
				break
		if matched:
			return True
	return False


def plural_category(lang: str, n: int | float) -> str:
	operands = _operands(n)
	rules = _rules_for(lang)
	for category in _categories[:-1]:
		if (rule := rules.get("pluralRule-count-" + category)) is not None and _rule_matches(rule, operands):
			return category
	return "other"


class Translator:
	def __init__(self, lang: str, format_locale: str) -> None:
		index = _read_index()
		# Only shipped locale codes can become filesystem paths, including direct callers.
		self.lang = next((entry["code"] for entry in index["locales"] if entry["code"].lower() == lang.lower()), index["source"])
		self.dir = _direction(self.lang, index)
		self.format_locale = format_locale
		self._source = self._load(index["source"], index["namespaces"])
		self._messages = self._source if self.lang == index["source"] else self._load(self.lang, index["namespaces"])

	def _load(self, locale: str, namespaces: list[str]) -> dict:
		messages = {}
		for namespace in namespaces:
			path = paths.ui_dir() / "i18n" / locale / f"{namespace}.json"
			try:
				catalog = json.loads(path.read_text(encoding="utf-8"))
				if catalog["namespace"] != namespace or not isinstance(catalog["messages"], dict):
					raise ValueError("Invalid catalog structure")
				messages.update(catalog["messages"])
			except FileNotFoundError:
				continue
			except (OSError, ValueError, KeyError):
				_logger.exception("Could not load catalog %s/%s", locale, namespace)
		return messages

	def t(self, key: str, **params) -> str:
		translated = key in self._messages
		entry = self._messages.get(key) if translated else self._source.get(key)
		if entry is None:
			_logger.error("Missing i18n key: %s", key)
			return key
		text = entry.get("text")
		if "forms" in entry:
			driver = entry["plural"]
			# English fallback text must not inherit a target's different singular category.
			category = plural_category(self.lang if translated else "en", params[driver])
			text = entry["forms"].get(category, entry["forms"].get("other"))
			if text is None:
				fallback = self._source.get(key, {}).get("forms", {})
				text = fallback.get(plural_category("en", params[driver]), fallback.get("other"))
			params[driver] = self.number(params[driver])
		if not isinstance(text, str):
			_logger.error("Invalid i18n entry: %s", key)
			return key
		def insert(match):
			name = match[1]
			if name not in params:
				_logger.error("Missing i18n parameter: %s.{%s}", key, name)
				return match[0]
			value = str(params[name])
			return "\u2068" + value + "\u2069" if self.dir == "rtl" else value
		return _placeholder.sub(insert, text)

	def number(self, n: int) -> str:
		return winfmt.format_int(n, self.format_locale)

	def time(self, dt: datetime) -> str:
		return winfmt.format_time(dt, self.format_locale)


def reload() -> Translator:
	from rfapp import prefs
	global _current
	with _lock:
		index = _read_index()
		lang, _, region = resolve(prefs.load().get("language", "system"), [entry["code"] for entry in index["locales"]])
		_current = Translator(lang, region)
		return _current


def current() -> Translator:
	with _lock:
		return _current if _current is not None else reload()
