# Linux 试用部署

**每次更新前先阅读 [更新执行约束](UPDATE_POLICY.md)。** 后续更新按该约束确定验证范围；下方首次部署和运行环境验收用于对应场景。

当前云端入口为 `app.py`，发布分支为 `main`，使用独立的运行账号与数据目录。网页通过现有 HTTPS 入口访问。适合少量使用者共同试用同一套资料；尚未实现各实验室账号和数据隔离。

## 服务器操作入口（2026-11-10）

后续服务器维护统一使用本机 **OrcaTerm 桌面客户端**，不再使用浏览器中的腾讯云 / OrcaTerm 网页终端。2026-11-10 已在本机确认以下远程会话已连接：

- 服务器：`159.75.243.120`
- 登录账号：`ubuntu`
- 终端：`ubuntu@VM-0-7-ubuntu: ~`

打开本机 OrcaTerm，复用已有连接并核对目标服务器及账号，在该会话中执行部署、服务管理和日志命令。上传或取回文件使用客户端的文件管理器，核对传输目录和文件；源码更新仍在 `/opt/ljqcapp`，在用资料仍位于 `/var/lib/ljqc`。连接失效时在客户端恢复，不自行改回网页终端。

业务网页版的页面查看、下载核对和功能验收仍使用浏览器。GitHub 的自动构建、签名和发布继续走既有流程，需要人工或助手维护服务器时使用本机 OrcaTerm。

## 首次部署

使用 Ubuntu LTS、x86_64。服务器需允许 HTTP 80 端口，应用自身仅监听本机 8501。

```bash
sudo install -d -o "$USER" -g "$(id -gn)" /opt/ljqcapp
git clone --depth 1 --single-branch --branch main https://github.com/Arcjang999/LJQCapp_clean.git /opt/ljqcapp
cd /opt/ljqcapp
sudo bash scripts/deploy_cloud.sh YOUR_PUBLIC_IP
```

脚本安装 Noto 和文泉驿中文字体、网页运行依赖和 Nginx，并启用开机自动运行。`requirements-cloud.txt` 不安装桌面启动器所用的 Qt。`MPLCONFIGDIR` 指向运行账号可写的缓存；图表使用无界面渲染，PDF 选用可嵌入的 TrueType 中文字体并保留中文字形映射。

运行记录在 `/var/lib/ljqc/qc_lj_app.db`，附件跟随该库保存，备份在 `/var/lib/ljqc/backups`。源码目录的 `data/storage_config.json` 只保存云端路径，不复制个人电脑上的配置。部署脚本遇到不同的已有路径会停止，避免覆盖现有数据。

云端设置页提供完整备份下载及上传恢复。恢复会替换全部使用者共同使用的数据，须确认；原有完整校验、附件恢复和恢复前保护备份仍执行。服务器文件夹选择与打开按钮不在网页里显示。

## 首次部署或运行环境变更时的验收

```bash
sudo -u ljqc env MPLCONFIGDIR=/var/cache/ljqc/matplotlib MPLBACKEND=Agg OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 /opt/ljqcapp/.venv/bin/python /opt/ljqcapp/scripts/verify_cloud_runtime.py --output-dir /var/lib/ljqc/verification
sudo systemctl status ljqcapp nginx --no-pager
curl --fail http://127.0.0.1:8501/_stcore/health
```

验收脚本仅使用新建临时库，检查真实 LJ、多水平报告、中文和符号字形、PDF字体嵌入及文字提取，保留 PDF、首页图片和 `verification.json`。再从外部浏览器核对首页、项目勾选后打开、质控图、报告下载与设置备份。单次报告验收的内存记录不能当作并发容量。

## 后续更新

常规更新复用已有备份和部署机制。先确认本地已验证版本、服务器当前提交及本轮main目标；部署前备份服务器当时最新的数据、附件和配置，再正常快进源码并重启现有服务。随后确认服务健康、页面版本、本次改动涉及的关键操作，以及原数据、报告和附件保持，简要记录结果和备份位置。

本地已完成的完整功能回归直接引用结果，云端只补跑受影响的少量检查。每次更新使用最新备份；依赖没有变化时沿用当前环境。原有HTTPS、Nginx、systemd和桌面补丁接收端保持。数据库结构、运行依赖或更新通道发生变化时，按[更新执行约束](UPDATE_POLICY.md)扩大对应验证。

上面的首次部署脚本用于新服务器准备。现有服务器更新保留在用资料；已有新写入时，旧备份不得覆盖新记录。桌面程序通过main的既有双平台构建和签名流程发布，网页版按本次要求独立部署。查看服务日志使用 `sudo journalctl -u ljqcapp -n 80 --no-pager`。
