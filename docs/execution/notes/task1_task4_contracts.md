# 任务1事件、检测与处理版本合同

2026-09-22，T1-01准备卡交付。**本文锁定设计合同；拟新增对象、接口和约束尚未实现。** 本次仅静态核对源码及纯内存样例，不运行应用、导入业务模块或连接数据库。任务4仅保留身份衔接边界，不代表账号合同已完成。

需求依据：[任务1](../../task_01_out_of_control_reports.md) §§1—3、6（下文记为R1），[合并稿](../../requirements_merged_2026_09_21.md) §4.3（R2），[T1-01](../cards/T1-01.md)。验收及边界见[交付记录](../deliveries/T1-01.md)。

## 1. 已存在的源码事实

所有路径均相对仓库根目录；这里只读源码，未调用所列函数。

| 真实入口/对象 | 实际签名、返回或字段 | 必须注意 |
|---|---|---|
| `database.get_result(result_id: int) -> sqlite3.Row` | `results.id`；不存在抛ValueError | 原表保存value、log_value、test_time、operator、manual_note等；不存LJ最终phase/status，不能直接按原表失控列查询 |
| `database.get_zscore_run_with_levels(run_id: int) -> dict` | 返回id/run_id、phase、run_status、rule_hits_run、level_count、level_results；不存在抛ValueError | 主键是`zscore_runs.id`；子证据为`zscore_level_results.id/run_id/level_id`，不能用子表ID建事件 |
| `lot_lifecycle_service.source_context(connection, method, batch_id)` | 返回(source, binding, batch)，从绑定/批次取得配置 | **有写入副作用**：可能INSERT OR IGNORE到`qc_detection_systems`；并且当前配置不等于检测当时配置，不能用于事件历史补齐或只读候选查询 |
| `lot_lifecycle_service.context_dataframe(method,batch_id)` | DataFrame含result_id、context_id、批号、target_profile_id、provenance、source_context_id等 | 只读展示摘要，不含完整结果、判读版本、质量要求；不能拿展示用“未记录”当真实标识 |
| `lot_lifecycle_service.append_evaluation(method,result_id,payload,reason='current_review')` | 无返回ID；有上下文时追加`qc_result_evaluations`，相同最新JSON不追加，无上下文直接返回 | 这不是处理版本保存服务；首次reason由provenance决定；事件打开/查询不得调用它伪造原判读 |
| `qc_logic.py`现有分析链 | 保存LJ判读payload：result、target_mean、target_sd | 判读来自现有算法；不得为了查询调用会写入的分析链，缺判读版本时标缺失 |
| `zscore_logic.create_zscore_run(*args, **kwargs)` | 整次保存后在事务中追加判读 | Z正式期内部值`formal_qc`，结论`reject/warning/accept`；LJ判读正式期为`正式数据`，结论`失控/警告/在控` |
| LJ/Z异常备注入口 | `render_lj_abnormal_note_quick_entry(latest_row: pd.Series \| None) -> None`；`render_zscore_abnormal_note_quick_entry(latest_run: dict[str, Any] \| None) -> None` | 分别保存原结果manual_note与整次manual_note；没有独立处理状态，备注不推断原因或完成 |

现有表以`migrations/v1_2_lot_lifecycle.py`为依据：

- `qc_result_contexts.id`通过唯一的`lj_result_id/zscore_run_id/instant_result_id`三选一关联检测；`config_snapshot_json`冻结配置，另有实际试剂批号、`target_profile_id`和provenance。
- **命名区分**：本文拟新增`origin_context_id`指检测自己的`qc_result_contexts.id`。既有`qc_result_contexts.source_context_id`是来源转换链（如即时法转LJ），不能误当该检测自己的上下文ID。
- `qc_result_context_levels`按`(context_id,level_order)`唯一，含qc_level_id、qc_lot_id、lot_no、level_name；`migrations/material_workflow.py`另补level_code、expiry_date。水平显示名不作为关联键。
- `qc_result_evaluations`含id、context_id、evaluation_json、algorithm_version、reason、previous_id、created_at；原表没有显式version_no或有效版本指针。当前可按同context的id顺序读取追加链，不能称它已有任务5修订发布机制。
- `qc_target_profiles`按(qc_method,batch_id,version_no)唯一，levels_json保留参数。质量要求优先取检测config_snapshot_json中的quality_goal_json、quality_review_json，不读今日目录替换历史。
- `qc_detection_systems`含id、template_item_id、identity_json、snapshot_json；已有系统身份为模板检测项ID加identity的0/3/4/5/6/7项，即仪器、检测项、输入类型、单位、方法学、试剂ID。材料批号不在系统身份内。identity顺序已核对`workbench_config_service.py`与`zscore_workbench_service.py`。

