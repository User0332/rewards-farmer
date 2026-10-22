import _bootstrap  # noqa: F401

import ast
import configparser
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from rfapp import __version__

ROOT = Path(__file__).resolve().parents[2]
PACKAGING = ROOT / "app/packaging"
INSTALLER_LANGUAGES = {
	"en": "Default.isl", "de": "Languages/German.isl", "es": "Languages/Spanish.isl",
	"fr": "Languages/French.isl", "it": "Languages/Italian.isl", "pt-BR": "Languages/BrazilianPortuguese.isl",
	"ja": "Languages/Japanese.isl", "ar": "Languages/Arabic.isl",
}


def installer_language_entries():
	text = (PACKAGING / "RewardsFarmer.iss").read_text(encoding="utf-8")
	section = text.split("[Languages]", 1)[1].split("[InstallDelete]", 1)[0]
	return re.findall(r'^Name: "([^"]+)"; MessagesFile: "([^"]+)"$', section, re.MULTILINE)


def custom_messages(path):
	data = path.read_bytes()
	if data.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
		raise AssertionError(f"{path.name} must be UTF-8 without BOM")
	text = data.decode("utf-8")
	parser = configparser.ConfigParser(interpolation=None, strict=True)
	parser.optionxform = str
	parser.read_string(text)
	if parser.sections() != ["CustomMessages"]:
		raise AssertionError(f"{path.name} must contain only CustomMessages")
	return dict(parser["CustomMessages"])


def installer_user_message_expressions(text):
	code = text.split("[Code]", 1)[1]
	# Pascal strings stay whole, so commas/semicolons/comments inside them do not
	# confuse argument or assignment boundaries. Comments cannot hide a callsite.
	tokens = re.findall(r"'(?:''|[^'])*'|//[^\n]*|\{[^}]*\}|\(\*.*?\*\)|[A-Za-z_][A-Za-z_0-9]*|:=|\S", code, re.DOTALL)
	tokens = [token for token in tokens if not token.startswith(("//", "{", "(*"))]
	expressions = []
	names = []
	routine = ""
	for index, token in enumerate(tokens):
		lower = token.lower()
		if lower in ("function", "procedure"):
			routine = tokens[index + 1].lower()
		if routine == "preparetoinstall" and lower == "result" and tokens[index + 1] == ":=":
			end = tokens.index(";", index + 2)
			expressions.append(("PrepareToInstall Result", tokens[index + 2:end]))
		if lower in ("msgbox", "suppressiblemsgbox") and tokens[index + 1] == "(":
			depth = 0
			argument = []
			for part in tokens[index + 2:]:
				if part == "," and depth == 0:
					break
				if part == "(":
					depth += 1
				elif part == ")":
					depth -= 1
				argument.append(part)
			expressions.append((token, argument))
		if lower == "custommessage" and tokens[index + 1] == "(":
			name = tokens[index + 2]
			if not re.fullmatch(r"'[A-Za-z_][A-Za-z_0-9]*'", name) or tokens[index + 3] != ")":
				raise AssertionError("CustomMessage must use one static message name")
			names.append(name[1:-1])
	return expressions, names


def load_layout():
	spec = importlib.util.spec_from_file_location("rf_packaging_layout_tests", PACKAGING / "layout.py")
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def powershell_scan(blocks):
	shell = shutil.which("powershell.exe")
	if shell is None:
		raise unittest.SkipTest("Windows PowerShell powershell.exe is unavailable; automatic-variable AST check requires its real variable table")
	result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(PACKAGING / "scan_powershell_variables.ps1")], input=json.dumps(blocks), encoding="utf-8", capture_output=True, timeout=30)
	if result.returncode != 0:
		raise AssertionError(result.stdout + result.stderr)
	return json.loads(result.stdout.lstrip("\ufeff"))


