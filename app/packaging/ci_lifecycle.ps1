param(
	[Parameter(Mandatory = $true)][string]$Setup,
	[Parameter(Mandatory = $true)][string]$Scratch
)

# This real app/task proof belongs to an ephemeral CI account, never a workstation.
if ($env:GITHUB_ACTIONS -ne 'true') { throw 'Run this lifecycle only on a GitHub Actions runner.' }
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$installed = Join-Path $env:LOCALAPPDATA 'Programs\RewardsFarmer'
$exe = Join-Path $installed 'RewardsFarmer.exe'
$appKey = 'HKCU:\Software\RewardsFarmer'
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$approvedKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run'
$uninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{C3A933C3-9605-469E-A520-F2ABFC859D29}_is1'
$taskName = $null
$app = $null
if (Test-Path -LiteralPath $installed) { throw 'The runner already has an app installation.' }
if (Test-Path -LiteralPath $appKey) { throw 'The runner already has app task metadata.' }
New-Item -ItemType Directory -Path $Scratch | Out-Null
$dataHome = Join-Path $Scratch 'data-home'
New-Item -ItemType Directory -Path $dataHome | Out-Null
$utf8 = New-Object Text.UTF8Encoding($false)
$sentinel = Join-Path $dataHome 'sentinel.txt'
[IO.File]::WriteAllText($sentinel, 'synthetic data, preserve on uninstall', $utf8)
$sentinelHash = (Get-FileHash -LiteralPath $sentinel -Algorithm SHA256).Hash
$preferences = @{ v = 1; update_check = $false; close_to_tray = $false; tray_hint_shown = $true; setup_done = $true } | ConvertTo-Json
[IO.File]::WriteAllText((Join-Path $dataHome 'app-prefs.json'), $preferences, $utf8)
$scheduleDay = (Get-Date).AddDays(2)
$day = $scheduleDay.ToString('ddd', [Globalization.CultureInfo]::InvariantCulture).ToLowerInvariant()
$configuration = @('REWARDS_ACCOUNTS=synthetic', 'REWARDS_SCHEDULE_MODE=automatic', 'REWARDS_SCHEDULE_WINDOW=09:45-21:45', "REWARDS_SCHEDULE_DAYS=$day", 'REWARDS_NOTIFY=never', 'QUERY_SOURCE=trends') -join "`n"
[IO.File]::WriteAllText((Join-Path $dataHome '.env'), $configuration, $utf8)
$env:REWARDS_FARMER_DATA_HOME = $dataHome
foreach ($name in @('REWARDS_ACCOUNTS', 'REWARDS_SCHEDULE_MODE', 'REWARDS_BROWSER_TESTS', 'REWARDS_APP_UI_TESTS')) { [Environment]::SetEnvironmentVariable($name, $null, 'Process') }

