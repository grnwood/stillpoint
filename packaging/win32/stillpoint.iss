; StillPoint installer - Inno Setup
; Requires: version.iss generated from sp/__init__.py

#include "version.iss"

[Setup]
AppId={{E2D0C38B-BA4E-4C9D-9D75-2E6E7F9B6C7E}
AppName=StillPoint
AppVersion={#AppVersion}
AppPublisher=Joe Greenwood
DefaultDirName={localappdata}\Programs\StillPoint
DefaultGroupName=StillPoint
DisableDirPage=yes
DisableProgramGroupPage=yes
UsePreviousAppDir=no
OutputBaseFilename=StillPointSetup-win-x64
SetupIconFile=..\..\sp\assets\icons\sp-full-transparent.ico
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
UninstallDisplayIcon={app}\stillpoint\stillpoint.exe
PrivilegesRequired=lowest
CloseApplications=yes
RestartApplications=no

[InstallDelete]
; Always replace each PyInstaller tree wholesale. Leaving removed Python
; modules or Qt DLLs behind can create a mixed-version application.
Type: filesandordirs; Name: "{app}\stillpoint"
Type: filesandordirs; Name: "{app}\stillpoint-capture"

; Migrate the older PowerShell layout, which copied the main bundle directly
; into %LOCALAPPDATA%\Programs\StillPoint.
Type: files; Name: "{app}\stillpoint.exe"
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\install-win32.ps1"
Type: files; Name: "{app}\README.txt"
Type: files; Name: "{app}\LICENSE"
Type: files; Name: "{app}\NOTICE"
Type: files; Name: "{app}\StillPoint.ico"

; Inno releases before the unified layout installed here. This directory
; contains application binaries and its old uninstaller only; preferences and
; vaults live in the user's profile and are intentionally outside it.
Type: filesandordirs; Name: "{localappdata}\StillPoint"

; Remove old launch points before [Icons] recreates the canonical shortcuts.
Type: files; Name: "{userprograms}\StillPoint.lnk"
Type: files; Name: "{userprograms}\StillPoint Capture.lnk"
Type: filesandordirs; Name: "{userprograms}\StillPoint"
Type: files; Name: "{userdesktop}\StillPoint.lnk"
Type: files; Name: "{userdesktop}\StillPoint Capture.lnk"

; A taskbar/Start pin stores its old executable path. Unpin it once during
; layout migration so Windows cannot keep resolving an obsolete installation.
Type: files; Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar\StillPoint*.lnk"; Check: HadLegacyInstall
Type: files; Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\User Pinned\StartMenu\StillPoint*.lnk"; Check: HadLegacyInstall

[Dirs]
Name: "{app}\stillpoint"
Name: "{app}\stillpoint-capture"

[Files]
Source: "..\..\dist\stillpoint\*"; DestDir: "{app}\stillpoint"; Flags: recursesubdirs createallsubdirs
Source: "..\..\dist\stillpoint-capture\*"; DestDir: "{app}\stillpoint-capture"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\StillPoint"; Filename: "{app}\stillpoint\stillpoint.exe"
Name: "{userprograms}\StillPoint Capture"; Filename: "{app}\stillpoint-capture\stillpoint-capture.exe"
Name: "{userdesktop}\StillPoint"; Filename: "{app}\stillpoint\stillpoint.exe"; Tasks: desktopicon
Name: "{userdesktop}\StillPoint Capture"; Filename: "{app}\stillpoint-capture\stillpoint-capture.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop icon"; GroupDescription: "Additional icons:"

[Run]
Filename: "{app}\stillpoint\stillpoint.exe"; Description: "Launch StillPoint"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove runtime leftovers inside application-only bundle directories. User
; settings, vaults, templates, and caches are stored outside {app}.
Type: filesandordirs; Name: "{app}\stillpoint"
Type: filesandordirs; Name: "{app}\stillpoint-capture"
Type: files; Name: "{app}\installed-version.txt"

[Code]
var
  LegacyInstallWasPresent: Boolean;

function InitializeSetup: Boolean;
begin
  { Older installers allowed an all-users override. A lowest-privilege setup
    cannot safely remove that machine-wide copy, and installing beside it
    would leave Windows with two StillPoint registrations again. }
  if RegKeyExists(
    HKLM,
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\{E2D0C38B-BA4E-4C9D-9D75-2E6E7F9B6C7E}_is1'
  ) then
  begin
    SuppressibleMsgBox(
      'An older all-users StillPoint installation is still registered.' + #13#10 + #13#10 +
      'Open Windows Settings > Apps > Installed apps, uninstall StillPoint, then run this installer again.' + #13#10 +
      'Your vaults and preferences will not be removed.',
      mbCriticalError,
      MB_OK,
      IDOK
    );
    Result := False;
    Exit;
  end;

  LegacyInstallWasPresent :=
    DirExists(ExpandConstant('{localappdata}\StillPoint')) or
    FileExists(ExpandConstant('{localappdata}\Programs\StillPoint\stillpoint.exe'));
  Result := True;
end;

function HadLegacyInstall: Boolean;
begin
  Result := LegacyInstallWasPresent;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    SaveStringToFile(
      ExpandConstant('{app}\installed-version.txt'),
      '{#AppVersion}' + #13#10,
      False
    );
end;
