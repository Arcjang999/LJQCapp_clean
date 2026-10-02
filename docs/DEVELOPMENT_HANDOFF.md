# LJQC 开发交接

> 2026-10-02用户已明确授权推送当前源码版本并核对九月二十九日演示反馈，取代下文旧轮次的“不提交推送”安排。本次检查点包含B1—B3及后续资料管理/界面/录入修正；35组相关检查及B1/B2/B3完整业务链通过。差距与验证边界见[本轮核验](execution/deliveries/DEMO_GAP_REVIEW_2026-10-02.md)。自由时段报告、试剂与质控品自动联动、人员名称调整、权限及完整历史维护仍存在差距；未实施B4/B5，未生成Windows安装包。实际工作区状态以Git为准，下文“未提交”仅保留当时事实。

> 当前入口已统一为 `启动质控软件.command` → `app.py` → `data/qc_lj_app.db`（若用户在设置迁移，沿用保存位置）。本轮按用户要求清除原工程测试资料，直接在正式库录入20个真实目标、22个项目和24项方法配置；不得恢复下文旧双环境、demo入口或固定样例日期。界面、数据名称和用户说明均使用普通业务语言，无教学／演示／模拟标签。操作说明为 [操作说明](OPERATION_GUIDE.md)，具体最终计数及检查见[本轮记录](execution/deliveries/TEACHING_DEMO_2026-09-28.md)。B4/B5仍未实现，正式历史值禁改不等于完整历史修订重算。

> 对照与质控要求已改为自动带入参考、允许按本地修改或清空；数值、定性和信号精密度的既有适用约束保持。项目／批次、日常入口、导出和报告已同步，相关服务与页面、实际浏览器验收通过，见[本轮交付](execution/deliveries/QUALITY_PROCESS_LOCAL_2026-09-28.md)。下文历史“所有过程条款必须关联”由本条覆盖。

> 试剂列表已补齐新增／编辑／停用恢复，单选不跳页，另点“管理批号”进入；13项相关检查通过，见[本轮交付](execution/deliveries/REAGENT_LIST_ACTIONS_2026-09-28.md)。

> 最新质控品页面改为名录主页面单选，新增／编辑／停用在主页面，关联项目和批次分别打开管理弹窗；两个入口共用，29项隔离检查通过。见[本轮交付](execution/deliveries/QC_CATALOGUE_DIALOGS_2026-09-28.md)。旧双页签描述不再适用。

> 最新资料管理纠偏已验收：原讨论要求的独立质控品入口、基础资料共用名录及厂家/仪器/试剂管理已补齐，启动时准备内置目录。此前B2只在测试库验证发布，不能据此称当时演示已导入；当前演示库已具备1054/252。20项自动检查、实际浏览器和演示数据保留核对通过，原2012行业务内容及9份PDF、3附件保持一致，见[纠偏交付记录](execution/deliveries/MATERIAL_MANAGEMENT_2026-09-28.md)。未进入B4/B5，不重新打包。

> 最新界面修正：默认首页为项目工作台，整组录入归属所选项目，移除旧首页方法选择；分模块配色和独立使用说明已验收。见[UI交付](execution/deliveries/UI_2026-09-28.md)，后续仍为B4/T2B-01。

> 同日已按用户要求清理旧包二进制、分发ZIP和构建缓存，轻量验收证据保留；详见[清理记录](CLEANUP_2026-09-28.md)。当前只使用直接本机演示入口。


> 2026-09-28交付：B1、B2及B3常规范围已验收。用户最新只需要明天本机演示，不再压缩/打包；实际入口为根目录 `启动演示.command`，使用独立 `output/demo-2026-09-29/acceptance.db` 及附件。详见[演示说明](DEMO_2026-09-29.md)。已有候选包仅作阶段证据。本轮停止于B3；下一轮B4首卡T2B-01，Windows/实机200%仍待R0-02。

