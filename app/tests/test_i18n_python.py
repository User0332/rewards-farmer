import _bootstrap  # noqa: F401

import ctypes
import json
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rfapp import i18n, prefs, winfmt
from tools.i18n_report import report, source_hash


def _samples(rule):
	for section in rule.split("@")[1:]:
		_, values = section.split(" ", 1)
		for token in values.split(","):
			token = token.strip()
			if not token or token == "\u2026":
				continue
			if "~" not in token:
				yield token if "c" in token else Decimal(token)
				continue
			start, end = map(Decimal, token.split("~"))
			step = Decimal(1).scaleb(min(start.as_tuple().exponent, end.as_tuple().exponent))
			value = start
			while value <= end:
				yield value
				value += step


class PluralTests(unittest.TestCase):
	def test_every_cldr_integer_decimal_and_compact_sample(self):
		count = 0
		locales = set()
		for locale, rules in i18n._plural_data().items():
			locales.add(locale)
			for rule_key, rule in rules.items():
				category = rule_key.removeprefix("pluralRule-count-")
				for sample in _samples(rule):
					with self.subTest(locale=locale, category=category, sample=str(sample)):
						self.assertEqual(category, i18n.plural_category(locale, sample))
					count += 1
		self.assertTrue({"en", "de", "fr", "ru", "pl", "ar", "ja", "cy"} <= locales)
		self.assertGreater(count, 10000)

	def test_float_operands_match_js_and_decimal_preserves_visible_zeros(self):
		self.assertEqual("one", i18n.plural_category("en", 1.0))
		self.assertEqual("other", i18n.plural_category("en", Decimal("1.0")))
		self.assertEqual("one", i18n.plural_category("en", -1))
		self.assertEqual({"n": Decimal("12.3400"), "i": 12, "v": 4, "w": 2, "f": 3400, "t": 34, "e": 0, "c": 0}, i18n._operands(Decimal("12.3400")))
		self.assertEqual(6, i18n._operands("1.1c6")["e"])

	def test_rules_support_all_relations_and_precedence(self):
		cases = [
			("n in 2..4", "2.5", False),
			("n within 2..4", "2.5", True),
			("n not within 2..4", "2.5", False),
			("n not in 2..4", "2.5", True),
			("n != 2..4", "2.5", True),
			("i mod 10 = 1,3,5", "23", True),
			("n is 1", "1", True),
			("n is not 1", "1", False),
			("n = 1 or n = 2 and v = 0", "1.0", True),
			("n = 1 or n = 2 and v = 0", "2.0", False),
		]
		for rule, number, expected in cases:
			with self.subTest(rule=rule):
				self.assertEqual(expected, i18n._rule_matches(rule, i18n._operands(Decimal(number))))
		with self.assertRaises(ValueError):
			i18n._compile_rule("n == 1")

	def test_locale_exact_base_and_root_fallback(self):
		self.assertEqual("few", i18n.plural_category("ru-RU", 3))
		self.assertEqual("one", i18n.plural_category("DE-at", 1))
		self.assertEqual("other", i18n.plural_category("not-shipped", 1))
		for n in (float("inf"), float("nan")):
			with self.assertRaises(ValueError):
				i18n.plural_category("en", n)

	def test_vendored_version_and_license(self):
		data = json.loads(i18n.paths.resource("rfapp", "cldr_plurals.json").read_text(encoding="utf-8"))
		self.assertEqual("48", data["supplemental"]["version"]["_cldrVersion"])
		self.assertEqual("48.0.0", data["_meta"]["cldr_version"])
		self.assertEqual("Unicode-3.0", data["_meta"]["license"])
		self.assertIn("COPYRIGHT AND PERMISSION NOTICE", data["_meta"]["license_text"])
		self.assertIn("Permission is hereby granted", data["_meta"]["license_text"])


