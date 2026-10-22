<#
Installs rewards-farmer on Windows, or updates an install made with it.

	irm https://raw.githubusercontent.com/User0332/rewards-farmer/main/install.ps1 | iex

Everything goes into one folder, %LOCALAPPDATA%\rewards-farmer unless
REWARDS_FARMER_HOME names another one. Started from a clone, it sets up that
clone instead of downloading the code:

	powershell -ExecutionPolicy Bypass -File .\install.ps1

Running it again updates the code and leaves .env, data-dir and the visual
search image as they are.
The desktop app uses Setup.exe and has its own schedule. This script sets up
the CLI task, so do not enable both schedules for the same browser profile.
#>
param(
	[string]$InstallDir = $(if ($env:REWARDS_FARMER_HOME) { $env:REWARDS_FARMER_HOME } else { Join-Path $env:LOCALAPPDATA "rewards-farmer" }),

	# What to download, for trying a fork or a branch before it is merged.
	[string]$Repo = "User0332/rewards-farmer",
	[string]$Branch = "main",

	# A time such as 09:30 sets up the daily run without asking.
	[string]$DailyAt = "",
	[string]$TaskName = "rewards-farmer",

	[switch]$NoConfig,
	[switch]$NoSchedule,
	[switch]$NoShortcut
)

