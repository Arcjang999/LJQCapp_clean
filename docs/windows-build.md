# Windows 安装包构建与验收

源码构建资料供维护人员在 Windows 上生成安装包。`windows-source-kit.zip` 本身不是安装程序，不能当作已完成的 Windows Setup 交付给使用者。

GitHub Actions 两平台构建入口见 [GitHub 构建说明](github-build.md)。该流程使用 Windows x64 与 Mac arm64 runner，生成待验收安装产物；不自动发布到客户下载页。

## 构建环境

- Windows 10（1809 或以上）或 Windows 11，x64。
- Python 3.13 x64，使用 Python 官方安装程序。
- Inno Setup 6.5 或以上的6.x版，使用其官方安装程序。缺少简体中文语言文件时，准备脚本会下载固定官方版本并验证SHA-256。
- 能连接正常的 Python 包仓库安装项目依赖。脚本不下载或执行额外远程安装脚本。

把构建资料解压到普通工作目录，在 `LJQCApp-source` 文件夹打开 PowerShell。保留 `source_identity.json`，它记录来源完整提交、未提交状态及发行输入摘要；任何源码变化都需要重新导出构建资料。

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
.\.venv\Scripts\python.exe -m pip freeze | Out-File -Encoding utf8 windows-build-environment.txt
.\packaging\prepare_windows_ci.ps1
.\packaging\build_windows_release.ps1 -InnoCompiler $env:LJQC_CI_INNO_COMPILER -ChineseMessagesFile $env:LJQC_CI_CHINESE_MESSAGES
```

如需显式指定 Inno 编译器：

```powershell
powershell -NoProfile -File .\packaging\build_windows_release.ps1 -InnoCompiler "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
```

构建输出位于 `release\<版本>\<构建号>\windows-x86_64\<本次编号>\`，包含 `Setup.exe`、完整程序 ZIP、元数据和构建回执。过程目录位于当前用户的 `%LOCALAPPDATA%\LJQCAppBuild\<本次编号>`。每次使用新目录，原安装包和失败证据保留。`-SkipInstaller` 仅生成程序 ZIP，不表示已经产生安装程序。

新安装使用空白业务资料。软件默认保存到当前用户 `%LOCALAPPDATA%\LJQCApp`；已选的资料位置继续生效。安装器只管理 `Programs\LJQCApp\versions` 内的程序文件，普通卸载保留资料。发行包不包含数据库、保存位置配置、业务附件、旧试用快照或清库／演示数据工具。

## 原生验收

构建成功后，仍要分别在 Windows 10 和 Windows 11 的独立测试用户或测试机上验收；源码测试和 Setup 编译成功不能替代安装验收。不要用在用客户资料做这些检查。

1. 用普通系统用户安装，核对开始菜单、桌面快捷方式及显示版本。全新安装应出现首次引导，业务项目为空。
2. 在独立测试资料中准备最小项目，保存检测、附件与报告，关闭后重新打开，核对记录及原件。
3. 正常退出并确认本机服务已结束，安装后续版本或重新安装；核对版本变化、自选资料位置及原记录、附件和报告保持。
4. 卸载后确认资料仍在，重新安装后能继续使用。运行期间安装应要求先正常关闭，不能强制覆盖已占用程序文件。
5. 核对已安装目录中的 `build_info.json`、`release_payload_manifest.json` 与构建输出的 `build_receipt.json`；保存安装日志、环境清单及实际截图。诊断可使用安装目录中的“生成诊断包_发给开发者.bat”。

安装器当前未配置 Windows 程序签名。公开分发前记录签名状态并完成目标系统验收；构建回执中的 `native_installation_validation=pending` 只有在真实验收后才能另行出具通过记录。
