param(
	[Parameter(Mandatory = $true)][string]$Commit,
	[string]$ScratchRoot = $(Join-Path ([IO.Path]::GetTempPath()) 'rewards-farmer-build'),
	[string]$Python = '',
	[string]$Iscc = ''
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if (-not $Python) { $Python = Join-Path $repo '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python)) { throw 'Pass -Python with the app/build environment interpreter.' }
if (-not $Iscc) {
	$candidates = @((Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'), (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'))
	$Iscc = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $Iscc) { throw 'Inno Setup is missing. Install the per-user compiler before building.' }

$sha = & git -C $repo rev-parse --verify "$Commit^{commit}"
if ($LASTEXITCODE -ne 0 -or $sha -notmatch '^[0-9a-f]{40}$') { throw 'Commit does not resolve to one commit.' }
$identity = & $Python -c "import sys,struct,PyInstaller; assert sys.version_info[:2] == (3,12); assert struct.calcsize('P') == 8; assert PyInstaller.__version__ == '6.22.3'; print(sys.version); print('PyInstaller ' + PyInstaller.__version__)"
if ($LASTEXITCODE -ne 0) { throw 'Use 64-bit Python 3.12 and PyInstaller 6.22.3.' }
$identity

$support = Join-Path $PSScriptRoot 'build_support.py'
$work = & $Python $support workspace --scratch $ScratchRoot --commit $sha
if ($LASTEXITCODE -ne 0) { throw 'Could not create an empty scratch workspace.' }
$archive = Join-Path $work 'source.zip'
& git -C $repo archive --format=zip "--output=$archive" $sha
if ($LASTEXITCODE -ne 0) { throw 'git archive failed.' }
& $Python $support prepare --work $work --commit $sha --tools $PSScriptRoot
if ($LASTEXITCODE -ne 0) { throw 'Clean-source preparation failed.' }

$env:RF_SOURCE_ROOT = Join-Path $work 'source'
$env:RF_BUILD_ASSETS = Join-Path $work 'assets'
$env:PYINSTALLER_CONFIG_DIR = Join-Path $work 'pyinstaller-cache'
$env:PYSTRAY_BACKEND = 'win32'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:TEMP = Join-Path $work 'tmp'
$env:TMP = $env:TEMP
$env:PYTHONUTF8 = '1'
$version = & $Python $support version --root $env:RF_SOURCE_ROOT
if ($LASTEXITCODE -ne 0) { throw 'rfapp.__version__ could not be read.' }

& $Python $support freeze --work $work --spec (Join-Path $PSScriptRoot 'RewardsFarmer.spec')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed. Read build.log in the scratch workspace.' }
& $Python $support manifest --work $work
if ($LASTEXITCODE -ne 0) { throw 'Bundle validation or manifest failed.' }

$bundle = Join-Path $work 'dist\RewardsFarmer'
$output = Join-Path $work 'installer'
& $Iscc '/?'
$timer = [Diagnostics.Stopwatch]::StartNew()
& $Iscc "/DAppVersion=$version" "/DBundleDir=$bundle" "/DOutputDir=$output" (Join-Path $PSScriptRoot 'RewardsFarmer.iss')
if ($LASTEXITCODE -ne 0) { throw 'ISCC failed.' }
$timer.Stop()
& $Python $support finish --work $work --version $version --compile-seconds $timer.Elapsed.TotalSeconds.ToString([Globalization.CultureInfo]::InvariantCulture)
if ($LASTEXITCODE -ne 0) { throw 'Installer result validation failed.' }
"BUILD_WORKSPACE=$work"
