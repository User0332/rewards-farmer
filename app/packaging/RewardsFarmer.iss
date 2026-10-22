#ifndef AppVersion
  #error AppVersion must come from rfapp.__version__ through build.ps1
#endif
#ifndef BundleDir
  #error BundleDir must point to the validated onedir bundle
#endif
#ifndef OutputDir
  #error OutputDir must point to the scratch installer output
#endif

[Setup]
AppId={{C3A933C3-9605-469E-A520-F2ABFC859D29}
AppName=Rewards Farmer
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\RewardsFarmer
DisableDirPage=yes
DefaultGroupName=Rewards Farmer
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=RewardsFarmer-{#AppVersion}-windows-x64-Setup
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\RewardsFarmer.exe
CloseApplications=no
AppMutex=RewardsFarmer.Installed
RestartApplications=no
SetupLogging=yes
WizardStyle=modern
; https://jrsoftware.org/ishelp/topic_setup_languagedetectionmethod.htm
; https://jrsoftware.org/ishelp/topic_setup_showlanguagedialog.htm
; https://jrsoftware.org/ishelp/topic_setup_usepreviouslanguage.htm
LanguageDetectionMethod=uilanguage
ShowLanguageDialog=no
UsePreviousLanguage=no

[Languages]
; The first language is the fallback. No official Chinese file ships in Inno 6.7.3.
; https://jrsoftware.org/ishelp/topic_languagessection.htm
Name: "en"; MessagesFile: "compiler:Default.isl,languages\en.isl"
Name: "de"; MessagesFile: "compiler:Languages\German.isl,languages\de.isl"
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl,languages\es.isl"
Name: "fr"; MessagesFile: "compiler:Languages\French.isl,languages\fr.isl"
Name: "it"; MessagesFile: "compiler:Languages\Italian.isl,languages\it.isl"
; Inno language identifiers allow underscores, not locale-tag hyphens.
Name: "pt_BR"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl,languages\pt-BR.isl"
Name: "ja"; MessagesFile: "compiler:Languages\Japanese.isl,languages\ja.isl"
Name: "ar"; MessagesFile: "compiler:Languages\Arabic.isl,languages\ar.isl"

; InstallDelete runs only after AppMutex and PrepareToInstall permit installation.
; https://jrsoftware.org/ishelp/topic_installdeletesection.htm
; https://jrsoftware.org/ishelp/topic_scriptevents.htm
; https://jrsoftware.org/ishelp/topic_setup_appmutex.htm
[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\Rewards Farmer"; Filename: "{app}\RewardsFarmer.exe"; WorkingDir: "{app}"

[Code]
const
  TaskPrefix = 'RewardsFarmerApp_';
  AppKey = 'Software\RewardsFarmer';
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';
  ApprovedKey = 'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run';
  WebViewKey = 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  BootstrapperURL = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703';

function IsAppName(Name: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  if Length(Name) <> Length(TaskPrefix) + 8 then exit;
  if Copy(Name, 1, Length(TaskPrefix)) <> TaskPrefix then exit;
  for I := Length(TaskPrefix) + 1 to Length(Name) do
    if Pos(Copy(Name, I, 1), '0123456789abcdef') = 0 then exit;
  Result := True;
end;

function StartsWithInstalledExe(Command: String): Boolean;
var
  ExePath: String;
  N: Integer;
begin
  Command := Trim(Command);
  ExePath := ExpandConstant('{app}\RewardsFarmer.exe');
  Result := False;
  if Copy(Command, 1, 1) = '"' then
  begin
    N := Pos('"', Copy(Command, 2, Length(Command)));
    if N = 0 then exit;
    if CompareText(Copy(Command, 2, N - 1), ExePath) <> 0 then exit;
    if Length(Command) > N + 1 then
      if (Command[N + 2] <> ' ') and (Command[N + 2] <> #9) then exit;
    Result := True;
  end
  else
  begin
    N := Length(ExePath);
    if CompareText(Copy(Command, 1, N), ExePath) <> 0 then exit;
    if Length(Command) > N then
      if (Command[N + 1] <> ' ') and (Command[N + 1] <> #9) then exit;
    Result := True;
  end;
end;

function HasRuntimeVersion(Root: Integer): Boolean;
var
  Version: String;
  PackedVersion: Int64;
begin
  Result := False;
  if RegQueryStringValue(Root, WebViewKey, 'pv', Version) then
    if StrToVersion(Version, PackedVersion) then
      Result := PackedVersion > 0;
end;

function WebView2Installed(): Boolean;
begin
  { Microsoft documents the HKLM 32-bit view and the per-user key. }
  Result := HasRuntimeVersion(HKLM32) or HasRuntimeVersion(HKCU32) or HasRuntimeVersion(HKCU64);
end;

function InstalledAppIdle(AllowWmiFailure: Boolean): Boolean;
var
  Locator, WMI, Processes, Process: Variant;
  I: Integer;
  ExpectedPath: String;
begin
  Result := True;
  ExpectedPath := ExpandConstant('{app}\RewardsFarmer.exe');
  if not FileExists(ExpectedPath) then exit;
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    WMI := Locator.ConnectServer('', 'root\CIMV2');
    Processes := WMI.ExecQuery('SELECT ExecutablePath FROM Win32_Process WHERE Name = ''RewardsFarmer.exe''');
    for I := 0 to Processes.Count - 1 do
    begin
      Process := Processes.ItemIndex(I);
      if VarIsNull(Process.ExecutablePath) then
      begin
        Result := False;
        exit;
      end;
      if CompareText(Process.ExecutablePath, ExpectedPath) = 0 then
      begin
        Result := False;
        exit;
      end;
    end;
  except
    if AllowWmiFailure then
      Log('WMI idle query failed; uninstall relies on the startup AppMutex check.')
    else
      Log('WMI idle query failed; installation is blocked.');
    Result := AllowWmiFailure;
  end;
end;

procedure InitializeWizard();
begin
  Log('Selected Setup language: ' + ActiveLanguage());
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
  Bootstrapper, VerifyPath, VerifyCommand: String;
begin
  Result := '';
  if not InstalledAppIdle(False) then
  begin
    Result := CustomMessage('QuitBeforeUpdate');
    exit;
  end;
  if WebView2Installed() then exit;
  { Silent installation never implies consent to a separate download. }
  if WizardSilent() then
  begin
    Result := CustomMessage('WebView2Missing');
    exit;
  end;
  if MsgBox(CustomMessage('WebView2Prompt'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) <> IDYES then
  begin
    Result := CustomMessage('WebView2Required');
    exit;
  end;
  Bootstrapper := ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe');
  try
    DownloadTemporaryFile(BootstrapperURL, 'MicrosoftEdgeWebview2Setup.exe', '', nil);
  except
    Result := CustomMessage('WebView2DownloadFailed');
    exit;
  end;
  VerifyPath := Bootstrapper;
  StringChangeEx(VerifyPath, '''', '''''', True);
  VerifyCommand := '-NoProfile -NonInteractive -Command "' +
    '$s=Get-AuthenticodeSignature -LiteralPath ''' + VerifyPath + '''; ' +
    'if ($s.Status -ne ''Valid'' -or $null -eq $s.SignerCertificate -or $s.SignerCertificate.Subject -notmatch ''(^|,\s*)O=Microsoft Corporation(,|$)'') { exit 1 }; exit 0"';
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), VerifyCommand, '', SW_HIDE, ewWaitUntilTerminated, Code) then
    Code := -1;
  if Code <> 0 then
  begin
    Result := CustomMessage('WebView2InvalidSignature');
    exit;
  end;
  if not Exec(Bootstrapper, '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, Code) then
    Code := -1;
  if (Code <> 0) or not WebView2Installed() then
    Result := CustomMessage('WebView2InstallFailed');
end;

function InitializeUninstall(): Boolean;
begin
  Log('Selected uninstall language: ' + ActiveLanguage());
  Result := InstalledAppIdle(True);
  if not Result then
    MsgBox(CustomMessage('QuitBeforeUninstall'), mbError, MB_OK);
end;

procedure RemoveAppIntegration();
var
  Name, Command: String;
  Names: TArrayOfString;
  I, Code: Integer;
begin
  if RegQueryStringValue(HKCU, AppKey, 'TaskName', Name) then
    if IsAppName(Name) then
    begin
      if not Exec(ExpandConstant('{sys}\schtasks.exe'), '/Delete /TN "' + Name + '" /F', '', SW_HIDE, ewWaitUntilTerminated, Code) then
        Log('App task deletion could not start.')
      else
        Log(Format('App task deletion returned %d.', [Code]));
    end;
  if RegGetValueNames(HKCU, RunKey, Names) then
    for I := 0 to GetArrayLength(Names) - 1 do
    begin
      Name := Names[I];
      if IsAppName(Name) then
        if RegQueryStringValue(HKCU, RunKey, Name, Command) then
          if StartsWithInstalledExe(Command) then
          begin
            if not RegDeleteValue(HKCU, RunKey, Name) then Log('Could not delete app Run value.');
            if RegValueExists(HKCU, ApprovedKey, Name) then
              if not RegDeleteValue(HKCU, ApprovedKey, Name) then Log('Could not delete app StartupApproved value.');
          end;
    end;
  if RegKeyExists(HKCU, AppKey) then
    if not RegDeleteKeyIncludingSubkeys(HKCU, AppKey) then Log('Could not delete app registry key.');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then RemoveAppIntegration();
end;