try {
	$setupProcess = Start-Process -FilePath $Setup -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', "/LOG=`"$(Join-Path $Scratch 'install.log')`"") -Wait -PassThru
	if ($setupProcess.ExitCode -ne 0) { throw "Setup failed ($($setupProcess.ExitCode))." }
	if (-not (Test-Path -LiteralPath $exe)) { throw 'The installed executable is missing.' }
	$installedBytes = (Get-ChildItem -LiteralPath $installed -File -Recurse | Measure-Object -Property Length -Sum).Sum
	"Installed size: $installedBytes bytes, including the uninstaller, before user data"
	$app = Start-Process -FilePath $exe -WorkingDirectory $env:WINDIR -PassThru
	$deadline = (Get-Date).AddSeconds(60)
	do {
		if (Test-Path -LiteralPath $appKey) { $taskName = (Get-ItemProperty -LiteralPath $appKey -Name TaskName -ErrorAction SilentlyContinue).TaskName }
		if ($taskName) { break }
		Start-Sleep -Milliseconds 250
		$app.Refresh()
	} while ((Get-Date) -lt $deadline -and -not $app.HasExited)
	if ($taskName -cnotmatch '^RewardsFarmerApp_[0-9a-f]{8}$') { throw 'The installed app did not record its own task.' }

	$xmlText = & schtasks.exe /Query /TN $taskName /XML
	if ($LASTEXITCODE -ne 0) { throw 'The installed app task cannot be queried.' }
	$xmlText | Set-Content -LiteralPath (Join-Path $Scratch 'installed-task.xml') -Encoding Unicode
	[xml]$xml = $xmlText -join "`n"
	$namespace = New-Object Xml.XmlNamespaceManager($xml.NameTable)
	$namespace.AddNamespace('t', 'http://schemas.microsoft.com/windows/2004/02/mit/task')
	$command = $xml.SelectSingleNode('/t:Task/t:Actions/t:Exec/t:Command', $namespace).InnerText
	$arguments = $xml.SelectSingleNode('/t:Task/t:Actions/t:Exec/t:Arguments', $namespace).InnerText
	if ($command -ne $exe -or $arguments -ne '--scheduled') { throw 'The task does not run the installed executable with --scheduled.' }
	foreach ($boundary in $xml.SelectNodes('//t:StartBoundary', $namespace)) {
		$next = [datetime]$boundary.InnerText
		while ($next.DayOfWeek -ne $scheduleDay.DayOfWeek) { $next = $next.AddDays(1) }
		if ($next -le (Get-Date).AddHours(2)) { throw 'The test task might fire during acceptance.' }
	}

	# Registry cleanup is independent of whether a schedule currently exists.
	New-Item -Path $runKey -Force | Out-Null
	New-ItemProperty -LiteralPath $runKey -Name $taskName -Value ('"' + $exe + '" --tray') -PropertyType String -Force | Out-Null
	New-Item -Path $approvedKey -Force | Out-Null
	New-ItemProperty -LiteralPath $approvedKey -Name $taskName -Value ([byte[]](2,0,0,0,0,0,0,0,0,0,0,0)) -PropertyType Binary -Force | Out-Null
	$app.Refresh()
	if (-not $app.HasExited) {
		$closeDeadline = (Get-Date).AddSeconds(20)
		do { $app.Refresh(); if ($app.MainWindowHandle -ne 0) { break }; Start-Sleep -Milliseconds 100 } while ((Get-Date) -lt $closeDeadline)
		if (-not $app.CloseMainWindow() -or -not $app.WaitForExit(30000)) { throw 'The test app did not close normally.' }
	}
	$app = $null
} finally {
	if ($app) {
		$app.Refresh()
		if (-not $app.HasExited) { $app.CloseMainWindow() | Out-Null; if (-not $app.WaitForExit(30000)) { Stop-Process -Id $app.Id -Force } }
	}
	$uninstaller = Join-Path $installed 'unins000.exe'
	if (Test-Path -LiteralPath $uninstaller) {
		$uninstall = Start-Process -FilePath $uninstaller -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=`"$(Join-Path $Scratch 'uninstall.log')`"") -Wait -PassThru
		if ($uninstall.ExitCode -ne 0) { throw "Uninstall failed ($($uninstall.ExitCode))." }
	}
}

$deadline = (Get-Date).AddSeconds(20)
while ((Test-Path -LiteralPath $installed) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 100 }
if (-not $taskName) { throw 'Task registration evidence is missing.' }
$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
	& schtasks.exe /Query /TN $taskName /XML 2>&1 | Out-Null
	$queryCode = $LASTEXITCODE
} finally {
	$ErrorActionPreference = $previousErrorAction
}
if ($queryCode -eq 0) { throw 'The app task survived uninstall.' }
if (Get-ItemProperty -LiteralPath $runKey -Name $taskName -ErrorAction SilentlyContinue) { throw 'The app Run value survived uninstall.' }
if (Get-ItemProperty -LiteralPath $approvedKey -Name $taskName -ErrorAction SilentlyContinue) { throw 'StartupApproved survived uninstall.' }
foreach ($path in @($appKey, $uninstallKey, $installed, (Join-Path ([Environment]::GetFolderPath('Programs')) 'Rewards Farmer.lnk'))) {
	if (Test-Path -LiteralPath $path) { throw "Uninstall left $path" }
}
if (-not (Test-Path -LiteralPath $dataHome) -or (Get-FileHash -LiteralPath $sentinel -Algorithm SHA256).Hash -ne $sentinelHash) { throw 'Uninstall changed user data.' }
@{ task = $taskName; command = $command; arguments = $arguments; installed_bytes = $installedBytes; task_absent = $true; run_absent = $true; startup_approved_absent = $true; app_key_absent = $true; install_absent = $true; data_preserved = $true } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Scratch 'lifecycle.json') -Encoding UTF8
'Installed app task and uninstall lifecycle PASS'
exit 0
