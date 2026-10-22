import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
FUNCTION_MUTATION = ""
HARNESS = r'''
param([string]$Installer, [string]$CasePath)
$ErrorActionPreference = 'Stop'
$case = [IO.File]::ReadAllText($CasePath) | ConvertFrom-Json
$tokens = $null
$parseErrors = $null
$syntax = [Management.Automation.Language.Parser]::ParseFile($Installer, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
$names = @('Write-Step', 'Get-LockedPackages', 'Test-Wheel', 'Get-WheelBlockers', 'Test-Python', 'Find-Python', 'Install-Python', 'Select-Python')
$definitions = @($syntax.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -in $names }, $true))
if ($definitions.Count -ne $names.Count) { throw 'Expected only the Python selection helpers' }
foreach ($definition in $definitions) {
    $code = $definition.Extent.Text
    if ($definition.Name -eq 'Find-Python' -and $env:RF_TEST_FIND_PYTHON_MUTATION) { $code = $env:RF_TEST_FIND_PYTHON_MUTATION }
    . ([scriptblock]::Create($code))
}
$messages = New-Object 'Collections.Generic.List[string]'
$pyCalls = New-Object 'Collections.Generic.List[string]'
$wingetCalls = New-Object 'Collections.Generic.List[object]'
$searched = New-Object 'Collections.Generic.List[string]'
$afterInstall = $false
function Write-Host([object]$Object, [object]$ForegroundColor) { $messages.Add([string]$Object) }
function Get-Command([string]$Name, [string]$ErrorAction) {
    switch ($Name) {
        'py' { if ($case.launcher) { [pscustomobject]@{ Source = 'stub-py' } } }
        'python' { if ($case.path_python) { [pscustomobject]@{ Source = $case.path_python } } }
        'winget' { if ($case.winget) { [pscustomobject]@{ Source = 'stub-winget' } } }
        default { throw "Unexpected command lookup: $Name" }
    }
}
function py {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$PyArguments)
    $pyCalls.Add(($PyArguments -join ' '))
    $paths = @($case.installed)
    if ($script:afterInstall) { $paths += @($case.after_install) }
    $global:LASTEXITCODE = 0
    if ($PyArguments[0] -eq '-3') {
        & $paths[0] -c 'synthetic probe'
    } elseif ($PyArguments[0] -eq '-0p' -or $PyArguments[0] -eq '--list-paths') {
        if ($case.fail_short_listing -and $PyArguments[0] -eq '-0p') { $global:LASTEXITCODE = 1; return }
        foreach ($path in $paths) { " -V:3.0 * $path" }
    } else { throw 'Stub py only permits listings or the explicit old-choice mutation' }
}
function winget {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$WingetArguments)
    $wingetCalls.Add(@($WingetArguments))
    $script:afterInstall = $true
    $global:LASTEXITCODE = 0
}
function Get-ChildItem([string]$Path, [string]$ErrorAction) {
    $searched.Add($Path)
    if ($Path -like '*\Programs\Python\Python3*\python.exe') {
        $paths = @($case.local_installs)
        if ($script:afterInstall) { $paths += @($case.after_install) }
    } elseif ($Path -like '*\Python\*\x64\python.exe') {
        $paths = @($case.cache_installs)
    } else { throw "Unexpected directory lookup: $Path" }
    foreach ($path in $paths) { if ($path) { [pscustomobject]@{ FullName = $path } } }
}
$selected = $null
$caught = $null
$lockReport = $null
try {
    if ($case.mode -eq 'lock') {
        $packages = @(Get-LockedPackages $case.lock_path)
        $coverage = @()
        foreach ($minor in 11..16) { $coverage += @{ version = "3.$minor"; blockers = @(Get-WheelBlockers $packages $minor) } }
        $lockReport = @{ packages = $packages.Count; source_only = @($packages | Where-Object { -not $_.Wheels.Count } | ForEach-Object { $_.Name }); coverage = $coverage }
    } else { $selected = Select-Python $case.root }
} catch { $caught = $_.Exception.Message }
$calls = @($syntax.FindAll({ param($node) $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -in @('Get-Code', 'Select-Python') }, $true))
$order = @($calls | Sort-Object { $_.Extent.StartOffset } | ForEach-Object { $_.GetCommandName() })
@{ selected = $selected; error = $caught; messages = $messages.ToArray(); py_calls = $pyCalls.ToArray(); winget_calls = $wingetCalls.ToArray(); searched = $searched.ToArray(); lock = $lockReport; parser_errors = $parseErrors.Count; code_selection_order = $order } | ConvertTo-Json -Depth 12 -Compress
'''


def lock_text(packages):
	return "\n".join(
		'[[package]]\nname = "' + name + '"\nversion = "1.0"\nfiles = [\n'
		+ "\n".join('    {file = "' + filename + '", hash = "synthetic"},' for filename in filenames)
		+ "\n]\n" for name, filenames in packages.items()
	)


