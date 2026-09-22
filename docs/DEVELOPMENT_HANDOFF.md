# LJQC 开发交接

**2026-09-22 最新接手方式：[后续任务执行总表](execution/README.md)。** 用户要求极致细化后逐项开发，本轮交付独立执行卡、规则、决策清单和进度表，未开始任务 1—5 功能实现。下一次指定一张卡，完成验收后停止；原任务书和本文其余段落仅提供相应范围的背景。当前首卡为 R0-01 本机业务链核验，后续 T1-01；发布环境另列 R0-02，未测不得声称通过。

更新：2026-09-22，供下一 session 接手。任务 0 按用户反馈重新核查并修正，当前以 [本轮复核记录](task_00_reaudit_2026_09_22.md) 为准；此前 [实施记录](task_00_implementation.md) 保留轮次证据。本文是当前状态入口；旧记录见 [历史交接归档](DEVELOPMENT_HANDOFF_HISTORY_2026-09-21.md)，旧“下一步”不再作为执行顺序。

同日需求合并及任务书拆分：以 [任务书总览](TASK_BOOKS.md) 查看拆分、依赖和验收归属，[需求合并稿](requirements_merged_2026_09_21.md) 记录统一口径，[面聊确认记录](2026_09_21_iqc_meeting_requirements.md) 保留来源细节。任务 0—3 保留，基础资料/厂家/panel/邦德盛目录拆为 2A，临时质控拆为 2B。主框架后的任务 4 提供账号权限，任务 5 实现更正、补录、记录排除及联动重算；不能将更正/补录与计算更新拆成两次不完整交付。

用户已明确授权任务 0 开发。开发前文档检查点 `54eaa44` 已提交并推送；其后任务 0 源码与文档增量保留在当前分支工作区。测试仅使用独立库和用户库副本；原库及六个核心保护文件哈希与本轮开工前一致。首轮证据在 `output/task00-2026-09-22/`，补完证据在 `output/task00-completion-2026-09-22/`。用户最新明确：旧数据不作适配约束，旧进程可以关闭；质量要求只显示现行依据，界面不得出现编程语言标签、代码、开发注释或内部实现提示。本轮证据在 `output/task00-reaudit-2026-09-22/`。

实验室入口更正：只读源码已确认“系统设置 → 报告默认信息”维护实验室名称等默认资料，当前存于 `app_settings`，仪器尚无独立实验室标识关联。复用既有入口，不重复新增实验室信息页；将来由管理员维护通用实验室资料并关联账号/仪器是讨论建议，不能当作已确认多实验室或多租户需求。详见合并稿第 4.2 节。

## 1. 当前版本与现场

| 项目 | 接手事实 |
|---|---|
| 本地仓库 | `/Users/gaohongchong/Documents/Codex/LJQCapp` |
| 工作分支 | `codex/project-workflow-alignment` |
| 远端 | `https://github.com/Arcjang999/LJQCapp_clean.git`，同名分支；未合并到 main |
| 已推送功能基线 | `7ac92c65d7c775ced9209fb6b84a82bbd2932a7f`（`7ac92c6`），含前一个检查点 `f263cff` |
| 开发前检查点 | `54eaa44800504bf02f812d35625538ab05c028ce` 已推送；任务 0 增量未另行提交，最新情况以 Git 为准 |
| 工作区 | 保留任务 0 的源码、测试和文档修改；不要 reset/clean |
| Python | `/Users/gaohongchong/Documents/Codex/.venvs/ljqcapp/bin/python` |
| 演示预览 | 旧打包进程已关闭；8505 当前运行本轮源码隔离入口 `output/task00-reaudit-2026-09-22/preview/app_preview.py`，绑定同目录 `preview.db`；接手重新核对现场 |
| 真实用户库 | `data/qc_lj_app.db`；不用于测试、不运行测试种子或迁移 |

本次真实用户库未用于测试。当前基线及最终检查见 `output/task00-2026-09-22/baseline.json` 和 `final-integrity.json`；副本重复初始化时，业务内容、旧检测及来源快照一致，既有种子逻辑仅刷新时间戳及自增序列。用户库可能随用户实际操作变化，下次应重新记录基线。

## 2. 已完成与尚未完成

