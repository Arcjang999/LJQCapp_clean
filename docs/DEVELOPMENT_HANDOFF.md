# LJQC 开发路线与跨设备接手说明

更新时间：2026-09-09

## 1. 这份文件解决什么问题

这份文件是 LJQC 当前开发状态的跨电脑交接入口。它把此前围绕竞品、产品路线、V1.1、V1.2 和后续版本的讨论，整理成仓库内可持续维护的说明。

新的电脑或新的 Codex 任务不需要依赖旧聊天记录。只要能拉取本仓库的远端分支，并依次阅读本文件、`AGENTS.md` 和两个版本规格，就能够继续工作。

当前开发分支：`codex/v1-2-lj-integration`

当前代码基线：本地 `main@3c737fb`。远端工作分支推送后，以 `origin/codex/v1-2-lj-integration` 的最新提交为准。

## 2. 一句话产品方向

先补管理能力，再补外部连接能力；已经稳定的计算核心暂时保持稳定。

```text
main 稳定基线
  ↓
V1.1 基础字典与新版项目/批次管理
  ↓
V1.2 接入现有 LJ、Z-score、即时法工作台
  ↓
V1.3 质量目标、定性项目、失控闭环与报告完善
  ↓
V2 LIS 对接
  ↓
V3 可选的云端室间比对
```

这条路线的核心不是重写 LJ、Z-score 或即时法，而是先把它们的上游配置统一，再逐步建设业务闭环和外部接口。

## 3. 竞品比较后形成的产品判断

此前结合 Q-expert、Bio-Rad Unity、操作视频和面聊内容进行了对比。结论是：LJQC 的统计和质控核心并不弱，主要差距在管理效率和业务完整性。

最值得吸收的能力：

1. 批号复制和多项目配置模板；
2. 基础字典和质量目标库；
3. 跨项目批量日常录入；
4. 结构化失控原因、纠正措施和验证闭环。

当前不应照搬的能力：

- 竞品的老式大菜单和大量无意义必填项；
- 多用户、复杂权限、两级审核和电子签名；
- 厂商封闭的全球比对模式；
- 高成本仪器端口直连；
- 任意 Word 模板编辑器；
- 删除质控品时连同历史一起删除的做法。

## 4. 开发完成度总览

| 阶段 | 状态 | 当前结论 |
|---|---|---|
| V1 稳定计算基线 | 已完成并持续回归 | LJ、Z-score、即时法、离群维护、月报、报告历史、设置和存储已可运行 |
| V1.1 基础字典 | 已完成 | 新数据模型、296 个 WS/T 886—2026 定量项目、本地新增、别名、来源和软停用已实现 |
| V1.1 新版项目/批次管理 | 已完成 | 多项目模板、批号配置、复制上一批号、批量配置、XLSX 导入导出和快照已实现 |
| V1.2 LJ 接入 | 第一阶段已完成 | 新版配置已接入 LJ，旧计算不变；单位、方法等已带入工作台和月报 |
| V1.2 Z-score 接入 | 未开始 | 下一项主任务 |
| V1.2 即时法接入 | 未开始 | 现有即时法仍可用，转入 LJ 链路已保留 |
| V1.2 人工/厂家靶值接入 | 未开始 | 需先锁定如何与现有建靶逻辑共存 |
| V1.3 | 未开始 | 质量目标、定性/半定量、失控闭环、报告增强 |
| V2 LIS | 未开始 | 等医院和开发商确认接口条件 |
| V3 室间比对 | 未开始 | 独立云端服务，不进入本地 SQLite 核心 |

## 5. V1 稳定基线已经有什么

当前正式入口包括：

- 主页；
- 单水平（LJ 法）；
- 多水平（Z-score 法）；
- 即时法；
- 基础资料；
- 新版项目/批次管理；
- 报告历史；
- 系统设置。

已经稳定并应优先复用的能力：

- LJ 建靶、Westgard 判读、图表和月报；
- Z-score 2/3 水平、按 run 最终判定、图表和月报；
- 即时法格拉布斯检验、20 个有效点后人工确认转入 LJ；
- LJ 和 Z-score 建靶期离群点提醒、保留、禁用和恢复；
- 项目级输入值类型锁定；
- 固定模板月度 PDF；
- 报告历史；
- 实验室信息、数据库位置、备份和恢复。