@unittest.skipUnless(shutil.which("powershell.exe"), "Windows PowerShell is unavailable; installer function tests require its parser")
class InstallScriptTests(unittest.TestCase):
	def setUp(self):
		temporary = tempfile.TemporaryDirectory()
		self.addCleanup(temporary.cleanup)
		self.root = Path(temporary.name)
		self.probes = self.root / "probes.txt"
		self.case = {
			"root": str(self.root), "installed": [], "after_install": [], "path_python": None,
			"local_installs": [], "cache_installs": [], "launcher": True, "winget": True,
			"fail_short_listing": False,
		}

	def interpreter(self, minor, *, free_threaded=False, location=None, platform="win-amd64", patch=0):
		folder = self.root / (location or f"Python3{minor}{'t' if free_threaded else ''}")
		folder.mkdir(parents=True, exist_ok=True)
		path = folder / "python.ps1"
		details = json.dumps({"exe": str(path), "version": f"3.{minor}.{patch}", "major": 3, "minor": minor, "free_threaded": free_threaded, "platform": platform})
		quote = lambda value: str(value).replace("'", "''")
		path.write_text(
			"param([string]$c)\n[IO.File]::AppendAllText('" + quote(self.probes) + "', '" + quote(path) + "' + [Environment]::NewLine)\n"
			+ "$global:LASTEXITCODE = 0\n'" + quote(details) + "'\n", encoding="utf-8",
		)
		return str(path)

	def run_case(self, packages=None, **changes):
		if packages is None:
			packages = {"matplotlib": [f"matplotlib-1.0-cp3{minor}-cp3{minor}-win_amd64.whl" for minor in (12, 13, 14)]}
		(self.root / "poetry.lock").write_text(lock_text(packages), encoding="utf-8")
		case = dict(self.case, **changes)
		if case.get("lock_text") is not None:
			(self.root / "poetry.lock").write_text(case["lock_text"], encoding="utf-8")
		if case.get("missing_lock"):
			(self.root / "poetry.lock").unlink()
		case_path = self.root / "case.json"
		case_path.write_text(json.dumps(case), encoding="utf-8")
		harness = self.root / "harness.ps1"
		harness.write_text(HARNESS, encoding="utf-8")
		env = os.environ.copy()
		env.update(LOCALAPPDATA=str(self.root / "local"), RUNNER_TOOL_CACHE=str(self.root / "toolcache"), AGENT_TOOLSDIRECTORY="", RF_TEST_FIND_PYTHON_MUTATION=FUNCTION_MUTATION)
		result = subprocess.run(
			[shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness), str(ROOT / "install.ps1"), str(case_path)],
			stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", env=env, timeout=30,
		)
		self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
		return json.loads(result.stdout.lstrip("\ufeff"))

	def test_newer_unsupported_python_is_skipped_for_314(self):
		newest = self.interpreter(15)
		supported = self.interpreter(14)
		result = self.run_case(installed=[newest, supported])
		self.assertIsNone(result["error"])
		self.assertEqual(result["selected"], supported)
		self.assertTrue(any("Skipping Python 3.15.0" in line and "matplotlib" in line and "wheel" in line for line in result["messages"]))
		self.assertTrue(any("Using Python 3.14.0" in line for line in result["messages"]))
		self.assertEqual(len([line for line in result["messages"] if line.startswith("Skipping Python")]), 1)
		self.assertTrue(all("\n" not in line and "\r" not in line for line in result["messages"]))
		self.assertNotIn("-3", result["py_calls"])
		self.assertEqual(result["winget_calls"], [])

	def test_only_315_installs_313_then_checks_the_same_lock(self):
		newest = self.interpreter(15)
		fallback = self.interpreter(13)
		result = self.run_case(installed=[newest], after_install=[fallback])
		self.assertIsNone(result["error"])
		self.assertEqual(result["selected"], fallback)
		self.assertEqual(len(result["winget_calls"]), 1)
		self.assertEqual(result["winget_calls"][0], ["install", "--id", "Python.Python.3.13", "--exact", "--scope", "user", "--silent", "--accept-package-agreements", "--accept-source-agreements"])
		self.assertEqual(result["py_calls"].count("-0p"), 2)

	def test_313_is_preferred_to_312_regardless_of_listing_order(self):
		older = self.interpreter(12)
		newer = self.interpreter(13)
		result = self.run_case(installed=[older, newer])
		self.assertEqual(result["selected"], newer)
		self.assertIsNone(result["error"])

	def test_free_threaded_python_is_never_selected(self):
		threaded = self.interpreter(14, free_threaded=True)
		regular = self.interpreter(13)
		result = self.run_case(installed=[threaded, regular])
		self.assertEqual(result["selected"], regular)
		self.assertTrue(any("free-threaded" in line for line in result["messages"]))

	def test_pure_wheels_and_source_only_package_allow_newest(self):
		newest = self.interpreter(15)
		result = self.run_case({"pure": ["pure-1.0-py2.py3-none-any.whl"], "source": ["source-1.0.tar.gz"]}, installed=[newest])
		self.assertEqual(result["selected"], newest)
		self.assertIsNone(result["error"])

	def test_abi3_wheel_covers_newer_regular_python(self):
		newest = self.interpreter(15)
		result = self.run_case({"native": ["native-1.0-cp37-abi3-win_amd64.whl"]}, installed=[newest])
		self.assertEqual(result["selected"], newest)

	def test_abi3_wheel_from_a_newer_python_does_not_cover_older(self):
		real = self.interpreter(14)
		result = self.run_case({"native": ["native-1.0-cp315-abi3-win_amd64.whl"]}, installed=[real], winget=False)
		self.assertIsNone(result["selected"])
		self.assertTrue(any("native" in line for line in result["messages"]))

	def test_generic_windows_wheel_covers_newest(self):
		newest = self.interpreter(15)
		result = self.run_case({"native": ["native-1.0-py3-none-win_amd64.whl"]}, installed=[newest])
		self.assertEqual(result["selected"], newest)

	def test_windowsapps_stub_is_not_probed(self):
		stub = self.interpreter(15, location="WindowsApps")
		real = self.interpreter(13)
		result = self.run_case(installed=[stub, real], path_python=stub)
		self.assertEqual(result["selected"], real)
		self.assertNotIn(stub, self.probes.read_text(encoding="utf-8"))

	def test_path_local_and_toolcache_interpreters_are_enumerated(self):
		for source in ("path_python", "local_installs", "cache_installs"):
			with self.subTest(source=source):
				real = self.interpreter(14)
				result = self.run_case(launcher=False, **{source: real if source == "path_python" else [real]})
				self.assertEqual(result["selected"], real)
				self.assertIsNone(result["error"])

	def test_launcher_list_paths_fallback_is_supported(self):
		real = self.interpreter(14)
		result = self.run_case(installed=[real], fail_short_listing=True)
		self.assertEqual(result["selected"], real)
		self.assertEqual(result["py_calls"], ["-0p", "--list-paths"])

	def test_old_python_and_wrong_architecture_are_not_selected(self):
		old = self.interpreter(11)
		wrong = self.interpreter(15, platform="win32")
		real = self.interpreter(13)
		result = self.run_case(installed=[old, wrong, real])
		self.assertEqual(result["selected"], real)

	def test_newest_patch_version_is_selected_numerically(self):
		older = self.interpreter(14, patch=9, location="older")
		newer = self.interpreter(14, patch=10, location="newer")
		result = self.run_case(installed=[older, newer])
		self.assertEqual(result["selected"], newer)

	def test_missing_or_malformed_lock_stops_before_any_install(self):
		for changes in ({"missing_lock": True}, {"lock_text": "[[package]]\nname = \"broken\"\n"}):
			with self.subTest(changes=changes):
				result = self.run_case(**changes)
				self.assertIsNone(result["selected"])
				self.assertIn("poetry.lock", result["error"])
				self.assertEqual(result["winget_calls"], [])
				self.assertEqual(result["py_calls"], [])

	def test_future_lock_without_313_explains_fallback_failure(self):
		newest = self.interpreter(15)
		fallback = self.interpreter(13)
		result = self.run_case({"native": ["native-1.0-cp314-cp314-win_amd64.whl"]}, installed=[newest], after_install=[fallback])
		self.assertIsNone(result["selected"])
		self.assertIn("poetry.lock does not cover Python 3.13", result["error"])
		self.assertIn("native", result["error"])
		self.assertEqual(len(result["winget_calls"]), 1)

	def test_free_threaded_wheel_does_not_cover_regular_python(self):
		real = self.interpreter(14)
		result = self.run_case({"native": ["native-1.0-cp314-cp314t-win_amd64.whl"]}, installed=[real], winget=False)
		self.assertIsNone(result["selected"])
		self.assertTrue(any("native" in line for line in result["messages"]))

	def test_other_architecture_wheel_does_not_cover_x64(self):
		real = self.interpreter(14)
		result = self.run_case({"native": ["native-1.0-cp314-cp314-win_arm64.whl"]}, installed=[real], winget=False)
		self.assertIsNone(result["selected"])
		self.assertTrue(any("native" in line for line in result["messages"]))

	def test_download_precedes_selection_and_parser_has_no_errors(self):
		result = self.run_case(mode="lock", lock_path=str(ROOT / "poetry.lock"))
		self.assertEqual(result["parser_errors"], 0)
		self.assertEqual(result["code_selection_order"], ["Get-Code", "Select-Python"])

	def test_real_lock_qualifies_312_to_314_and_blocks_315(self):
		result = self.run_case(mode="lock", lock_path=str(ROOT / "poetry.lock"))
		self.assertIsNone(result["error"])
		coverage = {row["version"]: row["blockers"] for row in result["lock"]["coverage"]}
		self.assertEqual([version for version, blockers in coverage.items() if not blockers], ["3.12", "3.13", "3.14"])
		self.assertIn("matplotlib 3.11.1", coverage["3.15"])
		self.assertEqual(set(result["lock"]["source_only"]), {"proxy-tools", "pygetwindow", "pyrect"})


if __name__ == "__main__":
	unittest.main()
