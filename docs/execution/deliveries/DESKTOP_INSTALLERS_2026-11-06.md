# 桌面安装包首轮交付（2026-11-06）

后续说明已按Westgard来源纠正并重建2026110602：最新下载和验收以[说明修订交付](WESTGARD_FIRST_USE_2026-11-06.md)为准。本文01文件仅保留首轮证据；另发现的规则范围差异已单列，不应据首轮打包验收宣称完整Westgard符合性。

用户授权开始正式安装包，平台为 Windows 10/11 x64、Apple 芯片 Mac。用户确认没有 Apple Developer 证书，先制作待签名包；Windows 由已登录的 Windows 客户端继续本机构建，传回产物后再统一验收和发布。

## 实现范围

- 新增统一 `release.json`：1.0.0，构建号 2026110601，资料格式 1。构建记录完整 Git 基线、未提交状态、发行输入 SHA-256、目标平台与时间；运行时不执行 Git。
- Mac 默认资料根为 `~/Library/Application Support/LJQCApp`，Windows 为 `%LOCALAPPDATA%\LJQCApp`。`LJQC_DATA_ROOT` 用于隔离验收；源码和云端保留原路径。资料/config 与程序包分开，坏配置或原库缺失时不创建替代空库。
- 显式旧资料接入支持保留自选指针，或在原程序关闭后核验复制默认库、已提交 WAL 和完整附件目录，最后写新配置。旧原件保留。客户可沿用系统设置的完整备份/恢复迁入，无需手输路径。
- 初始化前拒绝未来格式、未知迁移登记和核心表未知列；它是最小兼容防线，不是完整升级账本。未新增业务表迁移，不改质控算法。
- Qt 与本机服务分别持锁。关闭以专用请求通知 Streamlit 正常停止，服务锁保留至非守护脚本线程结束，主窗口等待服务退出；不以强杀作为常规退出动作。
- 空白新装先选择单水平/多水平，无默认值，仅预填首项草稿且可修改；进入真实工作台显示对应短教程，阅读进度按资料库和方法保存。帮助新增“新手教程”。无 HIV RNA 或其他演示业务资料。
- Windows 配方为 Inno Setup 普通用户安装，程序按版本/构建/摘要分目录，快捷方式指向新程序；卸载不删除独立资料。尚未在 Windows 真机编译或安装。
- Mac 构建配方生成 arm64 `.app`、只读分发 DMG；最低 macOS 13 来自本机 QtCore arm64 的 LC_BUILD_VERSION。包仅采用本地 ad-hoc 签名，未做 Developer ID 签名或公证。

## 成品与来源

最终 Mac 文件位于：

`output/installers/1.0.0-2026110601-macos-arm64-20261106T053020Z/LJQCApp-1.0.0-2026110601-macos-arm64-pending-signing.dmg`

大小 331,448,678 字节；SHA-256：`32189a2a2191680c5199847f5368d6e579c9dc8d47823409ed20bd40d844bbd5`。

Git 基线 `1b1a717ebba46f54702a9605a14f6e1fd375d56e`，`source_dirty=true`；Mac 固定源码快照摘要 `7387a7b8b1475d4ea326be58d7005919ae5efbf6dcd39e035ceb4b407c46e809`。不能将该包称为上述提交的纯净构建。`artifact.json`、逐文件 `release_payload_manifest.json`、固定源码快照及构建日志同目录保留。

Windows 源码构建资料位于：

`output/installers/1.0.0-2026110601-windows-source-kit-20261106T053158Z-d5da203a/LJQCApp-1.0.0-2026110601-windows-source-kit.zip`

大小 3,657,782 字节；SHA-256：`9a323e0ddc233436a3092376c8ac1bd7541cd6d6221f26b967dc0de5dbc96b0d`。202 个白名单发行输入文件，不含 `.git`、数据库、配置、附件、output 或试用快照。离线源码摘要复核及 ZIP 逐文件回读校验通过；`source_identity.json` 在任一源码修改后失效。其来源摘要 `a457578c9157681ec3c7bbd2a718f4f76c5754bde4f2b9c371246a3acf34bab2` 与 Mac 不同，差异为后续加入的 Windows 构建资料导出辅助文件；业务与 Qt 文件未改。未来统一发布按实际两端元数据记录，不假称相同摘要。

该 ZIP 是 Windows 构建资料，不是 Windows 安装包。操作见 [Windows 构建说明](../../windows-build.md)。

## 验证与证据

源代码检查共 20 套 148 项：存储 8 套57项、首次引导6套59项、帮助/导航4套16项、Qt/发行资源1套11项、发行运行边界1套5项。全部通过，隔离数据库守卫命中、失败及跳过均0。运行边界最后一项用独立子进程确认主循环返回后，未结束的保存线程仍使服务锁保持占用。

- 存储：`output/desktop-storage-2026-11-06/delivery.json`。
- 引导：`output/desktop-first-use-2026-11-06/verification-final.json`。
- 浏览器：同目录 `browser-qa/verification.json`，两份新资料、8张截图；无默认方法、两种选择、首项修改、帮助两种教程/返回通过，业务表均0。完整项目保存/自动工作台教程链由 AppTest 验证，未冒称该整链已人工点击。
- 帮助/导航：`output/installers/common-qa/*/result.json`。
- Qt/发行：`output/installers-2026-11-06/windows-validation/receipt.json` 及日志。Windows 原生构建/安装状态明确 pending。
- Mac 安装：最终成品目录 `installation.json`、`installation-checks.json`，DMG 校验及只读挂载、复制到 `/private/tmp` 独立 Applications、复制后严格递归签名验证及 executable arm64 检查通过。
- Mac 实包：`frozen-cli-checks.json`，版本/诊断命令通过且不建立业务库；`installed-first-use.png` 为实际 Qt 安装版首次空库窗口。自动正常关闭及重新安装/重开资料保持见同目录 `frozen-application-verification.json`、`reopen.log`；所有资料在独立临时目录。

编译与差异空白检查通过。已保留初败：图标系统工具受权限限制；Documents 下系统自动附加 FinderInfo 影响签名校验，最终改在 `/private/tmp` 组装/签名，且只移除签名不支持的 FinderInfo/ResourceFork，保留隔离标记，不改变 Gatekeeper 设置。前两份失败或未通过安装校验的中间产物不作为交付文件。

## 当前边界

未提交推送、未部署服务器、未把清单标为已发布。帮助的下载入口仍显示准备状态。Windows Setup 仍待在 Windows 原生 PowerShell 构建并传回，需记录实际测试系统；Mac 待 Apple Developer ID 签名和公证。完整迁移阶段日志、自动在线检查/下载/安装、可信更新签名及网页/桌面统一发布尚未实现，本轮不能称为自动热更新已完成。