计算核心主要位于：

- `qc_logic.py`
- `zscore_logic.py`
- `services/instant_service.py`
- `plotting.py`
- `zscore_plotting.py`

除非明确发现算法错误，不要为了接入新版配置而重写这些模块。

## 6. V1.1 已完成内容

详细字段和关系见 `docs/v1_1_dictionary_project_management_spec.md`。

### 6.1 基础字典

已经建立：

- 数据来源；
- 厂家；
- 单位；
- 方法学；
- 检验项目；
- 仪器型号和医院本地仪器；
- 试剂；
- 质控品；
- 质控品批号；
- 质控水平；
- 别名；
- 来源记录。

内置词库目前只有 `data/dictionaries/wst_886_2026_quantitative.csv`，包含 WS/T 886—2026 的 296 个定量检验项目。药监局注册/UDI 数据目前只确定了来源策略，尚未导入完整仪器、试剂或质控品词库。

官方词条和医院本地词条必须分层：官方更新不能覆盖医院本地新增、别名或备注。

### 6.2 新版项目和批号管理

当前目标流程已经实现：

```text
选择本地仪器
→ 选择质控品
→ 批量勾选检验项目
→ 表格配置方法、输入值类型、单位、试剂、水平数和 CV 要求
→ 保存并启用项目模板
→ 选择质控品批号和水平
→ 配置靶值来源、靶值和 SD
→ 保存并启用批号配置
```

已经支持：

- 多项目批量配置；
- 搜索不到的仪器、试剂、质控品和方法现场新增；
- 一键复制上一批号；
- 新批号只调整批号、效期、靶值和 SD；
- 配置 XLSX 导入导出；
- 项目模板和批号配置软停用；
- 配置版本快照；
- 历史引用不因字典名称更新而被覆盖。

基础资料和项目/批次管理是右上角全局入口，与报告历史、系统设置同级，不属于 LJ 或 Z-score 的内部页签。

### 6.3 旧数据口径

用户已经明确：旧项目、旧批次和旧结果均为测试数据，不需要迁移，也不需要为它们维持新版 UI 兼容。

因此：

- 新版管理页不读取旧测试项目；
- 不做旧数据到 V1.1 模型的迁移；
- 不为普通旧项目建立双轨映射；
- 旧底层表暂时保留，仅作为现有计算运行时结构；
- 即时法确认转入 LJ 的批次属于现行业务链路，不按普通旧测试数据处理。

## 7. V1.2 LJ 接入已经完成什么

详细规格见 `docs/v1_2_lj_workbench_integration_spec.md`。

已经新增 `qc_workbench_bindings` 作为新版配置和稳定计算运行时之间的适配层：

```text
V1.1 已启用批号项目
  → qc_workbench_bindings
  → 现有 projects / batches 运行时身份
  → 现有 results、LJ 算法、图表和月报
```

已完成行为：

- LJ 页面只显示已启用、单水平、`qc_method = lj` 的新版配置；
- 当前第一阶段只接入 `target_source = building`；
- 普通旧测试项目不出现在新版选择器；
- 同一配置重复同步不会重复创建运行时项目、批次或结果；
- 上游停用后绑定变为 inactive，但不删除历史结果；
- 再次启用时复用原绑定和历史结果；
- 单位、方法、仪器、试剂、质控品、批号、水平、效期和 CV 要求自动带入 LJ 工作台；
- LJ 月报带出单位、检测方法和新版靶值来源；
- 即时法已转入的 LJ 批次继续可见，并保留来源项目、来源批次和转入时间；
- `qc_logic.py` 的统计和 Westgard 计算未修改。

尚未接入 LJ 的配置：

- 人工靶值；
- 厂家靶值；
- 复制后待确认靶值。

这些配置仍保存在 V1.1 数据模型中，页面会说明暂未接入原因。

## 8. 下一项开发任务：V1.2 Z-score 接入

建议从现有 `codex/v1-2-lj-integration` 分支继续，不要从旧 `main` 重新做 V1.1。

