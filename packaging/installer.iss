; Inno Setup script for the ytuploader desktop app.
;
; Built by build_installer.ps1, which passes /DAppVersion=<ytuploader.__version__>
; and expects PyInstaller's one-folder build in dist\ytuploader\.
;
; Per-user install (no admin): the app goes to %LOCALAPPDATA%\Programs\ytuploader,
; its data (config, upload history, tokens, browser profile) to
; %LOCALAPPDATA%\ytuploader. Uninstalling keeps the data unless the user says
; otherwise.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "YT Capture Uploader"
#define AppExe "ytuploader.exe"

[Setup]
AppId={{6F0C2A47-6C1B-4E0B-9D6B-2F3D5A7C9E11}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=LOCKhart07
DefaultDirName={autopf}\ytuploader
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=ytuploader-setup-{#AppVersion}
SetupIconFile=..\build\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; The app holds this mutex while running, so setup/uninstall ask to close it.
AppMutex=ytuploader-app
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "autostart"; Description: "Start with Windows (minimised to the tray)"; GroupDescription: "Startup:"

[Files]
Source: "..\dist\ytuploader\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Leftovers from an older build's bundled libraries.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; Same value the app's Settings > "Start with Windows" checkbox writes.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ytuploader"; ValueData: """{app}\{#AppExe}"" --minimized"; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  // Unticking "Start with Windows" on an upgrade turns it off.
  if (CurStep = ssPostInstall) and not WizardIsTaskSelected('autostart') then
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'ytuploader');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;
  // The app may have registered itself from Settings, so always remove it.
  RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'ytuploader');

  DataDir := ExpandConstant('{localappdata}\ytuploader');
  if DirExists(DataDir) and not UninstallSilent then
    if MsgBox('Also delete your ytuploader data?' + #13#10#13#10 +
              DataDir + #13#10#13#10 +
              'This holds your settings, upload history (which clips are already ' +
              'on YouTube), API token and YouTube login. Choose No to keep it for ' +
              'a later reinstall.',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      DelTree(DataDir, True, True, True);
end;
