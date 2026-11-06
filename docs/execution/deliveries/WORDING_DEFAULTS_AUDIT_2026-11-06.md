# 全应用文案与默认内容清理

2026-11-06，本地完成。用户要求先清理全部页面、默认语言及说明中的车轱辘话，自定 Ct 线只提供方案。

## 修改

审查22个页面、共用UI、服务消息、帮助正文、建项默认内容及报告/配置导出，修改25个生产Python文件，并同步操作说明。

- 删除反复解释软件能力边界的句子，说明直接写具体用途、输入条件、保存结果和异常处理。
- 默认SD要求、CV说明、建靶提示、即时法提示、批号登记、厂家参考值、不可用入口和报告依据同步简化。
- 截图的长要求及“依据本软件已核查目录匹配项目、单位及尺度”来自 `tools/build_teaching_demo.py` 的早期建库预填，已清理生成源。后续安装仍按空业务库方案执行。
- 对已保存的已知完整系统模板，`services/terminology_service.py` 提供显示格式化。页面摘要、编辑初值、重跑缓存、新报告和配置导出使用同一格式。仅处理 `requirement_text`、`evidence`、`rationale` 三类文字字段，用户改写内容保留。
- CV数值继续取结构化限值；旧/新自动CV尾句均由原保存路径统一管理，修改限值后生成对应文字。
- 原始标准条款、内部核查资料、保存JSON、确认指纹、运行快照与原PDF保留；打开页面和生成显示副本不写回原要求。需要保存新措辞时仍走原确认流程。

## 功能边界

无数据库迁移、新依赖、公式、规则阈值或判定粒度变化。`qc_logic.py`、`zscore_logic.py`、`services/instant_service.py`仅修改提示文字。当前工作区原有即时法整组录入修改、安装更新方案和文档修改全部保留。

Ct功能没有新增控件、存储或判读逻辑。具体入口、条件、保存及历史生效方案见 [实验室自定 Ct 线方案](../CT_ALERT_LINE_PLAN_2026-11-06.md)。

## 验证

使用项目现有Python环境，所有数据写入独立临时库或 `output/wording-audit-2026-11-06/preview.db`。

14套相关检查去重81项通过。35个相关Python文件语法通过；三份核心算法文件去除文字字面量后AST与HEAD一致，证据为 `output/wording-audit-2026-11-06/source-verification.json`。

| 检查 | 结果 |
| --- | --- |
| `quality_simple_ui_smoke_test` | 12项通过，含旧预填显示、原JSON保持、人工编辑及CV变更 |
| `user_guide_smoke_test` | 3项通过，两种帮助视图与源文档一致 |
| `quality_simple_setup_smoke_test` | 8项通过 |
| `quality_targets_smoke_test` | 10项通过，按脚本入口运行，最终日志为 `output/wording-audit-2026-11-06/quality-targets.log` |
| `report_period_smoke_test` | 8项通过 |
| `quality_process_requirements_smoke_test` | 7项通过，标准准入、复制和冻结来源保持 |
| `phase_transition_20_point_smoke_test` | 7项通过，建靶/正式期边界保持 |
| `quality_process_ui_smoke_test` | 3项通过，参考要求草稿、确认及重开保持 |
| `user_interface_smoke_test` | 2项通过 |
| `reagent_entry_navigation_smoke_test` | 3项通过 |
| `zscore_record_workspace_smoke_test` | 7项通过 |
| `operation_action_feedback_smoke_test` | 8项通过，首次字体初始化超时后重跑 |
| `cv_setup`计算和非法限值场景 | 1项通过 |
| `quality_report_layout_smoke_test` | 2项通过 |

`quality_targets_smoke_test`的函数式场景按脚本入口执行，未把unittest模块加载的0项当作通过。

旧 `cv_setup` 完整UI流程仍引用已移除的 `create_material_1_1_0` 控件。用内存加载HEAD版 `ui/cv.py` 复现相同失败，确认为旧套件与当前多产品流程失配；当前质量表单的SD/CV和Ct/Log场景已通过。没有因此改回旧界面。

真实Chrome独立预览核对了“要求与文件依据”中的旧预填转换、已保存摘要及应用内“设置质量目标”帮助。预览未保存业务修改。报告输出验证生成1页PDF并用Poppler渲染，目视检查中文、换行和页面范围；同时核对配置导出中的简短要求及原JSON保持。证据在 `output/wording-audit-2026-11-06/output-verification.json` 及同目录的PDF/PNG。

未操作在用数据库、附件或原报告；未提交推送、打包或更新云端。
