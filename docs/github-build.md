# GitHub Actions 构建桌面安装包

2026-11-06已按用户要求完整合入`main`，自动构建入口同步转移；`main`首次自动运行[42179158278](https://github.com/Arcjang999/LJQCapp_clean/actions/runs/42179158278)对应候选build2026110604，记录时仍在构建。下条03为已完成的历史验收，不代替04结果。

2026-11-06真实双平台构建已完成：版本1.0.0/build2026110603，两端各82项来源检查和实包/服务校验通过，成品与边界见[本轮交付](execution/deliveries/GITHUB_DESKTOP_BUILD_2026-11-06.md)。

同一份源码分别在 GitHub 的 `windows-2022` x64 和 `macos-15` arm64 环境打包。Windows 10/11共用一个x64安装包；Mac仅Apple芯片。业务资料不参与打包，构建产物仍需桌面安装验收。Mac仅本地ad-hoc签名，Developer ID签名和公证尚未配置。

## 当前入口

工作流：`.github/workflows/desktop-build.yml`，名称 **Desktop installers**。

自动构建入口转到默认分支 `main`：推送 `release.json`、`.gitattributes`、打包配方、测试或该工作流变化时自动构建。单独修改业务源码或依赖文件不会触发；准备新的安装包时需一并递增 `release.json` 的构建号。开发分支推送不再重复自动打包。

工作流进入 `main` 后，也可在 **Actions → Desktop installers → Run workflow** 手动构建，并选择需要构建的分支。手动构建仍须满足相同的源码、版本和实包检查。

构建号由 `release.json` 统一读取；每次正式候选更新递增构建号，业务版本有变化时同时更新版本号。两端构建必须来自同一Git提交、同一发行输入摘要，源码不允许在构建过程中变化。`.gitattributes`及checkout设置保证源文本的LF一致。

## 一次构建的步骤

1. 固定GitHub官方Actions提交，安装Python3.13及项目依赖、PyInstaller6.22.3。
2. 每个平台运行10套隔离源检查。SQLite审计钩子在业务模块导入前启用，只准使用本次临时资料目录；跳过、失败和越界访问都阻止构建。
3. Windows使用runner内置Inno6.5+的6.x版；缺简中时从固定官方源取语言文件并验证摘要。必须编译真实Setup，不能以只生成ZIP替代。
4. Mac生成arm64应用及DMG，在本机临时目录完成ad-hoc签名，避免文档目录属性污染。
5. 验证发行输入/内外版本/文件清单和平台架构，使用独立资料根启动冻结服务，检查健康、正常退出、资料锁和端口释放。Mac另做DMG校验、只读挂载及复制安装后严格签名验证。
6. 所有检查通过后上传安装文件、小型清单、校验回执及环境信息，保留3天。失败时仅上传选定JSON/log，不上传整个output、QA库、附件、源码快照或整个.app。

## 获取产物

在仓库 **Actions → Desktop installers → 本次运行 → Artifacts** 下载：

- `LJQCApp-windows-x86_64-<运行号>-<尝试号>`：Setup.exe与验收回执。
- `LJQCApp-macos-arm64-<运行号>-<尝试号>`：待签名DMG与验收回执。

这些是构建附件，没有创建GitHub公开Release，也没有修改软件的下载清单或服务器。进入客户下载页面前，需核对SHA-256、版本和平台，并完成对应桌面安装/升级/卸载验收。

## 真实验收边界

GitHub Windows runner是Windows Server，不能替代Windows10与11桌面环境验收。无窗口服务健康检查没有创建Streamlit业务会话，不能据此宣称首次引导、业务空库初始化、检测保存或PDF均已实包验收。

Mac运行环境实际为macOS15；组件最低目标/包内声明为13+，不代表已经在macOS13设备上验证。已有本地02的实际Qt窗口验收是单独证据，不冒充每次CI都运行过GUI。

既有[Westgard规则范围差异](execution/notes/WESTGARD_RULE_SCOPE_AUDIT_2026-11-06.md)仍待专项处理。本轮是打包流程接线，不改变质控算法、历史资料或已有报告，也不表示完整规则符合性通过。自动在线更新及网页/桌面统一发布仍待实施。