**2026-09-28 全项目数据约定（优先于本文件及旧任务卡）：** 用户确认现有旧数据全部由此前测试建立。后续整个项目不以旧数据保留、兼容或迁移为约束，不为旧库、旧历史格式或旧版备份另加兼容分支；基于新建隔离数据验收即可。旧文档中的用户旧库副本验证、旧数据保护基线及旧版备份兼容，从本日起不再是验收必需项；已完成的历史验证记录保留为事实，不要求继续重复。这不要求现在主动删除任何数据库或文件。新功能投入使用后产生的原始记录、修订、判读、报告、附件仍保留追溯，完整备份恢复、保存位置迁移、原子保存和失败回滚仍须验收。永久入口见[README](../README.md)及[AGENTS.md](../AGENTS.md)。

**2026-09-28 最新接手方式：[5轮合并执行总表](execution/BATCHES.md)。** 用户要求减少执行次数，剩余79张细卡归为B1—B5；建议前三轮先交付常规业务可用版本。指定一轮后连续执行其全部细卡，不再逐卡停止；明确单卡请求仍按单卡执行。原卡保留完整需求和验收，功能状态见[进度表](execution/STATUS.md)。R0-01本机业务链、T1-01合同与样例已验收；B1—B3本轮结果以文首及STATUS为准。本次合并安排已按原讨论及后续任务书[复核并纠偏](execution/REQUIREMENTS_RECHECK_2026-09-28.md)，合并安排本身不作为功能完成证据。R0-02目标环境另验。

更新：2026-09-22，供下一 session 接手。任务 0 按用户反馈重新核查并修正，当前以 [本轮复核记录](task_00_reaudit_2026_09_22.md) 为准；此前 [实施记录](task_00_implementation.md) 保留轮次证据。以下2026-09-22段落保留为当时基线，最新执行状态以文首为准；旧记录见 [历史交接归档](DEVELOPMENT_HANDOFF_HISTORY_2026-09-21.md)，旧“下一步”不再作为执行顺序。

同日需求合并及任务书拆分：以 [任务书总览](TASK_BOOKS.md) 查看拆分、依赖和验收归属，[需求合并稿](requirements_merged_2026_09_21.md) 记录统一口径，[面聊确认记录](2026_09_21_iqc_meeting_requirements.md) 保留来源细节。任务 0—3 保留，基础资料/厂家/panel/邦德盛目录拆为 2A，临时质控拆为 2B。主框架后的任务 4 提供账号权限，任务 5 实现更正、补录、记录排除及联动重算；不能将更正/补录与计算更新拆成两次不完整交付。

用户已明确授权任务 0 开发。开发前文档检查点 `54eaa44` 已提交并推送；其后任务 0 源码、文档和执行卡已在 R0-01 执行前提交并推送为 `430d2a0`。当前工作区保留 R0-01 增量、T1-01 合同与纯内存样例，以及2026-09-28合并执行安排与进度入口更新。测试仅使用独立库和用户库副本；原库及六个核心保护文件哈希与本轮开工前一致。首轮证据在 `output/task00-2026-09-22/`，补完证据在 `output/task00-completion-2026-09-22/`。用户最新明确：旧数据不作适配约束，旧进程可以关闭；质量要求只显示现行依据，界面不得出现编程语言标签、代码、开发注释或内部实现提示。本轮证据在 `output/task00-reaudit-2026-09-22/`。

实验室入口更正：只读源码已确认“系统设置 → 报告默认信息”维护实验室名称等默认资料，当前存于 `app_settings`，仪器尚无独立实验室标识关联。复用既有入口，不重复新增实验室信息页；将来由管理员维护通用实验室资料并关联账号/仪器是讨论建议，不能当作已确认多实验室或多租户需求。详见合并稿第 4.2 节。

## 1. 当前版本与现场

| 项目 | 接手事实 |
|---|---|
| 本地仓库 | `/Users/gaohongchong/Documents/Codex/LJQCapp` |
| 工作分支 | `codex/project-workflow-alignment` |
| 远端 | `https://github.com/Arcjang999/LJQCapp_clean.git`，同名分支；未合并到 main |
| 已推送功能基线 | `7ac92c65d7c775ced9209fb6b84a82bbd2932a7f`（`7ac92c6`），含前一个检查点 `f263cff` |
| 开发前检查点 | `430d2a099be99c417e958fe440e7a9cd6f127421` 已推送，包含任务0与执行卡；R0-01增量未另行提交 |
| 工作区 | 保留R0-01/T1-01以及B1—B3全部源码、测试与交付记录；未提交，不要reset/clean |
| Python | `/Users/gaohongchong/Documents/Codex/.venvs/ljqcapp/bin/python` |
| 演示预览 | R0-01 自建8507/8508隔离预览已关闭；既有8505未用于本卡，接手重新核对实际进程及绑定 |
| 既有测试库 | `data/qc_lj_app.db`；用户已确认旧数据均为测试数据，保留/兼容/旧格式迁移不作为交付约束，后续验收使用新建隔离库 |

