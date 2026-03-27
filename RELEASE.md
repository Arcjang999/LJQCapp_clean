# 试用版发布前整理

## Version 1.1 更新说明

本轮相较于最初继续开发时的版本，主要完成了以下优化：

- 录入区支持 `log10` 实时计算，输入过程中即时刷新，并对空值、非数字、0、负数做轻量提示
- 同一批次内支持检测人姓名记忆，可下拉复用历史姓名，也可直接输入新姓名
- `work_tab` 页面重新分层，首屏优先突出“录入 -> 看图 -> 看最新分析”的使用流程
- 最新结果分析移动到 LJ 图下方，并改为更紧凑的状态条样式
- 图表控制改为默认折叠，并在标题中显示当前图表模式摘要
- LJ 图支持“标准视图 / 全范围视图”，标准视图下超界点会保留边界提示，不会误解为数据丢失
- 本批次规则汇总改为紧凑卡片展示，并修复了 HTML 源码外露问题
- Westgard 规则说明默认折叠，当前批次检测记录默认展开且支持手动折叠
- 检测记录维护改为弹出式窗口，主页面仅保留入口按钮
- 导出支持 `Excel (.xlsx)`、`CSV (.csv)`、当前 LJ 图 PNG 和月度质控图 PNG
- 页面可见字段、表头和摘要信息进一步中文化
- 新建批次时，“建靶所需次数”默认值调整为 20
- 实时统计口径改为：仅纳入当前批次中判定为“在控”的正式数据，自动排除警告和失控结果
- 当检测记录被修改或删除后，实时 `Mean / SD / CV%` 会按当前有效在控数据自动重算

## 当前项目目录

- 当前项目根目录统一为：`D:\LJQCapp`
- 后续运行、打包、分发说明均以该目录为准

## 数据库存储位置

- 当前数据库保存到：`%LOCALAPPDATA%\LJQCApp\qc_lj_app.db`
- 这个位置同时适用于源码运行和 PyInstaller 打包后的 EXE
- 程序会自动创建目录
- 如果项目目录中存在旧数据库：
  - `data/qc_lj_app.db`
  - `lj_qc.db`
  - 启动时会在新位置不存在数据库的前提下，自动迁移到新位置

## 安全重置数据库

关闭程序后，任选一种方式：

```powershell
python reset_db.py
```

或直接双击：

- `reset_db.bat`

效果：

- 删除当前持久化数据库文件
- 如果项目目录里还残留旧数据库文件，也会一并清理
- 下次启动应用时，`init_db()` 会自动重新创建数据库和表结构

注意：

- 不会删除任何代码文件
- 不会在正常启动时自动清库

## 发布前推荐流程

1. 关闭正在运行的程序
2. 执行 `python reset_db.py` 或双击 `reset_db.bat`
3. 启动一次应用，确认会自动创建全新数据库
4. 关闭应用
5. 执行 PyInstaller 打包
6. 把 `dist/LJQCApp/` 整个目录发给同事试用

## PyInstaller 方案

### 推荐类型

推荐使用 `one-folder`：

- 更稳定
- `Streamlit / pandas / matplotlib / pyarrow` 这类依赖对 `one-folder` 更友好
- 启动速度通常比 `one-file` 更平稳
- 即使 EXE 更新，数据库仍然保存在 `%LOCALAPPDATA%\LJQCApp\qc_lj_app.db`

### 安装打包工具

```powershell
python -m pip install pyinstaller
```

### 推荐打包方式

优先双击：

- `build_exe.bat`

或手动执行：

```powershell
python -m PyInstaller --clean -y LJQCApp.spec
```

### 产物说明

- EXE：`dist/LJQCApp/LJQCApp.exe`
- 分发方式：把整个 `dist/LJQCApp` 文件夹发给同事

如果项目路径包含中文，推荐优先使用 `build_exe.bat`：

- 它会先在 `%LOCALAPPDATA%\LJQCApp\pyinstaller_dist` 下完成稳定构建
- 然后再同步回项目内的 `dist/LJQCApp`

## 入口文件说明

- `run_app.py` 是打包入口
- 它内部通过 `streamlit run app.py` 启动现有应用
- `app.py`、`database.py`、`plotting.py`、`qc_logic.py` 保持现有结构
- `run_app.bat` 可用于本地双击启动做冒烟检查
