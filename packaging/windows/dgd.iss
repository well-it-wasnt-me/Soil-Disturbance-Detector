; Inno Setup script for the Windows installer. Run after PyInstaller has built dist\DGD:
;
;   iscc /DAppVersion=0.2.0 packaging\windows\dgd.iss
;
; Installs for the current user only (no administrator prompt) into
; %LOCALAPPDATA%\Programs, adds Start menu and desktop icons, and offers to
; start the app when it is done. Results live in Documents and are never
; touched by install, upgrade or uninstall.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Disturbed Ground Detector"

[Setup]
AppId={{6B0E1C1A-3E0B-4C55-9A2E-0D6F2B9C4E71}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Soil-Disturbance-Detector contributors
AppPublisherURL=https://github.com/well-it-wasnt-me/Soil-Disturbance-Detector
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\dist
OutputBaseFilename=DGD-Setup-{#AppVersion}
SetupIconFile=dgd.ico
UninstallDisplayIcon={app}\DGD.exe
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; An upgrade replaces the whole bundle, so files a newer version dropped do not linger.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\..\dist\DGD\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\DGD.exe"; WorkingDir: "{app}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\DGD.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\DGD.exe"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
