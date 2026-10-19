# Linux 试用部署

当前云端入口沿用 `app.py` 和 `codex/project-workflow-alignment` 分支，使用独立的运行账号与数据目录。适合少量使用者共同试用同一套资料；尚未实现各实验室账号和数据隔离。

## 首次部署

使用 Ubuntu LTS、x86_64。服务器需允许 HTTP 80 端口，应用自身仅监听本机 8501。

```bash
sudo install -d -o "$USER" -g "$(id -gn)" /opt/ljqcapp
git clone --depth 1 --single-branch --branch codex/project-workflow-alignment https://github.com/Arcjang999/LJQCapp_clean.git /opt/ljqcapp
cd /opt/ljqcapp
sudo bash scripts/deploy_cloud.sh YOUR_PUBLIC_IP
```

脚本安装 Noto 和文泉驿中文字体、网页运行依赖和 Nginx，并启用开机自动运行。`requirements-cloud.txt` 不安装桌面启动器所用的 Qt。`MPLCONFIGDIR` 指向运行账号可写的缓存；图表使用无界面渲染，PDF 选用可嵌入的 TrueType 中文字体并保留中文字形映射。

运行记录在 `/var/lib/ljqc/qc_lj_app.db`，附件跟随该库保存，备份在 `/var/lib/ljqc/backups`。源码目录的 `data/storage_config.json` 只保存云端路径，不复制个人电脑上的配置。部署脚本遇到不同的已有路径会停止，避免覆盖现有数据。

云端设置页提供完整备份下载及上传恢复。恢复会替换全部使用者共同使用的数据，须确认；原有完整校验、附件恢复和恢复前保护备份仍执行。服务器文件夹选择与打开按钮不在网页里显示。

## 运行验收

```bash
sudo -u ljqc env MPLCONFIGDIR=/var/cache/ljqc/matplotlib MPLBACKEND=Agg OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 /opt/ljqcapp/.venv/bin/python /opt/ljqcapp/scripts/verify_cloud_runtime.py --output-dir /var/lib/ljqc/verification
sudo systemctl status ljqcapp nginx --no-pager
curl --fail http://127.0.0.1:8501/_stcore/health
```

验收脚本仅使用新建临时库，检查真实 LJ、多水平报告、中文和符号字形、PDF字体嵌入及文字提取，保留 PDF、首页图片和 `verification.json`。再从外部浏览器核对首页、项目勾选后打开、质控图、报告下载与设置备份。单次报告验收的内存记录不能当作并发容量。

## 后续更新

先在网页设置中下载完整备份，再在无人录入时更新：

```bash
sudo systemctl stop ljqcapp
cd /opt/ljqcapp
git pull --ff-only origin codex/project-workflow-alignment
sudo bash scripts/deploy_cloud.sh YOUR_PUBLIC_IP
```

脚本不会恢复试用快照或重建运行库。只有首次明确选用试用资料时才从 `test_data/trial-2026-10-13/trial-data.zip` 恢复；后续发布继续使用服务器已有记录。

查看服务日志使用 `sudo journalctl -u ljqcapp -n 80 --no-pager`。公网目前按 IP 提供 HTTP；域名和 HTTPS 须在有域名后另行配置。
