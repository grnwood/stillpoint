; StillPoint installer - Inno Setup
; Requires: version.iss generated from sp/__init__.py

#include "version.iss"

[Setup]
AppId={{E2D0C38B-BA4E-4C9D-9D75-2E6E7F9B6C7E}
AppName=StillPoint
AppVersion={#AppVersion}
AppPublisher=Joe Greenwood
DefaultDirName={localappdata}\StillPoint
DefaultGroupName=StillPoint
DisableProgramGroupPage=yes
OutputBaseFilename=StillPointSetup-x86_64
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
UninstallDisplayIcon={app}\stillpoint\stillpoint.exe
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

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