function Install-RewardsFarmer([bool]$InstallDirGiven) {
	$ErrorActionPreference = "Stop"
	$ProgressPreference = "SilentlyContinue"
	[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

	# The helpers live in here so that running this through iex leaves nothing
	# but this one function behind in the window it ran in.

	function Write-Step([string]$Text) {
		Write-Host ""
		Write-Host "== $Text" -ForegroundColor Cyan
	}

	function Test-Python([string]$Exe, [string[]]$Arguments = @()) {
		$ErrorActionPreference = "Continue"

		try {
			$out = @(& $Exe @Arguments -c "import sys; print(sys.version_info >= (3, 12)); print(sys.executable)" 2>$null)
		} catch {
			return $null
		}

		if ($LASTEXITCODE -eq 0 -and $out.Count -ge 2 -and $out[0] -eq "True") {
			return $out[1]
		}

		return $null
	}

	function Find-Python {
		# The py launcher knows every install from python.org. The python.exe in
		# WindowsApps is only a stub that opens the Microsoft Store, so it is skipped.
		if (Get-Command py -ErrorAction SilentlyContinue) {
			$found = Test-Python "py" @("-3")

			if ($found) { return $found }
		}

		$python = Get-Command python -ErrorAction SilentlyContinue

		if ($python -and $python.Source -notlike "*\WindowsApps\*") {
			$found = Test-Python $python.Source

			if ($found) { return $found }
		}

		# Python installed a moment ago by winget is not on this window's PATH yet.
		foreach ($exe in @(Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending)) {
			$found = Test-Python $exe.FullName

			if ($found) { return $found }
		}

		return $null
	}

	function Install-Python {
		if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
			throw "No Python 3.12 or newer was found, and winget is not available to install it. Install Python from https://www.python.org/downloads/windows/ and run this again."
		}

		Write-Step "Installing Python 3.13 for this user with winget"
		& winget install --id Python.Python.3.13 --exact --scope user --silent --accept-package-agreements --accept-source-agreements

		if ($LASTEXITCODE -ne 0) {
			throw "winget could not install Python (exit code $LASTEXITCODE). Install it from https://www.python.org/downloads/windows/ and run this again."
		}
	}

	function Get-Code([string]$Destination, [string]$FromRepo, [string]$FromBranch) {
		Write-Step "Downloading $FromRepo ($FromBranch)"

		$zip = Join-Path ([IO.Path]::GetTempPath()) "rewards-farmer-$([guid]::NewGuid()).zip"
		$unpacked = "$zip-files"

		try {
			Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/$FromRepo/archive/refs/heads/$FromBranch.zip" -OutFile $zip
			Expand-Archive -LiteralPath $zip -DestinationPath $unpacked

			# The archive holds a single folder named after the repository and branch.
			$top = @(Get-ChildItem -LiteralPath $unpacked -Directory)[0].FullName

			New-Item -ItemType Directory -Force -Path $Destination | Out-Null
			Copy-Item -Path (Join-Path $top "*") -Destination $Destination -Recurse -Force
		} finally {
			Remove-Item -LiteralPath $zip, $unpacked -Recurse -Force -ErrorAction SilentlyContinue
		}
	}

	function Install-Dependencies([string]$Python, [string]$Root) {
		# Poetry gets an environment of its own inside the install folder, so the
		# Python installation itself stays untouched and the folder holds everything.
		$tools = Join-Path $Root ".venv-poetry"

		if (-not (Test-Path -LiteralPath (Join-Path $tools "Scripts\python.exe"))) {
			& $Python -m venv $tools

			if ($LASTEXITCODE -ne 0) { throw "Could not create $tools." }
		}

		& (Join-Path $tools "Scripts\python.exe") -m pip install --quiet --upgrade pip poetry

		if ($LASTEXITCODE -ne 0) { throw "Could not install Poetry into $tools." }

		$poetry = Join-Path $tools "Scripts\poetry.exe"
		$previous = $env:POETRY_VIRTUALENVS_IN_PROJECT

		# The project environment goes to .venv beside the code, built from the
		# Python found above rather than whichever one comes first on PATH.
		$env:POETRY_VIRTUALENVS_IN_PROJECT = "true"

		try {
			& $poetry env use $Python

			if ($LASTEXITCODE -ne 0) { throw "Poetry could not create the environment in $Root\.venv." }

			& $poetry install --no-interaction

			if ($LASTEXITCODE -ne 0) { throw "Poetry could not install the dependencies." }
		} finally {
			$env:POETRY_VIRTUALENVS_IN_PROJECT = $previous
		}
	}

	function ConvertTo-Time([string]$Text) {
		$parsed = [datetime]::MinValue

		if ([datetime]::TryParseExact($Text.Trim(), [string[]]@("H:mm", "HH:mm"), [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::None, [ref]$parsed)) {
			return $parsed
		}

		return $null
	}

	function Read-DailyTime {
		while ($true) {
			$answer = "$(Read-Host 'Run rewards-farmer every day? Enter a time such as 10:30, or press Enter to skip')".Trim()

			if (-not $answer) { return $null }

			$at = ConvertTo-Time $answer

			if ($at) { return $at }

			Write-Host "That is not a time like 10:30."
		}
	}

	function Register-DailyRun([string]$Python, [string]$Root, [datetime]$At, [string]$Name) {
		# Start at the next such time rather than one earlier today, which the
		# catch-up setting below would otherwise run straight away.
		$first = [datetime]::Today.Add($At.TimeOfDay)

		if ($first -le (Get-Date)) { $first = $first.AddDays(1) }

		$action = New-ScheduledTaskAction -Execute $Python -Argument "`"$(Join-Path $Root 'src\main.py')`" --unattended" -WorkingDirectory $Root
		$trigger = New-ScheduledTaskTrigger -Daily -At $first

		# A time missed while the computer was off runs once it is back on. The
		# default priority of 7 is below normal, and on a busy machine the waits
		# in the tasks ran out at that priority.
		$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2) -Priority 5

		# Edge opens a visible window, so the run needs the logged in user's desktop.
		$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited

		Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "Runs rewards-farmer once a day" -Force | Out-Null

		Write-Host "rewards-farmer now runs every day at $($At.ToString('HH:mm')), as '$Name' in Task Scheduler."
	}

	if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
		throw "This installer is for Windows. On Linux and macOS follow the steps in the README, or use Docker."
	}

	$root = $InstallDir
	$fromClone = $PSScriptRoot -and (Test-Path -LiteralPath (Join-Path $PSScriptRoot "pyproject.toml")) -and -not $InstallDirGiven

	if ($fromClone) {
		$root = $PSScriptRoot
		Write-Host "Setting up the clone in $root"
	} elseif ((Test-Path -LiteralPath $root) -and @(Get-ChildItem -LiteralPath $root -Force).Count -gt 0 -and -not (Test-Path -LiteralPath (Join-Path $root "pyproject.toml"))) {
		throw "$root already exists and does not hold rewards-farmer. Set REWARDS_FARMER_HOME to another folder and run this again."
	}

	$python = Find-Python

	if (-not $python) {
		Install-Python
		$python = Find-Python

		if (-not $python) {
			throw "Python was installed but cannot be found yet. Open a new PowerShell window and run this again."
		}
	}

	Write-Host "Using Python at $python"

	if (-not $fromClone) {
		Get-Code $root $Repo $Branch
	}

	Push-Location -LiteralPath $root

	try {
		Write-Step "Installing the dependencies, which takes a few minutes the first time"
		Install-Dependencies $python $root

		$venvPython = Join-Path $root ".venv\Scripts\python.exe"
		$mainScript = Join-Path $root "src\main.py"
		$configScript = Join-Path $root "src\init-config.py"

		if (-not (Test-Path -LiteralPath (Join-Path $root "visual_search.jpg"))) {
			Write-Step "Fetching an image for the visual search task"
			& $venvPython (Join-Path $root "src\random_image_for_visual_search.py")

			if ($LASTEXITCODE -ne 0) {
				Write-Warning "There is no visual search image yet. Every other task still runs, and src\random_image_for_visual_search.py fetches one later."
			}
		}

		if ($NoConfig) {
			Write-Host "Skipping the configuration."
		} elseif (Test-Path -LiteralPath (Join-Path $root ".env")) {
			Write-Host "Keeping the configuration in $root\.env"
		} else {
			Write-Step "Configuring rewards-farmer and signing in"
			& $venvPython $configScript

			if ($LASTEXITCODE -ne 0) {
				throw "The configuration script stopped (exit code $LASTEXITCODE). Run it again with: & `"$venvPython`" `"$configScript`""
			}
		}

		if (-not $NoShortcut) {
			$shell = New-Object -ComObject WScript.Shell
			$link = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Programs")) "Rewards Farmer.lnk"))
			$link.TargetPath = $venvPython
			$link.Arguments = "`"$mainScript`""
			$link.WorkingDirectory = $root
			$link.Description = "Run rewards-farmer now"
			$link.Save()
			Write-Host "Added Rewards Farmer to the Start menu."
		}

		if ($NoSchedule) {
			Write-Host "Skipping the daily run."
		} elseif ($DailyAt) {
			Write-Host "The desktop app has its own schedule. Do not enable both for the same browser profile."
			$at = ConvertTo-Time $DailyAt

			if (-not $at) { throw "-DailyAt takes a time such as 10:30, not '$DailyAt'." }

			Register-DailyRun $venvPython $root $at $TaskName
		} elseif (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
			Write-Host "The daily run '$TaskName' already exists and stays as it is."
		} else {
			Write-Host "The desktop app has its own schedule. Do not enable both for the same browser profile."
			$at = Read-DailyTime

			if ($at) { Register-DailyRun $venvPython $root $at $TaskName }
		}

		Write-Step "Done"
		Write-Host "Installed in $root"
		Write-Host "Run it now with:   & `"$venvPython`" `"$mainScript`""
		Write-Host "Manage accounts:   & `"$venvPython`" `"$configScript`" --manage-accounts"
		Write-Host "Update it by running the install line again."
	} finally {
		Pop-Location
	}
}

# A folder named on the command line or in REWARDS_FARMER_HOME wins over the
# clone the script sits in.
Install-RewardsFarmer ($PSBoundParameters.ContainsKey("InstallDir") -or [bool]$env:REWARDS_FARMER_HOME)
