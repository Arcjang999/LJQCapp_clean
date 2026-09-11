# LJQC 下一阶段任务书与 session 交接

更新时间：2026-09-11。用户准备在同一台 Mac 的新 session 继续开发。

## 1. 接手位置与已完成状态

| 项目 | 当前事实 |
|---|---|
| 本地仓库 | `/Users/gaohongchong/Documents/Codex/LJQCapp` |
| 分支 / 基线 | `codex/v1-2-lj-integration`；本轮开发起点 `b9355b6`，最新提交以 `git log -1` 为准 |
| Git 交接 | 本轮功能、测试及文档统一提交到当前分支；接手时检查本地/远端同步状态，并保留之后产生的未提交改动 |
| Python | `/Users/gaohongchong/Documents/Codex/.venvs/ljqcapp/bin/python`，Python 3.13 |
| 本机应用 | `http://127.0.0.1:8501/`；交接时已启动，新 session 应重新检查运行状态 |
| 当前数据库 | 仓库内 `data/qc_lj_app.db`，已完成生命周期迁移，无需重复迁移 |
| 最近备份 | `data/backups/before-lot-lifecycle-20260911-142235.db` |
| 迁移结果 | 3 次 Z-score 检测、6 条水平结果原值保留；3 条来源上下文回填，实际试剂批号仍未知 |
| 最近测试 | 19 组 smoke suites 通过，其中新增 11 项批号验收；日志在 `output/lot-lifecycle-2026-09-11/` |

已完成：项目文件夹内默认数据库及旧库迁移；模板的仪器/试剂/质控品字典选择；LJ、Z-score、即时法新版配置接入；即时法 3 点 SI、20 点人工转入 LJ；三方法实际试剂批号、换批验证与事件、质控品平行/结束、部分水平新组合、LJ/Z-score 参数版本、逐次快照和报告追溯。

最新功能范围与限制以 [批号生命周期实现说明](v1_2_lot_lifecycle_implementation.md) 为准。[整体设计](v1_2_lot_lifecycle_design.md) 保留实施前问题和完整目标，不表示每一目标均已交付。

## 2. 新 session 先做什么

1. 阅读 `AGENTS.md`、`DEVELOPMENT_HANDOFF.md`、V1.1 数据模型规格、最新实现说明及本任务书。
2. 检查分支、未提交改动和最近提交，保留现有工作区；不要通过 reset、clean 或重新克隆来“恢复基线”。其他电脑拉取该分支并核对提交；数据库、备份和验收产物需另行迁移，不包含在 Git 中。
3. 检查 8501 是否在运行。已运行则打开现有应用；未运行才用下方命令启动。
4. 阅读迁移审计及最近回归日志。测试用独立临时数据库；不要把测试种子写入当前用户库，也不要清空现有测试记录。
5. 先完成第 3 节的换批工作流核查，发现可复现问题就修复并做相关回归。若用户在新 session 指定不同下一步，以新指令为准。

```bash
cd /Users/gaohongchong/Documents/Codex/LJQCapp
git status --short --branch
git log -1 --oneline
lsof -nP -iTCP:8501 -sTCP:LISTEN
```

仅在应用未运行时启动：

```bash
/Users/gaohongchong/Documents/Codex/.venvs/ljqcapp/bin/python -m streamlit run app.py --server.port 8501 --server.address 127.0.0.1
```

## 3. 下一阶段：换批流程试用与边界核查

本阶段目的是检查现有实现能否顺畅支持用户的实际操作，修复证据明确的问题。以下是待核查项，不是已确认的缺陷列表。

