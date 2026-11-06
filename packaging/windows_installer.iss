; Build only from the validated program payload produced by build_windows_release.ps1.
#ifndef SourceDir
  #error SourceDir is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif
#ifndef OutputBase
  #error OutputBase is required
#endif
#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef BuildNumber
  #error BuildNumber is required
#endif
#ifndef PayloadId
  #error PayloadId is required
#endif
#ifndef ChineseMessagesFile
  #define ChineseMessagesFile "compiler:Languages\ChineseSimplified.isl"
#endif

[Setup]
AppId={{D0A767BD-0CF0-4197-BA45-024CD72945B3}
AppName=LJQCApp
AppVersion={#AppVersion}
AppVerName=LJQCApp {#AppVersion} (build {#BuildNumber})
AppPublisher=LJQCApp
DefaultDirName={localappdata}\Programs\LJQCApp
DefaultGroupName=LJQCApp
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.17763
DisableProgramGroupPage=yes
AllowNoIcons=yes
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBase}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\versions\{#PayloadId}\LJQCApp.exe
UninstallDisplayName=LJQCApp
VersionInfoVersion={#AppVersion}.0
AppMutex=Local\LJQCApp.Desktop
CloseApplications=yes
RestartApplications=no
ChangesEnvironment=no
SetupLogging=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "{#ChineseMessagesFile}"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}\versions\{#PayloadId}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\LJQCApp"; Filename: "{app}\versions\{#PayloadId}\LJQCApp.exe"; WorkingDir: "{app}\versions\{#PayloadId}"
Name: "{userdesktop}\LJQCApp"; Filename: "{app}\versions\{#PayloadId}\LJQCApp.exe"; WorkingDir: "{app}\versions\{#PayloadId}"; Tasks: desktopicon

[Run]
Filename: "{app}\versions\{#PayloadId}\LJQCApp.exe"; Description: "打开 LJQCApp"; Flags: nowait postinstall skipifsilent

; No data directory is installed or removed. Inno's uninstall log owns only the
; exact program files listed above; user config, database, reports and attachments
; remain in their independent locations.