任务0当时未在既有库运行测试；当时基线及检查见 `output/task00-2026-09-22/baseline.json` 和 `final-integrity.json`，副本重复初始化曾验证旧检测及来源快照一致。这些是历史证据；按2026-09-28用户澄清，不再将该旧测试库称为真实用户业务库，不要求后续重新记录旧库保护基线或继续验证旧格式兼容。

## 2. 已完成与尚未完成

2026-09-28增量：B1失控处理、B2基础资料/panel/今日总览/整组录入、B3常规批量月报已有实现与独立验收记录；最终候选交付已通过，证据见文首和STATUS。下表保留旧功能基线，不覆盖该增量。稳定接口分别见 `execution/notes/task1_task4_contracts.md`、`task2_contracts.md`、`task3c_contracts.md`。

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
| 业务回归 | 此前29组为旧轮次证据；本轮最终23组回归、真实项目及HCV/HIV专项条件通过，见复核记录与 final-verification.json。R0-01已补本机连贯业务链，详见本卡交付；发布环境仍按R0-02验收 |
| 高缩放及安装包 | 本轮 macOS arm64 包重新构建，启动、页面加载及独立空库初始化通过，13个关键源码/资源与包一致；720×900视口换行通过。Windows安装环境及 `2880×1800 @ 200%` 未实测 |
| 原任务 1→2→3 | 主体均未实施。分别为失控处理/独立报告、今日总览/多项目录入、换批比对/批量月报 |

**历史事件更正 ≠ 历史检测实际批号更正；验证登记 ≠ 新旧批号配对比对计算和报告。** 后两项不能因弹窗已完成而标记为完成。

## 3. 下一 session 从哪里开始

接手先读[逐卡总表](execution/README.md)、[规则](execution/EXECUTION_RULES.md)、[进度](execution/STATUS.md)及本次指定卡。以下是背景和阶段边界，不是单次完成所有任务的指令：