目标是复用 LJ 已建立的运行绑定思路，把已启用的新版 Z-score 配置接入现有 Z-score 工作台。

第一阶段准入建议：

- 项目模板和批号配置均 active 且未停用；
- `qc_method = zscore`；
- `level_count` 为 2 或 3；
- 有效水平数量必须与 `level_count` 一致；
- 第一阶段同样只接入本批次建靶；
- 输入值类型、单位、方法、仪器、试剂和质控品来自新版配置快照。

必须保持不变：

- Z-score 最终判定单位是 run；
- 任一 level 触发 reject 类规则时，整次 run 失控；
- 其他 level 继续作为明细证据展示；
- 建靶期保留、禁用和恢复按整个 run 联动；
- 正式期后隐藏建靶维护操作，只保留只读历史；
- 保存新 run 后保持当前单水平/合并视图；
- 不重写现有规则模板和绘图模块。

验收至少覆盖：

1. 2 水平配置进入工作台并完成建靶、正式期和报告；
2. 3 水平配置进入工作台并完成建靶、正式期和报告；
3. 普通旧测试项目不出现在新版选择器；
4. 配置重复同步不重复创建运行时对象；
5. 停用和恢复不丢失历史结果；
6. 单位、方法、仪器、试剂、质控品和目标来源进入工作台与月报；
7. 现有 49 条 Z-score smoke tests 继续通过。

## 9. V1.2 后续顺序

Z-score 接入完成后：

1. 接入即时法新版上游配置，固定 20 个有效点规则不变；
2. 保留 3 个有效点后开始格拉布斯检验；
3. 保留“提示 + 人工确认”转入 LJ，不自动转入；
4. 再单独确定人工/厂家靶值如何进入 LJ 和 Z-score；
5. 三种工作台稳定后，清理不再使用的旧管理入口代码。

## 10. V1.3 业务完整性增强

管理链路稳定后，补以下能力：

- 质量目标标准库和批量导入；
- 自定义质量目标来源；
- TEa、生物学变异、行业标准等目标的版本与适用范围；
- 定性、半定量项目；
- 阴性、阳性、±、+、++、+++ 等结果响应字典；
- 每个水平的预期响应；
- 结构化失控原因和纠正措施模板；
- 失控事件状态：待处理、已分析、已纠正、已验证、已关闭；
- 复测结果关联原失控记录，不覆盖原始记录；
- 全方法补录和修改审计；
- 跨项目批量录入中心；
- 月报补齐方法、仪器、试剂、单位和质量目标来源；
- 项目/批次配置导出与恢复。

定性项目应独立建模，不应硬塞入数值型 LJ/Z-score。定性报告应输出符合率、异常次数和失控明细，不计算均值、SD 或 CV。

## 11. V2 LIS 对接

等医院和 LIS 开发商确认后再实现，优先顺序：

1. 监听指定目录中的 CSV/TXT；
2. 首次建立仪器、项目、批号和定性结果映射；
3. 映射保存后自动入库；
4. 增加幂等去重、错误队列、重试和导入日志；
5. 后续再考虑数据库、API、HL7 或 ASTM。

V1.1 的字典和项目配置会直接作为 LIS 映射基础，因此当前主数据工作不会浪费。

## 12. V3 室间比对

室间比对应作为独立云端服务，不塞进本地 SQLite 核心：

- 实验室自愿上传脱敏数据；
- 中心端按对等组、方法组和所有组统计；
- 返回 SDI、CVR、偏倚、百分位和月报；
- 需要多家实验室及统一质控品批号才能启动；
- 本地 LJQC 不订阅室间比对时仍应完整运行。

## 13. 已锁定且不能随意改变的规则

### 13.1 输入值类型

- 一个项目只能选择真实检测值、Ct 值或 log 值中的一种；
- 输入值类型在项目级锁定，批次继承且不能修改；
- 录入字段、导入模板、图表纵轴和报告名称必须一致；
- 不重新引入录入时实时 log10 换算的主交互。

### 13.2 建靶离群处理