## 2. 拟新增对象和稳定关系（R1 §§2、3、6；R2 §4.3）

以下为逻辑字段名，T1-02再登记实际表名与实现签名；禁止将这些名称说成现有数据库结构。

| 拟新增对象 | 字段/关系 | 唯一性、空值及保存约束 |
|---|---|---|
| SourceRef 来源检测引用 | source_type: `lj_result`/`zscore_run`；qc_method: `lj`/`zscore`；source_id: 正整数；record_type: `routine`（未来`temporary`） | 同当前数据库作用域内(source_type,source_id)是唯一来源；方法必须与来源相符。记录用途不参与唯一键，避免同一检测被改标签后重复建事件 |
| Event 事件主记录 | 稳定event_id；SourceRef；origin_context_id；origin_evaluation_id；original_classification；opened_by/opened_at；current_revision_no | 一个来源检测最多一个事件；版本、规则和水平不进入唯一键。重复打开返回同event_id。上下文/判读确实缺失用null并写missing_fields，不造ID；存在的引用必须精确属于同一来源/context |
| OriginSnapshot 原始证据 | snapshot_schema_version、frozen_at、字段来源及缺失清单、完整上下文和水平证据 | 创建时冻结，不因处理保存、今日配置或后续重算更新。缺记录的字段显示“未记录”；事件能登记可用证据，不冒充历史完整 |
| HandlingRevision 处理版本 | event_id、revision_no、previous_revision_no、内容快照、status、saved_by/saved_at、change_reason | 首次事件含版本1（待处理、内容可空）；每次明确保存草稿/提交/确认/退回/补充均追加一个版本。只打开或编辑未保存不产生版本 |
| StatusChange 状态记录 | event_id、revision_no、from_status、to_status、actor_text、occurred_at、reason | 与新处理版本同事务；同状态内容修改仍有版本，状态日志仅记录实际转移；不修改原判读 |
| SaveRequest 保存请求 | request_id、规范化payload_digest、expected_revision_no、返回回执 | request_id在库内唯一；同ID同负载返回原event_id/version回执，同ID异负载拒绝；事务内先查重，再查预期版本，陈旧窗口拒绝。失败不留半个事件/版本/状态记录 |
| RetestLink 复测证据 | 处理版本→复测SourceRef、context/evaluation引用和冻结证据、difference_reason | 可多次复测，完整Z run，原检测不可自关联；处理版本冻结当时采用的复测判读，未来变化另展示 |
| 附件/报告引用边界 | event_id + revision_no + 稳定附件或报告标识 | 留给T1-08—11，不实现文件保存；原报告冻结，补充不覆盖历史文件 |

创建事件也使用request_id；不同请求同时打开同一来源由数据库唯一约束和事务复核归并，返回既有事件，不额外生成处理版本。保存使用现有atomic_write的同连接事务，不在页面另开独立提交。原值、来源、原判读不参与处理事务的更新。

取消只丢弃未保存编辑（或由页面保留本地草稿），已保存版本可再次进入恢复。异常时保留未保存输入供重试；接口返回明确的来源不存在、来源不匹配、不支持范围、版本冲突、重复请求异负载、材料差异缺说明等错误，不暴露堆栈。实现和故障注入留给T1-02及后续卡。

## 3. 冻结字段与缺失规则（R1 §§2、3）

| 内容 | 已有证据来源 | 冻结要求 |
|---|---|---|
| 项目、检测项、仪器、方法学 | 检测config_snapshot_json中的稳定ID与名称；原运行project_id/batch_id | 两套ID含义分别标明；只有旧名称时保留名称和缺失标记，不从名称补系统ID |
| 检测时间、操作者、输入类型和单位 | 原结果/run + 原配置快照 | 原始数值、log值与使用尺度分开；不临时换算尺度 |
| 各水平结果 | LJ单结果；Z全部level_results及完整run结论 | Z三个水平即使仅两个失控也必须保留三个；记录level_id、顺序、数值、局部规则、整次规则 |
| 实际材料 | 原context试剂批号与context_levels逐水平材料 | 允许部分水平不同批号，不能用配置头批号覆盖；缺失为未记录 |
| 参数 | 原target_profile_id与levels_json；原evaluation内实际采用均值/SD | 参数来源/版本随证据冻结，缺版本不可用当前参数冒填；可用旧判读数值须注明证据来源 |
| 质量要求 | 原配置的quality_goal_json/quality_review_json | 保存已采用内容、版本、适用范围；缺失不从最新目录猜补 |
| 规则和判读 | 指定qc_result_evaluations.id及其完整payload、algorithm_version | 分开原结论与处理状态；快照缺失时可保存原run现存证据，但不能声称它是检测当时原版本 |
| 既有备注 | 原manual_note，在frozen_at时采集 | 标“已有资料”，不是原因分类、已确认效果或已完成状态 |

