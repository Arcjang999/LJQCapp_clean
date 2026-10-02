# 任务 2A / 2 源码事实与接口合同

2026-09-22 建立准备材料；2026-09-28 按 B2 实施补入第 7、8 节真实接口及专项证据。第 1—6 节保留原准备要求，其中“拟新增/当前未实现”按第 7、8 节对应接口的实际交付记录取代。本文只记录合同和已完成的专项检查，不代表 B2 整轮或浏览器验收完成；卡片状态以 [STATUS](../STATUS.md) 为准。

2026-09-28目录输入补充：用户已提供邦德盛源表，目录录入收敛为**产品编号、产品名称、浓度、浓度编号**，见[来源记录](bondson_catalog_source_2026-09-28.md)。产品编号保持唯一；浓度与浓度编号为文本，任一有明确有效内容即可，不要求同时填写。阴性、阳性、分型、基因型和基因位点均按原文有效保留，不数值化或给阴性补造浓度；单项空白或“/”而另一项有效不标缺项，仅两项均无有效内容时提示核对。所有明确多个检测目标的混合产品及血筛排除本次目录并保留审计，包括生化/免疫明确复合多个检测项；同一目标多水平、分型/基因型/位点本身不等于多目标混合，不按斜杠或业务关联数量机械判定。产品与多个检测项的业务关系独立维护，不能从名称/浓度猜测；组合水平是同一质控品包含多个水平，本身不属于异常。本次预处理未实现或验收任何本合同接口。

## 1. 当前已有入口与关系

| 业务对象 | 当前真实位置/入口 | 复用边界 |
|---|---|---|
| 实验室资料 | `services/settings_service.py` 的 `get_report_settings`、`save_report_settings_form`；`app_settings` | 当前数据库的一份报告默认资料；没有可直接消费的独立 `lab_id` |
| 仪器与项目 | `services/project_config_service.py` 的 `get_project_template`、`list_template_items` | 模板与检测项已存在，不重新建一套 panel |
| 配置与材料 | 同服务的 `list_lot_config_items`、`list_lot_item_levels`；`services/material_workflow_service.py` | 配置头批号不能代替逐水平真实材料关系 |
| 基础资料编辑 | `ui/master_data_dialogs.py`、`ui/master_data_workspace.py`、`ui/material_catalog.py` | 列表/详情/弹窗及取消体验已完成，只补类别和关联增量 |
| 项目编辑 | `ui/project_dialogs.py`、`pages/project_management_page.py` | 复用整组带入、表格、预览、保存和激活流程 |
| 首页导航 | `pages/main_page.py`、`ui/project_navigation.py` | 当前是项目工作台，尚无统一今日总览和跨项目日常录入表 |
| 模糊搜索 | `services/search_service.py` 的 `fuzzy_match`、`filter_frame` | 仅发现候选；产品、标准和保存身份必须使用精确标识 |

身份链须分别保留：仪器 → 模板 → 模板检测项 → 批次配置项 → 实际运行项目/批次 → 各水平实际材料。产品 `qc_material_id`、采购批次 `qc_material_lot_id`、材料水平 `qc_level_id` 与配置 `lot_config_id` 不能混用。

`qc_lot_config_item_levels` 和 `qc_level_combination_members` 是实际多水平关系。Z-score 合法组合可能来自不同实际批号；按一个主材料定位时仍须带出完整组合。

产品身份、实际材料水平与配置项`qc_method`是分开的关系。产品选择、更换及目录/配置导入均不能按材料水平数推导Z-score或覆盖已有显式方法；未配置时由用户选择或沿用用户已明确的panel设置。同一多水平产品可关联各自使用独立LJ或显式Z-score配置的不同检测项；消费合同必须读取每项已确认的方法，不能从产品反推。原方法的水平数、算法及完整run约束仍分别生效，不能为匹配产品包装而放宽。

`save_template_items` 接收完整当前行集合，会处理原配置中未保留的行。整组新增不能只传新增子集而丢掉旧项目。新行质量要求仍由任务 0 核查，模板保存不等于确认或启用。

## 2. 已有事务基础及必须验证的缺口

`database.atomic_write` 使用 `ContextVar` 保存当前连接，`get_connection` 在同一同步上下文复用它；`_DatabaseConnection.__exit__` 在外层事务期间不自行提交。最外层负责 `BEGIN IMMEDIATE`、提交或回滚。