| 范围 | 当前状态及边界 |
|---|---|
| 项目入口和混合配置 | 已完成：首页项目→检验项目→批次→业务工作台。项目默认带入，逐个检验项目可调整质控方法、方法学及输入值类型 |
| 管理弹窗 | 项目、材料、批次、基础资料、质控品换批、参数、试剂换批和历史事件更正均已接通；明确保存、取消恢复、返回定位已验证 |
| 项目停用 | 已放在编辑项目内，须两次确认 |
| 浓度及批号 | 浓度水平和浓度编号分开；可为不同水平登记不同实际批号，首次配置和部分水平换批保留实际材料对应关系 |
| **试剂换批、历史事件更正** | **已完成，包含在 `7ac92c6`。下一 session 不重复做这两个弹窗**。更正追加事件、保留原事件；仅调整试剂默认使用安排，不改写既往检测实际批号 |
| 质量目标首版 | 保留 94 条 WS/T 403/406 要求、逐水平采用记录和既有 CV 比较；适用性已改为结构化条件核对，自由文本排除不能绕过适用标准 |
| 分子、免疫标准扩展及严格必选 | 已按反馈改为仅现行的项目目录与通用要求，补具体分子/定性免疫检索映射、真实名称/代码与标本核对；保留适用必选、草稿和多来源追溯。专项指南覆盖边界及证据见 [本轮复核](task_00_reaudit_2026_09_22.md) |
| 首页和仓库使用说明 | 已更新，见 [新版使用说明](current_user_guide.md) |
| 业务回归 | 此前29组为旧轮次证据；本轮最终23组回归、真实项目及HCV/HIV专项条件通过，见复核记录与 final-verification.json。完整人工业务链仍按发布清单验收 |
| 高缩放及安装包 | 本轮 macOS arm64 包重新构建，启动、页面加载及独立空库初始化通过，13个关键源码/资源与包一致；720×900视口换行通过。Windows安装环境及 `2880×1800 @ 200%` 未实测 |
| 原任务 1→2→3 | 主体均未实施。分别为失控处理/独立报告、今日总览/多项目录入、换批比对/批量月报 |

**历史事件更正 ≠ 历史检测实际批号更正；验证登记 ≠ 新旧批号配对比对计算和报告。** 后两项不能因弹窗已完成而标记为完成。

## 3. 下一 session 从哪里开始

接手先读[逐卡总表](execution/README.md)、[规则](execution/EXECUTION_RULES.md)、[进度](execution/STATUS.md)及本次指定卡。以下是背景和阶段边界，不是单次完成所有任务的指令：