空白/null与零值区分；missing_fields记录字段路径和原因（无上下文、无判读、原快照缺项），不把缺失值序列化成虚假的0或空对象“已具备”。首次登记时能确认的异常证据保留采集时间；来源关系不可靠不妨碍草稿登记，但阻止自动复测匹配。若连正式期/异常类型也无法可靠确认，则返回待核实候选，不能伪造一个正式失控。

## 4. 状态转移与确认矩阵（R1 §§2、3、6）

四种状态内部值分别为pending、in_progress、pending_confirmation、completed；用户名称为待处理、处理中、待确认、已完成。original_classification独立，警告始终保留原警告名称。

| 当前状态 | 显式动作 | 下一状态 | 必要记录 |
|---|---|---|---|
| 无事件 | 打开失控/显式发起警告处理 | 待处理 | 来源、原证据、登记人、时间；打开既有事件不再创建 |
| 待处理 | 保存处理草稿 | 处理中 | 处理人、保存时间，内容允许不完整 |
| 处理中 | 继续保存草稿 | 处理中 | 新版本、人员、时间 |
| 处理中 | 提交确认 | 待确认 | 原因、措施、效果依据及处理信息完整；不自动确认 |
| 待确认 | 人工确认效果并完成 | 已完成 | 确认人、确认时间、效果说明及必要信息完整 |
| 待确认 | 退回/继续修改 | 处理中 | 退回原因、人员、时间、新版本 |
| 已完成 | 补充或更正处理内容 | 处理中 | 必须新版本、修订原因、人员、时间；旧完成版本和报告保留，重新提交并确认后才再次完成 |

待处理不直接跳已完成；待确认不静默编辑，先退回。后续在控、添加附件、修改原备注、读取最新判读都不是状态转移动作。未列转移拒绝，不新增审批层级。

| 字段 | 待处理/处理中草稿 | 提交待确认 | 确认完成 |
|---|---|---|---|
| 原因分类、原因分析 | 可空 | 必填；不能由旧备注自动分类 | 必填 |
| 纠正措施 | 可空 | 必填 | 必填 |
| 补充说明 | 可空 | 可空 | 可空，退回/完成后修订另须原因 |
| 效果说明与依据 | 可空 | 必填 | 必填并由人确认，不能由在控结果自动代填 |
| 处理人及处理保存时间 | 保存时记录 | 必填 | 保留 |
| 确认人及确认时间 | 不伪填 | 可尚未填写 | 必填，时间不早于提交；服务记录动作时间 |
| 复测 | 可无，未完成复测可继续处理中 | 已关联时须同系统、后续时间且证据完整；差异须说明 | 不编造复测；不能仅凭一个在控值判完成 |
| 患者影响范围、需否评估、措施和依据 | 可记录/待评估 | 展示已填信息及缺口 | 原需求未规定统一必填阈值，不擅加患者放行门槛；从不自动宣称可放行 |

没有复测且尚不能提供处理效果依据时保留处理中。原需求未规定所有事件必须有复测、固定复测次数或统一合格阈值，本合同不新增这些临床规则；人工仍须给出真实效果依据与确认信息，缺必要信息拒绝完成。

## 5. 准确复测匹配（R1 §§2、3、6）

1. 只读解析原检测及候选的原始上下文，优先采用其中已有system_id并核对实际系统身份记录；不得调用source_context登记新身份，也不因名称相同自动关联。
2. 同一准确系统ID，且身份字段无冲突、方法及水平结构可比；异系统/异项目/异仪器拒绝。任一侧缺可靠系统关系返回“无法确认”，不把两个null看成相等。若历史确有完整身份快照而无ID，后续可只读精确核实已有身份记录；找不到仍未知，不猜补。
3. 复测test_time严格晚于原检测，排除自身、较早、相同时间及无有效时间的记录。现有时间归一沿Asia/Shanghai秒精度；不借较大主键推定同秒先后（任务5同时间排序另定）。
4. 质控品批号、试剂实际批号或参数版本变化不自动拒绝；返回逐水平差异，要求difference_reason。未知与已知的差异也明确显示，不把“都缺失”当“证实相同”。材料水平组成不可核实则不自动关联。
5. 复测是另一次原保存链产生的新检测，仅建立引用；不更正/覆盖原失控值。后续结果可以仍失控；是否完成始终经过人工效果确认。