| 原入口 | 当前能力 | 整组接入要求 |
|---|---|---|
| `database.add_result` | L-J 保存及参数准备、上下文、分析快照 | 外层共享事务；不能用参数准备当无副作用预览 |
| `zscore_logic.create_zscore_run` | 原完整 run 保存、联合判读和上下文 | 必须调用整个入口，不拆成逐水平独立提交 |
| `services.instant_service.save_instant_result` | 即时法原保存与分析快照 | 保留 3 点建立与 20 点人工转入规则 |
| `services.lot_lifecycle_service.import_reviewed_results` | 原 L-J / Z 结果导入 | 目前没有即时法分支，不可宣称三方法导入已完成 |
| `services.project_config_io_service.import_project_template_xlsx` | 配置行导入，局部已有事务 | 部分基础资料在内层事务前新建；完整导入和新关系需纳入外层事务 |

因此可复用现有事务基础，不等于已有整组保存功能。不得并行线程写同一组、另开连接或内层显式提交。T1 新增事件写入也必须核实是否参与同一事务；文件等外部副作用不能未经设计就声称能回滚。

T2-07 必须对第 2 项、末项、Z 中间水平、分析快照、提交回执分别注入失败，核对结果、参数、实际批号上下文、快照及提交记录没有残留。重试并发时取得写锁后再次检查提交标识。

## 3. 拟新增资料查询合同：T2A-07 交付

建议位置：`services/daily_context_service.py`，**当前未实现**，函数名由该卡实施后登记。

| 合同部分 | 至少包含 |
|---|---|
| 输入 | 当前数据库作用域、仪器 ID、产品 ID、明确采购批次 ID、拟检测时间、可选模板筛选 |
| 项目身份 | 模板项/配置项 ID、原运行项目/批次 ID、方法、输入尺度、单位、稳定排序 |
| 水平身份 | 水平 ID、真实采购批次 ID/文字、效期、浓度编号、角色、组合版本 |
| 当前状态 | 配置/使用修订、可写性、阶段及可定位的阻断原因 |
| 冲突 | 同主批号多个可用组合必须明确选择；无绑定不能猜配或在查询中创建 |

接口只读。相同条件输出稳定身份和顺序；停用、过期、时间边界及旧修订可明确识别。写入时仍需重新校验，查询结果不是保存授权。正式产品内容缺失时保留明确人工关系路径，不虚构邦德盛货号或覆盖项。

## 4. 拟新增日常请求与回执：T2-01 / 06 / 07 交付

建议位置：`services/daily_entry_service.py`，**当前未实现**。首版技术默认为全选项目共同成功或共同失败，原任务允许此方案。

| 对象 | 合同要求 |
|---|---|
| 草稿 | 稳定草稿/行标识、原始输入字符串、选中项目、顺序、筛选及返回位置；至少跨重绘、辅助页往返和保存失败保留 |
| 请求 | 唯一提交标识、规范化负载校验值、上下文修订、仪器/产品/主批次、共同时间/操作者、每项目完整水平及实际试剂 |
| 预校验 | 同时返回每个问题的项目/水平/字段；无写入副作用；单位或 raw/Ct/log 尺度不静默转换 |
| 保存 | 事务内重新核对身份、时间、效期、使用状态和版本；原三方法保存链参加同一事务 |
| 成功回执 | 当前提交每项结果 ID 或完整 run ID、判读/上下文版本、可打开明细；不能查全局最新值代替 |
| 重复请求 | 同标识同负载返回原回执；同标识不同负载拒绝；唯一约束与事务内复核处理并发 |
| 失败 | 全组无新增，完整草稿保留，错误定位明确；不通过补偿删除冒充回滚 |
| 新一轮检测 | 明确创建新提交标识、核对本次时间，保留仪器和顺序；同时间约束沿用原方法规则 |

用途内部值为 `routine`，用户界面和导出业务表显示“常规质控”。往返所需内部 ID 可放文件兼容结构中，不把开发字段解释铺在页面或业务列标题中。

## 5. 上游及后续消费边界

