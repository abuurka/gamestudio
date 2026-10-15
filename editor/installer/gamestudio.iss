; SPDX-License-Identifier: GPL-3.0-or-later
; GameStudio Editor: the Windows installer (Inno Setup 6.5+).
;
; Built by `python editor/tools/gsfork.py package --installer` (editor/tools/gsfork_package.py),
; which stages the release tree and passes the defines below. See docs/fork/RELEASING.md.
;
; Per-user and without admin rights: installs into %LOCALAPPDATA%\Programs\GameStudio, registers
; its uninstaller under HKCU, adds a "GameStudio" Start-menu shortcut (desktop optional).
; It registers no file types or associations, so Blender's own are never taken over.

#ifndef StageDir
  #error Build this script with: python editor/tools/gsfork.py package --installer
#endif

[Setup]
; Never change AppId: upgrades find the previous install through it.
AppId={{F56BAC38-D8A3-470F-BE3E-3CE0378FD7A7}
AppName=GameStudio
AppVersion={#AppVersion}
AppVerName=GameStudio {#AppVersion}
AppPublisher=GameStudio contributors
AppComments=Game engine IDE, based on Blender's open source code
AppCopyright=GameStudio contributors. GNU GPL v3 or later.
VersionInfoVersion={#AppVersionNumeric}
VersionInfoProductName=GameStudio Editor
VersionInfoDescription=GameStudio Setup
VersionInfoCompany=GameStudio contributors
PrivilegesRequired=lowest
DefaultDirName={autopf}\GameStudio
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
DirExistsWarning=auto
UninstallFilesDir={app}\uninstall
UninstallDisplayName=GameStudio {#AppVersion}
UninstallDisplayIcon={app}\GameStudio.ico
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
ChangesAssociations=no
ChangesEnvironment=no
CloseApplications=yes
RestartApplications=no
SourceDir={#StageDir}
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBase}
SetupIconFile={#SetupIcon}
WizardStyle=modern dynamic
WizardImageFile={#WizardImage}
WizardSmallImageFile={#WizardSmallImage}
InfoBeforeFile={#InfoFile}
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=8
SetupLogging=yes

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; Upgrades replace these trees whole, so packages a new version dropped do not linger.
Type: filesandordirs; Name: "{app}\gamestudio\node"
Type: filesandordirs; Name: "{app}\gamestudio\node_modules"
Type: filesandordirs; Name: "{app}\gamestudio\runtime"
Type: filesandordirs; Name: "{app}\{#Series}\scripts\startup\bl_app_templates_system\GameStudio"

[Files]
Source: "*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; blender-launcher.exe starts the editor without a console window. The AppUserModelID is the one
; the editor's windows carry (seam S03), so the taskbar groups them under this shortcut.
Name: "{autoprograms}\GameStudio"; Filename: "{app}\blender-launcher.exe"; WorkingDir: "{app}"; IconFilename: "{app}\GameStudio.ico"; AppUserModelID: "{#AppUserModelID}"; Comment: "GameStudio game editor"
Name: "{autodesktop}\GameStudio"; Filename: "{app}\blender-launcher.exe"; WorkingDir: "{app}"; IconFilename: "{app}\GameStudio.ico"; AppUserModelID: "{#AppUserModelID}"; Comment: "GameStudio game editor"; Tasks: desktopicon

[Run]
Filename: "{app}\blender-launcher.exe"; Description: "{cm:LaunchProgram,GameStudio}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Files the app writes at run time: into its own folders (Python caches, optional assistant
; packages, caches of examples opened in place) and caches. Never {app} itself, never user projects.
Type: filesandordirs; Name: "{app}\{#Series}"
Type: filesandordirs; Name: "{app}\gamestudio"
Type: filesandordirs; Name: "{localappdata}\GameStudio\npm-cache"
; The editor's own caches (shaders, asset indices; seam S02), never its settings in {userappdata}.
Type: filesandordirs; Name: "{localappdata}\GameStudio\Editor\Cache"
Type: dirifempty; Name: "{localappdata}\GameStudio\Editor"
Type: dirifempty; Name: "{localappdata}\GameStudio"
Type: dirifempty; Name: "{app}"

[Code]
function ParamGiven(const Name: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), Name) = 0 then
      Result := True;
end;

{ Settings live in %APPDATA%\GameStudio (seam S02). They stay unless the user says otherwise:
  a question when uninstalling interactively, /REMOVEPROFILE when silent. }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Profile, Question: String;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;
  Profile := ExpandConstant('{userappdata}\GameStudio');
  if not DirExists(Profile) then
    Exit;
  Question := 'Also delete your GameStudio settings and recent-projects list?' + #13#10#13#10 + Profile + #13#10#13#10 + 'Your game projects are never deleted.';
  if ParamGiven('/REMOVEPROFILE') or
     ((not UninstallSilent) and (MsgBox(Question, mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES)) then
    DelTree(Profile, True, True, True);
end;