class LanguageTests(unittest.TestCase):
	def setUp(self):
		self.index = {"source": "en", "namespaces": [], "locales": [{"code": code, "dir": "rtl" if code == "ar" else "ltr"} for code in ("en", "de", "de-AT", "zh-Hant", "zh-Hans", "ar")]}
		self.addCleanup(patch.stopall)
		patch.object(i18n, "_read_index", return_value=self.index).start()
		patch.object(i18n, "_format_locale", return_value="de-AT").start()
		self.language_patch = patch.object(i18n, "_preferred_languages", return_value=["de-AT", "en-US"])
		self.languages = self.language_patch.start()

	def test_override_preserves_windows_region(self):
		self.assertEqual(("ar", "rtl", "de-AT"), i18n.resolve("ar", ["en", "de", "ar"]))
		self.languages.assert_not_called()

	def test_exact_case_insensitive_then_base(self):
		self.assertEqual(("de", "ltr", "de-AT"), i18n.resolve("system", ["en", "de"]))
		self.languages.return_value = ["DE-at"]
		self.assertEqual(("de-AT", "ltr", "de-AT"), i18n.resolve("system", ["en", "de", "de-AT"]))

	def test_chinese_script_and_region_rules(self):
		for tag in ("zh-TW", "zh-HK", "zh-MO", "zh-Hant", "zh-Hant-HK"):
			with self.subTest(tag=tag):
				self.languages.return_value = [tag]
				self.assertEqual("zh-Hant", i18n.resolve("system", ["en", "zh-Hans", "zh-Hant"])[0])
		for tag in ("zh", "zh-CN", "zh-SG", "zh-Hans-TW"):
			with self.subTest(tag=tag):
				self.languages.return_value = [tag]
				self.assertEqual("zh-Hans", i18n.resolve("system", ["en", "zh-Hans", "zh-Hant"])[0])

	def test_language_order_and_no_match(self):
		self.languages.return_value = ["unknown-X", "ar-SA", "de-AT"]
		self.assertEqual("ar", i18n.resolve("system", ["en", "de", "ar"])[0])
		self.languages.return_value = ["zz-ZZ"]
		self.assertEqual(("en", "ltr", "de-AT"), i18n.resolve("not-shipped", ["en", "de"]))

	def test_same_base_portuguese_precedes_the_next_windows_language(self):
		for tags in (["pt-PT"], ["pt-AO"], ["PT_pt"], ["pt-PT", "en-US"], ["pt-AO", "en-US"]):
			with self.subTest(tags=tags):
				self.languages.return_value = tags
				self.assertEqual(("pt-BR", "ltr", "de-AT"), i18n.resolve("system", ["en", "en-US", "pt-BR"]))

	def test_exact_and_bare_base_win_before_a_registered_variant(self):
		self.languages.return_value = ["pt-PT", "en-US"]
		self.assertEqual("pt-PT", i18n.resolve("system", ["en", "pt-BR", "pt", "pt-PT"])[0])
		self.assertEqual("pt", i18n.resolve("system", ["en", "pt-BR", "pt"])[0])
		self.assertEqual("pt-BR", i18n.resolve("system", ["en", "pt-BR", "pt-AO"])[0])
		self.assertEqual("pt-AO", i18n.resolve("system", ["en", "pt-AO", "pt-BR"])[0])

	def test_same_base_variant_is_case_insensitive_and_not_a_prefix_match(self):
		self.languages.return_value = ["de-AT", "en-US"]
		self.assertEqual("de-DE", i18n.resolve("system", ["en", "de-DE"])[0])
		self.languages.return_value = ["PT-ao", "en-US"]
		self.assertEqual("pt-br", i18n.resolve("system", ["en-US", "pt-br"])[0])
		self.languages.return_value = ["pt-PT", "en-US"]
		self.assertEqual("en-US", i18n.resolve("system", ["en-US", "ptx-BR"])[0])

	def test_chinese_script_does_not_cross_when_no_script_match_is_registered(self):
		for tag in ("zh-TW", "zh-HK", "zh-MO", "zh-Hant", "zh-Hant-HK"):
			with self.subTest(tag=tag):
				self.languages.return_value = [tag, "de-AT", "en-US"]
				self.assertEqual("de", i18n.resolve("system", ["en", "zh-Hans", "de"])[0])
				self.languages.return_value = [tag, "en-US"]
				self.assertEqual("en-US", i18n.resolve("system", ["en-US", "zh-Hans"])[0])
				self.languages.return_value = [tag]
				self.assertEqual("en", i18n.resolve("system", ["en", "zh-Hans"])[0])
		for tag in ("zh", "zh-CN", "zh-SG", "zh-Hans-TW"):
			with self.subTest(tag=tag):
				self.languages.return_value = [tag, "pt-PT"]
				self.assertEqual("pt-BR", i18n.resolve("system", ["en", "zh-Hant", "pt-BR"])[0])

	def test_chinese_exact_and_script_matches_keep_priority(self):
		self.languages.return_value = ["ZH-tw", "en-US"]
		self.assertEqual("zh-TW", i18n.resolve("system", ["en-US", "zh-Hans", "zh-Hant", "zh-TW"])[0])
		self.assertEqual("zh-Hant", i18n.resolve("system", ["en-US", "zh-Hans", "zh-Hant"])[0])
		self.languages.return_value = ["zh-CN", "en-US"]
		self.assertEqual("zh-Hans", i18n.resolve("system", ["en-US", "zh-Hant", "zh-Hans"])[0])

	def test_current_is_cached_reload_replaces_it(self):
		with patch.object(i18n, "_current", None), patch.object(prefs, "load", return_value={"language": "en"}) as load:
			first = i18n.current()
			self.assertIs(first, i18n.current())
			self.assertEqual(1, load.call_count)
			load.return_value = {"language": "ar"}
			self.assertIs(first, i18n.current())
			second = i18n.reload()
			self.assertIs(second, i18n.current())
			self.assertIsNot(first, second)
			self.assertEqual(("ar", "rtl", "de-AT"), (second.lang, second.dir, second.format_locale))

	def test_off_windows_language_metadata_fallbacks(self):
		with patch.object(i18n.sys, "platform", "linux"):
			self.language_patch.stop()
			self.assertEqual(["en"], i18n._preferred_languages())
			self.assertEqual("en-US", LanguageTests.region_function())

	@unittest.skipUnless(i18n.sys.platform == "win32", "Windows language APIs")
	def test_actual_windows_apis_return_language_tags(self):
		# Reading locale metadata cannot change Windows or Edge language settings.
		self.language_patch.stop()
		with patch.object(i18n, "_read_index", wraps=lambda: self.index):
			self.assertTrue(i18n._preferred_languages())
			self.assertTrue(all(isinstance(tag, str) and tag for tag in i18n._preferred_languages()))

	@unittest.skipUnless(i18n.sys.platform == "win32", "Windows regional API")
	def test_actual_windows_region_is_read_without_preferences(self):
		function = ctypes.WinDLL("kernel32", use_last_error=True).GetUserDefaultLocaleName
		function.argtypes = [ctypes.c_wchar_p, ctypes.c_int]
		function.restype = ctypes.c_int
		buffer = ctypes.create_unicode_buffer(85)
		self.assertGreater(function(buffer, len(buffer)), 0)
		# resolve's selected text does not overwrite the independently read region.
		with patch.object(i18n, "_format_locale", wraps=LanguageTests.region_function):
			self.assertEqual(buffer.value, i18n.resolve("ar", ["en", "ar"])[2])

	region_function = staticmethod(i18n._format_locale)