- T1-12：消费其**已验收接口合同**中的待处理查询、事件打开和新结果事件关联。当前不能编造已存在的函数签名。
- T2A-09：消费已验收的资料关系查询、修订口径及材料组合规则；正式目录内容缺口与可用功能合同分别登记。
- T2-02：今日范围按本地检测时间；跨日未完成事件单独查询。Z 按完整 run，阶段与 CV 统计期间分别明确；没有计划不计算漏做率。
- T2B：记录类型、临时独立入口、统计隔离和临时事件接入由其完成；本阶段只保留合同，不用备注或即时法代替。
- T5：有效结果/判读版本变化后总览、表格、图和导出失效刷新；本阶段不提前实现更正、补录或重算。
- T4：当前单库实验室资料不冒充组织和账号范围；身份与权限以后消费真实已验收合同。

## 6. 转为已验收合同时需补齐

逐接口登记真实文件和签名、字段类型与单位、空值/失效/多组合错误样例、事务参与范围、版本/幂等约束、隔离库结果及浏览器证据。草稿仅列设计要求，未补齐这些内容前不能据此把执行卡标为完成。

## 7. B2整组配置与交换接口（2026-09-28实现）

- `project_config_service.template_item_rows(template_id)` 返回无缺失标量的完整活动行，保留稳定 `uid`、备注及原质量核查资料。逻辑唯一键为 `(test_item_id,qc_method,input_value_type)`。
- `preview_panel_items(template_id,candidate_rows,expected_revision=None)` 只读；返回 `expected_revision/rows/entries/errors/added_count/retained_count`，`rows`始终包含原有全量项。相同逻辑键保留原配置，不从产品水平推导方法；新增行不带入来源项目的质量确认。`preview_panel_defaults`仅预览显式选中行与字段，可选择只填空项或覆盖；质量要求不能批量代替逐项确认。
- `save_panel_items(template_id,rows,expected_revision=...)` 在单次事务内保存完整草稿并返回 `saved_count/revision_no/errors`；`save_template_items(...,expected_revision=None)`仍为全量替换接口。版本过期、停用引用或方法/水平不相容均拒绝且不部分写入。新增或变更行按任务0逐项核查，`activate_project_template`在同一事务内执行质量准入。
- `project_config_io_service.prepare_project_template_import(template_id,data,mode='merge')`在只读快照下返回文件校验值、目标修订、产品覆盖修订、逐行预览、错误、拟新增资料和替换移除清单。确认接口`import_project_template_xlsx(...,expected_revision,file_sha256,product_version,replace_confirmed)`重新核对身份与版本；完整词条创建、覆盖映射和项目保存参加同一外层事务。
- 配置表可带检验项目、单位、方法学、试剂及厂家稳定标识和厂家类别；仅精确标识/标准编码/完整名称匹配，不使用模糊搜索保存。产品来源工作表保留四字段原文、稳定外部键、来源版本/校验值；覆盖工作表分别映射检测项及方法，来源库数字ID仅为映射键。目标产品必须已明确选择且身份一致，文件不自动创建替代产品。质量证据表只供查阅，导入后须本机重新确认。
- 项目/批次导出分别保留明确方法、输入尺度；批次表展开每个水平实际批号、材料批次/水平及目录规格标识。完整备份恢复的材料关系由日常录入整链验证，不把配置表当作检测结果导入。
- 专项证据：`tests/panel_configuration_smoke_test.py` 8组覆盖20项、选择性默认设置、质量准入、跨库映射/幂等、末段失败原子回滚、陈旧预览、同一检测项多方法合并、实际三批号及弹窗逐项编辑；配置交换原6组回归通过。样本与运行记录为`output/execution/B2/panel-config/`。此段记录本模块接口及专项验证，不代替B2整轮实际浏览器和备份恢复验收。

## 8. B2 只读批次与整组保存接口（2026-09-28 实现）

### 8.1 稳定选择与只读快照

`services.daily_context_service.list_daily_choices()` 返回 `instruments/materials/lots` 三个数组；仪器/产品/采购批次均用本库稳定整数 ID，各材料与采购批次提供其真实配置关联的 `instrument_ids`。产品名称、采购批号文字仅展示，不作匹配键。

`get_daily_context(lab_instrument_id, qc_material_id, qc_material_lot_id, test_time, template_id=None, lot_config_id=None)` 返回：

```text
{
  selection: {lab_instrument_id, qc_material_id, qc_material_lot_id, template_id, lot_config_id},
  test_time: "YYYY-MM-DD HH:MM:SS", context_revision: "SHA-256",
  requires_combination: false, combinations: [...], items: [...], issues: [...]
}
```