| 优先级 | 核查任务 | 验收结果 |
|---|---|---|
| P0 | 按 LJ、2/3 水平 Z-score、即时法各走一遍试剂换批，再查看原记录、图和导出 | 原项目不重复创建；实际批号保存正确；适用参数和连续规则不因试剂换批被清空 |
| P0 | 同一项目的质控品新旧批平行、正式启用、结束旧批，包括未来生效计划 | 新序列无旧检测结果；状态按生效时间应用；旧批可追溯，结束后写入被阻止 |
| P0 | 复核换批验证的适用范围：不同检测项、后续失败结论、验证日期和效期、单个水平更换 | 不得拿其他项目/批号的验证通过记录启用目标批；检查旧的通过结论能否错误绕过较新的失败结论 |
| P0 | 复核历史补录、参数版本切换、旧表单与导入预览后变更默认批号 | 根据检测时间使用适用批号/版本；陈旧确认被提示；失败事务不留下半条结果 |
| P1 | 走完部分水平换批、未换水平参数预填、全部水平确认的界面链路 | 用户能辨认实际质控批号和参数来源，不把参考值误当作已确认的正式参数 |
| P1 | 当前库历史数据、上游停用和结束批次的查询入口 | 无实际批号的旧数据明确显示未知；原值保留；用户能找到可用的历史查询及报告入口 |
| P1 | 指定高缩放环境与常规显示环境对比 | 重点复测 `2880×1800 @ 200%` 的表单、表格和图例，明确记录是否具备该环境 |

隔离浏览器此前已验证：Z-score 连续保存 3 次后保持合并视图；三条实际试剂批号 R001/R002/R002；检测人输入后按 Enter 保留数值；指定即时法检测系统换批成功。新 session 应聚焦上述未充分走通的边界，避免无目的重跑全部流程。

## 4. 暂不自行展开的功能

以下属于可讨论的后续增强，并非本次交接自动批准的开发范围：单条历史检测实际批号的专用更正界面、验证附件上传、患者样本比对计算、质量目标库、结构化失控闭环、即时法月报、LIS、权限与电子签名、安装包发布。

如核查发现需要这些能力才能解决用户明确问题，先写清具体场景、现有行为和拟议改动，再由用户决定优先级。事件更正与逐条结果更正要区分；不能通过修改默认批号回写旧结果。

## 5. 代码定位

| 范围 | 主要文件 |
|---|---|
| 迁移和逐次数据写入 | `migrations/v1_2_lot_lifecycle.py`、`scripts/migrate_lot_lifecycle.py`、`database.py` |
| 批号、验证、状态、参数版本和追溯 | `services/lot_lifecycle_service.py` |
| 管理与录入界面 | `pages/lot_lifecycle_section.py`、`pages/project_management_page.py`、三方法页面及 sections |
| 配置、水平组合、工作台绑定 | `services/project_config_service.py`、`services/workbench_config_service.py`、`services/zscore_workbench_service.py`、`services/instant_workbench_service.py` |
| 按版本计算与转换 | `qc_logic.py`、`zscore_logic.py`、`services/instant_service.py` |
| 图表与报告 | `plotting.py`、`zscore_plotting.py`、`services/report_service.py`、`services/report_pdf_layout.py` |
| 批号验收 | `tests/lot_lifecycle_smoke_test.py` |

## 6. 验证及交付要求

按改动范围运行相关 smoke tests。当前虚拟环境未安装 pytest，仓库测试可直接运行；示例：

```bash
MPLCONFIGDIR=output/zscore-v12-2026-09-11/mpl-cache /Users/gaohongchong/Documents/Codex/.venvs/ljqcapp/bin/python tests/lot_lifecycle_smoke_test.py
```

三方法规则和维护改动需追加对应核心及接入测试；报告改动需生成、渲染并检查 PDF；界面改动需实际浏览器验证。现有批号测试会在 `output/lot-lifecycle-2026-09-11/` 更新验收 PDF，重要旧验收产物需保留时先复制到新的验收目录。

完成时记录：复现场景、修复行为、原始数据是否受影响、执行的验证及未验证环境。更新本任务书、README 和开发交接说明。数据库及备份、运行日志不进入 Git；仅本地修改不等于已同步到 GitHub，提交/推送按用户后续指令执行。
