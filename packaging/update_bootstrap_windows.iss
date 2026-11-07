; This is a one-time updater bootstrap, never the immutable 1.00 base installer.
#ifndef SourceDir
  #error SourceDir is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif
#ifndef OutputBase
  #error OutputBase is required
#endif
#ifndef BuildNumber
  #error BuildNumber is required
#endif
#ifndef ChineseMessagesFile
  #define ChineseMessagesFile "compiler:Languages\ChineseSimplified.isl"
#endif

[Setup]
AppId={{E8BE4330-8DA6-43B3-A8C0-E197A5018085}
AppName=LJQCApp 更新接入补丁
AppVersion=1.0.0
AppVerName=LJQCApp 更新接入补丁（构建 {#BuildNumber}）
AppPublisher=LJQCApp
DefaultDirName={localappdata}\Programs\LJQCAppUpdateBootstrap\{#BuildNumber}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.17763
DisableProgramGroupPage=yes
DisableDirPage=yes
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBase}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=LJQCApp 更新接入补丁
CloseApplications=no
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "{#ChineseMessagesFile}"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Run]
Filename: "{app}\LJQCAppUpdater.exe"; Parameters: "--install"; Description: "接入更新并保留现有资料"; Flags: postinstall nowait skipifsilent

; The bootstrap itself asks for the exact original application and creates the
; stable entry. Its copied program and all business data are not Inno-owned.