每个 `items[]` 的稳定身份为 `row_key`（配置项 ID 的十进制字符串）、`lot_config_item_id/template_item_id/template_id/lot_config_id`、`runtime_project_id/runtime_batch_id`。同时返回 `qc_method`（`lj/zscore/instant`）、`input_value_type`（`raw/ct/log`）、`unit_id/unit_symbol`、检测项/仪器/方法学名称、排序、`level_count/target_n`。`levels[]` 按既有 `level_order` 排序，包含 `qc_level_id`、`level_id`、实际 `qc_material_lot_id/lot_no/expiry_date`、水平名/浓度编号、角色及组合版本。主批号只定位相关配置项；Z 的其他真实采购批号完整保留，独立 B 批项目不混入 A 批查询。

状态字段为 `phase/usage_state/writable/issues`，并提供 `quality_goal/quality_review/source_snapshot/target_profile` 及 `reagent_options/suggested_reagent_lot_id/system_id/usage_revision`。参数版本缺失时 `target_profile=null`，不在查询中建靶。缺运行绑定时运行 ID 为 `null`、`writable=false`，返回应到项目设置核对的问题，不准备绑定。多个组合且未传明确 `lot_config_id` 时，`requires_combination=true`、`items=[]`；调用方须展示组合的真实水平后明确单选。

`context_revision` 同时覆盖选择、拟检测时间、配置/使用修订、真实水平、冻结来源、质量核查、原批次参数和检测/判读变化；`items[].revision` 是逐项版本。任何检测时间修改都应保留原输入并重新查询、核对；旧版本不可直接保存。

`database.read_snapshot()` 使用只读 SQLite 连接、`query_only=ON` 及同一读取事务；已有外层事务时复用当前连接。资料查询和预校验不调用会写入的 `resolve_batch_binding/source_context`，不生成目标参数、使用事件或结果。原任务 0 质量规则按精确项目、单位、尺度、来源指纹和逐水平要求检查，无冻结批次跳过核查的额外入口。

### 8.2 请求、校验与回执

`services.daily_entry_service.validate_submission(request)` 返回 `valid/errors/warnings/frozen_request/summary`。示意输入：

```text
{
  submission_id: "一次检测的稳定唯一标识",
  selection: {lab_instrument_id, qc_material_id, qc_material_lot_id, template_id, lot_config_id},
  context_revision: "查询所得版本", test_time: "YYYY-MM-DD HH:MM:SS",
  operator: "操作者", purpose: "routine",
  items: [{row_key: "配置项 ID", reagent_lot_id: 整数,
           levels: [{qc_level_id: 整数, value: "原输入字符串"}],
           manual_note: "本次备注", allow_same_time: false}]
}
```

可选的 `lot_config_item_id/runtime_project_id/runtime_batch_id/qc_method/unit_id/input_value_type` 如果传入，必须与当前配置一致；不存在隐式单位或尺度转换。每个选中项目只出现一次，必须属于明确的当前组合，并包含该项目所有必需水平。服务调用原 `parse_project_input_value`；Ct/log 的 `"0"` 保留，空值、非数及非有限值逐行/水平报告。校验只接受 `routine` 用途；临时质控由后续 B4 独立完成。

每个问题含 `row_key/field/qc_level_id/message/settings_target`，例如完整 Z 缺一个水平返回对应行的 `field="value"`；水平过期返回该 `qc_level_id` 和 `field="expiry_date"`；配置变化返回上下文变化信息。一次返回全部已知行问题。精确同时间的已有检测须逐行明确 `allow_same_time=true` 后再核对；同日新时间可作为新检测，不按日期禁止保存。

只有全组通过才生成 `frozen_request`，它包含规范化负载与 `validation_hash`。`submit_daily(frozen_request)` 在一次 `BEGIN IMMEDIATE` 事务中复核版本，再按原方法顺序保存全组；失败抛出 `DailyEntryError`，包含 `errors` 和 `retryable`。锁冲突可保留同一请求重试。输入内容变化后旧核对结果失效，须重新核对。

