; Aider-Deck installer — Inno Setup 6 script
; Build: install Inno Setup 6 (jrsoftware.org), open this file, Build > Compile
;        (or from a terminal:  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" AiderDeck.iss)
; Place this script and aider-deck.ico in the ROOT of the Aider-Deck repo before compiling.
; Output: dist\AiderDeck-Setup-0.1.0.exe

#define AppName "Aider-Deck"
#define AppVersion "0.1.0"
#define AppPublisher "Jared W. Hall"

[Setup]
AppId={{8F3C2A41-9D2E-4B6A-A7C1-5E2D0B94F6A3}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
; Per-user install under %LocalAppData%\Programs — no admin prompt, matches how
; the app lives (its .venv is created beside the program files on first run).
DefaultDirName={localappdata}\Programs\Aider-Deck
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=AiderDeck-Setup-{#AppVersion}
SetupIconFile=aider-deck.ico
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\aider-deck.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
; The icon the Start Menu / desktop shortcuts wear
Source: "aider-deck.ico"; DestDir: "{app}"; Flags: ignoreversion
; Launcher and app entry point
Source: "run.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "app.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "presets.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion
; Application packages and web assets
Source: "deck\*"; DestDir: "{app}\deck"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__"
Source: "static\*"; DestDir: "{app}\static"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "templates\*"; DestDir: "{app}\templates"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "prompts\*"; DestDir: "{app}\prompts"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start Menu entry — name "Aider-Deck", target run.bat, icon from the shipped .ico.
Name: "{group}\{#AppName}"; Filename: "{app}\run.bat"; WorkingDir: "{app}"; IconFilename: "{app}\aider-deck.ico"; Comment: "Local console for Aider"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\run.bat"; WorkingDir: "{app}"; IconFilename: "{app}\aider-deck.ico"; Tasks: desktopicon

[Run]
; Offer a first launch on the finish page. nowait matters: run.bat stays open
; as the server console, so the installer must not wait for it to exit.
Filename: "{app}\run.bat"; Description: "Launch {#AppName} now (first launch builds its Python environment — give it a minute)"; Flags: postinstall skipifsilent nowait

[UninstallDelete]
; First run creates .venv and Python caches inside the install dir; remove them
; with the rest of the program on uninstall.
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\deck\__pycache__"

[Messages]
; The one prerequisite the installer does not provide: Python 3.11+ on PATH
; (run.bat calls `python`). Said plainly on the wizard's first page.
WelcomeLabel2=This will install Aider-Deck on your computer.%n%nAider-Deck needs Python 3.11 or newer available on your PATH (the python command). If Python is missing, the first launch will fail with an error — install Python from python.org, tick "Add python.exe to PATH", then launch Aider-Deck again.
