; Installer di Jarvis Clipper (Inno Setup 6) - si installa senza permessi di amministratore
#ifndef AppVer
  #define AppVer "1.0.0"
#endif

[Setup]
AppId={{6B1F3C2A-9D7E-4C1B-A8F2-4A5256495301}
AppName=Jarvis Clipper
AppVersion={#AppVer}
AppPublisher=MrJake
DefaultDirName={localappdata}\Programs\Jarvis Clipper
DefaultGroupName=Jarvis Clipper
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=JarvisClipperSetup
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\JarvisClipper.exe
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "it"; MessagesFile: "compiler:Languages\Italian.isl"

[Tasks]
Name: "desktopicon"; Description: "Crea un'icona sul desktop"; GroupDescription: "Icone:"

[Files]
Source: "dist\JarvisClipper\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Jarvis Clipper"; Filename: "{app}\JarvisClipper.exe"
Name: "{autodesktop}\Jarvis Clipper"; Filename: "{app}\JarvisClipper.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\JarvisClipper.exe"; Description: "Apri Jarvis Clipper"; Flags: nowait postinstall skipifsilent
