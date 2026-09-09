# LJQC V1.1 基础字典与项目管理规格

状态：已实现规格 v0.3
日期：2026-09-02
代码基线：main 3c737fb
开发分支：codex/v1-1-master-data-management

## 1. 目标与边界

V1.1 只建设上游基础资料和新版项目、批次配置能力，不修改已经稳定的质控计算。

本阶段交付：

- 检验项目、仪器、试剂、质控品、批号、水平、方法学、单位等基础字典；其中内置 WS/T 886—2026 的 296 个定量项目；
- 官方词条、本地词条、别名和来源记录的分层管理；
- 仪器加质控品维度的多项目配置模板；
- 新质控品批号复制、项目批量配置、靶值和 SD 配置；
- 项目配置批量导入、导出；
- 字典、模板、批号配置软停用；
- 配置版本快照。

本阶段不做：

- 不修改 LJ、Z-score、即时法计算规则；
- 不把新版配置直接切换为三个工作台的数据源，该接入属于 V1.2；
- 不做完整质量目标标准库、定性和半定量项目、结构化失控闭环；
- 不做 LIS、数据库接口、API、HL7、ASTM；
- 不把药监局全量原始数据直接装入业务 SQLite；
- 不做旧项目、旧批次和旧测试数据迁移；
- 不做新旧项目双轨映射；
- 不做硬删除历史配置、结果或报告。

## 2. 已锁定的建模原则

1. 现有 projects、batches、instant_projects、instant_batches 和结果表暂时保持不动，但 V1.1 新页面不读取其测试数据。
2. 新版多项目管理作为独立的上游配置层；V1.2 再建立新配置到工作台的适配。
3. 一个项目配置项继续只允许一种输入值类型：真实检测值、Ct 值或 log 值。
4. 即时法建靶有效点数固定为 20；LJ 和 Z-score 沿用当前 5 至 20 的配置范围。
5. Z-score 最终判定仍按 run；V1.1 不改变任何判定粒度。
6. 字典升级只更新官方词条，不覆盖医院本地词条、医院别名和本地备注。
7. 所有业务删除均改为软停用；已被历史配置或报告引用的记录永不硬删除。
8. 配置复制只复制结构和配置，不复制检测结果、质控状态、异常记录或报告。
9. 旧测试数据不导入新模型、不展示在新版管理页，也不作为新模型的兼容约束。

## 3. 术语

| 术语 | 含义 |
|---|---|
| 官方词条 | 来自 WS/T、药监局注册或 UDI、经 LJQC 清洗发布的基础词条 |
| 本地词条 | 医院或用户现场新增的词条 |
| 仪器型号 | 厂家产品型号，例如 AU5800 |
| 本地仪器 | 医院实际使用的一台仪器，可包含资产编号、序列号和科室 |
| 项目模板 | 某台本地仪器与某种质控品下的一组检验项目默认配置 |
| 批号配置 | 项目模板在某个具体质控品批号、效期和水平上的一次配置 |
| 配置项 | 批号配置中的一个检验项目 |
| 水平配置 | 某个配置项在某个质控水平下的靶值、SD 和靶值来源 |

## 4. 必填等级

下表使用三个等级：

| 标记 | 含义 |
|---|---|
| R | 创建记录时必须有值，数据库非空 |
| A | 草稿可为空，但启用模板或批号配置前必须补齐 |
| O | 选填 |

所有新主数据和配置表统一包含以下公共字段：

| 字段 | 等级 | 说明 |
|---|---:|---|
| id | R | SQLite 自增主键，仅限本数据库内部使用 |
| uid | R | UUID，导入导出和跨数据库恢复使用，唯一且不可变 |
| origin_type | R | official、hospital 或 import；legacy 不进入 V1.1 新模型 |
| is_disabled | R | 0 为启用，1 为停用 |
| disabled_at | O | 停用时间 |
| disabled_reason | O | 停用原因 |
| created_at | R | 创建时间 |
| updated_at | R | 最后更新时间 |

## 5. 基础字典数据模型

### 5.1 md_sources：数据来源

| 字段 | 等级 | 说明 |
|---|---:|---|
| source_code | R | 来源稳定代码，例如 WST886_2026、NMPA_UDI |
| source_name | R | 来源名称 |
| source_kind | R | standard、nmpa_registration、nmpa_udi、vendor、hospital、legacy 或 import |
| publisher | O | 发布机构 |
| version_label | O | 标准或数据包版本 |
| effective_date | O | 生效日期 |
| source_url | O | 来源地址 |
| checksum | O | 导入包校验值 |
| imported_at | O | 最近导入时间 |