1. **保留任务 0 本轮修订。** 只展示现行依据，分开检验项目与通用要求；使用真实项目身份映射和标本核对，不能恢复“101 条”混合目录。核查记录列明 403/406/230/494/641、中国CDC丙肝技术规范2023和艾滋病质控指南2024这7份现行来源的已核条款；HIV-1 RNA定量必须使用Log10输入，其他项目和专项仍按真实范围继续核对；关联条款不代表完整符合或新增自动评价。
2. 发布前补全人工业务链、指定系统缩放及 Windows 安装环境验收，修复证据明确的问题。范围见 [下一任务的验收清单](NEXT_SESSION_TASKS.md#3-整体验收清单)。
3. 依次继续 [任务 1](task_01_out_of_control_reports.md) → [任务 2](task_02_daily_workbench.md) → [任务 3](task_03_comparisons_and_monthly_reports.md)。

新增基础资料关系是任务 2 完整流程的依赖，已拆为任务 2A；独立临时质控为 2B。建议任务 2 阶段按 2A→2→2B，主框架后按任务 4→5，此处是工程依赖顺序，不是日历排期承诺。原整体验收针对当时已有功能，后续增量另验收，不把尚未开发的权限作为任务 0 验收条件。

目录没收录不等于国家没有标准；有现行适用标准必须采用，无对应数值指标也可能存在应关联的过程标准。按方法、标本、结果尺度、单位、浓度和用途判断，不能仅按“分子/免疫”分组判断。WS/T 按卫生行业标准准确称呼，软件要求采用不等于法律强制属性；任务书中的来源信息按记录日期使用，开发时重新核查时效。

## 4. 已确认的产品规则

- 核心统计方案不改。保护 `qc_logic.py`、`zscore_logic.py`、`services/instant_service.py`、`services/outlier_service.py`、`plotting.py`、`zscore_plotting.py`；有新统计需求时另行明确，不夹在 UI 或标准目录改动里。
- 更正、补录后的计算联动已经明确为后续必做：按有效输入复用现有算法重算受影响范围并更新有效判读、图表、汇总及新报告；原始数据与历史版本保留。具体接入方案另行落实，不能把“核心算法不改”误解为不执行必要重算。
- “单水平（LJ）/多水平法”是当前界面名称；即时法保留在单水平内，3 个有效点开始检验、20 个有效建靶点后人工确认转 LJ。工作分组、检测方法学与质控方法相互独立，同一项目可混合。
- 单一检验项目配置只有一种输入值类型；同一业务项目可含不同类型的检验项目。旧文档“项目级单值类型”不能误读成整个项目容器只能使用一种类型。
- 新增/编辑采用弹窗，明确保存才写入。Enter 不等于保存；取消可恢复草稿；返回保留筛选与选中项。停用保留历史，项目停用必须两次确认。
- 旧检测、原判读、实际批号、参数和质量来源版本保留；更换试剂不重置原参数或连续规则，质控品新批不复制旧检测结果。
- 新字段及来源规则如需迁移，先在独立库及用户库副本验证，检查重复初始化、旧数据和备份恢复。不得因读交接资料就在真实库上启动新代码做迁移。
- 界面用实验室常用词，参考 Q-expert 与 Unity 手册及现行用语表；不得自创机器式名称。编程语言标签、代码、开发注释、错误堆栈和内部实现提示永不进入用户界面；允许 PDF/CSV 等文件类型及实际业务条件。Unity 不是已核实的 QCBOX 对应版本手册。

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
- 任务 0：29 套相关隔离测试、实际浏览器操作、LJ/多水平 PDF 目视及长中文边界、用户库副本重复初始化通过。详见 `output/task00-2026-09-22/final-tests.json`、`final-integrity.json` 与实施记录；没有新增数据库迁移。
- 本次补完：29 组回归、统一目录/项目搜索的实际页面操作、本机 macOS 服务包启动和目录资源搜索通过，见 `output/task00-completion-2026-09-22/final-tests.json`、`package-build.log`、`package-runtime.log`。
- 本轮复核：真实名称/代码与基质专项 `quality_identity` 通过；10 组既有相关回归及 `quality_real_items` 集成通过，汇总为 `output/task00-reaudit-2026-09-22/subagent-final-tests.json`。九个真实内置项目从选择、采用到项目/批次确认均通过，未重命名官方项目。
- 各步结果覆盖该步实现，不等于完整人工业务链、指定系统缩放或 Windows 安装包通过。旧浏览器和旧包通过不能替代本轮目录修订验收；历史证据里的 HEAD/未提交标记仅对应记录时点。
- `output/`、真实数据库、备份和第三方手册 PDF 仅本地保存，不随 Git 推送。换电脑需要另行复制；找不到日志时按相关测试复验，不能伪造通过结果。

按实际改动运行相关测试，不重复无关测试。下一步至少关注质量来源、项目/批次确认、导入/复制/完整及部分换批、三方法接入和旧数据保护；新增迁移或报告变化再补对应回归和 PDF 目视检查。命令示例：

```bash
MPLCONFIGDIR=output/zscore-v12-2026-09-11/mpl-cache /Users/gaohongchong/Documents/Codex/.venvs/ljqcapp/bin/python tests/quality_review_smoke_test.py
```

## 7. 安全继续预览

先检查当前分支、未提交改动、监听端口和入口绑定的数据库。用户已明确允许关闭旧进程，旧打包验证进程已关闭。本轮8505为最新源码隔离预览，使用 `output/task00-reaudit-2026-09-22/preview/preview.db`；不要仅依据沿用的端口号推断版本或数据库。

若独立预览未运行，确认包装入口仍将 `database.DB_PATH` 指向演示库，且 `database.LEGACY_DB_CANDIDATES=[]`，再启动：

```bash
cd /Users/gaohongchong/Documents/Codex/LJQCapp
MPLCONFIGDIR=output/task00-reaudit-2026-09-22/mplconfig /Users/gaohongchong/Documents/Codex/.venvs/ljqcapp/bin/python -m streamlit run output/task00-reaudit-2026-09-22/preview/app_preview.py --server.port 8505 --server.address 127.0.0.1 --server.headless true --browser.gatherUsageStats false
```

包装入口与演示库是本地验收产物；如果缺失，先建立独立测试入口和测试库。不要直接改为运行 `app.py` 来“恢复预览”，该入口会初始化当前配置数据库。打包基线见 `packaging/LJQCApp.spec` 和 `run_app.py`；源码启动成功不代表 Windows 包或 macOS 包已验收。

## 8. 可直接交给新 session 的说明

> 继续 `/Users/gaohongchong/Documents/Codex/LJQCapp`。本次只执行 `docs/execution/cards/R0-01.md`。先读 AGENTS.md 当前口径、docs/execution/EXECUTION_RULES.md 和 docs/execution/STATUS.md，再读该卡列出的必要文件。保留工作区，复用任务0已有有效证据，只补本机连贯业务链验收；不重做任务0、不自动开发任务1。真实目标缩放和Windows包按R0-02另验，未测不能写通过。界面不得显示开发语言、代码、注释、堆栈和内部实现提示；搜索复用现有模糊匹配，标准适用性仍精确核对。只用独立库，旧进程可以关闭。按卡完成后留下证据、更新进度并停止。

后续将卡号替换为进度表中已满足前置的下一张，例如 T1-01；不要把原任务1整份正文当作一次性开发指令。