class RegisteredLanguageTests(unittest.TestCase):
	def test_each_registered_locale_is_resolved_from_windows_tags(self):
		index = i18n._read_index()
		available = [entry["code"] for entry in index["locales"]]
		self.assertEqual(9, len(available))
		for locale, tags in {
			"en": ("en-US",), "de": ("de-DE", "de-AT"), "es": ("es-ES", "es-MX"),
			"fr": ("fr-FR", "fr-CA"), "it": ("it-IT",), "pt-BR": ("pt-BR", "pt-PT", "pt-AO"),
			"ja": ("ja-JP",), "zh-Hans": ("zh-Hans", "zh-CN", "zh-SG"), "ar": ("ar-SA", "ar-EG"),
		}.items():
			for tag in tags:
				with self.subTest(locale=locale, tag=tag), patch.object(i18n, "_preferred_languages", return_value=[tag, "en-US"]), patch.object(i18n, "_format_locale", return_value="de-AT"):
					self.assertEqual((locale, "rtl" if locale == "ar" else "ltr", "de-AT"), i18n.resolve("system", available))

	def test_app_info_exposes_the_registered_index_with_real_resolve(self):
		from rfapp.bridge import Bridge
		index = i18n._read_index()
		with tempfile.TemporaryDirectory(prefix="rf-i18n-registration-") as temp:
			home = Path(temp)
			bridge = Bridge(dependencies={"prefs": SimpleNamespace(load=lambda: {"language": "system", "setup_done": True}), "accounts_admin": SimpleNamespace(list_accounts=lambda: {"accounts": []}), "i18n": i18n})
			try:
				with patch.object(i18n, "_preferred_languages", return_value=["ar-SA", "en-US"]), patch.object(i18n, "_format_locale", return_value="de-AT"), patch("rfapp.bridge.paths.data_home", return_value=home), patch("rfapp.bridge.paths.env_path", return_value=home / ".env"), patch("rfapp.window._theme", return_value="dark"), patch("rfapp.window._reduced_motion", return_value=True):
					result = bridge.app_info({})
				self.assertTrue(result["ok"], result)
				self.assertEqual(index["locales"], result["data"]["locales"])
				self.assertEqual(9, len(result["data"]["locales"]))
				self.assertEqual(("ar", "rtl", "de-AT"), (result["data"]["lang"], result["data"]["dir"], result["data"]["format_locale"]))
			finally:
				bridge._close()


class TranslatorTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory(prefix="rf-i18n-python-")
		self.addCleanup(self.temp.cleanup)
		self.ui = Path(self.temp.name)
		self.root = self.ui / "i18n"
		self.root.mkdir()
		self.index = {"source": "en", "namespaces": ["sample"], "locales": [{"code": code, "name": code, "dir": "rtl" if code == "ar" else "ltr"} for code in ("en", "ru", "ar")]}
		(self.root / "index.json").write_text(json.dumps(self.index), encoding="utf-8")
		self.source = {"sample.title": {"text": "Hello {account}", "note": "Test account label."}, "sample.points": {"plural": "count", "forms": {"one": "{count} point for {account}", "other": "{count} points for {account}"}, "note": "count numeric; account label."}}
		self.save("en", self.source)
		self.addCleanup(patch.stopall)
		patch.object(i18n.paths, "ui_dir", return_value=self.ui).start()

	def save(self, locale, messages, namespace="sample"):
		folder = self.root / locale
		folder.mkdir(exist_ok=True)
		(folder / f"{namespace}.json").write_text(json.dumps({"namespace": namespace, "messages": messages}, ensure_ascii=False), encoding="utf-8")

	def test_english_numbers_and_fallback(self):
		translator = i18n.Translator("en", "de-DE")
		self.assertEqual("Hello Ada", translator.t("sample.title", account="Ada"))
		self.assertEqual("1 point for Ada", translator.t("sample.points", count=1, account="Ada"))
		self.assertEqual(winfmt.format_int(2300, "de-DE") + " points for Ada", translator.t("sample.points", count=2300, account="Ada"))
		with self.assertLogs("rfapp", "ERROR"):
			self.assertEqual("unknown.key", translator.t("unknown.key"))

	def test_russian_three_integer_forms_and_decimal_other(self):
		self.save("ru", {"sample.points": {"plural": "count", "forms": {category: category + " {count} {account}" for category in ("one", "few", "many", "other")}, "src": "00000000"}})
		translator = i18n.Translator("ru", "en-US")
		for count, category in ((1, "one"), (2, "few"), (5, "many")):
			self.assertEqual(f"{category} {count} Ada", translator.t("sample.points", count=count, account="Ada"))
		self.assertEqual("Hello Ada", translator.t("sample.title", account="Ada"))
		self.assertEqual("other", i18n.plural_category("ru", 1.5))

	def test_arabic_forms_and_all_insertions_isolated(self):
		self.save("ar", {"sample.points": {"plural": "count", "forms": {category: category + " {count} {account}" for category in i18n._categories}, "src": "00000000"}})
		translator = i18n.Translator("ar", "en-US")
		for count, category in ((0, "zero"), (1, "one"), (2, "two"), (3, "few"), (11, "many"), (100, "other")):
			self.assertEqual(f"{category} \u2068{count}\u2069 \u2068Ada\u2069", translator.t("sample.points", count=count, account="Ada"))
		self.assertEqual("Hello \u2068Ada\u2069", translator.t("sample.title", account="Ada"))

	def test_missing_plural_form_uses_other_and_driver_may_be_omitted(self):
		self.save("ru", {"sample.points": {"plural": "count", "forms": {"other": "Some points for {account}"}, "src": "00000000"}})
		self.assertEqual("Some points for Ada", i18n.Translator("ru", "en-US").t("sample.points", count=2, account="Ada"))

	def test_missing_plural_key_falls_back_to_english_grammar(self):
		self.save("ru", {})
		self.assertEqual("21 points for Ada", i18n.Translator("ru", "en-US").t("sample.points", count=21, account="Ada"))

	def test_path_traversal_locale_is_never_loaded(self):
		self.assertEqual("en", i18n.Translator("../../outside", "en-US").lang)

	def test_time_delegates_to_windows_regional_formatter(self):
		translator = i18n.Translator("en", "de-DE")
		value = datetime(2026, 10, 14, 9, 45)
		with patch.object(winfmt, "format_time", return_value="09:45") as formatter:
			self.assertEqual("09:45", translator.time(value))
			formatter.assert_called_once_with(value, "de-DE")

	def test_report_hash_missing_stale_and_no_note_dependency(self):
		entry = self.source["sample.title"]
		changed_note = {**entry, "note": "Revised guidance"}
		self.assertEqual(source_hash(entry), source_hash(changed_note))
		self.assertNotEqual(source_hash(entry), source_hash({**entry, "text": "New {account}"}))
		self.save("ru", {"sample.title": {"text": "Hello {account}", "src": "00000000"}})
		self.assertEqual({"locale": "ru", "missing": ["sample.points"], "stale": ["sample.title"], "plural_categories": ["one", "few", "many", "other"], "dir": "ltr"}, report("ru", self.root))
		self.assertEqual([], report("en", self.root)["missing"])
		self.assertEqual([], report("en", self.root)["stale"])
		self.assertEqual("rtl", report("ar", self.root)["dir"])
		with self.assertRaises(ValueError):
			report("../bad", self.root)

	def shell_fixture(self, translated):
		self.index["namespaces"].append("shell")
		(self.root / "index.json").write_text(json.dumps(self.index), encoding="utf-8")
		source = {
			"shell.mock.example": {"text": "English mock {account}", "note": "Synthetic mock label; account is a test label."},
			"shell.demo.example": {"text": "English demo", "note": "Synthetic demonstration label. No placeholders."},
			"shell.real_label": {"text": "English label", "note": "Synthetic real UI label. No placeholders."},
		}
		self.save("en", source, "shell")
		self.save("ru", translated, "shell")
		self.save("ru", {key: {**{field: value for field, value in entry.items() if field != "note"}, "src": source_hash(entry)} for key, entry in self.source.items()})
		return source

	def test_report_omits_missing_mock_and_demo_but_lists_real_key(self):
		self.shell_fixture({})
		result = report("ru", self.root)
		self.assertEqual(["shell.real_label"], result["missing"])
		self.assertEqual([], result["stale"])

	def test_report_omits_stale_mock_and_demo_but_lists_real_key(self):
		self.shell_fixture({key: {"text": "Old translation", "src": "00000000"} for key in ("shell.mock.example", "shell.demo.example", "shell.real_label")})
		result = report("ru", self.root)
		self.assertEqual([], result["missing"])
		self.assertEqual(["shell.real_label"], result["stale"])

	def test_python_missing_mock_demo_and_real_keys_use_english_source(self):
		self.shell_fixture({"shell.real_label": {"text": "Target label", "src": "00000000"}})
		translator = i18n.Translator("ru", "en-US")
		self.assertEqual("English mock Ada", translator.t("shell.mock.example", account="Ada"))
		self.assertEqual("English demo", translator.t("shell.demo.example"))
		self.assertEqual("Target label", translator.t("shell.real_label"))
		self.assertEqual("Hello Ada", translator.t("sample.title", account="Ada"))

	def test_report_prefix_exclusion_keeps_similarly_named_real_keys(self):
		self.shell_fixture({})
		self.save("en", {"shell.mockery_label": {"text": "Real mockery label", "note": "Synthetic real label. No placeholders."}, "shell.demo_label": {"text": "Real demo label", "note": "Synthetic real label. No placeholders."}}, "shell")
		self.assertEqual(["shell.demo_label", "shell.mockery_label"], report("ru", self.root)["missing"])


