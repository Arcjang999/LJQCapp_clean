; The wrapper extracts only to its private temporary directory. The installer
; registers the stable updater and accepted program using their own journals.
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
#ifndef ChineseMessagesFile
  #define ChineseMessagesFile "compiler:Languages\ChineseSimplified.isl"
#endif

[Setup]
AppId={{9E90D501-F54A-4D90-8D95-0A2CED20D1E1}
AppName=LJQCApp
AppVersion={#AppVersion}
AppVerName=LJQCApp 1.01（含自动更新）
AppPublisher=LJQCApp
CreateAppDir=no
Uninstallable=no
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
CloseApplications=no
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "{#ChineseMessagesFile}"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
WelcomeLabel2=安装 LJQCApp 1.01 和自动更新器。已有用户请先保存工作并关闭软件。安装会继续使用原有资料，之后可从原图标打开。
FinishedLabel=已安装 LJQCApp 1.01 和自动更新器。请从开始菜单中的 LJQCApp 打开软件。后续更新会从服务器获取。

[Files]
Source: "{#SourceDir}\*"; DestDir: "{tmp}\LJQCAppIntegrated-{#BuildNumber}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  ExitCode: Integer;
  InstallerPath: String;
begin
  if CurStep = ssPostInstall then
  begin
    InstallerPath := ExpandConstant('{tmp}\LJQCAppIntegrated-{#BuildNumber}\LJQCAppInstaller.exe');
    if not Exec(InstallerPath, '--install --no-gui',
      ExpandConstant('{tmp}\LJQCAppIntegrated-{#BuildNumber}'), SW_HIDE,
      ewWaitUntilTerminated, ExitCode) then
      RaiseException('安装程序未能启动，请重新运行安装包。');
    if ExitCode <> 0 then
      RaiseException('安装未完成。请保存工作并关闭 LJQCApp 后重新运行安装包；原有资料会保留。');
  end;
end;
