; Inno-Setup-Skript für den Windows-Installer der Netzentrale.
; Aufruf (macht der GitHub-Ablauf automatisch):
;   iscc /DAppVersion=0.7.0 installer\netzentrale.iss
; Installiert ohne Administratorrechte in den Benutzerordner (Softwareverteilung kann /ALLUSERS nutzen).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6C1F0B6E-6E4B-4E2B-9D0A-BSB-NETZENTRALE}
AppName=Netzentrale
AppVersion={#AppVersion}
AppPublisher=Bodensee-Schiffsbetriebe – IT
DefaultDirName={autopf}\Netzentrale
DefaultGroupName=Netzentrale
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline dialog
OutputDir=..\dist
OutputBaseFilename=Netzentrale-Setup-{#AppVersion}
SetupIconFile=..\build\AppIcon.ico
UninstallDisplayIcon={app}\Netzentrale.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "Symbol auf dem Desktop anlegen"; GroupDescription: "Zusätzliche Symbole:"

[Files]
Source: "..\dist\Netzentrale\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Netzentrale"; Filename: "{app}\Netzentrale.exe"
Name: "{autodesktop}\Netzentrale"; Filename: "{app}\Netzentrale.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Netzentrale.exe"; Description: "Netzentrale jetzt starten"; Flags: nowait postinstall skipifsilent
