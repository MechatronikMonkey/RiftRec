; Inno Setup script for RiftRec (EW-89).
;
; Wraps the PyInstaller folder from dist\RiftRec into a single setup .exe that
; installs without Python, pip or an internet connection - the three things that
; stopped the zipped version from starting on a co-founder's PC, and that 10-30
; strangers would fail on far harder.
;
; Deliberately NOT registered here: an autostart-with-Windows entry. Recording is
; a conscious act - the participant has to put the chest strap on anyway, so
; launching RiftRec belongs to the same decision. The Start-menu (and optional
; desktop) shortcut is how a session gets started.
;
; Build:  ISCC.exe /DAppVersion=0.1.0 packaging\riftrec.iss
; Signed: ISCC.exe /DSIGN /Ssigntool="<signtool command> $f" ... (see README)

#ifndef AppVersion
  ; Fallback for a bare ISCC run. tests/test_packaging.py keeps this in step
  ; with riftrec.__version__, so an unversioned build is never mislabelled.
  #define AppVersion "0.1.0"
#endif

#define AppName "RiftRec"
#define AppPublisher "MechatronikMonkey"
#define AppURL "https://github.com/MechatronikMonkey/RiftRec"
#define AppExe "RiftRec.exe"

[Setup]
; Never change AppId - it is how Windows recognises an existing installation and
; upgrades it in place instead of leaving two copies behind.
AppId={{4C9A2E71-5D3B-4A16-9C0E-7F1B8D6E2A54}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
VersionInfoVersion={#AppVersion}

; Per-user install: no admin prompt, no UAC dialog on a machine we do not own.
; One scary dialog less between a participant and a working recorder.
PrivilegesRequired=lowest
DefaultDirName={userpf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto

OutputDir=..\dist
OutputBaseFilename=RiftRec-Setup-{#AppVersion}
SetupIconFile=riftrec.ico
UninstallDisplayIcon={app}\{#AppExe}
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; If RiftRec is running, ask before touching its files rather than killing a
; recording that may be halfway through a ranked game.
CloseApplications=yes
RestartApplications=no

#ifdef SIGN
; Enabled only for a signed build, so an unsigned local build still works.
; Certificate lead time is weeks; the build does not wait for it (EW-89).
SignTool=signtool
SignedUninstaller=yes
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a shortcut on the desktop"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\RiftRec\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start RiftRec now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Settings and the stale lock file go with the program. Recordings never do -
; they live in the folder the participant chose and are the point of the whole
; study (EW-52). riftrec.log stays too: if an uninstall happens because
; something did not work, that log is the only evidence of why.
Type: files; Name: "{userappdata}\RiftRec\prefs.ini"
Type: files; Name: "{userappdata}\RiftRec\riftrec.lock"
Type: dirifempty; Name: "{userappdata}\RiftRec"