1. **保留任务 0 本轮修订。** 只展示现行依据，分开检验项目与通用要求；使用真实项目身份映射和标本核对，不能恢复“101 条”混合目录。核查记录列明 403/406/230/494/641、中国CDC丙肝技术规范2023和艾滋病质控指南2024这7份现行来源的已核条款；HIV-1 RNA定量必须使用Log10输入，其他项目和专项仍按真实范围继续核对；关联条款不代表完整符合或新增自动评价。
2. R0-01已补齐本机业务链，发布前继续按R0-02补指定系统缩放及 Windows 安装环境验收，修复证据明确的问题。范围见 [下一任务的验收清单](NEXT_SESSION_TASKS.md#3-整体验收清单)。
3. 依次继续 [任务 1](task_01_out_of_control_reports.md) → [任务 2](task_02_daily_workbench.md) → [任务 3](task_03_comparisons_and_monthly_reports.md)。

新增基础资料关系是任务 2 完整流程的依赖，已拆为任务 2A；独立临时质控为 2B。建议任务 2 阶段按 2A→2→2B，主框架后按任务 4→5，此处是工程依赖顺序，不是日历排期承诺。原整体验收针对当时已有功能，后续增量另验收，不把尚未开发的权限作为任务 0 验收条件。

目录没收录不等于国家没有标准；有现行适用标准必须采用，无对应数值指标也可能存在应关联的过程标准。按方法、标本、结果尺度、单位、浓度和用途判断，不能仅按“分子/免疫”分组判断。WS/T 按卫生行业标准准确称呼，软件要求采用不等于法律强制属性；任务书中的来源信息按记录日期使用，开发时重新核查时效。

## 4. 已确认的产品规则

- 核心统计方案不改。保护 `qc_logic.py`、`zscore_logic.py`、`services/instant_service.py`、`services/outlier_service.py`、`plotting.py`、`zscore_plotting.py`；有新统计需求时另行明确，不夹在 UI 或标准目录改动里。
- 更正、补录后的计算联动已经明确为后续必做：按有效输入复用现有算法重算受影响范围并更新有效判读、图表、汇总及新报告；原始数据与历史版本保留。具体接入方案另行落实，不能把“核心算法不改”误解为不执行必要重算。
- “单水平（LJ）/多水平法”是当前界面名称；即时法保留在单水平内，3 个有效点开始检验、20 个有效建靶点后人工确认转 LJ。工作分组、检测方法学与质控方法相互独立，同一项目可混合。
- 单一检验项目配置只有一种输入值类型；同一业务项目可含不同类型的检验项目。旧文档“项目级单值类型”不能误读成整个项目容器只能使用一种类型。
- 新增/编辑采用弹窗，明确保存才写入。Enter 不等于保存；取消可恢复草稿；返回保留筛选与选中项。停用保留历史，项目停用必须两次确认。
- 新功能产生的原检测、原判读、实际批号、参数和质量来源版本保留；更换试剂不重置原参数或连续规则，质控品新批不复制原检测结果。该业务追溯要求不等于必须保留开发前的旧测试数据。
- 新字段及来源规则在新建隔离库验证初始化、重复初始化、新功能数据一致性及完整备份恢复；旧库副本验证、旧历史格式迁移及旧版备份兼容不再是必需项。不因本次约定主动删除现存文件。
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

按实际改动运行相关测试，不重复无关测试。下一步至少关注质量来源、项目/批次确认、导入/复制/完整及部分换批、三方法接入，以及新功能数据的原始记录和版本一致性；新增结构或报告变化再补对应回归和 PDF 目视检查，不再追加旧测试数据保护或兼容检查。命令示例：

```bash
MPLCONFIGDIR=output/zscore-v12-2026-09-11/mpl-cache /Users/gaohongchong/Documents/Codex/.venvs/ljqcapp/bin/python tests/quality_review_smoke_test.py
```

## 7. 安全继续预览

先检查当前分支、未提交改动、监听端口和入口绑定的数据库。用户已明确允许关闭旧进程，旧打包验证进程已关闭。任务0历史8505入口为 `output/task00-reaudit-2026-09-22/preview/app_preview.py`；R0-01没有复用它。R0-01临时8507/8508已关闭，不要仅依据沿用的端口号推断版本或数据库。

若独立预览未运行，确认包装入口仍将 `database.DB_PATH` 指向演示库，且 `database.LEGACY_DB_CANDIDATES=[]`，再启动：

```bash
cd /Users/gaohongchong/Documents/Codex/LJQCapp
MPLCONFIGDIR=output/task00-reaudit-2026-09-22/mplconfig /Users/gaohongchong/Documents/Codex/.venvs/ljqcapp/bin/python -m streamlit run output/task00-reaudit-2026-09-22/preview/app_preview.py --server.port 8505 --server.address 127.0.0.1 --server.headless true --browser.gatherUsageStats false
```

包装入口与演示库是本地验收产物；如果缺失，先建立独立测试入口和测试库。不要直接改为运行 `app.py` 来“恢复预览”，该入口会初始化当前配置数据库。打包基线见 `packaging/LJQCApp.spec` 和 `run_app.py`；源码启动成功不代表 Windows 包或 macOS 包已验收。

## 8. 可直接交给新 session 的说明

> 继续 `/Users/gaohongchong/Documents/Codex/LJQCapp`。按 `docs/execution/BATCHES.md`，本次只执行B4；先从T2B-01确认临时判读和月报口径，再按真实依赖完成本轮。先读AGENTS、执行规则、STATUS和B1—B3稳定合同，保留工作区，不重做已验收功能。旧数据均为测试数据，不做兼容或迁移适配；新功能追溯、备份与失败回滚继续验收。未决算法/计数规则只阻塞相应分支，不将建议当用户确认。轮末验收更新进度后停止，不自动进入B5。目标Windows及实机缩放单列R0-02。

B1—B3为本轮常规大版本的已执行范围，后续不要默认重复领取。要仅执行细卡时明确卡号；默认不再把每张细卡当一次单独任务。
