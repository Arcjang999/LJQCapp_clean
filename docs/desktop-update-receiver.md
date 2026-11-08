# 桌面补丁服务器一次性接线

适用现有 Ubuntu 服务器 `159.75.243.120`。本流程保留 1.00 基础安装包，不修改 `/opt/ljqcapp`、业务 systemd、数据库、附件或备份。先完成补丁与接入组件的两平台验收，再开启自动发布。

## 1 导出可核对的工具包

在已提交且干净的项目检查点执行：

```sh
python scripts/install_desktop_update_receiver.py bundle --output output/desktop-update-release-2026-11-07/receiver-tools.tar.gz
```

输出包含整包 SHA-256、长度和完整源码提交。包内只有接线脚本、固定发布工具、更新协议/HTTPS模块、公开发行公钥和逐文件摘要清单；没有 SSH 私钥、更新签名私钥、业务资料或基础安装包。工具包不接收任意路径列表。后续源码改变须重新提交并导出新包。

将工具包和专用部署身份的 **Ed25519 SSH 公钥** `ljqc-update-deploy.pub` 上传到控制台可见的临时位置。私钥只留在受控本机及 GitHub 部署环境，不能上传服务器。核对服务器上的工具包 SHA-256 与本机输出一致；查看归档成员后，在 root 所有、`0755` 的新 `/var/tmp/ljqc-update-receiver-<build>/` 目录使用 `tar --no-same-owner` 解开。工具包已将成员归一为 root/0644，不携带本机用户身份。后续命令使用该目录内的脚本。

## 2 先检查，再安装专用接收身份

```sh
sudo /usr/bin/python3 /var/tmp/ljqc-update-receiver-<build>/scripts/install_desktop_update_receiver.py check --authorized-key-file /home/ubuntu/ljqc-update-deploy.pub
sudo /usr/bin/python3 /var/tmp/ljqc-update-receiver-<build>/scripts/install_desktop_update_receiver.py install --authorized-key-file /home/ubuntu/ljqc-update-deploy.pub
```

`check` 不写文件。`install` 要求 root，并再次检查全部前置条件；遇到已有用户、目录、工具或 SSH 策略不匹配时停止，不递归改权限、不改现有用户、不替换外来文件，也不调整业务目录权限。现有 SSH 须启用 Ubuntu PAM 公钥登录、关闭 `PermitUserEnvironment`，`AcceptEnv` 仅接收语言区域变量；否则脚本停止供人工审核，不替你修改 SSH 配置。部分执行中断可能留下本工具自己新建的只读文件；保留现场，核对后再继续。

sudo 核对固定使用 `LC_ALL=C`、`LANG=C`、`LANGUAGE=C`。部分 sudo 版本在 root 成功查询另一账号策略时，即使结论是无授权也返回 0；因此只接受完整单行 `User ljqc-update is not allowed to run sudo on <本机主机名>.`，返回码须为 0 或 1，且标准错误为空。授权列表、错误、警告、其他账号/主机、空输出或混合输出都停止，不能把“返回非零”当作无权限的证明。

若旧版检查因此停在“已创建专用只读环境和锁定账号、尚未安装公钥”，保留现场并核对这个阶段；不要放宽检查或重建客户端。修复只涉及服务器接线脚本、其回归和本文，不进入客户端发行摘要。需从新的已提交源码导出新名字的 receiver 工具包，保留旧 tar 和 manifest。已有 root 只读脚本或工具 manifest 与新包不一致时仍会停止；应先分别备份、核对并受控更新这些确切工具文件，再重新 `check` 和 `install`，不能覆盖整个运行环境或递归改权限。

2026-11-08 的 HTTPS 修复要求专用接收环境也有明确的 CA 根。`check` 只读报告 `runtime_dependencies`；已有 `cryptography==50.0.2` 且缺少 certifi 时，`install` 只在 `/opt/ljqc-update-publisher/venv` 执行 `pip install --no-deps --only-binary=:all: certifi==2026.7.22`，随后实际加载根并再次检查 root 所有权和只读权限。已有完整环境不再安装任何包；crypto 缺失、版本冲突或 CA 加载失败都停止，不能借此重解 crypto/cffi 或覆盖业务虚拟环境。业务环境已有的其他 certifi 版本保持原样。旧版 receiver 仍需先核对并受控替换新工具包明确列出的源码文件和 manifest，不覆盖旧归档，也不改变基础安装包。