- LJ 和 Z-score 建靶期默认使用格拉布斯法；
- 系统只提醒疑似离群，不自动剔除；
- 用户可以保留、禁用和恢复；
- 禁用不删除原始记录；
- 禁用后不参与建靶统计，默认不显示在主图；
- 禁用记录必须在数据表、维护区和报告附表中可追溯。

### 13.3 即时法

- 单水平；
- 3 个有效点后开始检验；
- 20 个有效建靶点后提示可转入 LJ；
- 转入必须人工确认；
- 转入后即时法源批次冻结为只读。

### 13.4 报告

- V1 使用固定月度 PDF 模板；
- 报告历史是正式功能；
- 旧报告摘要不能被当前字典名称更新覆盖；
- 图例必须与实际绘图阶段一致；
- 不做自由 Word 模板编辑器。

### 13.5 追溯和删除

- 业务停用优先使用软停用；
- 不通过删除原始结果实现离群处理；
- 配置复制不复制结果、失控状态、异常记录或报告；
- 不为了界面干净丢失历史记录。

## 14. 代码结构与优先复用位置

| 位置 | 用途 |
|---|---|
| `app.py` | 总路由与全局页面状态 |
| `database.py` | 初始化、迁移入口和现有数据访问接口 |
| `migrations/v1_1_master_data.py` | V1.1 字典、模板和批号配置迁移 |
| `migrations/v1_2_workbench.py` | 工作台绑定迁移 |
| `services/master_data_service.py` | 字典服务 |
| `services/project_config_service.py` | 模板、批号、复制、校验和快照 |
| `services/project_config_io_service.py` | 配置 XLSX 导入导出 |
| `services/workbench_config_service.py` | 新版配置到 LJ 运行时绑定 |
| `pages/master_data_page.py` | 基础资料全局页 |
| `pages/project_management_page.py` | 新版项目/批次管理全局页 |
| `pages/lj_config_section.py` | LJ 新版配置选择 |
| `pages/lj_page.py` | LJ 工作台页面编排 |
| `pages/zscore_page.py` | Z-score 工作台页面编排 |
| `pages/instant_page.py` | 即时法工作台 |
| `services/report_service.py` | 报告数据和历史能力 |
| `services/report_pdf_layout.py` | 固定 PDF 排版 |
| `ui/common.py` | 公共 UI 和全局入口 |

复杂业务判断优先放入 service/logic 层，不要继续把页面文件变重。

## 15. 数据库和迁移

- V1.1 和 V1.2 都是幂等增量迁移，由 `database.init_db()` 自动调用；
- 不需要手工执行 SQL；
- 新版配置和运行绑定使用同一个 SQLite 数据库；
- 数据库文件不应提交到 Git；
- 版本化的基础词库 CSV 应提交；
- macOS 默认数据库路径为 `~/.ljqcapp/qc_lj_app.db`；
- Windows 默认数据库路径为 `%LOCALAPPDATA%/LJQCApp/qc_lj_app.db`。

本阶段不需要旧测试数据。新电脑可以从空数据库启动，迁移和种子会自动建立新模型。

## 16. 在 Mac 上拉取并运行

### 16.1 获取代码

```bash
git clone https://github.com/Arcjang999/LJQCapp_clean.git
cd LJQCapp_clean
git fetch origin
git switch --track origin/codex/v1-2-lj-integration
```

如果本地已经存在该分支：

```bash
git switch codex/v1-2-lj-integration
git pull --ff-only
```

### 16.2 创建环境

建议先使用普通 Python 虚拟环境从源码开发，不要尝试直接运行 Windows 打包文件。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 16.3 启动

```bash
python -m streamlit run app.py --server.port 8501
```

然后访问 `http://localhost:8501`。

也可以运行：

```bash
python run_app.py
```

但当前 `run_app.py` 和 `packaging/LJQCApp.spec` 主要按 Windows 打包环境维护。Mac 开发阶段优先使用前面的 Streamlit 命令。

### 16.4 当前 Mac 已知边界

