# GitHub 两平台安装包构建完成

2026-11-06。用户要求现在使用GitHub直接构建，已将必要安装源码和工作流推送当前开发分支；[最终运行42176122800](https://github.com/Arcjang999/LJQCapp_clean/actions/runs/42176122800)的Windows和Mac任务均成功。

## 来源和产物

源码提交 `76c51b081ff73a8f69bdd2ad374ad4d17f911815`，分支 `codex/project-workflow-alignment`；版本1.0.0，构建2026110603，资料格式1，`source_dirty=false`。两端发行输入摘要均为 `7834a84b6ed5fccabaa61c5d268629192537c1c17fb14d33333abe8cb68555cd`。

| 平台 | GitHub附件 | 附件ZIP大小 / SHA-256 |
| --- | --- | --- |
| Windows10/11 x64 | [LJQCApp-windows-x86_64-5-1](https://github.com/Arcjang999/LJQCapp_clean/actions/runs/42176122800/artifacts/13482624585) | 246,104,914字节；`8293533386b4d80fa82badb5f6ccd96eab96d037a08a32d27aef9554e1d8d564` |
| Mac Apple芯片 | [LJQCApp-macos-arm64-5-1](https://github.com/Arcjang999/LJQCapp_clean/actions/runs/42176122800/artifacts/13484130342) | 358,400,432字节；`b040b76eb82878e4f28a4196aa343643459e6ae9400447097d2d564b0fd549bf` |

Windows内含真实 `LJQCApp-1.0.0-2026110603-7834a84b6ed5-windows-x64-Setup.exe`（244,431,072字节，SHA256 `9dc4679dbe83e858b1dcdadbb56b8cd1eb7f6292f40843cb70fcda53c996ae10`）；Mac内含待签名DMG（356,336,800字节，SHA256 `94b34c8ceaece8d6084132aca6ce0b22fbaf9950fb7bd40629ef8692ccfed4a9`）。上表是附件ZIP校验，不与内部安装文件混用。附件保留3天至2026-11-09，不作为长期客户下载地址。两文件已取回本机，逐文件SHA及两端来源一致性复核通过。

## 验证

Windows采用windows-2022 x64，Mac采用macos-15 arm64，Python3.13及PyInstaller6.22.3。两端各10套82项来源检查通过，失败/跳过/数据库访问守卫命中均0。SQLite只允许各检查的新隔离目录，在用资料未访问。

Windows真实Inno6.7.1及简中准备、两冻结入口版本命令、Setup编译、PE x64、内外元数据/清单、独立资料根的冻结服务健康/正常退出、锁和端口释放均通过。Mac另通过DMG校验/只读挂载/独立复制安装、严格递归签名、两个arm64二进制及逐文件清单验证。

导出仅安装文件及小型版本、清单、环境和校验记录，不含业务库、QA数据、源码快照、旧演示资料或重复程序ZIP。最终附件取回本机 `output/github-actions-2026-11-06/final-downloads/`；本机身份/文件SHA复核见同轮 `delivery-final.json`。

## 已修复与边界

首轮接线修正了Mac路径夹具未模拟宿主、Inno文件版本为0而改用实际ISPP探针、冻结入口错误日志未收全，以及英文Windows的cp1252无法输出中文。后者已有精确traceback，以Windows spec的官方 `X utf8=1`及入口UTF-8配置修复，并新增强制cp1252子进程回归。没有改中文为英文，也未取消版本校验。桌面服务子进程标准输入显式关闭，维护输出保留日志。四轮失败/中间产物保持，最终交付同源第五轮。

Windows runner是Windows Server，Win10/11桌面安装/升级/卸载仍待实机验收。Mac实际runner为macOS15，最低目标13+不等于macOS13实机已验。无窗口健康检查不创建业务会话，未冒称每次CI都验证GUI、首次引导、检测保存或PDF；本地02实际Qt验收是单独证据。Mac仍仅ad-hoc签名，Developer ID及公证未配置。

既有[Westgard规则范围差异](../notes/WESTGARD_RULE_SCOPE_AUDIT_2026-11-06.md)未改变，本轮不表示算法符合性验证通过。未合并main、部署服务器、创建公开GitHub Release或发布客户下载清单。操作见[GitHub构建](../../github-build.md)。本轮日志、每轮摘要和最终来源记录均在 `output/github-actions-2026-11-06/`；最终文档回填不改变上述构建源身份。