## 6. 首轮范围、候选和后续接口（R1 §§1—3；R2 §4.3）

以下为**拟新增逻辑接口**，非可调用函数；真实签名由承接卡交付。

| 合同操作 | 输入 | 输出与边界 | 承接 |
|---|---|---|---|
| 查询待处理 | 时间/项目/仪器/状态过滤 | 合并“无事件的正式期失控候选”和“已建未完成事件”，按SourceRef去重；event_id可null，candidate_key为SourceRef；跨日未完成单独返回，不被今日范围或最新在控隐藏 | T1-07→T2 |
| 打开事件 | SourceRef、显式发起标记、request_id、人员时间 | 已有event_id/版本，或原子创建；警告须显式发起并保留原规则名称 | T1-02/03 |
| 保存处理 | event_id、expected_revision_no、request_id、动作及内容 | 新版本/原重试回执或明确校验错误；不改原检测 | T1-02/04/06 |
| 查询/关联复测 | event_id、候选SourceRef、预期版本、差异说明 | 精确身份与时间校验、完整水平证据及差异；仅查询无写入 | T1-05 |
| 读取原始/最新判读 | SourceRef、origin_context_id、origin_evaluation_id | original（固定）与latest（可变）两份证据、版本变化标记/缺失原因；当前同context按id取最新，不覆盖original | T1-02→T5 |

首轮仅常规正式期LJ/Z失控及显式警告；即时法疑似离群继续原维护流程，建靶期不自动进入。未打开失控也属于待处理候选，**查询不得为了发现事件而创建事件或判读快照**；缺LJ正式判读证据时列为待核实缺口，不声称候选齐全，T1-07须用独立库验证真实取数。

record_type保留routine/temporary扩展位置；目前临时来源表和适配器未实现，收到temporary须明确不支持，不能当routine接入。T2B交付后沿同一SourceRef/event_id协议接入，具体存储来源不同则另登记来源类型，禁止通过用途重复一个原检测；临时规则及统计分母仍按D-04/D-05待决。

任务5追加V2时原V1引用、快照和处理历史全部保留；当前事件仍同event_id，latest读取改为消费任务5已发布有效版本接口（不能简单取未发布最大ID）。更正后仍异常也不复制事件；变为在控不删除或自动关闭旧事件；原报告不覆盖。状态更新交互由T5-10细化，本卡不实现重算。

任务4接入前actor_text/handler_text/confirmer_text只是人员文字记录，不是账户认证、权限、电子签名或多级审批。以后可增加经已验证会话获得的actor_user_id并保留文字快照，历史未知ID为null，不按同名猜账号，不默认多实验室。T4-01仍未执行。

## 7. 具名样例及验收边界

`tests/out_of_control_fixtures.py`为纯内存、固定期望的合成样例；数值和规则标签用于结构验收，不计算临床结论。没有数据库、应用或服务实现。

| 样例ID | 情境 | 期望事件数/结果 | 对应要求 |
|---|---|---|---|
| LJ_IGG_A | IgG仪器A正式失控，同记录重复打开 | 1个事件、1水平；未打开时先是1个候选/0事件 | R1 §2、6 |
| Z2_HIGH | 两水平run仅高值失控 | 1个事件、2水平证据 | R1 §2、6 |
| Z3_TWO_REJECT | 三水平run两个水平失控 | 1个事件、3水平证据（验收A1） | R1 §2、6 |
| WARNING_NOTE | 警告已有“已处理”备注 | 默认0事件；显式发起1事件、待处理、仍称警告（A3） | R1 §3 |
| MISSING_SOURCE | 有可用异常证据但缺context/system/原判读 | 可登记1事件、缺失信息未记录；复测无法确认 | R1 §2 |
| SAME_NAME_OTHER_INSTRUMENT | IgG仪器A/B同名且原系统缺失 | 无法确认；补充对照：双方系统可靠但不同则拒绝（A2） | R1 §2、6 |
| EVALUATION_V2 | 同一来源后续V2变为在控 | 仍1事件，origin=V1/latest=V2，原处理版本保留（A4） | R2 §4.3 |

样例另列同系统换批/参数差异、早于/等于时间、请求重试/异负载/陈旧版本、退回及完成后补充的固定期望，用于后续卡消费。当前通过只说明合同覆盖和样例结构自洽；数据库唯一约束、并发回滚、实际候选查询、UI、附件和PDF均须后续卡运行验收。
