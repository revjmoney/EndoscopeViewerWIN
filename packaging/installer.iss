#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{B024A809-CB38-48FC-99E4-B8E4B85D2E66}
AppName=EndoscopeViewerWIN
AppVersion={#AppVersion}
AppPublisher=revjmoney
AppPublisherURL=https://jmscnc.com
AppSupportURL=https://github.com/revjmoney/EndoscopeViewerWIN/issues
AppUpdatesURL=https://github.com/revjmoney/EndoscopeViewerWIN/releases
DefaultDirName={localappdata}\Programs\EndoscopeViewerWIN
DisableDirPage=no
DefaultGroupName=EndoscopeViewerWIN
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
LicenseFile=..\LICENSE
OutputDir=..\dist\release
OutputBaseFilename=EndoscopeViewerWIN-{#AppVersion}-windows-x64-setup
UninstallDisplayIcon={app}\EndoscopeViewerWIN.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#AppVersion}

[Tasks]
Name: desktopicon; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\app\EndoscopeViewerWIN\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\EndoscopeViewerWIN"; Filename: "{app}\EndoscopeViewerWIN.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\EndoscopeViewerWIN"; Filename: "{app}\EndoscopeViewerWIN.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\EndoscopeViewerWIN.exe"; Description: "Open EndoscopeViewerWIN"; Flags: nowait postinstall skipifsilent

; User-created {app}\data is deliberately retained during uninstall.
