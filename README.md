# 实验室室内质控管理工具

## 项目简介

这是一个基于 Python、Streamlit、SQLite、pandas 和 matplotlib 的本地室内质控工具，当前版本包含以下页面与模块：

- `Main` 欢迎页：用于说明产品定位、方法入口、快速开始和使用教程
- `LJ` 模块：单水平 LJ 曲线质控主流程
- `Z-score` 模块：双水平 / 三水平多水平 IQC 主流程
- `Instant` 页面：预留页，当前仅保留模块定位说明，未接入正式业务逻辑

当前版本适合作为内部试用、演示和小范围部署的基线版本。

## 当前功能概览

### LJ

- 项目与批次管理
- 建靶与正式质控
- Westgard 规则判读
- LJ 图查看
- 最新结果分析与规则汇总
- 检测记录维护
- 当前批次 Excel / CSV 导出
- 当前 LJ 图 PNG 导出
- 月度质控图 PNG 导出

### Z-score

- 项目级双水平 / 三水平配置
- 批次级水平说明配置
- 多水平检测记录级录入
- 建靶期 / 正式质控期管理
- 单水平图 / 合并图
- 建靶期图 / 正式质控图 / 全图切换
- 厂家参考值手工录入位
- 最新结果分析
- 正式期实时统计
- 检测记录维护与整批次重算

### Main

- 正式欢迎界面
- LJ / Z-score / Instant 方法入口说明
- 快速开始路径
- 结构化使用教程
- 当前版本边界说明

### Instant

- 当前仅为预留页面
- 未接入正式业务逻辑

## 当前限制 / 未完成项

- 批量导入未完成
- Z-score 导出未完成
- COA 解析未完成
- peer-group 数据未完成
- `target freeze / re-establish` 等高级流程未完成
- Instant 未实现正式业务功能

## 运行方式

### 安装依赖

```powershell
python -m pip install -r requirements.txt
```

### 启动应用

直接运行 Streamlit 入口：

```powershell
python -m streamlit run app.py --server.headless true --server.port 8506
```

也可以使用启动脚本：

- `run_app.py`
- `run_app.bat`

### 入口文件

- 主页面入口：`app.py`
- 打包后启动入口：`run_app.py`

### 数据库初始化

- 应用启动时会自动执行数据库初始化，无需手工建表
- 数据库默认持久化位置：
  - Windows：`%LOCALAPPDATA%\LJQCApp\qc_lj_app.db`
  - 无 `LOCALAPPDATA` 时：`~/.ljqcapp/qc_lj_app.db`
- 如需清空试用数据，可运行：

```powershell
python reset_db.py
```

也可以使用：

- `reset_db.bat`

## 使用说明摘要

### LJ

1. 新建项目
2. 新建批次并确认建靶所需次数
3. 录入检测结果
4. 建靶完成后自动进入正式质控
5. 查看图表、规则汇总和最新结果分析
6. 如需修正历史数据，使用记录维护入口

### Z-score

1. 新建项目，并确定双水平或三水平
2. 新建批次并填写各水平说明
3. 录入多水平检测记录
4. 全部水平达到建靶条件后进入正式质控
5. 查看单水平图、合并图和最新结果分析
6. 如需修正正式期记录，使用记录维护入口，系统会自动整批次重算

## 测试说明

当前项目至少包含以下检查方式：

- 语法检查：

```powershell
python -m py_compile app.py database.py zscore_logic.py zscore_plotting.py zscore_smoke_test.py qc_logic.py plotting.py
```

- Z-score smoke tests：

```powershell
python zscore_smoke_test.py
```

`zscore_smoke_test.py` 当前覆盖的重点包括：

- 双水平 / 三水平规则组合
- 建靶期与正式质控期切换
- 检测记录维护后的整批次重算
- 图表基础输出与图例
- 厂家参考值与正式期实时统计

## 版本状态

- 当前版本已经具备 LJ 与 Z-score 主流程，可用于内部试用、演示和小范围部署
- Z-score 主干流程已经落地，包括多水平录入、建靶、正式质控、图表与维护
- 仍有部分增强功能处于后续计划中，例如批量导入、COA 解析、peer-group 和高级靶值流程

## 相关脚本

- `build_exe.bat`：打包入口
- `LJQCApp.spec`：PyInstaller 配置
- `run_app.py` / `run_app.bat`：应用启动脚本
- `reset_db.py` / `reset_db.bat`：数据库重置脚本