def shipped_powershell_blocks(root):
	# Also works in git archives without reading a venv, fixtures or generated output.
	excluded = {".git", ".dev", ".impeccable", ".venv", ".venv-poetry", "node_modules", "dist", "build", "__pycache__", "data-dir", "runs"}
	blocks = []
	for directory, directories, filenames in os.walk(root):
		directories[:] = sorted(name for name in directories if name not in excluded)
		for name in sorted(filenames):
			if name.lower().endswith(".ps1"):
				path = Path(directory) / name
				blocks.append({"label": path.relative_to(root).as_posix(), "code": path.read_text(encoding="utf-8-sig")})
	return blocks


class PackagingTests(unittest.TestCase):
	def test_exact_measured_excludes_keep_runtime_dependencies(self):
		layout = load_layout()
		self.assertEqual(set(layout.EXCLUDES), {
			"matplotlib", "pygame", "keyboard", "pygetwindow", "pyrect", "ollama", "pydantic", "httpx",
			"tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "gi", "cefpython3",
		})
		self.assertTrue(set(layout.EXCLUDES).isdisjoint({"numpy", "selenium", "requests", "PIL", "dotenv", "trio", "urllib3", "websocket"}))

	def test_resources_have_the_paths_used_by_rfapp(self):
		layout = load_layout()
		with tempfile.TemporaryDirectory() as temporary:
			root = Path(temporary)
			files = ("app/ui/index.html", "app/ui/i18n/en/common.json", "app/ui/img/app-icon-256.png", "app/ui/js/mock-bridge.js", "app/ui/mock/setup/synthetic.json", "app/ui/mock/shell/reload.js", "app/rfapp/cldr_plurals.json", "nouns.txt", "LICENSE", ".env", "data-dir/profile", "app/tests/fixture.json")
			for name in files:
				path = root / name
				path.parent.mkdir(parents=True, exist_ok=True)
				path.write_text("synthetic", encoding="utf-8")
			data = {Path(source).relative_to(root).as_posix(): destination for source, destination in layout.app_data(root)}
			self.assertEqual(data, {
				"app/ui/index.html": "app/ui", "app/ui/i18n/en/common.json": "app/ui/i18n/en",
				"app/ui/img/app-icon-256.png": "app/ui/img", "app/ui/js/mock-bridge.js": "app/ui/js",
				"app/rfapp/cldr_plurals.json": "app/rfapp", "nouns.txt": ".", "LICENSE": ".",
			})

	def test_mock_tree_is_never_bundled_from_the_real_tree(self):
		layout = load_layout()
		data = layout.app_data(ROOT)
		self.assertFalse(any("mock" in Path(source).relative_to(ROOT / "app/ui").parts for source, _ in data if Path(source).is_relative_to(ROOT / "app/ui")))
		self.assertTrue(any(source.endswith("index.html") for source, _ in data))
		self.assertTrue(any(source.endswith("cldr_plurals.json") for source, _ in data))

	def test_windows_manager_filter_covers_input_and_final_toc(self):
		layout = load_layout()
		data = [("/venv/manager", f"selenium/webdriver/common/{platform}") for platform in ("windows", "linux", "macos")]
		toc = [(f"selenium/webdriver/common/{platform}/manager", "/venv/manager", "DATA") for platform in ("windows", "linux", "macos")]
		self.assertEqual(layout.windows_only(data), data[:1])
		self.assertEqual(layout.windows_only(toc, toc=True), toc[:1])

	def test_one_windowed_onedir_exe_and_external_lgpl_source(self):
		text = (PACKAGING / "RewardsFarmer.spec").read_text(encoding="utf-8")
		tree = ast.parse(text)
		calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
		exes = [call for call in calls if call.func.id == "EXE"]
		self.assertEqual(len(exes), 1)
		values = {keyword.arg: ast.literal_eval(keyword.value) for keyword in exes[0].keywords if keyword.arg in ("name", "console", "upx", "exclude_binaries")}
		self.assertEqual(values, {"name": "RewardsFarmer", "console": False, "upx": False, "exclude_binaries": True})
		self.assertEqual(sum(call.func.id == "COLLECT" for call in calls), 1)
		self.assertIn('module_collection_mode={"pystray": "py"}', text)
		self.assertIn('copy_metadata("pystray")', text)
		self.assertIn('collect_submodules("selenium")', text)
		self.assertIn('app_data(root)', text)
		self.assertIn('third-party/', text)
		self.assertIn('str(root / "app"), str(root / "src")', text)

	def test_installer_settings_and_version_input(self):
		text = (PACKAGING / "RewardsFarmer.iss").read_text(encoding="utf-8")
		settings = dict(re.findall(r"^([A-Za-z0-9]+)=(.+)$", text, re.MULTILINE))
		expected = {
			"AppId": "{{C3A933C3-9605-469E-A520-F2ABFC859D29}", "AppVersion": "{#AppVersion}",
			"PrivilegesRequired": "lowest", "DefaultDirName": "{localappdata}\\Programs\\RewardsFarmer",
			"ArchitecturesAllowed": "x64", "ArchitecturesInstallIn64BitMode": "x64", "CloseApplications": "no",
			"AppMutex": "RewardsFarmer.Installed", "RestartApplications": "no",
			"LanguageDetectionMethod": "uilanguage", "ShowLanguageDialog": "no", "UsePreviousLanguage": "no",
		}
		for key, value in expected.items():
			self.assertEqual(settings[key], value)
		self.assertIn('Name: "{userprograms}\\Rewards Farmer"; Filename: "{app}\\RewardsFarmer.exe"', text)
		self.assertNotIn("{userdesktop}", text)
		self.assertNotIn("{commondesktop}", text)
		self.assertNotIn("[Run]", text)
		self.assertNotIn("[UninstallDelete]", text)
		self.assertIn("#ifndef AppVersion", text)
		delete_section = text.split("[InstallDelete]", 1)[1].split("[Files]", 1)[0]
		entries = [line for line in delete_section.splitlines() if line and not line.startswith(";")]
		self.assertEqual(entries, ['Type: filesandordirs; Name: "{app}\\_internal"'])

	def test_installer_language_inventory_and_custom_file_wiring(self):
		entries = installer_language_entries()
		self.assertEqual([code for code, _ in entries], [code.replace("-", "_") for code in INSTALLER_LANGUAGES])
		release = json.loads((ROOT / "app/ui/i18n/index.json").read_text(encoding="utf-8"))
		self.assertEqual({row["code"] for row in release["locales"]}, set(INSTALLER_LANGUAGES) | {"zh-Hans"})
		for (name, files), code in zip(entries, INSTALLER_LANGUAGES):
			self.assertEqual(name, code.replace("-", "_"))
			self.assertEqual(files.replace("\\", "/").split(","), ["compiler:" + INSTALLER_LANGUAGES[code], f"languages/{code}.isl"])
			self.assertTrue((PACKAGING / "languages" / (code + ".isl")).is_file())
		self.assertEqual({path.stem for path in (PACKAGING / "languages").glob("*.isl")}, set(INSTALLER_LANGUAGES))

	def test_official_installer_files_exist_and_arabic_keeps_rtl(self):
		compiler = shutil.which("ISCC.exe")
		candidates = [Path(compiler).parent] if compiler else []
		for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
			if base:
				candidates += [Path(base) / "Programs/Inno Setup 6", Path(base) / "Inno Setup 6"]
		compiler_root = next((path for path in candidates if (path / "ISCC.exe").is_file()), None)
		if compiler_root is None:
			self.skipTest("Installed Inno Setup is unavailable; official file existence/RTL checked on the Windows packaging host")
		for official in INSTALLER_LANGUAGES.values():
			self.assertTrue((compiler_root / official).is_file(), official)
		arabic = (compiler_root / INSTALLER_LANGUAGES["ar"]).read_text(encoding="utf-8-sig")
		self.assertRegex(arabic, r"(?m)^RightToLeft=yes$")
		self.assertFalse(any((compiler_root / "Languages").glob("Chinese*.isl")))

	def test_custom_messages_have_exact_english_keys_and_utf8(self):
		source = custom_messages(PACKAGING / "languages/en.isl")
		self.assertEqual(set(source), {"WebView2Prompt", "QuitBeforeUninstall", "QuitBeforeUpdate", "WebView2Missing", "WebView2Required", "WebView2DownloadFailed", "WebView2InvalidSignature", "WebView2InstallFailed"})
		for path in sorted((PACKAGING / "languages").glob("*.isl")):
			messages = custom_messages(path)
			self.assertEqual(set(messages), set(source), path.name)
			self.assertTrue(all(value.strip() for value in messages.values()), path.name)
		text = (PACKAGING / "RewardsFarmer.iss").read_text(encoding="utf-8")
		self.assertIn("MsgBox(CustomMessage('WebView2Prompt')", text)
		self.assertIn("MsgBox(CustomMessage('QuitBeforeUninstall')", text)
		self.assertNotRegex(text, r"MsgBox\('")

	def test_installer_user_facing_code_uses_custom_messages(self):
		text = (PACKAGING / "RewardsFarmer.iss").read_text(encoding="utf-8")
		expressions, names = installer_user_message_expressions(text)
		self.assertEqual(len(expressions), 9, "Expected six preparing errors, one empty result and two message boxes")
		for label, expression in expressions:
			with self.subTest(callsite=label, expression=expression):
				if label == "PrepareToInstall Result" and expression == ["''"]:
					continue
				self.assertEqual(len(expression), 4, "User-facing text must come directly from CustomMessage")
				self.assertEqual(expression[0].lower(), "custommessage", "User-facing text must come from CustomMessage")
				self.assertEqual((expression[1], expression[3]), ("(", ")"))
		for path in sorted((PACKAGING / "languages").glob("*.isl")):
			self.assertTrue(set(names).issubset(custom_messages(path)), path.name)
		self.assertEqual(set(names), set(custom_messages(PACKAGING / "languages/en.isl")))
		# SuppressibleMsgBox and Pascal punctuation in a literal use the same gate.
		probe = "[Code]\nfunction PrepareToInstall(): String; begin Result := ''; end; SuppressibleMsgBox('literal, with; punctuation', mbError, MB_OK, IDOK);"
		self.assertEqual(installer_user_message_expressions(probe)[0][-1], ("SuppressibleMsgBox", ["'literal, with; punctuation'"]))

	def test_packaging_has_no_typographic_dashes(self):
		for directory, directories, filenames in os.walk(PACKAGING):
			directories[:] = [name for name in directories if name != "__pycache__"]
			for name in filenames:
				path = Path(directory) / name
				text = path.read_text(encoding="utf-8")
				self.assertNotRegex(text, "[\u2013\u2014]", str(path.relative_to(PACKAGING)))

	def test_only_uninstall_allows_a_wmi_query_exception(self):
		text = (PACKAGING / "RewardsFarmer.iss").read_text(encoding="utf-8")
		idle = text.split("function InstalledAppIdle(", 1)[1].split("procedure InitializeWizard", 1)[0]
		self.assertIn("AllowWmiFailure: Boolean", idle)
		self.assertIn("Result := AllowWmiFailure;", idle.split("except", 1)[1])
		self.assertIn("startup AppMutex check", idle)
		self.assertEqual(idle.count("Result := False;"), 2, "Known or unidentifiable active processes must still refuse")
		prepare = text.split("function PrepareToInstall(", 1)[1].split("function InitializeUninstall", 1)[0]
		uninstall = text.split("function InitializeUninstall", 1)[1].split("procedure RemoveAppIntegration", 1)[0]
		self.assertIn("InstalledAppIdle(False)", prepare)
		self.assertIn("InstalledAppIdle(True)", uninstall)
		self.assertIn("AppMutex=RewardsFarmer.Installed", text)

	def test_uninstall_requires_exact_lowercase_hex_identity_and_exe_boundary(self):
		text = (PACKAGING / "RewardsFarmer.iss").read_text(encoding="utf-8")
		guard = text.split("function IsAppName(", 1)[1].split("function StartsWithInstalledExe(", 1)[0]
		self.assertIn("Length(Name) <> Length(TaskPrefix) + 8", guard)
		self.assertIn("Copy(Name, 1, Length(TaskPrefix)) <> TaskPrefix", guard)
		self.assertIn("'0123456789abcdef'", guard)
		self.assertIn("for I := Length(TaskPrefix) + 1 to Length(Name)", guard)
		uninstall = text.split("procedure RemoveAppIntegration();", 1)[1]
		self.assertRegex(uninstall, r"(?s)RegQueryStringValue\(HKCU, AppKey, 'TaskName', Name\).*?if IsAppName\(Name\) then.*?'/Delete /TN")
		self.assertRegex(uninstall, r"(?s)RegGetValueNames\(HKCU, RunKey, Names\).*?if IsAppName\(Name\) then.*?StartsWithInstalledExe\(Command\).*?RegDeleteValue\(HKCU, RunKey, Name\).*?RegDeleteValue\(HKCU, ApprovedKey, Name\)")
		self.assertIn("RegDeleteKeyIncludingSubkeys(HKCU, AppKey)", uninstall)
		command_guard = text.split("function StartsWithInstalledExe(", 1)[1].split("function HasRuntimeVersion(", 1)[0]
		self.assertIn("CompareText", command_guard)
		self.assertIn("Command[N + 1]", command_guard)
		self.assertIn("Command[N + 2]", command_guard)
		self.assertNotIn("DATA_HOME", uninstall)
		self.assertNotIn("DelTree", text)
		for name in ("RewardsFarmer", "RewardsFarmerApp_0000BEEF", "RewardsFarmerApp_1234567", "RewardsFarmerApp_123456789", "RewardsFarmerApp_zzzzzzzz"):
			self.assertIsNone(re.fullmatch(r"RewardsFarmerApp_[0-9a-f]{8}", name))

	def test_webview_check_requires_official_signed_bootstrapper(self):
		text = (PACKAGING / "RewardsFarmer.iss").read_text(encoding="utf-8")
		self.assertIn("{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}", text)
		self.assertIn("HasRuntimeVersion(HKLM32)", text)
		self.assertIn("HasRuntimeVersion(HKCU32)", text)
		self.assertIn("PackedVersion > 0", text)
		self.assertIn("https://go.microsoft.com/fwlink/p/?LinkId=2124703", text)
		self.assertIn("if WebView2Installed() then exit", text)
		self.assertIn("if WizardSilent() then", text)
		self.assertIn("Get-AuthenticodeSignature", text)
		self.assertIn("Microsoft Corporation", text)
		self.assertIn("'/silent /install'", text)
		self.assertIn("or not WebView2Installed()", text)

	def test_build_uses_immutable_git_archive_and_rfapp_version(self):
		text = (PACKAGING / "build.ps1").read_text(encoding="utf-8")
		self.assertIn("archive --format=zip", text)
		self.assertNotRegex(text, r"\bgit\s+(?:checkout|reset|add|commit|stash)")
		self.assertIn('$support version --root $env:RF_SOURCE_ROOT', text)
		self.assertIn('"/DAppVersion=$version"', text)
		loader = importlib.util.spec_from_file_location("rf_packaging_support_tests", PACKAGING / "build_support.py")
		module = importlib.util.module_from_spec(loader)
		import sys
		old = sys.path[:]
		try:
			sys.path.insert(0, str(PACKAGING))
			loader.loader.exec_module(module)
			self.assertEqual(module.version(ROOT), __version__)
			with tempfile.TemporaryDirectory() as temporary:
				tools = Path(temporary)
				(tools / "licenses").mkdir()
				(tools / "licenses/notice.txt").write_text("synthetic notice", encoding="utf-8")
				self.assertEqual(list(module.tool_hashes(tools)), ["licenses/notice.txt"])
		finally:
			sys.path[:] = old

	def test_ci_lifecycle_is_runner_only_and_checks_real_task_and_data(self):
		text = (PACKAGING / "ci_lifecycle.ps1").read_text(encoding="utf-8")
		self.assertIn("$env:GITHUB_ACTIONS -ne 'true'", text)
		self.assertIn("REWARDS_ACCOUNTS=synthetic", text)
		self.assertIn("REWARDS_SCHEDULE_MODE=automatic", text)
		self.assertIn("AddDays(2)", text)
		self.assertIn("New-Object Text.UTF8Encoding($false)", text)
		self.assertIn("schtasks.exe /Query /TN $taskName /XML", text)
		self.assertIn("$command -ne $exe -or $arguments -ne '--scheduled'", text)
		for variable in ("$appKey", "$uninstallKey", "$installed", "$runKey", "$approvedKey"):
			self.assertIn(variable, text)
		self.assertIn("$sentinelHash", text)
		self.assertIn("$app.CloseMainWindow()", text)
		self.assertNotIn("Start-ScheduledTask", text)
		self.assertIn("$queryCode = $LASTEXITCODE", text)
		self.assertTrue(text.rstrip().endswith("exit 0"), "The expected absent-task exit code must not fail the CI shell")

	def test_shipped_powershell_has_no_automatic_variable_writes(self):
		blocks = shipped_powershell_blocks(ROOT)
		self.assertTrue(blocks, "No shipped PowerShell scripts were scanned")
		report = powershell_scan(blocks)
		self.assertEqual(report["scanned"], len(blocks))
		self.assertIn("home", report["readonly_names"])
		self.assertEqual(report["violations"], [], json.dumps(report["violations"]))

	def test_powershell_scanner_catches_all_write_shapes_not_reads(self):
		report = powershell_scan([
			{"label": "assignment", "code": "$home = 'synthetic'"},
			{"label": "typed", "code": "[string]$HOME = 'synthetic'"},
			{"label": "tuple", "code": "$safe, $args = 1, 2"},
			{"label": "scoped", "code": "$script:Home = 'synthetic'"},
			{"label": "foreach", "code": "foreach ($input in 1, 2) {}"},
			{"label": "parameter", "code": "param([string]$PSCommandPath)"},
			{"label": "increment", "code": "$HOME++"},
			{"label": "reads", "code": "$target = $HOME; $target = $matches[0]; $object[$HOME] = 2; $object.Path = $HOME; $env:HOME = 'synthetic'"},
		])
		self.assertEqual([row["label"] for row in report["violations"]], ["assignment", "typed", "tuple", "scoped", "foreach", "parameter", "increment"])

	def test_workflow_powershell_has_no_automatic_variable_writes(self):
		if shutil.which("powershell.exe") is None:
			self.skipTest("Windows PowerShell powershell.exe is unavailable for workflow AST checks")
		try:
			import yaml
		except ImportError:
			self.skipTest("PyYAML is not importable; shipped .ps1 AST checks still run")
		blocks = []
		for path in sorted((ROOT / ".github/workflows").glob("*.y*ml")):
			workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
			for job_name, job in workflow.get("jobs", {}).items():
				default_shell = job.get("defaults", workflow.get("defaults", {})).get("run", {}).get("shell", "")
				for index, step in enumerate(job.get("steps", [])):
					if "run" in step and step.get("shell", default_shell) in ("powershell", "pwsh"):
						blocks.append({"label": f"{path.name}/{job_name}/{index}", "code": step["run"]})
		self.assertTrue(blocks, "No workflow PowerShell run blocks were scanned")
		report = powershell_scan(blocks)
		self.assertEqual(report["scanned"], len(blocks))
		self.assertEqual(report["violations"], [], json.dumps(report["violations"]))

	def test_smoke_script_cannot_request_a_window_or_real_farmer(self):
		text = (PACKAGING / "smoke.py").read_text(encoding="utf-8")
		tree = ast.parse(text)
		flags = [ast.literal_eval(node) for node in ast.walk(tree) if isinstance(node, ast.List) and all(isinstance(item, ast.Constant) and isinstance(item.value, str) for item in node.elts)]
		self.assertIn(["--version"], flags)
		self.assertIn(["--farm", "--help"], flags)
		self.assertIn(["--scheduled"], flags)
		self.assertIn("REWARDS_SCHEDULE_MODE=manual", text)
		self.assertIn('"update_check": False', text)
		self.assertIn('"close_to_tray": False', text)
		self.assertIn('"tray_hint_shown": True', text)
		self.assertNotRegex(text, r'--run-now|--check-now|--signin|--tray')


if __name__ == "__main__":
	unittest.main()