说明：

- WS/T 886—2026 作为检验项目标准名称和代码的主要来源。
- 药监局注册和 UDI 数据只作为仪器、试剂、质控品候选来源。
- 原始全量数据在业务数据库外清洗，应用只接收整理后的版本化词库包。
- V1.1 随应用内置 WS/T 886—2026 中 296 个“定量”项目；75 个定性项目、13 个定名项目和 15 个定序项目留到 V1.3。
- 来源核对：[国家标准信息公共服务平台条目](https://std.samr.gov.cn/hb/search/stdHBDetailed?id=58FED2B32E4A886BE06397BE0A0A5BB3)、[国家卫健委标准正文](https://www.nhc.gov.cn/fzs/c100048/202606/1d8e67475848413cb4447e1b49037888/files/WST%20886%E2%80%942026.pdf)、[国家药监局 UDI 数据库](https://udi.nmpa.gov.cn/)。

### 5.2 md_manufacturers：厂家支持字典

这是仪器、试剂和质控品共用的支持表，避免三套厂家名称重复和别名失控。

| 字段 | 等级 | 说明 |
|---|---:|---|
| legal_name | R | 厂家或注册人标准名称 |
| display_name | R | 界面显示名称 |
| country_or_region | O | 国家或地区 |
| registration_holder_name | O | 注册人或备案人名称 |
| notes | O | 本地备注 |

约束：

- official 记录按来源记录键更新。
- hospital 记录不得被官方更新覆盖。
- 品牌、中英文名和历史名称进入 md_aliases，不在主表重复建行。

### 5.3 md_units：单位字典

| 字段 | 等级 | 说明 |
|---|---:|---|
| symbol | R | 单位符号，例如 mmol/L、U/L、Ct |
| unit_name | O | 单位中文名称 |
| ucum_code | O | 可用时保存 UCUM 代码 |
| quantity_kind | O | 浓度、活性、计数、无量纲等 |
| sort_order | R | 显示顺序，默认 0 |
| notes | O | 说明 |

约束：

- V1.1 不做自动单位换算。
- Ct、log 和无量纲需要有明确字典项，避免以空单位代替。

### 5.4 md_methods：方法学字典

| 字段 | 等级 | 说明 |
|---|---:|---|
| method_code | O | 标准或本地代码 |
| method_name | R | 方法学名称 |
| method_category | O | 生化、免疫、分子、血液等分类 |
| principle | O | 检测原理说明 |
| notes | O | 本地备注 |

### 5.5 md_test_items：检验项目字典

| 字段 | 等级 | 说明 |
|---|---:|---|
| standard_code | O | WS/T 或其他标准代码；官方标准记录必须有 |
| chinese_name | R | 中文标准名称 |
| english_name | O | 英文名称 |
| abbreviation | O | 常用缩写 |
| category_code | O | 专业分类代码 |
| category_name | O | 专业分类名称 |
| specimen_type | O | 适用样本类型 |
| result_type | R | V1.1 固定 quantitative；为 V1.3 预留 qualitative、semi_quantitative |
| default_unit_id | O | 默认单位，只作建议，不强制覆盖模板配置 |
| notes | O | 本地备注 |

约束：

- 标准代码不是医院 LIS 代码；LIS 代码作为 alias_type 为 lis_code 的别名保存。
- 同一标准项目可有多个医院简称、英文缩写和历史名称。
- V1.1 页面不开放定性和半定量配置。

### 5.6 md_instrument_models：仪器型号字典

| 字段 | 等级 | 说明 |
|---|---:|---|
| manufacturer_id | A | 关联 md_manufacturers |
| generic_name | R | 通用产品名称 |
| brand_name | O | 品牌名 |
| model | R | 型号 |
| registration_no | O | 注册证或备案编号 |
| device_category_code | O | 医疗器械分类编码 |
| catalog_no | O | 产品目录号 |
| notes | O | 本地备注 |

唯一性建议：manufacturer_id 加 normalized model。官方来源去重使用来源记录键，不使用名称覆盖。

### 5.7 lab_instruments：医院本地仪器

项目模板选择的是本地仪器，而不是抽象型号。

| 字段 | 等级 | 说明 |
|---|---:|---|
| instrument_model_id | R | 关联 md_instrument_models |
| display_name | R | 医院内显示名称 |
| asset_code | O | 资产编号 |
| serial_number | O | 序列号 |
| department_name | O | 所属科室 |
| instrument_group | O | 仪器组 |
| location | O | 放置位置 |
| notes | O | 本地备注 |

现场新增流程：先创建 hospital 类型的仪器型号，再创建本地仪器实例。

### 5.8 md_reagents：试剂字典

| 字段 | 等级 | 说明 |
|---|---:|---|
| manufacturer_id | A | 关联 md_manufacturers |
| generic_name | R | 通用名称 |
| trade_name | O | 商品名称 |
| specification | O | 规格型号 |
| registration_no | O | 注册证或备案编号 |
| catalog_no | O | 产品货号 |
| applicable_instrument_text | O | 来源中记录的适用仪器 |
| notes | O | 本地备注 |

V1.1 先管理试剂产品，不管理每次检测使用的试剂批号；试剂批号和导入映射在后续版本扩展。

### 5.9 md_qc_materials：质控品字典

| 字段 | 等级 | 说明 |
|---|---:|---|
| manufacturer_id | A | 关联 md_manufacturers |
| generic_name | R | 质控品通用名称 |
| trade_name | O | 商品名称 |
| matrix | O | 基质 |
| physical_form | O | 液体、冻干等 |
| catalog_no | O | 产品货号 |
| registration_no | O | 注册证或备案编号 |
| nominal_level_count | O | 产品通常包含的水平数 |
| notes | O | 本地备注 |

### 5.10 md_qc_material_lots：质控品批号

| 字段 | 等级 | 说明 |
|---|---:|---|
| qc_material_id | R | 关联 md_qc_materials |
| lot_no | R | 质控品批号 |
| manufacture_date | O | 生产日期 |
| expiry_date | A | 新批号启用前必须填写 |
| received_date | O | 收货日期 |
| opened_date | O | 开瓶或启用日期 |
| notes | O | 本地备注 |

唯一性：qc_material_id 加 normalized lot_no。

### 5.11 md_qc_levels：质控品批号水平

| 字段 | 等级 | 说明 |
|---|---:|---|
| qc_material_lot_id | R | 关联 md_qc_material_lots |
| level_code | O | 厂家水平代码 |
| level_name | R | 显示名称，例如低值、中值、高值 |
| level_order | R | 1、2、3 等顺序 |
| concentration_label | O | 厂家浓度或水平说明，不等于项目靶值 |
| notes | O | 本地备注 |

唯一性：同一批号内 level_order 唯一；同一批号内 normalized level_name 唯一。

### 5.12 md_aliases：别名

| 字段 | 等级 | 说明 |
|---|---:|---|
| entity_type | R | manufacturer、test_item、instrument_model、reagent、qc_material、method 或 unit |
| entity_id | R | 对应实体 id |
| alias_text | R | 别名内容 |
| normalized_alias | R | 用于搜索和去重 |
| alias_type | R | short_name、english、brand、lis_code、historical、vendor_text 或 custom |
| source_id | O | 关联 md_sources |
| is_primary | R | 是否为该类型首选别名，默认 0 |

说明：服务层验证 entity_type 和 entity_id 真实存在；停用别名不影响历史映射。

### 5.13 md_source_records：来源记录

| 字段 | 等级 | 说明 |
|---|---:|---|
| entity_type | R | 被来源记录支持的实体类型 |
| entity_id | R | 对应实体 id |
| source_id | R | 关联 md_sources |
| external_record_id | R | 原始来源中的稳定记录键 |
| source_status | R | active、expired、revoked 或 unknown |
| source_updated_at | O | 来源更新时间 |
| raw_display_name | O | 来源中的原始名称 |
| record_hash | O | 清洗前关键字段哈希 |
| first_seen_at | R | 首次出现时间 |
| last_seen_at | R | 最近出现时间 |

约束：

- source_id 加 external_record_id 唯一。
- 业务库只保存轻量来源记录，不保存药监局全量原始 JSON。
- 来源记录失效时软停用或标记 source_status，不删除已被使用的词条。

## 6. 新版项目与批号配置模型

### 6.1 qc_project_templates：项目模板

模板按本地仪器加质控品组织，可以一次包含多个检验项目。

| 字段 | 等级 | 说明 |
|---|---:|---|
| template_name | R | 模板名称 |
| lab_instrument_id | A | 关联 lab_instruments |
| qc_material_id | A | 关联 md_qc_materials |
| department_name_snapshot | O | 保存时科室显示值，便于追溯 |
| status | R | draft 或 active |
| revision_no | R | 当前修订号，从 1 开始 |
| notes | O | 模板备注 |

启用要求：

- 已选择本地仪器和质控品；
- 至少有一个未停用的配置项；
- 每个配置项满足 6.2 的启用校验。

### 6.2 qc_project_template_items：模板检验项目

| 字段 | 等级 | 说明 |
|---|---:|---|
| template_id | R | 关联 qc_project_templates |
| test_item_id | R | 关联 md_test_items |
| qc_method | R | lj、zscore 或 instant |
| input_value_type | R | raw、ct 或 log，项目级锁定 |
| unit_id | A | 关联 md_units |
| method_id | A | 关联 md_methods |
| reagent_id | A | 关联 md_reagents |
| level_count | R | LJ 和即时法为 1；Z-score 为 2 或 3 |
| target_n | R | 即时法固定 20；LJ 和 Z-score 为 5 至 20 |
| cv_limit | O | 当前已有的 CV 要求，必须大于 0 |
| quality_target_source_text | O | V1.1 的轻量来源说明 |
| sort_order | R | 表格显示顺序 |
| notes | O | 项目备注 |

唯一性：同一模板内 test_item_id 加 qc_method 加 input_value_type 唯一。

V1.1 质量目标范围：

- 可维护现有 CV 要求和自由文本来源；
- 不在本阶段建设完整标准库、TEa 规则和版本匹配；
- V1.3 增加质量目标库后，通过新外键关联，不改动现有 CV 数据。

### 6.3 qc_lot_configs：批号配置

| 字段 | 等级 | 说明 |
|---|---:|---|
| template_id | R | 来源项目模板 |
| qc_material_lot_id | A | 具体质控品批号 |
| lab_instrument_id | R | 从模板复制的本地仪器，作为本批号快照引用 |
| qc_material_id | R | 从模板复制的质控品，作为本批号快照引用 |
| config_name | R | 默认由质控品、批号和仪器生成，可修改 |
| status | R | draft、active、superseded 或 disabled |
| revision_no | R | 当前修订号 |
| copied_from_config_id | O | 复制来源批号配置 |
| effective_from | O | 启用日期 |
| effective_to | O | 结束日期 |
| activated_at | O | 启用时间 |
| notes | O | 批号备注 |

约束：

- 同一模板和同一质控品批号只允许一个非停用配置。
- 启用前批号效期必须完整。
- 已绑定工作台且已有结果后，仪器、质控品、项目方法、输入值类型和水平数不可直接改变。

### 6.4 qc_lot_config_items：批号项目配置

该表复制模板配置项的实际值，避免模板以后修改时改变旧批号。

| 字段 | 等级 | 说明 |
|---|---:|---|
| lot_config_id | R | 关联 qc_lot_configs |
| source_template_item_id | O | 来源模板配置项 |
| test_item_id | R | 检验项目 |
| qc_method | R | lj、zscore 或 instant |
| input_value_type | R | raw、ct 或 log |
| unit_id | A | 单位 |
| method_id | A | 方法学 |
| reagent_id | A | 试剂 |
| level_count | R | 1、2 或 3 |
| target_n | R | 建靶有效点数 |
| cv_limit | O | CV 要求 |
| quality_target_source_text | O | 质量目标来源说明 |
| sort_order | R | 显示顺序 |
| is_enabled | R | 是否在本批号启用 |
| notes | O | 本批号项目备注 |

### 6.5 qc_lot_config_item_levels：项目水平配置

| 字段 | 等级 | 说明 |
|---|---:|---|
| lot_config_item_id | R | 关联 qc_lot_config_items |
| qc_level_id | R | 关联 md_qc_levels |
| level_order | R | 本配置项内水平顺序 |
| target_source | R | building、manufacturer、manual 或 copied_pending |
| target_mean | O | 靶值 |
| target_sd | O | SD，填写时必须大于 0 |
| target_confirmed | R | 复制后的靶值和 SD 是否已确认 |
| notes | O | 水平备注 |

校验：

- LJ 和即时法必须且只能选择一个水平。
- Z-score 必须选择 2 或 3 个水平，并与 level_count 一致。
- target_source 为 manufacturer 或 manual 时，启用前必须填写 target_mean、target_sd 并确认。
- target_source 为 building 时允许靶值和 SD 为空，由现有工作台建靶计算。
- 即时法不要求预设靶值和 SD。

### 6.6 qc_config_snapshots：配置历史快照

| 字段 | 等级 | 说明 |
|---|---:|---|
| lot_config_id | R | 对应批号配置 |
| revision_no | R | 修订号 |
| action_type | R | create、edit、copy、activate、disable、reactivate 或 import |
| snapshot_json | R | 完整配置、字典 uid、显示名称和关键字段 |
| change_summary | O | 本次变更说明 |
| created_by | O | V1.1 单机版可为空或使用本地操作者名称 |
| created_at | R | 快照时间 |

规则：

- 批号配置每次创建、保存水平配置、复制、启用、停用和恢复都生成快照。
- 项目模板使用 revision_no 记录当前修订；模板导入在 V1.1 不写 qc_config_snapshots，因为该表只追踪已实例化的批号配置。
- 快照只追加，不更新、不删除。
- 后续报告在 summary_json 中记录 config_snapshot_id。
- 字典名称后续变化不改变旧快照中的显示内容。

### 6.7 schema_migrations：增量迁移记录

| 字段 | 等级 | 说明 |
|---|---:|---|
| migration_key | R | 迁移稳定编号，唯一 |
| applied_at | R | 执行时间 |
| app_version | O | 应用版本 |
| checksum | O | 迁移脚本校验值 |

## 7. 实体关系

    md_manufacturers
      ├─ md_instrument_models ─ lab_instruments
      ├─ md_reagents
      └─ md_qc_materials ─ md_qc_material_lots ─ md_qc_levels

    md_test_items ─┐
    md_methods ────┼─ qc_project_template_items
    md_units ──────┤             │
    md_reagents ──┘             │ 复制
                                 ▼
    lab_instruments ─ qc_project_templates ─ qc_lot_configs
                                                │
                                                ├─ qc_lot_config_items
                                                │      └─ qc_lot_config_item_levels
                                                └─ qc_config_snapshots

## 8. 新版页面操作流程

“基础资料”和“项目/批次管理”属于全局管理功能，放在页面右上角，与“报告历史”“系统设置”同级。顶部主导航只保留主页、单水平（LJ法）、多水平（Z-score法）和即时法，不把管理页面伪装成质控方法。

### 8.1 基础资料页

分为六个主区域，其中质控品区域再分质控品、批号和水平三个页签：

1. 检验项目；
2. 本地仪器；
3. 试剂；
4. 质控品、批号和水平；
5. 方法学；
6. 单位。

统一操作：

- 默认只显示启用项；
- 可切换查看已停用项；
- 检验项目可搜索名称、别名、缩写和标准代码；其他词典提供清单、现场新增和软停用；
- 搜索不到时现场新增 hospital 词条；
- 官方词条标准名称只读，本地显示通过别名或本地记录维护；
- 停用前显示被模板、批号配置和旧映射引用的数量；
- 停用不影响历史配置和报告查看。

### 8.2 新建项目模板

    选择或新增本地仪器
      → 选择或新增质控品
      → 批量勾选检验项目
      → 表格配置质控方法、输入值类型、单位、方法学、试剂
      → 配置水平数、建靶点数和 CV 要求
      → 保存草稿
      → 校验并启用模板

表格支持：

- 关键字批量搜索和勾选；
- 对选中行批量设置单位、方法学、试剂、质控方法和建靶点数；
- 单行覆盖批量设置；
- 固定列显示检验项目和当前完整性状态；
- 不完整行可保存为草稿，但不能启用。

### 8.3 新建首个批号配置

    选择已启用模板
      → 选择现有质控品批号或现场新建批号
      → 填写效期
      → 建立并选择水平
      → 展开项目乘水平表格
      → 填写或选择靶值来源、靶值、SD
      → 保存草稿
      → 预览差异和缺失项
      → 启用批号配置

### 8.4 复制上一批号

    选择来源批号配置
      → 点击复制为新批号
      → 选择或新建目标批号
      → 填写新效期
      → 确认水平对应关系
      → 修改并确认靶值和 SD
      → 预览差异
      → 保存并启用

复制后必须显示：

- 来源批号和快照修订号；
- 新旧批号、效期差异；
- 项目数量和停用项目；
- 水平映射；
- 每个项目水平的靶值和 SD 是否已确认；
- 未完成项和阻断原因。

## 9. 复制规则

### 9.1 正式复制

复制以下内容：

- 项目模板和来源快照；
- 本地仪器、质控品；
- 全部启用项目；
- 每个项目的质控方法、输入值类型；
- 单位、方法学、试剂；
- 水平数和水平名称建议；
- 建靶点数；
- CV 要求和轻量质量目标来源；
- 项目排序和备注。

### 9.2 只预填、必须重新确认

- 各项目各水平的靶值；
- 各项目各水平的 SD；
- 水平与新质控品批号的对应关系。

manufacturer、manual 等已有靶值来源复制后改为 copied_pending，target_confirmed 为 0，用户确认后才可启用。building 来源不复制靶值和 SD，继续保持 building，并从新的建靶状态开始。

### 9.3 绝不复制

- 数据库主键、uid、创建时间和启用时间；
- 旧质控品批号和旧效期；
- 旧批次停用状态；
- 检测结果和检测序号；
- 建靶已完成状态、正式期状态、实时统计；
- 离群值、禁用点和维护记录；
- Westgard 或 Z-score 规则命中记录；
- 手动备注、失控原因和纠正措施；
- 报告文件、报告历史和报告统计；
- 即时法转入 LJ 的状态与去向。

新批号在接入工作台后必须从全新的建靶状态开始。

## 10. 初始化与数据库升级

### 10.1 数据库结构

1. 在单个事务中创建全部 V1.1 新表、索引和 schema_migrations 记录。
2. 不重建、不改名、不删除现有计算核心表。
3. 不读取或转换现有测试项目、批次和结果数据。
4. 迁移后执行 PRAGMA foreign_key_check。
5. 同一迁移脚本可重复运行，已完成时安全跳过。

### 10.2 数据起点

- 新版基础字典和项目模板从空数据或正式词库种子开始。
- 旧测试数据不会自动出现在新版基础资料页和项目管理页。
- 开发测试使用专门的 V1.1 种子数据，不依赖旧测试数据库内容。
- V1.2 接入工作台时，只接入新版 active 批号配置。

## 11. 批量导入导出

V1.1 提供三种 XLSX：

| 文件 | 工作表 | 用途 |
|---|---|---|
| 空白导入模板 | 项目配置、填写说明 | 批量填写模板项目 |
| 当前项目模板导出 | 模板信息、项目配置、填写说明 | 修改后可重新导入到一个已创建的项目模板 |
| 当前批号配置导出 | 批号信息、项目配置、水平靶值、修订记录 | 归档和人工核对；V1.1 不提供整库恢复 |

导入流程：

1. 选择一个已创建的目标项目模板；
2. 上传并解析，先按 Excel 行号展示校验结果，不立即写库；
3. 选择“合并”或“替换模板全部项目”；
4. 用户确认后写入；缺少的检验项目、单位、方法学、试剂和厂家作为 hospital 词条新增；
5. 模板回到草稿状态，人工校验后才能重新启用。

匹配顺序：

1. 检验项目优先匹配标准代码，其次匹配标准化名称；
2. 单位和方法学按标准化显示名称匹配；
3. 试剂按厂家、通用名、商品名组合匹配；
4. 匹配不到时新增本地词条，不修改同名官方词条。

导出只使用业务显示名称和标准代码，不暴露本数据库自增 id。批号配置导出用于归档，不等同于 V1.3 的“配置导出与恢复”。

## 12. 软停用与追溯

- 主数据、模板、批号和配置项均不提供业务硬删除按钮。
- 已停用项从新建选择器默认隐藏，但历史配置仍显示原名称和停用标记。
- 停用模板不自动停用已启用批号；停用质控品批号会阻止新建配置，但不影响历史批号查看。
- 停用当前仍被 active 批号使用的字典项时，必须给出引用警告并要求填写原因。
- 恢复停用项时检查唯一性冲突。
- 批号配置变化写入 qc_config_snapshots；模板变化使用 revision_no。
- 新版配置快照不跟随当前字典名称变化。

## 13. 服务与页面边界

已新增：

- services/master_data_service.py：字典查询、新增、别名、停用和来源更新；
- services/project_config_service.py：模板、批号配置、复制、校验和快照；
- services/project_config_io_service.py：XLSX 预览、校验、导入和导出；
- pages/master_data_page.py：基础资料页；
- pages/project_management_page.py：新版项目和批号管理页；
- migrations/v1_1_master_data.py：幂等增量迁移。

约束：

- 页面层只负责交互和展示，不直接拼接复杂 SQL。
- database.py 保留连接和现有兼容接口，不把全部新业务逻辑继续堆入该文件。
- V1.1 不改 qc_logic.py、zscore_logic.py、plotting.py 和 zscore_plotting.py 的计算行为。
- 现有 pages/management.py 和即时法管理区在 V1.2 接入前不作为 V1.1 新数据入口。

## 14. 验收标准

### 14.1 数据模型

- 新数据库可一次初始化全部 V1.1 表。
- 已有数据库升级时不读取或修改旧测试数据。
- 重复初始化不产生重复表、重复种子或重复映射。
- foreign_key_check 无错误。

### 14.2 字典

- 可搜索检验项目，可新增本地词条、添加项目别名、停用和恢复。
- 官方词库更新不会覆盖 hospital 词条。
- 已停用词条不出现在默认新建选择器，但历史配置可正常展示。
- 搜索不到的仪器、试剂和质控品可在当前流程中新增后立即选择。

### 14.3 项目和批号管理

- 一次模板至少可批量配置 20 个项目。
- 批量设置后允许单行覆盖。
- 草稿可保存不完整配置，启用时准确列出全部阻断项。
- 新批号复制不携带任何结果、状态、异常和报告。
- 人工或厂家靶值复制后必须重新确认才能启用；建靶计算项目清空靶值与 SD，由新批号重新建靶。
- 项目、模板和批号软停用后历史仍可查看。

### 14.4 导入导出

- 导出的项目模板可导入到一个已创建且仪器、质控品已选定的目标模板。
- 必填、类型、范围和重复行问题先预览，确认前不写数据库。
- 校验问题给出 Excel 行号和原因；确认后可选择合并或替换模板项目。

### 14.5 计算核心隔离

- V1.1 新页面不调用 LJ、Z-score、即时法计算函数。
- V1.1 新配置不写入旧 projects、batches 和结果表。
- 现有计算相关 smoke tests 保持通过，证明本轮没有改变算法行为。

## 15. 已新增测试

- tests/master_data_smoke_test.py
- tests/project_management_v11_smoke_test.py
- tests/project_config_io_smoke_test.py

同时继续运行：

- tests/results_migration_smoke_test.py
- tests/storage_smoke_test.py
- tests/instant_smoke_test.py
- tests/building_outlier_smoke_test.py
- tests/zscore_smoke_test.py
- tests/lj_monthly_report_smoke_test.py
- tests/zscore_monthly_report_smoke_test.py
- tests/report_history_smoke_test.py

页面手工回归至少覆盖：

- 2880 × 1800，浏览器缩放 200%；
- 多项目表格横向滚动、批量设置和单行编辑；
- 新增本地词条后返回原流程且已选内容不丢失；
- 复制批号后的差异预览和靶值确认；
- 停用后新建选择器隐藏、历史详情仍可见。

## 16. 实施拆分

### V1.1-A：迁移与基础服务（已完成）

- 新表、索引和迁移记录；旧数据按用户口径不迁移，因此不做数据转换备份；
- 字典 CRUD、别名、来源、软停用；
- 配置快照基础能力；
- 单元和 smoke tests。

### V1.1-B：基础资料页（已完成）

- 检验项目、仪器、试剂、质控品、方法、单位管理；
- 搜索不到现场新增；
- 本地导入导出；
- 官方词条和本地词条隔离更新。

### V1.1-C：项目模板与批号复制（已完成）

- 多项目模板；
- 批量配置；
- 项目乘水平靶值表；
- 复制上一批号；
- 差异预览、启用校验、软停用和快照。

### V1.1-D：导入导出与整体验收（实现完成，回归状态见本次交付说明）

- 配置 XLSX 导入导出；
- 全量兼容回归。

V1.2 再实现新版配置到 LJ、Z-score、即时法工作台和报告的读取适配。

## 17. 已确认的实施决定

1. 项目模板以“本地仪器加质控品”为容器，一个模板内包含多个检验项目。
2. 复制旧批号时，靶值和 SD 只作为待确认预填值，未经确认不能启用。
3. 旧项目、旧批次和旧测试数据不迁移、不映射、不进入新版管理范围。
4. 直接实现新的 V1.1 数据链路，以新链路可完整跑通为验收目标。