class WindowsFormatTests(unittest.TestCase):
	def test_off_windows_fallback(self):
		with patch.object(winfmt.sys, "platform", "linux"):
			self.assertEqual("-1,234,567", winfmt.format_int(-1234567, "de-DE"))
			self.assertEqual("09:45", winfmt.format_time(datetime(2026, 10, 14, 9, 45), "en-US"))

	def test_grouping_repeat_stop_and_none(self):
		for grouping, expected in (("3;0", "12,345,678"), ("3;2;0", "1,23,45,678"), ("3", "12345,678"), ("0", "12345678")):
			self.assertEqual(expected, winfmt._group_digits("12345678", ",", grouping))

	def test_negative_sign_and_grouping_from_nls(self):
		values = {winfmt.LOCALE_STHOUSAND: ".", winfmt.LOCALE_SGROUPING: "3;0", winfmt.LOCALE_SNEGATIVESIGN: "minus "}
		with patch.object(winfmt.sys, "platform", "win32"), patch.object(winfmt, "_locale_info", side_effect=lambda locale, field: values[field]):
			self.assertEqual("minus 1.234.567", winfmt.format_int(-1234567, "de-DE"))

	def test_nls_failure_keeps_useful_fallback(self):
		with patch.object(winfmt.sys, "platform", "win32"), patch.object(winfmt, "_kernel32", side_effect=OSError("synthetic NLS failure")), self.assertLogs("rfapp", "WARNING"):
			self.assertEqual("1,234", winfmt.format_int(1234, "unknown"))
			self.assertEqual("09:45", winfmt.format_time(datetime(2026, 10, 14, 9, 45), "unknown"))

	@unittest.skipUnless(i18n.sys.platform == "win32", "Windows NLS APIs")
	def test_actual_windows_nls(self):
		self.assertEqual("1.234.567", winfmt.format_int(1234567, "de-DE"))
		self.assertEqual("1,234,567", winfmt.format_int(1234567, "en-US"))
		self.assertEqual("1,23,45,678", winfmt.format_int(12345678, "hi-IN"))
		self.assertEqual("-1.234", winfmt.format_int(-1234, "de-DE"))
		self.assertEqual("09:45", winfmt.format_time(datetime(2026, 10, 14, 9, 45, 37), "de-DE"))
		self.assertIn("AM", winfmt.format_time(datetime(2026, 10, 14, 9, 45, 37), "en-US"))