成功回执为 `{submission_id,saved_at,test_time,operator,purpose,count,status,items}`。每项包含精确的 `source_type/source_id/result_id`（Z 另有 `run_id`）、运行批次、`context_id/evaluation_id/target_profile_id`、`classification/phase/conclusion` 及 `levels` 原值明细。水平明细冻结本次实际批号、单位、尺度、原值、对应数值与判读信息，不查询当前最新结果来替换。`conclusion` 只使用实验室中文：在控/警告/失控、参数建立中、即时法尚不足 3 个有效点、即时法疑似离群或 20 点可核对转入提示。即时法 3/20 点规则继续由原分析链决定。

`get_submission(submission_id)` 只读返回原始保存回执或 `None`。同标识同负载在取得写锁后返回同一原回执；同标识不同负载拒绝。新一轮检测须新建标识。即使之后新增结果或更改参数，原提交回执不重写。三方法备注均传入原保存入口，即时法只新增 `manual_note` 参数转发，没有修改计算。

### 8.3 原子范围和专项证据

`migrations.daily_entry.ensure_daily_entry_schema(connection)` 随新库初始化创建 `qc_daily_submissions` 和 `qc_daily_submission_items`，前者以提交标识为主键，后者同时约束提交行与方法/结果唯一性。事务包含原 LJ/Z/即时检测、完整 Z 水平、实际批号上下文、分析快照、保存时可能发生的参数准备及回执/关联行；不以失败后删除补偿代替回滚。不自动创建、完成或更改 B1 失控事件。

- `tests/daily_entry_smoke_test.py` 使用新建 20 项隔离工程数据，覆盖 LJ/Z2/Z3/即时法、raw/Ct/log、Z 三个不同真实采购批号、真实试剂验证与使用事件。已通过的主要 12 组记录于 `output/execution/B2/daily-service-tests.log`：只读完整库对照、组合选择、一次收集第 3/8/20 行问题、陈旧版本、并发防重、原三方法逐表等价、各方法备注、20 项完整备份恢复。
- 第 2 项、末项和真正的 Z 中间水平、上下文、分析快照、回执及回执关联插入分别注入失败；整库业务内容与保存前一致。另在第 6 轮会建立参数的场景注入末项失败，全部参数准备回滚，同请求修复后成功。
- 独立写锁冲突、无残留及同请求成功重试通过，记录为 `output/execution/B2/daily-lock-test.log`。
- 使用截止前后 1 秒、采购批次停用、即时法已转入的只读阻断，以及真实便携式血糖仪低浓度 SD 规则与独立 log 项目不混用共 2 组通过，记录为 `output/execution/B2/daily-boundary-tests.log`。服务专项累计 15 组通过。
- `issues[].settings_target` 按字段定位：质量要求 `quality`、参数 `parameters`、试剂 `reagent`、使用期间 `usage`、材料/水平/效期 `materials`，其它为 `project`；调用方可显式覆盖。逐项与水平身份保留。新增映射断言及原整组问题回归通过，`daily-error-navigation-tests.log`，服务独立专项累计 16 组。
- 原三方法集成回归分别 LJ 2 组、Z 7 组、即时法 10 组通过，记录为 `output/execution/B2/{lj,zscore,instant}-integration.log`。
- 持久浏览器工程样例由 `tests/b2_business_chain.py` 在 `output/execution/B2/acceptance-01` 新建；同一主材料对应 20 项，并含即时法 2/3/20 点、正式 LJ/Z2/Z3 和明确失控样本。该目录交给浏览器验收后不再被服务测试改写。本段专项证据不代替 B2 整轮浏览器、报告和交付检查。
- 同一 fresh20 项完整链为 `tests/b2_integrated_acceptance.py`，最终证据 `output/execution/B2/integrated-03/integrated-results.json`、`visual-review.json` 与 `output/execution/B2/integrated-03.log`。三轮各 20 项经常规 XLSX 往返、整组校验保存及重复请求后，总览按完整检测单位共 102 次；三类正式异常（LJ/Z2/Z3）各关联两次后续复测、附件、人工确认及原报告。完整 ZIP 恢复、位置迁移和重新初始化后，原检测值、参数、上下文、判读、冻结回执及报告/附件字节全部一致。6 份 PDF 共 38 页逐页目视通过。`integrated-01` 保存了脚本固定确认时间被正确拒绝的尝试，`integrated-02` 保存了 B3 报告只读调用触发原隐式写入的问题；报告查询已改为只读取得来源，第三次整链通过。它们均不读写 `acceptance-01`。