| 路径/身份 | 安排 |
| --- | --- |
| `ljqc-update` | 独立系统用户和同名组、密码锁定、无附加组和 sudo 授权。 |
| `/opt/ljqc-update-publisher/` | root 所有的只读工具树及独立虚拟环境，固定 `cryptography==50.0.2`、`certifi==2026.7.22`。原有应用环境不变。 |
| `/opt/ljqc-update-publisher/home/.ssh/authorized_keys` | root 所有；仅专用公钥、`restrict` 和固定 forced command，用户不能改公钥。 |
| `/opt/ljqc-update-publisher/receiver-shell` | root 所有的受限入口；拒绝交互 shell、任意命令和 SFTP/SCP。 |
| `/var/lib/ljqc-update-publisher/` | 仅专用用户可访问的接收暂存目录，`0700`。 |
| `/var/www/ljqc-public/downloads/patches/`、`downloads/update-bootstrap/`、`updates/desktop/dev/` | 仅这三个新增静态子树交给专用用户，`0755`；下载根目录仍为 root/0755。 |
| `/var/lib/ljqc/` | 必须对专用用户不可读、不可遍历；脚本只检查权限，不读取业务文件或修改现有权限。 |

受限 shell 清理环境变量，只允许 SSH 原始命令 `ljqc-update-upload`，再执行以下固定接收命令：

```text
/opt/ljqc-update-publisher/venv/bin/python -I /opt/ljqc-update-publisher/packaging/publish_desktop_update.py receive-and-promote --staging-root /var/lib/ljqc-update-publisher --public-root /var/www/ljqc-public --trust /opt/ljqc-update-publisher/resources/desktop_update_trust.json
```

接收端只收有大小、文件数及路径限制的普通 tar 文件，不接受压缩输入、链接、稀疏文件或任意 shell。它验证两平台签名及来源、静态文件长度/哈希和实际 HTTPS 下载字节，最后才切换同一 `current` 指针。失败保留上一频道；成功删除本次私有大文件暂存副本。静态旧发行保留，两阶段均保留至少 1 GiB 空闲空间。

## 3 审核更新清单的 Nginx 静态入口

安装器只生成提案：`/opt/ljqc-update-publisher/nginx-updates-location.proposal.conf`。先读取现有配置，确认 HTTPS server、ACME、`/downloads/` 和应用反向代理，再将该片段加入现有 HTTPS server。它只服务 `/var/www/ljqc-public/updates/` 下的 JSON，禁止目录列表，设置 `no-store` 与 `nosniff`；不会自动改配置或重载服务。

人工核对后执行 `nginx -t`，通过才重载 Nginx。不要覆盖整个配置或重启业务 systemd。若现有 `disable_symlinks` 等规则阻止频道软链接，应先审核该静态路径的实际访问情况。

## 4 配置 GitHub 的部署环境

环境名称为 `desktop-updates`，限制可信 `main` 发布；签名身份与 SSH 上传身份分开。填入：

| 类型 | 名称 | 内容 |
| --- | --- | --- |
| Secret | `LJQC_UPDATE_SIGNING_KEY` | 与客户端内置发行公钥匹配的 Ed25519 原始 32 字节私钥的 Base64；不是 SSH 私钥。 |
| Secret | `LJQC_UPDATE_DEPLOY_KEY` | 专用 `ljqc-update` 身份的 SSH 私钥完整文本。 |
| Secret | `LJQC_UPDATE_KNOWN_HOSTS` | 从可信控制台核对的服务器 SSH 主机公钥行；不临时信任网络扫描结果。 |
| Variable | `LJQC_UPDATE_DEPLOY_USER` | `ljqc-update`。 |
| Variable | `LJQC_UPDATE_DEPLOY_HOST` | `159.75.243.120`。 |
| Variable | `LJQC_UPDATES_AUTO_PUBLISH` | 接线与真实发布验收完成后设为 `true`；此前保持未设置或 `false`。 |

客户端从自有 HTTPS 下载，不使用 Actions 临时附件 URL。缺少签名或上传配置时，工作流明确保留候选或已签名附件，不声称已发布。feature 分支可以构建验收，但不能晋级正式频道。

日常 `Desktop cumulative updates` 仅在同次两平台全部成功后签名；`main` 推送或每周刷新还须在实际部署环境内读取到 `LJQC_UPDATES_AUTO_PUBLISH=true` 才自动上传。手动运行须明确勾选 `publish`，未勾选时即使开启 AUTO 也只保留签名候选；feature 分支始终不能发布。旧 `desktop-build.yml` 手动入口默认也走补丁流程，只有负责人明确要求并选中 `rebuild_base_installers` 才制作基础安装器。每周 `Refresh desktop update metadata` 只重新签发已发布程序的元数据，清单有效 14 天；程序字节和基础包不变。应监测刷新结果，失败或过期时客户端保留本机兼容版本，不接受失效清单。

首次发布后核对两个平台 `https://159.75.243.120/updates/desktop/dev/<target>/latest.json`、签名/哈希、接入组件与补丁真实下载，以及原 1.00 文件和下载记录未变化。自动发布不能替代本轮 Windows/Mac 接入、业务资料保持和跨版本恢复验收。