- 尚未生成或验证 macOS `.app` 安装包；
- `packaging/LJQCApp.spec` 是 Windows 打包基线；
- `.bat` 启动、重置和演示数据脚本不能在 macOS 直接运行，应执行对应 Python 命令；
- 设置页的“打开文件夹”辅助函数目前偏 Windows，实现 macOS 正式包前需要改为调用系统 `open`；
- PDF 中文字体需要在 Mac 上单独做一次实际导出检查；
- 原生目录选择依赖 PySide6，首次安装后应验证设置页目录选择、数据库迁移和恢复。

这些边界不会阻止 Codex 在 Mac 上继续源码开发、运行 Streamlit 或执行绝大多数 smoke tests。

## 17. 新电脑首次基线检查

先不要继续开发，按以下顺序确认仓库完整：

```bash
git status --short --branch
git log -1 --oneline --decorate
python -m py_compile app.py database.py qc_logic.py plotting.py zscore_logic.py zscore_plotting.py run_app.py
```

然后运行当前关键 smoke tests：

```bash
python tests/master_data_smoke_test.py
python tests/project_config_io_smoke_test.py
python tests/project_management_v11_smoke_test.py
python tests/lj_v12_integration_smoke_test.py
python tests/building_outlier_smoke_test.py
python tests/lj_monthly_report_smoke_test.py
python tests/instant_smoke_test.py
python tests/zscore_smoke_test.py
python tests/zscore_monthly_report_smoke_test.py
python tests/report_history_smoke_test.py
python tests/settings_smoke_test.py
python tests/storage_smoke_test.py
python tests/results_migration_smoke_test.py
```

不要直接运行 `tests/demo_data_current_db_smoke_test.py`，除非已经确认它使用隔离数据库；它的名称表示可能接触当前配置数据库。

## 18. 最近一次已完成验证

在 Windows 开发机上已经完成：

- 所有应用源码 `py_compile`；
- V1.1 基础资料、项目管理和 XLSX 导入导出 smoke tests；
- V1.2 LJ 绑定和页面 smoke tests；
- LJ 建靶离群维护和月报 smoke tests；
- 即时法 15 条 smoke tests；
- Z-score 49 条 smoke tests和月报测试；
- 报告历史、设置、存储和结果迁移测试；
- 浏览器手工检查全局入口、新版 LJ 选择、当前批次上下文；
- 有效视口宽度 1280px 下无横向页面溢出。

Mac 上仍需重新运行第 17 节的检查，不能把 Windows 通过直接等同于 Mac 已通过。

## 19. 给 Mac 上 Codex 的接手提示

可把下面整段作为新任务的第一条消息：

```text
请先阅读仓库根目录 AGENTS.md、docs/DEVELOPMENT_HANDOFF.md、
docs/v1_1_dictionary_project_management_spec.md 和
docs/v1_2_lj_workbench_integration_spec.md。

当前应在 codex/v1-2-lj-integration 分支继续。先检查 git 状态、安装依赖，
运行 DEVELOPMENT_HANDOFF.md 第 17 节的基线测试并报告 macOS 特有问题。
不要迁移普通旧项目或旧测试数据，不要重写 LJ/Z-score/即时法计算核心。

下一项开发任务是 V1.2 Z-score 接入：复用 qc_workbench_bindings 的适配思路，
让已启用的新版 2/3 水平 Z-score 配置进入现有工作台和月报，同时保持最终判定、
建靶维护和禁用/恢复都按整个 run 处理。第一阶段只接入本批次建靶。
修改后运行全部相关 smoke tests，并对页面选择、合并视图保持、正式期锁定和图例位置做手工回归。
```

## 20. 接手成功判定

满足以下条件即可认为另一台 Mac 的 Codex 已经接得住：

1. 能看到并切换到远端 `codex/v1-2-lj-integration` 分支；
2. 能读取四份必读文档；
3. `data/dictionaries/wst_886_2026_quantitative.csv` 存在；
4. 空数据库启动时 V1.1/V1.2 迁移成功；
5. `lj_v12_integration_smoke_test.py` 通过；
6. 能打开基础资料、项目/批次管理和 LJ 工作台；
7. 能准确复述下一项是 Z-score 新版上游接入，而不是重写计算算法。

在 Mac 实机完成这七项之前，只能确认“仓库交接材料已准备好”，不能宣称“macOS 已完整验证”。
