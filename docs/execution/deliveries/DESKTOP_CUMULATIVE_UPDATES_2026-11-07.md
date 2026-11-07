# 固定 1.00 基础包的累计更新实施

状态：候选源码实现及本机隔离验证，双平台原生 CI、服务器接线和公开发行尚待完成。用户已明确授权“把 Windows 和 Mac 的更新接入补丁、后续自动更新功能都实现，现有 1.00 基础包保持不变”；用户后来反馈 Python 反复意外退出，此为当前任务中的修复要求，未取消原实施任务。

## 基线

- 本地与远端 main 已核对同为 `ddec9e5f1ca6057312d0f3346c34d59534cdd2be`；开发分支 `codex/desktop-patch-updates`。
- 固定原发行 tag `desktop-base-1.0.0-2026110606` 指向 `d07a57771475a9367e0face03d14f7629b97fe04`。原 Windows Setup 和 Mac DMG 文件、SHA-256、公开目录及 `resources/software_downloads.json` 保持不变。
- 新程序候选 `1.0.1/build2026110701`；基础身份与实际程序版本分开保存。操作见[更新说明](../../desktop-updates.md)。

## 已实现的源码

`desktop_updates` 实现固定原包全文件接入验证、两平台稳定启动入口、发行公钥与 Ed25519 清单验签、五秒清单超时、同源下载、完整归档/内链/权限检查、服务/桌面/维护锁、独立候选副本、原有类型单元格/BLOB/报告/附件保持、程序及数据切换日志与前向恢复。格式未知、旧数据丢失、破坏性迁移或附件文件需搬迁时拒绝自动安装。

候选副本初始化曾发现既有内置目录时间刷新造成严格校验失败；增加内部 `refresh_reference_data` 参数，默认保持原启动行为，仅已有候选副本暂停重复参考种子，完整结构检查仍执行。未放宽原客户记录核对规则。

新增日常补丁构建、双平台原生检查、一次性接入组件、签名与受限发布、每周元数据刷新源码；原基础安装器仅显式选择才构建。网页基础下载和接入下载分开，签名索引不存在时保持基础链接可用并禁用接入按钮。

## Python 意外退出的处理

本机离屏 Qt 初测出现 platform 插件发现失败及线程析构 SIGSEGV。用户反馈后立即停止全部相关启动实验，读取本机崩溃报告确认 `QObject::~QObject → QThread::~QThread`；没有业务库操作。修正为窗口主线程绑定槽、线程 wait、断开信号、解除引用及关闭清理。

唯一一次受控修复复核的四条 UI 流程全部通过，证据 `output/desktop-update-release-2026-11-07/ui-check.log`；此后不再在本机重复启动 native Qt。冻结 helper 新增隐藏 `--ui-self-test --probe-result`，仅在 GitHub runner 做成品图形探针，不把 `--version` 或无窗口健康冒充图形验收。

## 当前验证与后续

首轮15套中的前14套隔离检查已通过；最终包含网页接入的15套正在执行，准确计数以 `output/desktop-update-release-2026-11-07/final-source-checks/summary.json` 为准。核心25项、接入12项、manager15项、打包发布13项和网页接入14项已分别通过；Qt四项单独留证。

尚需完成：确定源码提交并推送，GitHub 双平台真实构建/预检/图形探针，下载核对接入包与程序补丁；原06真实安装后的接入与更新验收；受限服务器发布身份、公开版本路径、GitHub Secrets/每周刷新接线；公开 HTTPS 文件与签名校验，最后保留服务器当时全部资料发布网页接入入口。

服务器只读核对：2026-11-07当前仍 `b26fe36/main`，`ljqcapp/nginx` active，根盘39G、空闲27G；公开根和downloads为root:root0755，业务目录`/var/lib/ljqc`为ljqc:ljqc0750，尚无`ljqc-update`账号。本轮未改变服务器程序、业务资料或基础文件。

发行签名密钥和专用上传密钥仅在本地忽略目录 `output/desktop-update-release-2026-11-07/{signing,deployment}` 准备，目录0700/私钥0600，未在仓库、日志或公开成品中写入私钥，尚未给GitHub或服务器授予新权限。固定主机公钥沿已核对的 `output/cloud-download-release-2026-11-06/connection/known_hosts`，仍须与控制台公钥比对。新增服务器上传权限的实际启用须在可审阅接线完成后按操作界面的安全要求确认，不能以源码实现冒充配置完成。
