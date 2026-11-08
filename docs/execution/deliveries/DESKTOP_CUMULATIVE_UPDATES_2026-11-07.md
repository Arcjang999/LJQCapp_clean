# 固定 1.00 基础包的累计更新实施

## 0802原生与网页已验收，原包重发中

[run42433269255](https://github.com/Arcjang999/LJQCapp_clean/actions/runs/42433269255)的 Windows 和 Mac 两个原生任务均成功，来源固定 `dc4af0e84aac02a120294e19469c2d5bef96976c`，摘要 `90bc8c66b1978e7bb71fb1321d759cf910eec3ab7bcebd708dab8e2ca8ecd4af`，程序 `1.0.1/build2026110802`。真实冻结HTTPS、有效但未知自签拒绝、原06安装后的真实旧02 CLI接入、新助手修复及稳定助手联网、实际程序补丁应用、74表原typedcells/PDF BLOB/附件保持均通过。Windows版本化payload完整文件验证成功，卸载维护文件未进入载荷；未在用户Mac运行native。

原签名附件 `13599005609 / LJQCUpdate-signed-3-1` 为737617156字节，GitHub SHA-256 `d2f99866a418b6eafeb586bef3ade3e7bd030282ece5441626f646f7e6c378e0`。唯一失败为最后受限SSH传输超过900秒；原包/签名保留，不重建、不重签、不换源。脱敏日志375519字节/SHA `b82d18032a27262f861fa30b7cf6372e3a3fb149442e684029b60c9390150d4f`，证据在 `deployment/github-0802-42433269255-*`，两端原生来源绑定在 `github-artifacts-build0802/*-ci-binding.json`。发布控制单独改为小引用加受限服务器HTTPS拉取，并增加明确main手动重发入口；当前仍待实际晋级及公开下载核对。

网页独立工具 `cloud-tools-build0802-v2/ljqc-auto-updates-tools-20261108-0802-v2.tar.gz` 为35922字节/SHA `eb99c4fd52808f4b9b1849d926dc2ffd71abf9ce8511bda1e538c5129a09b057`，服务器使用全新 `/var/tmp/ljqc-auto-updates-20261108-0802-v2`。prepare、无安装依赖核对及21套235项check均成功，失败/跳过/越界0，业务环境117CA根、证书及主机名校验保留。v1唯一失败为测试夹具意外读取生产更新feed，v2仅隔离测试子进程的公开清单根，未改产品或放宽断言；所有失败现场保留。固定dc4源码bundle仍为68123字节/SHA `14427a8573b4aec5e9313130c79e2b93edd8ea2121c4e80e887a8d23d400dde7`，不得随控制提交重导出。

网页已按本轮fresh双备份从2adb发布到dc4，02:13:03服务健康；02:14:42最终accept通过，保留74表19404行201113原有类型单元格、28BLOB、4附件及24报告（22质控+2处理），两备份/配置/权限不变，data_restored=false。唯一许可的内置参考时间及3N/296N序列刷新为4轮，没有导航例外或客户记录差异。新网页实际Home和帮助下载页显示build0802；桌面频道尚0702/AUTO=false。reference重发42437417536因服务器实际约50KB/s、预计超过7200秒而取消；已确认无active receiver、频道未切换，失败证据及私有部分暂存保留。现以[42437890977](https://github.com/Arcjang999/LJQCapp_clean/actions/runs/42437890977)明确raw_transfer=true重发同一原签名附件，不能用取消run充成功证据。日常及重发默认改用raw7200秒/150分钟，小引用保留为显式opt-in；3文件最小修正及新增2回归共19项局部guard通过，原source267通过保持。实际published、原06最终SHA、公开四文件和AUTO开启仍待完成。

交付控制独立最终源码检查已通过22套267项，失败/错误/跳过/越界0，见 `control-reference-source-checks/summary.json` 及逐套guard回执；其中新引用接收20项、新重发17项，旧打包兼容19项全部实际执行。三方独立复核覆盖先验签/反回放、短期只读SAS、逐跳窄域名、ZIP64/中央目录分配上界、原JSON字节/全文件哈希、精确原生API来源及SSH回执白名单。原API-only预检匹配真实0802的两端和签名保留步骤，唯一原失败为Transfer。两套新检查已注册今后源码CI，手动重发也先执行这两套纯fixture；没有本机native、重新签名或重构建。此处仍为控制源码通过，服务器受控替换和实际重发结果待另记。

## 0801实物结果与0802候选

0802最终源检查20套230项通过，失败/跳过/越界均0，包含严格payload与额外文件拒绝的新回归；证据`build0802-source-checks/acceptance-summary.json`。无本机Qt、冻结程序或安装器运行，实际两端成品仍须本轮GitHub验收。

[0801 run42431705710](https://github.com/Arcjang999/LJQCapp_clean/actions/runs/42431705710)，源3bc718a/digest75ddb9...。Mac全job成功，真实原06→原02冻结CLI接入→新冻结CLI修复→稳定助手TLS→真实程序补丁/资料保持通过；Windows冻结TLS/未知证书拒绝/Qt及真实原02接入通过，最后新组件完整inventory因Inno生成的unins维护文件额外进入助手根而失败。签名发布自动skipped，0801未上feed；保留`deployment/github-0801-42431705710-*`完整脱敏日志与53KB Windows诊断，未重试冒充通过。

0802仅改Windows源安装布局：纯助手放版本化`payload-{BuildNumber}`，维护卸载文件在父层，现代CI仅从明确定义的payload根取源；原02定位不变。未知额外文件仍拒绝，不增加忽略清单，也不全目录删除。CA/助手修复/业务代码保持0801已验证实现；两平台仍必须来自新的同一确定源码完整重建验收。0801云端fresh工具已prepare及no-install依赖确认（现有CA117根）但未check/release，保留该现场；0802使用独立工具/OUT及当时资料基线，生产网页仍2adb。receiver已受控更新至3bc并补独立certifi，实际发布账号在无系统CA环境下通过121根HTTPS+发行签名，原SSH公钥/业务环境未变。AUTO仍false。

## 2026-11-08 云端接线与0801收尾

0801源码最终隔离检查20套228项通过，失败/跳过/越界0，证据`build0801-source-checks-final/acceptance-summary.json`；此前224项记录保留。较新helper工厂支持合法未来版本/构建（包含1.0.2/build2026110901回归），所有现代来源首次接入须完整清单，人工较新组件修复保持state/bootstrap字节；原02仅精确来源可作为缺清单例外。打包后的官方HTTPS、自签拒绝和真实旧02→新组件修复仍须本次GitHub两平台实证，未在本机运行Qt或冻结程序。

用户已完成腾讯云验证，并明确同意创建受限自动发布通道、专用GitHub签名/上传密钥及main-only环境。02网页已从b26fe36更新到2adb772，18套188项隔离检查通过；以实际停机时的新鲜资料创建双备份，最终保留74表19404行201113原单元格、28BLOB、4附件及24报告（22质控+2处理），差异0、两备份不变，未恢复旧库。原数据库格式号0按既有unversioned/format1兼容规则通过，未为对齐编号写库。

受限接收账号已启用并实际验证任意SSH命令拒绝126、业务目录不可读；权限只给三更新静态子树及私有暂存。新账号独有的SSH Match限定AcceptEnv，候选/在线语法检查和ubuntu/root有效配置保持通过，SSH仅reload。Nginx在现有HTTPS server新增JSON更新入口，候选/在线nginx-t均通过、TLS/ACME/原downloads/代理保持。sudo查询返回0但实际无授权的误判已用严格C locale/本机账号完整拒绝消息修正，18项专项与独立复核通过；提交a41684c，客户端输入摘要未变，未重复构建客户端。新receiver工具20763B/SHA`da4f8b1e841cc513e0c9e77de7fd980c815065cbaba13f5b2a46ad54bb4a67dd`，原只读installer及工具manifest分别备份/核对后受控替换，旧归档保留。

02四成品已真实发布到自有HTTPS；独立公网全文件长度/SHA匹配，原06两包在服务器public目录完整实算SHA匹配，外部HEAD来源/长度通过。[GitHub真实清单刷新直传42428985794](https://github.com/Arcjang999/LJQCapp_clean/actions/runs/42428985794)成功，source a41684c、程序仍2adb772，sequence1794096988210362；只更新14天签名清单，原安装包和程序字节未变。GitHub环境仅main、三项专用Secrets和host/user已设，**AUTO仍false**。完整脱敏Actions证据在`deployment/github-refresh-42428985794-*`；独立公网证据在`public-verification-build02-independent-ca.json`。

02云端回执已通过文件管理器取回：`deployment/cloud-build02-receipt.json`，4060B/SHA`2233309d64c5c5ff956115d82965bd26628a4124164944c34416bcd70b523e27`。公网检查首次因本机Python默认CA为空而失败的证据保留；使用已安装受信Mozilla CA并保留主机名/证书校验后通过。追查确认02 helper没有明确CA来源，因此**02已发布不等于Mac自动联网已最终验收**。正在制作`1.0.1/build2026110801`：显式certifi CA、冻结实物真实官方HTTPS正向/未知自签负向探针，以及已接入02时受控替换稳定helper的修复流程，保留current/base/highest_sequence、原06隐藏备份及客户资料。原06基础包不重做、不覆盖；本机不运行Qt/安装器/冻结helper。0801完整CI、发布、receiver更新及网页新鲜资料验收完成后才能开启AUTO。

历史状态（02初次验收完成、尚未接线时；当前进度以上节为准）：更新源码及02两平台原生全链验收完成，已经快进合入main；四成品已本地下载/校验及签名，服务器接线和公开发行当时尚待完成。用户已明确授权“把 Windows 和 Mac 的更新接入补丁、后续自动更新功能都实现，现有 1.00 基础包保持不变”；用户后来反馈 Python 反复意外退出，此为当前任务中的修复要求，未取消原实施任务。

## 02完成记录

[run42379066480](https://github.com/Arcjang999/LJQCapp_clean/actions/runs/42379066480)两端全部success，来源`2adb7721060582308164a4b10a13d8d3c66dd14f`，共同摘要`9b8100574296fa4d1083d9b91013d34276d57f65c8a7f59bba1e3596dcbabc72`，`1.0.1/build2026110702`。两端均实际从原06创建旧schema、安装接入、调用真实新冻结服务预检与健康、完整备份并保持74表全部原typedcells及PDF BLOB/附件；原旧包、生产公钥未改，系统保护未关闭。冻结接入窗口的线程及四阶段探针通过。Windows一次性runner的安装与Known Folder改动已恢复；这不冒充Windows10/11客户机完整GUI验收。

main已由`ddec9e5`快进到同一`2adb772`，未改历史；因此两份原生程序和main实际功能源一致。合入触发的同提交重复run42380302031已取消，完整成功的原run与成品保持。随后文档记录可前进，不要求重做同一版本程序。

四份成品在本地`output/desktop-update-release-2026-11-07/downloads-build02/`，平台源目录各带native/UI/adoption回执，根目录`download-verification-build02.json`记录实算长度和SHA：

| 目标 | 成品 | 字节 | SHA-256 |
| --- | --- | --- | --- |
| Windows x64 | 程序补丁 | 356658806 | `18007fa89a91e4a171b884a10091dde01f1b83007f2b7cde72412fd8a1ed25a6` |
| Windows x64 | 更新接入Setup | 39021001 | `813c75b7df56ef5e91bcc89a1db554f40af36972746181ac45ad126bc91983ea` |
| Mac arm64 | 程序补丁 | 290834228 | `4c5ba8abd8ae98047d4fb5ea9a4047b3de02f2ac32e94d48da21670f0771b863` |
| Mac arm64 | 更新接入DMG | 49730713 | `e4db85d3f172a8e6095cf0c01b02bab9c9086480347a5c955441f8bc61a00f93` |

已用本地专用Ed25519密钥签名，`signed-build02/`为精确四文件及签名清单，不含私钥；本地发布包`signed-build02-bundle.tar`为736266240字节，SHA`6a47ee905555e343370a8813e8ec5ef47f59790709794b0f238b577d31a023cc`。发行ID`1.0.1-2026110702-9b8100574296`，序号1794061200212969，签发2026-11-07T14:20:00.212969Z、有效14天。当前**signed_not_published**，这些预定URL尚不可当作已上线下载链接。

源码增量`main-updates-build02.bundle`为854719字节/SHA`c8b790235f1685f48411d46828ffc0460ea116d79395f18e5eaf1a90881e93cb`，只从服务器已知b26fe36推进到2adb；接线工具`receiver-tools-build02.tar.gz`为20296字节/SHA`0a28b95043101a4994665c97ab665c54a96fba01cc344d698440cad427736ae9`，只含六个公开程序/资源，无私钥和业务资料。汇总见本地`staged-transfer-files.json`。

网页保留发布工具最终包为`cloud-tools/ljqc-auto-updates-tools-20261107-v2.tar.gz`，33989字节/SHA`8a9f9fa914e0b9f84766bc420b21071bc12cb062cbc4cb5351ec267924272164`；15项源码/证据/依赖/权限模拟及23项新建74表资料保持测试通过，另有SQLite隔离探针。固定旧b26fe36→功能2adb，源码合同锁定56个变动文件及585个未改文件。服务器依赖单独dry-run，仅以`--no-deps`新增缺失的审核精确版本，禁止升级原包；准备/检查阶段不初始化业务库。实际发布必须新建本轮双备份与typedcell/BLOB/附件基线，V2策略拒绝旧snapshot及旧导航例外。此包仅在本地验证，不代表服务器已执行；同目录无`-v2`旧草稿禁用。

腾讯云会话再次超时，重新连接显示微信MFA，Chrome的OrcaTerm已留handoff并异步请用户扫码，当前仍在身份验证页。本轮尚未向服务器上传或执行工具、创建账号、改Nginx或业务运行程序。GitHub的专用环境/Secrets/变量也未配置；新的持续上传权限需在实际启用前确认，完成扫码不等于新增权限授权。后续必须以服务器当时资料新建双备份和严格基线，不能复用06快照、恢复旧库或沿用旧导航例外。

## 基线

- 本地与远端 main 已核对同为 `ddec9e5f1ca6057312d0f3346c34d59534cdd2be`；开发分支 `codex/desktop-patch-updates`。
- 固定原发行 tag `desktop-base-1.0.0-2026110606` 指向 `d07a57771475a9367e0face03d14f7629b97fe04`。原 Windows Setup 和 Mac DMG 文件、SHA-256、公开目录及 `resources/software_downloads.json` 保持不变。
- 新程序当前候选 `1.0.1/build2026110702`；基础身份与实际程序版本分开保存。操作见[更新说明](../../desktop-updates.md)。

## 已实现的源码

`desktop_updates` 实现固定原包全文件接入验证、两平台稳定启动入口、发行公钥与 Ed25519 清单验签、五秒清单超时、同源下载、完整归档/内链/权限检查、服务/桌面/维护锁、独立候选副本、原有类型单元格/BLOB/报告/附件保持、程序及数据切换日志与前向恢复。格式未知、旧数据丢失、破坏性迁移或附件文件需搬迁时拒绝自动安装。

候选副本初始化曾发现既有内置目录时间刷新造成严格校验失败；增加内部 `refresh_reference_data` 参数，默认保持原启动行为，仅已有候选副本暂停重复参考种子，完整结构检查仍执行。未放宽原客户记录核对规则。

新增日常补丁构建、双平台原生检查、一次性接入组件、签名与受限发布、每周元数据刷新源码；原基础安装器仅显式选择才构建。网页基础下载和接入下载分开，签名索引不存在时保持基础链接可用并禁用接入按钮。

## Python 意外退出的处理

本机离屏 Qt 初测出现 platform 插件发现失败及线程析构 SIGSEGV。用户反馈后立即停止全部相关启动实验，读取本机崩溃报告确认 `QObject::~QObject → QThread::~QThread`；没有业务库操作。修正为窗口主线程绑定槽、线程 wait、断开信号、解除引用及关闭清理。

唯一一次受控修复复核的四条 UI 流程全部通过，证据 `output/desktop-update-release-2026-11-07/ui-check.log`；此后不再在本机重复启动 native Qt。冻结 helper 新增隐藏 `--ui-self-test --probe-result`，仅在 GitHub runner 做成品图形探针，不把 `--version` 或无窗口健康冒充图形验收。

## 当前验证与后续

首个实现提交 `ecf8a1f7c09748a7410e8f784c371051186e1f92` 已推送，触发[原生CI首轮42373808170](https://github.com/Arcjang999/LJQCapp_clean/actions/runs/42373808170)。Mac完整构建、冻结预检/服务、接入组件及冻结UI探针全部成功；Windows在源码检查中发现 `_switch_data` 对只读句柄执行fsync触发EBADF，以及Mac权限/目录symlink跨平台夹具问题。本轮未发布首轮任何文件，原失败日志保留在本地 `ci-first-failure.log`。

Windows落盘改用读写句柄；Mac归档始终检查受签执行位，仅Windows重核跳过无法表达的本机POSIX位；Mac模拟夹具及目录symlink目标改为正确原生表示，不放宽生产路径/签名校验。另补新版程序直接从Dock/版本目录启动时先转稳定helper，受管启动标志避免循环；原06文件不改。

02本机17套182项已通过，记录 `output/desktop-update-release-2026-11-07/build02-source-checks/summary.json`；其后再新增一项Known Folder恢复回归，打包套件现16项单独通过。核心26、接入12、manager15、网页接入14、入口重定向4和接线8项分别通过。Qt四项单独留证，本机没有重复native启动。

新增 `ci_verify_update_adoption.py` 只允许匹配的GitHub-hosted runner执行，真实原06服务通过127.0.0.1本地WebSocket会话创建旧schema，再写隔离项目/报告PDF BLOB/附件；不调用新版初始化来冒充旧库。它实际安装原包/组件，使用临时QA签名和source manager的同源核心调用真实新冻结预检与健康服务、切换和资料核对；包内生产公钥不改、不打开业务Qt窗口、不上传QA库。Win临时Known Folder和Inno登记最终恢复；本机只验runner拒绝门槛、夹具和恢复模拟，实际全链结果须看02原生CI。

新增只读接线工具包/安装脚本和[部署操作](../../desktop-update-receiver.md)，独立复核8项安全检查通过。发布权限判断已移到实际GitHub environment步骤读取环境变量，feature永不发布，main仅明确手动请求或push/schedule且AUTO授权时发布；未手动选择发布的普通workflow_dispatch不会因AUTO而意外晋级。

尚需完成：确定源码提交并推送，GitHub 双平台真实构建/预检/图形探针，下载核对接入包与程序补丁；原06真实安装后的接入与更新验收；受限服务器发布身份、公开版本路径、GitHub Secrets/每周刷新接线；公开 HTTPS 文件与签名校验，最后保留服务器当时全部资料发布网页接入入口。

服务器只读核对：2026-11-07当前仍 `b26fe36/main`，`ljqcapp/nginx` active，根盘39G、空闲27G；公开根和downloads为root:root0755，业务目录`/var/lib/ljqc`为ljqc:ljqc0750，尚无`ljqc-update`账号。本轮未改变服务器程序、业务资料或基础文件。

发行签名密钥和专用上传密钥仅在本地忽略目录 `output/desktop-update-release-2026-11-07/{signing,deployment}` 准备，目录0700/私钥0600，未在仓库、日志或公开成品中写入私钥，尚未给GitHub或服务器授予新权限。固定主机公钥沿已核对的 `output/cloud-download-release-2026-11-06/connection/known_hosts`，仍须与控制台公钥比对。新增服务器上传权限的实际启用须在可审阅接线完成后按操作界面的安全要求确认，不能以源码实现冒充配置完成。
