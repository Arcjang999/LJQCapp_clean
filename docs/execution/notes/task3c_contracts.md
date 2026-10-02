# B3 常规批量月报实施合同与验收边界

2026-09-28：本页记录 T3C-01—06 的常规 LJ/Z-score 专项实现与证据。细卡/合并轮次总状态由执行总表收口；本页不表示临时质控、任务 3A/3B、T4 或 T5 已完成。验收全部使用新建隔离库。

## 1. 范围与稳定身份

- `services/batch_monthly_report_service.py::preview_monthly_reports(report_month, lab_instrument_id=None, template_id=None, methods=None)` 返回 `scope/items/exclusions/preview_fingerprint`。报告单元以方法、运行批次 ID、月份精确定位；同项目同月多个实际批次分列。仪器、项目按稳定 ID 筛选。
- 仅 LJ 和完整 2/3 水平 Z-score 正式期可生成。即时法原生月报、无当月正式结果、尚未确认并开始使用的批次配置等保留明确排除原因。即时法转入 LJ 后沿对应 LJ 批次处理。
- 预览、月份查询及数据包构建使用只读事务；LJ 复用既有纯计算函数，不持久化离群、评估或处理记录。`report_lot_trace` 查询检测系统时也使用只读路径。预览不建立任务或报告历史。

## 2. 资料版本与单份报告共用路径

- 单份与批量共用 `build_lj_monthly_report_package/build_zscore_monthly_report_package`、既有 PDF 生成器及归档器，批量页面不另写临床公式。
- `monthly_source_version` 冻结原始结果、每条上下文与最新判读、全部水平、参数版本、配置快照与检测系统身份、当月换批/参数事件、处理版本及独立处理报告引用、报告默认资料的指纹。批量取数与生成在一致事务中；确认前、生成前及归档前核验。公共单份归档器也在写事务内重新核验同一指纹，因此单份页、历史重新生成及批量保存均不能归档生成期间已变更的资料。单份页面会提示重新核对，并清除本次无效预览。
- 指纹保守包含该运行批次全部结果，其他月份同批资料变化也要求重新预览。生成期间不静默混用新数据；重新核对后另建任务/报告，原归档不覆盖。
- 每份 `summary_json` 保存 `source_version`、原统计/实际批号/参数追溯/质量依据、`handling_summaries` 与 `processing_candidates`。后者包括尚未登记处理的正式失控源记录，以及已登记事件的精确版本；打开后才出现事件 ID 的记录不会在旧汇总中被遗漏或被新状态替换。

## 3. 分份事务与重试

- `create_monthly_report_job(preview, selected_keys, request_id)` 在写事务内复核资料，记录清单与排除项。同请求同选择返回原任务，选择变化拒绝复用请求。
- `run_monthly_report_item(job_id, item_id)` 每份独立提交；状态为待生成、处理中、已生成、未生成。生成标记先保存，PDF、独立报告历史与成功状态同事务提交。生成/归档失败完整回滚该份，随后保存可读原因；其他成功项保留。
- 未完成项可继续或重试；已成功项直接返回原报告编号。同项并发通过写事务串行复核，不能重复归档。中断于处理中时，下次仍能继续。数据库取锁失败显示稍后重试提示，不在拿不到锁时强写失败状态。
- 新表 `qc_monthly_report_jobs/qc_monthly_report_job_items` 关联既有 `report_exports`；PDF 仍由公共 `report_export_files` 保存，不另外复制外部文件。

## 4. 下载、历史与汇总

- `read_monthly_report_item(job_id, item_id)` 读取校验通过的已归档 PDF；`build_monthly_report_zip(job_id)` 仅打包成功原件，并提供成功/失败/待生成/排除的 UTF-8 清单。下载不生成新历史。
- ZIP 名称包含项目、仪器、批号、月份和唯一任务项后缀；清理路径字符，主体限制 220 个 UTF-8 字节，避免中文文件名超出本机文件系统限制。成员时间固定为任务创建时间，同一任务状态与 PDF 对应相同 ZIP 字节。
- 单份历史入口以准确报告编号打开，不改为该批次最新报告；保留返回批量页路径。处理追溯使用 `(source_type, source_id)`，未登记事件没有 ID 时也不会互相覆盖。
- `monthly_job_summary(job_id)` 只消费成功归档摘要。LJ 按单水平记录数、Z-score 按完整检测次数计数；在控、警告、失控不因处理完成而改变。待处理检测、尚未登记处理、已登记待完成分别展示；不把事件/水平数当检测分母。
- CV 按运行批次、参数版本、水平分组，仅纳入正式期在控结果；Z-score 整次不在控时，该次其他正常水平也不进入在控 CV。复用原 `calculate_cv_percent/evaluate_cv`；保留数量、检测日数、日间条件及 SD 仅展示边界，不跨版本合并达标评价。未采用可自动评价要求、数据不足和未确认条件明确显示不能评价。
- 无执行计划不产生漏做率；不自动审核、签字或放行。临时质控不合入常规月度分母。T5 后续可复用源记录、上下文、判读及参数版本引用创建更正版；受影响报告标记/权限/更正重算仍属后续工作。

## 5. 专项证据

- `tests/batch_monthly_report_smoke_test.py`：13 组；覆盖清单全库只读、多仪器/同月多批、空月、LJ/Z2/Z3/即时法、单份统计及来源等价、部分失败/定向重试/重启、防重复请求与并发、插入历史后故障回滚、源变化前后拦截、换批事件指纹、写锁提示、未登记失控冻结、分版本 CV、SD/日间/整次检测边界、长中文文件名实际解压，以及真实 Streamlit 页面预览/生成。追加 3 组分别对 LJ/Z 验证：单份资料变化后保存失败且零历史；历史重新生成时资料变化保留原 PDF 和原历史；页面给出可读提示、无异常页、无失效下载。
- `tests/b3_report_acceptance.py`：全新库 `output/execution/B3/reports-01/acceptance.db`；真实公开服务生成 5 份代表月报，共 30 页，全部逐页渲染并目视通过。LJ 两实际批次 5+4 页，Z2 8 页，Z3 7 页，LJ 三参数版本 6 页；无裁切、重叠、空白页、缺水平、页码错误。对应 `result.json`、PDF、PNG、月份汇总与 ZIP 保留。
- LJ 三版本在控数为 2/2/1，检测日数 2/2/1；前两版 CV 均约 0.704%，第三版仅一条不能评价；合并月度 CV 留空并指向分版本附页。另测 Z3 一次检测两个水平失控，正式/失控/待处理次数均为 1。
- 原 `lj_monthly_report_smoke_test.py`、`zscore_monthly_report_smoke_test.py`、`report_history_smoke_test.py` 全部通过。
- `tests/batch_monthly_restore_smoke_test.py` 与 `output/execution/B3/restore-01`：真实内置 IgG 的 LJ/Z2/Z3，完整备份恢复、保存位置迁移并重初始化后，任务、摘要、三份 PDF 和完整 ZIP 字节一致，重试仍为三份历史，原始结果/上下文/判读/参数不变。该样本 PDF 在数据库中且无外部附件，备份为 `.db`；B2 另有含附件 ZIP 恢复证据。
- 实际浏览器链及最终本机包由主执行收口，另见 `output/execution/B3/browser-01` 与总执行证据；本页专项通过不替代最终包与发布环境验收。
