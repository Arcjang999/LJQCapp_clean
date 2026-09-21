# LJQC 开发交接

更新：2026-09-21，供下一 session 接手。本文是当前状态入口；之前的过程记录已完整保存在 [历史交接归档](DEVELOPMENT_HANDOFF_HISTORY_2026-09-21.md)，其中旧“下一步”不再作为执行顺序。

## 1. 当前版本与现场

| 项目 | 接手事实 |
|---|---|
| 本地仓库 | `/Users/gaohongchong/Documents/Codex/LJQCapp` |
| 工作分支 | `codex/project-workflow-alignment` |
| 远端 | `https://github.com/Arcjang999/LJQCapp_clean.git`，同名分支；未合并到 main |
| 已推送功能基线 | `7ac92c65d7c775ced9209fb6b84a82bbd2932a7f`（`7ac92c6`），含前一个检查点 `f263cff` |
| 本次交接整理 | 仅调整文档，作为上述功能基线之后的文档提交；最新提交和同步情况以 Git 为准 |
| 工作区 | 本次整理开始时干净；新 session 再检查并保留后续新增修改 |
| Python | `/Users/gaohongchong/Documents/Codex/.venvs/ljqcapp/bin/python` |
| 演示预览 | 本次检查 8504 正在监听；已保存的独立入口为 `output/ui-workflow-2026-09-20/preview/app_preview.py`，明确使用同目录 `preview.db` |
| 真实用户库 | `data/qc_lj_app.db`；不用于测试、不运行测试种子或迁移 |

本次仅记录用户库文件哈希，未打开应用或数据库执行写入。当前哈希见本地 `output/session-handoff-2026-09-21/baseline.json`。用户库可能随用户实际操作变化，不能拿早期哈希或记录数当作现在的内容，也不能据旧交接断言当前迁移状态。

## 2. 已完成与尚未完成

| 范围 | 当前状态及边界 |
|---|---|
| 项目入口和混合配置 | 已完成：首页项目→检验项目→批次→业务工作台。项目默认带入，逐个检验项目可调整质控方法、方法学及输入值类型 |
| 管理弹窗 | 项目、材料、批次、基础资料、质控品换批、参数、试剂换批和历史事件更正均已接通；明确保存、取消恢复、返回定位已验证 |
| 项目停用 | 已放在编辑项目内，须两次确认 |
| 浓度及批号 | 浓度水平和浓度编号分开；可为不同水平登记不同实际批号，首次配置和部分水平换批保留实际材料对应关系 |
| **试剂换批、历史事件更正** | **已完成，包含在 `7ac92c6`。下一 session 不重复做这两个弹窗**。更正追加事件、保留原事件；仅调整试剂默认使用安排，不改写既往检测实际批号 |
| 质量目标首版 | 已有 94 条 WS/T 403/406 要求、来源核对、批次采用记录和 CV 比较；目前候选主要按名称/别名，仍可登记不适用原因 |
| 分子、免疫标准扩展及严格必选 | **尚未实现**，已写 [优先任务书](task_00_standards_expansion.md) 并核查部分来源；不能把任务书或首页说明当作功能完成 |
| 首页和仓库使用说明 | 已更新，见 [新版使用说明](current_user_guide.md) |
| 整条业务验收 | 各步专项测试及浏览器验证已通过；仍需综合验证整条业务链和来回切换 |
| 高缩放及安装包 | `2880×1800 @ 200%` 和本次改造后的打包运行尚未验收；普通浏览器视口检查不能替代 |
| 原任务 1→2→3 | 主体均未实施。分别为失控处理/独立报告、今日总览/多项目录入、换批比对/批量月报 |

**历史事件更正 ≠ 历史检测实际批号更正；验证登记 ≠ 新旧批号配对比对计算和报告。** 后两项不能因弹窗已完成而标记为完成。

## 3. 下一 session 从哪里开始

按 `AGENTS.md` 的顺序读完交接资料，再读 [术语与流程口径](terminology_and_workflow_alignment.md)、[界面用语对照](ui_wording_manual_alignment.md) 和 [下一阶段任务书](NEXT_SESSION_TASKS.md)。执行顺序固定为：

