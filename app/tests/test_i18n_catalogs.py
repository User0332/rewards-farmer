import _bootstrap  # noqa: F401

import json
import re
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from rfapp import i18n
from tools.i18n_report import source_hash


APP = Path(__file__).resolve().parents[1]
CATALOG_ROOT = APP / "ui" / "i18n"
KEY = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")
VOCABULARIES = {
	"common.task.": ("daily_set", "explore_on_bing", "visual_search", "misc_cards", "quests", "searches", "bonus"),
	"common.section.": ("searches", "daily_set", "cards", "quests", "balance"),
	"errors.task.": ("not_available", "not_ready", "element_missing", "browser_closed", "offline", "error", "turned_off", "stopped", "not_run"),
	"errors.run.": ("ok", "partial", "failed", "skipped", "checked", "stopped", "busy", "crashed", "config_error", "interrupted", "unknown"),
	"errors.decision.": ("enough_open", "quest_unlocked", "below_threshold", "nothing_open", "unreadable", "unreadable_run_anyway"),
	"errors.warning.": ("llm_unavailable", "search_terms_unavailable", "signed_out", "daily_set_partial"),
	"errors.browser.": ("edge_missing", "driver_missing", "driver_mismatch", "profile_in_use", "edge_exited", "profile_unwritable", "unknown"),
	"errors.state.": ("terms_of_use", "signed_out", "page_blank", "sections"),
	"errors.app.": ("internal", "bad_request", "run_busy", "run_not_active", "run_not_stoppable", "run_no_accounts", "run_unknown", "settings_invalid", "schedule_failed", "schedule_legacy_not_found", "accounts_unknown", "accounts_migration_needed", "accounts_last_active", "accounts_root_needs_delete", "accounts_signin_active", "accounts_legacy_not_found", "ollama_unreachable", "url_not_allowed", "updates_failed", "diagnostics_failed", "autostart_failed", "accounts_recycle_failed", "accounts_root_recycle_failed", "accounts_add_unsupported"),
	"errors.settings.": ("window_order", "window_too_short", "days_empty", "url_invalid", "https_required", "file_missing", "file_wrong_name", "folder_invalid", "tasks_all_off", "key_required", "model_required", "run_active", "out_of_range", "dollar_brace"),
}


def _unique_object(pairs):
	result = {}
	for key, value in pairs:
		if key in result:
			raise ValueError(f"Duplicate JSON key: {key}")
		result[key] = value
	return result


def _json(path):
	return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)


def _placeholders(text):
	if not isinstance(text, str):
		raise ValueError("Message text must be a string")
	if "{" in PLACEHOLDER.sub("", text) or "}" in PLACEHOLDER.sub("", text):
		raise ValueError("Stray brace in message")
	return set(PLACEHOLDER.findall(text))


