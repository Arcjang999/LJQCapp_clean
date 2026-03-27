# 实验室质控 LJ 曲线软件

## 项目目录

- 当前项目根目录：`D:\LJQCapp`
- 运行脚本、打包脚本和产物路径均以该目录为准

## 项目简介

这是一个基于 Python、Streamlit、SQLite、pandas 和 matplotlib 的本地质控工具，用于：

- 项目管理
- 批次管理
- 数据录入
- Westgard 规则判定
- LJ 图查看
- Excel / CSV / PNG 导出
- 月度质控图导出

## Update 1.1

相较于最初“继续开发”时的版本，当前版本已经完成以下更新：

- 检测值输入支持 `log10` 实时计算，输入过程中即时刷新；空值、非数字、0、负数会自动清空结果并给出轻量提示
- 检测人支持同批次历史姓名记忆，可下拉选择，也可直接输入新姓名；保存后自动去重并加入候选列表
- `work_tab` 页面重构为“录入与统计 / 图表与判读 / 规则与记录 / 维护与导出”四层信息流，首屏更适合录入后直接看图和看最新结果
- 最新结果分析移动到 LJ 图下方，并改为更紧凑的状态条样式
- 左侧状态卡片按结果分级显示颜色：在控为绿色、警告为黄色、失控为红色
- LJ 图支持“标准视图 / 全范围视图”，标准视图默认聚焦 `Mean ± nSD`
- 超出标准视图范围的异常点不会丢失，会以边界标记和数值提示显示
- 图表控制改为默认折叠，并在标题中显示当前模式摘要，减少首屏占高
- 本批次规则汇总改为紧凑卡片形式，并修复了 HTML 源码外露问题
- 当前批次检测记录改为默认展开的折叠区，并修复了记录表格 HTML 源码外露问题
- 检测记录维护改为弹出式窗口，主页面只保留维护入口按钮
- 检测记录维护弹窗的状态管理已重新整理，删除记录后不会再触发 `session_state` 与 widget key 冲突
- 导出支持 `Excel (.xlsx)` 和 `CSV (.csv)`；CSV 使用适合 Excel 打开的中文编码
- 顶部批次基础信息改为紧凑一行摘要式展示，首屏空间更充分
- 新建批次时，“建靶所需次数”默认值调整为 `20`
- 实时统计口径调整为：仅基于当前批次中判定为“在控”的正式数据计算，自动排除警告和失控结果
- 当检测记录被修改或删除后，实时 `Mean / SD / CV%` 会按当前有效在控数据自动重算
- 页面可见字段、表头和摘要信息进一步中文化
- 页面顶部右上角增加“问题反馈”按钮，点击后会在新标签页打开反馈表单
- matplotlib 绘图层已补充中文字体回退策略，优先兼容 Linux 云服务器环境
- LJ 图已修复顶部标注与标题重叠问题，通过局部偏移避免干扰标题，不改变当前视图范围和整体观感

## 当前界面布局

当前 `work_tab` 页面按“录入效率 + 首屏可见性 + 模块层级清晰”整理为三部分：

### 第一部分：主操作区

左右两列布局：

- 左列：批次数据录入、保存检测结果、建靶统计、实时统计
- 右列：图表与判读、图表控制（默认折叠）、LJ 图、最新结果分析

### 第二部分：规则与记录

- 本批次规则汇总
- Westgard 规则说明（默认折叠）
- 当前批次检测记录（默认展开，可手动折叠）

### 第三部分：维护与导出

左右两列布局：

- 左列：检测记录维护入口按钮
- 右列：当前批次数据导出、当前 LJ 图 PNG 导出、月度质控图 PNG 导出

## 当前统计口径说明

- 建靶统计：仅基于建靶阶段前 `target_n` 次数据计算
- 实时统计：仅基于当前批次内判定为“在控”的正式数据计算
- 警告和失控结果不会纳入实时 `Mean / SD / CV%`
- 若时间范围内有效“在控”正式数据不足，界面会保留当前空值/提示逻辑，不会报错

## 图表中文字体说明

- matplotlib 已在绘图模块中自动扫描系统可用字体
- 中文字体优先尝试：
  - `Noto Sans CJK SC / TC / JP`
  - `Noto Serif CJK SC / TC / JP`
  - `Source Han Sans SC / CN`
  - `WenQuanYi Zen Hei / WenQuanYi Micro Hei`
  - `Microsoft YaHei / Microsoft YaHei UI / SimHei`
  - `PingFang SC`
  - `Arial Unicode MS`
- 若系统中存在上述任一字体，LJ 图标题、坐标轴、图例、规则标注中的中文会自动使用可用字体
- 同时已设置 `axes.unicode_minus = False`，避免负号显示异常
- 启动时会打印最终生效的 `CONFIGURED_FONT_FALLBACKS`，便于在 Linux 服务器上排查字体问题

## 常用命令

安装依赖：

```powershell
python -m pip install -r requirements.txt
```

启动应用：

```powershell
python -m streamlit run app.py --server.headless true --server.port 8506
```

或直接双击：

- `run_app.bat`

## 数据库说明

- 数据库持久化位置：`%LOCALAPPDATA%\LJQCApp\qc_lj_app.db`
- 应用启动时会自动创建数据库和表结构
- 当前更新不修改数据库字段结构，主要通过 `app.py` 页面组织、`qc_logic.py` 统计口径调整和 `plotting.py` 图形渲染优化实现
- 如需清空试用数据，可运行：

```powershell
python reset_db.py
```

或双击：

- `reset_db.bat`

## 打包说明

推荐使用 `one-folder` 方案。

直接双击：

- `build_exe.bat`

或手动执行：

```powershell
python -m PyInstaller --clean -y LJQCApp.spec
```

打包产物位于：

- `dist/LJQCApp`
