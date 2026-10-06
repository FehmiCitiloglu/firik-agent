#ifndef AppVersion
  #error AppVersion is required
#endif
[Setup]
AppId={{A52AB91C-69CB-4777-9FA1-B4DD6C942179}
AppName=firik-agent
AppVersion={#AppVersion}
AppPublisher=FIRIK
DefaultDirName={localappdata}\Programs\firik-agent
DefaultGroupName=firik-agent
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\release_assets
OutputBaseFilename=firik-agent-{#AppVersion}-windows-x64-setup
SetupIconFile=..\firik_agent\ui\assets\icon.ico
UninstallDisplayIcon={app}\firik-agent.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
[Files]
Source: "..\dist\firik-agent.exe"; DestDir: "{app}"; Flags: ignoreversion
[Icons]
Name: "{group}\firik-agent"; Filename: "{app}\firik-agent.exe"
Name: "{autodesktop}\firik-agent"; Filename: "{app}\firik-agent.exe"; Tasks: desktopicon
[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
[Run]
Filename: "{app}\firik-agent.exe"; Description: "Launch firik-agent"; Flags: nowait postinstall skipifsilent