1. **任务 0：标准扩展和适用标准必选。** 第一小步是核对 403 已有免疫覆盖、230/494 官方全文和现行状态，形成逐检验项目的来源及适用条件清单，再做数据结构、统一服务校验和弹窗。详细交付与样例见 [任务 0](task_00_standards_expansion.md)。不要直接批量录入未核对的数值。
2. 完成全流程、高缩放和打包验收，修复证据明确的问题。范围见 [下一任务的验收清单](NEXT_SESSION_TASKS.md#3-整体验收清单)。
3. 依次继续 [任务 1](task_01_out_of_control_reports.md) → [任务 2](task_02_daily_workbench.md) → [任务 3](task_03_comparisons_and_monthly_reports.md)。

目录没收录不等于国家没有标准；有现行适用标准必须采用，无对应数值指标也可能存在应关联的过程标准。按方法、标本、结果尺度、单位、浓度和用途判断，不能仅按“分子/免疫”分组判断。WS/T 按卫生行业标准准确称呼，软件要求采用不等于法律强制属性；任务书中的来源信息按记录日期使用，开发时重新核查时效。

## 4. 已确认的产品规则

- 核心统计方案不改。保护 `qc_logic.py`、`zscore_logic.py`、`services/instant_service.py`、`services/outlier_service.py`、`plotting.py`、`zscore_plotting.py`；有新统计需求时另行明确，不夹在 UI 或标准目录改动里。
- “单水平（LJ）/多水平法”是当前界面名称；即时法保留在单水平内，3 个有效点开始检验、20 个有效建靶点后人工确认转 LJ。工作分组、检测方法学与质控方法相互独立，同一项目可混合。
- 单一检验项目配置只有一种输入值类型；同一业务项目可含不同类型的检验项目。旧文档“项目级单值类型”不能误读成整个项目容器只能使用一种类型。
- 新增/编辑采用弹窗，明确保存才写入。Enter 不等于保存；取消可恢复草稿；返回保留筛选与选中项。停用保留历史，项目停用必须两次确认。
- 旧检测、原判读、实际批号、参数和质量来源版本保留；更换试剂不重置原参数或连续规则，质控品新批不复制旧检测结果。
- 新字段及来源规则如需迁移，先在独立库及用户库副本验证，检查重复初始化、旧数据和备份恢复。不得因读交接资料就在真实库上启动新代码做迁移。
- 界面用实验室常用词，参考 Q-expert 与 Unity 手册及现行用语表；不得自创机器式名称。Unity 不是已核实的 QCBOX 对应版本手册。

## 5. 代码和实施说明入口

| 业务 | 优先阅读 |
|---|---|
| 项目入口、默认配置、材料关系 | [首版实施说明](ui_workflow_trial_implementation.md)、`ui/project_navigation.py`、`ui/project_dialogs.py`、`services/material_workflow_service.py` |
| 批次与质量目标弹窗 | [批次实施说明](batch_dialog_workflow_implementation.md)、`ui/batch_workspace.py`、`ui/batch_dialogs.py`、`services/batch_edit_service.py` |
| 基础资料 | [基础资料实施说明](master_data_dialog_workflow_implementation.md)、`ui/master_data_workspace.py`、`ui/master_data_dialogs.py`、`services/master_data_edit_service.py` |
| 质控品换批、参数版本 | [换批及参数实施说明](lot_lifecycle_dialog_workflow_implementation.md)、`ui/qc_replacement_workspace.py`、`ui/qc_lifecycle_workspace.py`、`ui/target_profile_workspace.py` |
| 试剂换批、历史事件更正 | [试剂及历史实施说明](reagent_dialog_workflow_implementation.md)、`ui/reagent_lifecycle_workspace.py`、`ui/reagent_history_workspace.py`、`services/reagent_lifecycle_edit_service.py`、`pages/lot_lifecycle_section.py` |
| 现有质量来源校验 | [质量目标实施说明](quality_targets_implementation.md)、`services/quality_review_service.py`（`standard_candidates` / `build_review` / `validate_quality_review`）、`services/quality_target_service.py`、`resources/quality_targets_2024.json` |

复用 `services/lot_lifecycle_service.py`、项目配置及各方法现有服务，不在页面另造计算或保存规则。官方词条例行更新时间不应误报保存冲突，但真实业务字段变化必须拦截旧窗口；`migrations/v1_1_master_data.py` 已修复已有官方词条的本地备注被刷新覆盖的问题。

## 6. 已有验证证据及适用范围

- 试剂及历史阶段：23 套相关隔离测试通过；真实浏览器 1280×720 完成登记、验证、切换、追加更正、Enter/取消恢复及跨页定位。证据：`output/ui-reagent-dialogs-2026-09-20/final-summary.json`。
- 首页说明发布阶段：`user_interface`、`terminology_workflow`、`project_workspace`、`quality_review`、`quality_targets` 五套通过；首页指南浏览器核查、编译和差异检查通过。证据：`output/home-guide-release-2026-09-21/verification.json`。
- 各步结果覆盖该步实现，不等于尚未开展的标准扩展、整体验收、高缩放或新安装包通过。历史证据里的 HEAD/未提交标记仅对应记录时点。
- `output/`、真实数据库、备份和第三方手册 PDF 仅本地保存，不随 Git 推送。换电脑需要另行复制；找不到日志时按相关测试复验，不能伪造通过结果。

按实际改动运行相关测试，不重复无关测试。下一步至少关注质量来源、项目/批次确认、导入/复制/完整及部分换批、三方法接入和旧数据保护；新增迁移或报告变化再补对应回归和 PDF 目视检查。命令示例：

```bash
MPLCONFIGDIR=output/zscore-v12-2026-09-11/mpl-cache /Users/gaohongchong/Documents/Codex/.venvs/ljqcapp/bin/python tests/quality_review_smoke_test.py
```

## 7. 安全继续预览

先检查当前分支、未提交改动、监听端口和入口绑定的数据库。本机 8504 本次仍在监听，其他 session 必须重新确认；不要仅依据端口号推断数据库。用户可能在其他页面有未保存内容，不能随意重启已有服务。

若独立预览未运行，确认包装入口仍将 `database.DB_PATH` 指向演示库，且 `database.LEGACY_DB_CANDIDATES=[]`，再启动：

```bash
cd /Users/gaohongchong/Documents/Codex/LJQCapp
MPLCONFIGDIR=output/zscore-v12-2026-09-11/mpl-cache /Users/gaohongchong/Documents/Codex/.venvs/ljqcapp/bin/python -m streamlit run output/ui-workflow-2026-09-20/preview/app_preview.py --server.port 8504 --server.address 127.0.0.1 --server.headless true --browser.gatherUsageStats false
```

包装入口与演示库是本地验收产物；如果缺失，先建立独立测试入口和测试库。不要直接改为运行 `app.py` 来“恢复预览”，该入口会初始化当前配置数据库。打包基线见 `packaging/LJQCApp.spec` 和 `run_app.py`；源码启动成功不代表 Windows 包或 macOS 包已验收。

## 8. 可直接交给新 session 的说明

> 继续开发 `/Users/gaohongchong/Documents/Codex/LJQCapp`。按 AGENTS.md 顺序阅读交接，再读 docs/terminology_and_workflow_alignment.md、docs/ui_wording_manual_alignment.md 和 docs/task_00_standards_expansion.md。分支为 codex/project-workflow-alignment，已推送功能基线为 7ac92c6，其后的文档提交以 Git 为准。项目/批次/基础资料/换批/参数弹窗已完成，尤其不要重复做试剂换批和历史事件更正。先做任务 0 的官方来源与适用条件核对，再实施标准扩展及必选校验；之后整体验收，再接任务 1→2→3。保留当前工作区，核心计算不改，只使用独立库或副本测试，不写真实用户库。沿用已确认的实验室用语和弹窗交互。