def validate_catalogs(root: Path, require_complete: bool) -> list[str]:
	root = Path(root)
	errors = []
	try:
		index = _json(root / "index.json")
		assert index["source"] == "en", "English must be the source"
		namespaces = index["namespaces"]
		assert isinstance(namespaces, list) and len(set(namespaces)) == len(namespaces), "Invalid namespaces"
		assert all(re.fullmatch(r"[a-z][a-z0-9_]*", namespace) for namespace in namespaces), "Invalid namespace name"
		assert isinstance(index["locales"], list) and index["locales"], "Missing locale list"
		codes = []
		for locale in index["locales"]:
			code = locale["code"]
			assert re.fullmatch(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*", code) and not code.startswith("x-"), "Invalid shipped locale"
			assert code.lower() not in codes, "Duplicate locale"
			codes.append(code.lower())
			assert isinstance(locale["name"], str) and locale["name"].strip(), "Empty locale name"
			expected = "rtl" if code.split("-")[0].lower() in i18n._rtl_languages else "ltr"
			assert locale["dir"] == expected, f"Wrong direction for {code}"
			if require_complete:
				assert (root / code).is_dir(), f"Missing locale folder: {code}"
		assert "en" in codes, "Source locale is not listed"
		if require_complete:
			for namespace in namespaces:
				assert (root / "en" / f"{namespace}.json").is_file(), f"Missing source namespace: {namespace}"
	except (OSError, ValueError, AssertionError, KeyError, TypeError) as exc:
		return [f"index.json: {exc}"]

	catalogs = {}
	for path in sorted(root.glob("*/*.json")):
		try:
			catalog = _json(path)
			assert set(catalog) == {"namespace", "messages"}, "Invalid catalog fields"
			assert catalog["namespace"] == path.stem, "Namespace does not match filename"
			assert path.stem in namespaces, "Unlisted namespace"
			assert isinstance(catalog["messages"], dict), "Messages must be an object"
			catalogs[(path.parent.name, path.stem)] = catalog["messages"]
		except (OSError, ValueError, AssertionError, KeyError, TypeError) as exc:
			errors.append(f"{path.relative_to(root)}: {exc}")

	for (locale, namespace), messages in catalogs.items():
		for key, entry in messages.items():
			label = f"{locale}/{namespace}.json:{key}"
			try:
				assert KEY.fullmatch(key) and key.startswith(namespace + "."), "Invalid message key"
				assert isinstance(entry, dict), "Entry must be an object"
				plural = "plural" in entry
				fields = {"plural", "forms"} if plural else {"text"}
				fields.add("note" if locale == "en" else "src")
				assert set(entry) == fields, "Invalid entry fields"
				if locale == "en":
					assert isinstance(entry["note"], str) and entry["note"].strip(), "Missing translator note"
					_placeholders(entry["note"])
				else:
					assert isinstance(entry["src"], str) and re.fullmatch(r"[0-9a-f]{8}", entry["src"]), "Invalid source hash"
				if plural:
					assert isinstance(entry["plural"], str) and re.fullmatch(r"[a-z][a-z0-9_]*", entry["plural"]), "Invalid plural driver"
					assert isinstance(entry["forms"], dict), "Forms must be an object"
					expected = {"one", "other"} if locale == "en" else {rule.removeprefix("pluralRule-count-") for rule in i18n._rules_for(locale)}
					assert set(entry["forms"]) == expected, f"Missing or extra plural forms: expected {sorted(expected)}"
					sets = [_placeholders(text) for text in entry["forms"].values()]
					assert len({frozenset(values - {entry["plural"]}) for values in sets}) == 1, "Placeholder drift between plural forms"
				else:
					sets = [_placeholders(entry["text"])]
				if locale != "en":
					source = catalogs.get(("en", namespace), {})
					assert key in source, "Key absent from source"
					original = source[key]
					assert ("plural" in original) == plural, "Plural/text kind differs from source"
					if plural:
						assert entry["plural"] == original["plural"], "Plural driver differs from source"
						source_sets = [_placeholders(text) for text in original["forms"].values()]
						required = set.union(*source_sets) - {original["plural"]}
						assert all(values - {entry["plural"]} == required for values in sets), "Placeholder drift from source"
					else:
						assert sets[0] == _placeholders(original["text"]), "Placeholder drift from source"
				# Notes are prose too, but their references to {name} do not interpolate.
				content = json.dumps(entry, ensure_ascii=False)
				assert "\u2013" not in content and "\u2014" not in content, "Forbidden dash"
			except (ValueError, AssertionError, KeyError, TypeError) as exc:
				errors.append(f"{label}: {exc}")
	return errors


def validate_references(app: Path, require_complete: bool) -> list[str]:
	root = app / "ui" / "i18n" / "en"
	messages = {}
	for path in root.glob("*.json"):
		messages.update(_json(path)["messages"])
	landed = {path.stem for path in root.glob("*.json")}
	errors = []
	for prefix, vocabulary in VOCABULARIES.items():
		if require_complete or prefix.split(".")[0] in landed:
			for value in vocabulary:
				if prefix + value not in messages:
					errors.append("Missing vocabulary key: " + prefix + value)
	js_patterns = (r"\bt\(\s*[\"']([^\"']+)[\"']", r"\b\w*Key\s*:\s*[\"']([^\"']+)[\"']", r"data-i18n=[\"']([^\"']+)[\"']")
	for folder, patterns, glob in ((app / "ui" / "js", js_patterns, "*.js"), (app / "rfapp", (r"\.t\(\s*[\"']([^\"']+)[\"']",), "*.py")):
		for path in folder.rglob(glob):
			text = path.read_text(encoding="utf-8")
			for pattern in patterns:
				for key in re.findall(pattern, text):
					if key in VOCABULARIES:
						continue
					if not KEY.fullmatch(key):
						errors.append(f"{path.relative_to(app)}: Invalid source key {key}")
					elif (require_complete or key.split(".")[0] in landed) and key not in messages:
						errors.append(f"{path.relative_to(app)}: Missing source key {key}")
	return errors


class _VisibleText(HTMLParser):
	def __init__(self):
		super().__init__()
		self.hidden = []
		self.visible = []
	def handle_starttag(self, tag, attrs):
		if tag in ("script", "style"):
			self.hidden.append(tag)
	def handle_endtag(self, tag):
		if self.hidden and self.hidden[-1] == tag:
			self.hidden.pop()
	def handle_data(self, data):
		if data.strip() and not self.hidden:
			self.visible.append(data.strip())


class CatalogTests(unittest.TestCase):
	def test_existing_catalogs(self):
		# Every namespace and locale in index.json has its files now, so a
		# missing one is a defect rather than work that has not landed yet.
		self.assertEqual([], validate_catalogs(CATALOG_ROOT, require_complete=True))

	def test_existing_code_references(self):
		self.assertEqual([], validate_references(APP, require_complete=True))

	def test_f54_locale_registration_and_glossary_decisions(self):
		index = _json(CATALOG_ROOT / "index.json")
		expected = [
			("en", "English", "ltr"), ("de", "Deutsch", "ltr"), ("es", "Español", "ltr"),
			("fr", "Français", "ltr"), ("it", "Italiano", "ltr"), ("pt-BR", "Português (Brasil)", "ltr"),
			("ja", "日本語", "ltr"), ("zh-Hans", "中文（简体）", "ltr"), ("ar", "العربية", "rtl"),
		]
		self.assertEqual(expected, [(entry["code"], entry["name"], entry["dir"]) for entry in index["locales"]])
		self.assertEqual([], validate_catalogs(CATALOG_ROOT, require_complete=True))
		glossary = _json(CATALOG_ROOT / "glossary.json")
		self.assertEqual("en", glossary["source"])
		self.assertEqual("informal", glossary["form_of_address"]["default"])
		self.assertEqual("informal", glossary["form_of_address"]["locales"]["en"])
		self.assertEqual({"Task Scheduler", "Recycle Bin"}, set(glossary["windows_terms"]))
		self.assertTrue(all(isinstance(value, str) for value in glossary["windows_terms"].values()))
		terms = {
			"de": ("Aufgabenplanung", "Papierkorb"), "es": ("Programador de tareas", "Papelera de reciclaje"),
			"fr": ("Planificateur de tâches", "Corbeille"), "it": ("Utilità di pianificazione", "Cestino"),
			"pt-BR": ("Agendador de Tarefas", "Lixeira"), "ja": ("タスク スケジューラ", "ごみ箱"),
			"zh-Hans": ("任务计划程序", "回收站"), "ar": ("جدولة المهام", "سلة المحذوفات"),
		}
		address = {
			"de": ("du/dein", "4.1.14"), "es": ("tú", "3.3"), "fr": ("vous", "4.1.13"),
			"it": ("tu", "2.1", "2.2.1", "2.2.4"), "pt-BR": ("você", "pronoun out", "4.1.11"),
			"ja": ("です/ます", "してください", "no second-person pronoun", "2.1.3", "4.1.8", "4.2"),
			"zh-Hans": ("你", "pronoun out", "4.1.12"), "ar": ("second person", "Modern Standard Arabic", "2.1", "2.2.1"),
		}
		self.assertEqual(set(terms), set(glossary["windows_terms_locales"]))
		for locale, (scheduler, recycle) in terms.items():
			with self.subTest(locale=locale):
				self.assertEqual({"Task Scheduler": scheduler, "Recycle Bin": recycle}, glossary["windows_terms_locales"][locale])
				rule = glossary["form_of_address"]["locales"][locale]
				self.assertIsInstance(rule, str)
				for marker in (*address[locale], "Microsoft", "Style Guide", "https://"):
					self.assertIn(marker, rule)
		for existing in ("never_translate", "voice"):
			self.assertTrue(glossary[existing])
		self.assertEqual({
			"meaning": "The Microsoft Rewards task named Quests.",
			"step": "quest step",
			"translation": "Use the locale's term for Quests for both the task and a step of it.",
			"never_use": ["punch card"],
		}, glossary["ui_terms"]["Quests"])
		self.assertEqual("A quest step was unlocked.", _json(CATALOG_ROOT / "en/errors.json")["messages"]["errors.decision.quest_unlocked"]["text"])

	def test_risk_notice_preserves_the_frozen_source(self):
		path = CATALOG_ROOT / "en" / "common.json"
		if not path.is_file():
			self.skipTest("W2-3a common.json has not landed yet")
		self.assertEqual("Use at your own risk. Microsoft may take action against your account for using automated scripts to gain Rewards points.", _json(path)["messages"]["common.risk_notice"]["text"])

	def test_f43_f49_error_copy_and_notes(self):
		messages = _json(CATALOG_ROOT / "en" / "errors.json")["messages"]
		expected = {
			"errors.app.accounts_add_unsupported": "A second account isn't supported yet, because Edge would connect it to the account you already use. Your current account keeps working.",
			"errors.app.bad_request": "Something went wrong in the app. Try again. If it keeps happening, export diagnostics under Settings, Advanced.",
			"errors.app.schedule_failed": "Windows did not accept the schedule. Try again. If it keeps happening, export diagnostics under Settings, Advanced.",
			"errors.run.crashed": "The run stopped unexpectedly. Try Run now again. If it keeps happening, export diagnostics under Settings, Advanced.",
			"errors.browser.unknown": "Edge did not start. Close any Edge window of this account and try again. If it keeps happening, export diagnostics under Settings, Advanced.",
			"errors.task.error": "Something went wrong on the page. The next run tries this task again.",
			"errors.run.unknown": "This run was recorded by a newer version of the app. Update the app to see it.",
			"errors.browser.driver_mismatch": "The Edge driver does not match your Edge version. Clear the driver path under Settings, Advanced, so the right driver is downloaded.",
			"errors.settings.dollar_brace": "This value cannot contain a $ sign right before a curly bracket. Remove one of them.",
			"errors.app.run_not_stoppable": "This run was started outside the app, so it can't be stopped from here.",
		}
		for key, text in expected.items():
			with self.subTest(key=key):
				self.assertEqual(text, messages[key]["text"])
				self.assertTrue(messages[key]["note"].strip())
		self.assertIn("run_not_stoppable", VOCABULARIES["errors.app."])

	def test_index_has_no_visible_text(self):
		path = APP / "ui" / "index.html"
		if not path.exists():
			self.skipTest("W2-3a index.html has not landed yet")
		parser = _VisibleText()
		parser.feed(path.read_text(encoding="utf-8"))
		self.assertEqual([], parser.visible)

	def test_css_uses_logical_properties(self):
		pattern = re.compile(r"(?<![\w-])(?:margin-(?:left|right)|padding-(?:left|right)|left|right)\s*:|text-align\s*:\s*(?:left|right)\b")
		for path in (APP / "ui" / "css").rglob("*.css"):
			with self.subTest(path=path.name):
				css = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
				self.assertIsNone(pattern.search(css))


class ValidatorProofTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory(prefix="rf-i18n-catalog-")
		self.addCleanup(self.temp.cleanup)
		self.root = Path(self.temp.name)
		self.index = {"source": "en", "namespaces": ["sample"], "locales": [{"code": "en", "name": "English", "dir": "ltr"}, {"code": "ru", "name": "Russian", "dir": "ltr"}]}
		self.source = {"namespace": "sample", "messages": {
			"sample.title": {"text": "Hello {name}", "note": "Test label; name is the account label."},
			"sample.points": {"plural": "count", "forms": {"one": "{count} point for {name}", "other": "{count} points for {name}"}, "note": "Test count; count is numeric, name is the account label."},
		}}
		self.target = {"namespace": "sample", "messages": {
			"sample.title": {"text": "Hello {name}", "src": source_hash(self.source["messages"]["sample.title"])},
			"sample.points": {"plural": "count", "forms": {category: "{count} points for {name}" for category in ("one", "few", "many", "other")}, "src": source_hash(self.source["messages"]["sample.points"])},
		}}
		self.save("index.json", self.index)
		self.save("en/sample.json", self.source)
		self.save("ru/sample.json", self.target)

	def save(self, relative, value):
		path = self.root / relative
		path.parent.mkdir(parents=True, exist_ok=True)
		path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

	def test_valid_fixture(self):
		self.assertEqual([], validate_catalogs(self.root, True))

	def test_placeholder_mutation_is_detected_and_restored(self):
		self.target["messages"]["sample.title"]["text"] = "Hello {wrong}"
		self.save("ru/sample.json", self.target)
		self.assertTrue(any("Placeholder drift" in error for error in validate_catalogs(self.root, True)))
		self.target["messages"]["sample.title"]["text"] = "Hello {name}"
		self.save("ru/sample.json", self.target)
		self.assertEqual([], validate_catalogs(self.root, True))

	def test_plural_mutation_is_detected_and_restored(self):
		text = self.target["messages"]["sample.points"]["forms"].pop("few")
		self.save("ru/sample.json", self.target)
		self.assertTrue(any("Missing or extra plural forms" in error for error in validate_catalogs(self.root, True)))
		self.target["messages"]["sample.points"]["forms"]["few"] = text
		self.save("ru/sample.json", self.target)
		self.assertEqual([], validate_catalogs(self.root, True))

	def test_complete_mode_requires_namespaces(self):
		self.index["namespaces"].append("later")
		self.save("index.json", self.index)
		self.assertEqual([], validate_catalogs(self.root, False))
		self.assertTrue(any("Missing source namespace" in error for error in validate_catalogs(self.root, True)))

	def test_complete_mode_requires_locale_folder(self):
		self.index["locales"].append({"code": "de", "name": "German", "dir": "ltr"})
		self.save("index.json", self.index)
		self.assertTrue(any("Missing locale folder" in error for error in validate_catalogs(self.root, True)))

	def test_stray_brace_and_extra_key_are_rejected(self):
		self.target["messages"]["sample.title"]["text"] = "Wrong {"
		self.save("ru/sample.json", self.target)
		self.assertTrue(any("Stray brace" in error for error in validate_catalogs(self.root, True)))
		self.target["messages"]["sample.title"]["text"] = "Hello {name}"
		self.target["messages"]["sample.extra"] = {"text": "Extra", "src": "00000000"}
		self.save("ru/sample.json", self.target)
		self.assertTrue(any("Key absent from source" in error for error in validate_catalogs(self.root, True)))

	def test_plural_driver_and_entry_kind_are_binding(self):
		self.target["messages"]["sample.points"]["plural"] = "amount"
		self.save("ru/sample.json", self.target)
		self.assertTrue(any("Plural driver differs" in error for error in validate_catalogs(self.root, True)))
		self.target["messages"]["sample.points"] = {"text": "Hello {name}", "src": "00000000"}
		self.save("ru/sample.json", self.target)
		self.assertTrue(any("Plural/text kind differs" in error for error in validate_catalogs(self.root, True)))

	def test_notes_namespace_and_key_format(self):
		self.source["messages"]["sample.title"]["note"] = ""
		self.save("en/sample.json", self.source)
		self.assertTrue(any("Missing translator note" in error for error in validate_catalogs(self.root, True)))
		self.source["namespace"] = "wrong"
		self.save("en/sample.json", self.source)
		self.assertTrue(any("Namespace does not match" in error for error in validate_catalogs(self.root, True)))
		self.source["namespace"] = "sample"
		self.source["messages"]["Sample.bad"] = {"text": "Bad", "note": "Test"}
		self.save("en/sample.json", self.source)
		self.assertTrue(any("Invalid message key" in error for error in validate_catalogs(self.root, True)))

	def test_unlisted_namespace_is_rejected(self):
		self.save("en/notlisted.json", {"namespace": "notlisted", "messages": {}})
		self.assertTrue(any("Unlisted namespace" in error for error in validate_catalogs(self.root, True)))

	def test_reference_scan_covers_static_props_and_html_attributes(self):
		ui = self.root / "reference-app" / "ui"
		(ui / "js").mkdir(parents=True)
		catalog = ui / "i18n" / "en"
		catalog.mkdir(parents=True)
		(catalog / "sample.json").write_text(json.dumps(self.source), encoding="utf-8")
		(ui / "js" / "screen.js").write_text('t("sample.title"); const props={labelKey:"sample.absent"}; const html=\'data-i18n="sample.missing"\';', encoding="utf-8")
		errors = validate_references(ui.parent, False)
		self.assertEqual(2, len(errors))
		self.assertTrue(any("sample.absent" in error for error in errors))
		self.assertTrue(any("sample.missing" in error for error in errors))

	def test_non_driver_required_but_driver_can_be_omitted(self):
		self.target["messages"]["sample.points"]["forms"]["one"] = "One point for {name}"
		self.save("ru/sample.json", self.target)
		self.assertEqual([], validate_catalogs(self.root, True))
		self.target["messages"]["sample.points"]["forms"]["one"] = "One point"
		self.save("ru/sample.json", self.target)
		self.assertTrue(any("Placeholder drift" in error for error in validate_catalogs(self.root, True)))

	def test_direction_and_duplicate_json_keys_are_rejected(self):
		self.index["locales"].append({"code": "ar", "name": "Arabic", "dir": "ltr"})
		self.save("index.json", self.index)
		self.assertTrue(any("Wrong direction" in error for error in validate_catalogs(self.root, False)))
		(self.root / "index.json").write_text('{"source":"en","source":"de"}', encoding="utf-8")
		self.assertTrue(any("Duplicate JSON key" in error for error in validate_catalogs(self.root, False)))

	def test_stale_and_missing_translations_do_not_fail_validation(self):
		self.target["messages"].pop("sample.title")
		self.target["messages"]["sample.points"]["src"] = "00000000"
		self.save("ru/sample.json", self.target)
		self.assertEqual([], validate_catalogs(self.root, True))
