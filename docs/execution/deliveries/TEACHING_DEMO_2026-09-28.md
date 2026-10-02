# 操作资料、来源核对与验收交付

日期：2026-09-28。本文为内部技术交付记录，不在应用帮助正文中展示。业务数据、跨页关联及最终10份PDF共78页的逐页视觉检查均通过。

## 最终使用安排

用户要求在正常界面使用一套资料。唯一入口为 `启动质控软件.command` → `app.py`，固定使用8501端口，数据库为 `data/qc_lj_app.db`。使用实际当天日期，不强制历史日期；查询已准备记录时在页面选择2026-09-28。APP、项目、批次和用户操作说明不展示demo、教学、演示、模拟等标识或来源免责声明。

用户说明为 `docs/OPERATION_GUIDE.md`，通过“帮助 → 操作说明”常驻访问；原“使用说明”保留。本文及`sources.json`如实记录人工构造的数据来源，不宣称临床实测、厂家靶值或特定设备/试剂/质控品的认证适配。历史技术目录名称保留追溯用途，不构成第二套APP入口。

## 实际资料范围

- 20个目标、22个项目、24个检验项目配置，分别对应17个分子目标及ALT、TG、HbA1c；ALT与HIV1各另设初始监测项目，补齐三方法×三尺度九种组合。一个项目绑定一款质控产品；HBV同目标包含LJ、3水平和即时法三种方法。
- 24个材料组合配置、25个工作台绑定，加已转入LJ的NG，共26个工作台均已检查（原24个全查后，对新增两个作增量核查）。TG新批次待确认；RSVA新批次B双水平已有19次完整建立记录。
- 浏览器在2026-09-28 19:46:20实际保存HBV三方法一组后，共1008次检测上下文：LJ563条、Z-score324次完整检测、即时121条；Z-score共741条水平值。HBV即时现已20个有效点，可人工核对转入LJ。
- 随后追加ALT原值即时及HIV1 Log10即时各3个有效点，总检测上下文1014，即时127条；原1008次检测保留。两项当前均在控，现场第4点分别40.0 U/L、3.90 Log10。
- 6条失控处理事件：HBV两事件已完成，CMV待处理、EBV待效果确认、HSV1及HSV2处理中。HBV LJ在9:20的复测仍失控，12:00复测才在控；多水平首次复测已在控。
- 18类规则实命中，涵盖LJ、2水平、3水平每组六类规则。前后记录可追溯，恢复在控不消除当日异常经历。
- 当前输出包含8份月报、2份失控处理报告和2份月报ZIP。排版修复重出另存版本，旧历史保留；报告历史共20份（18个月报归档+2个失控报告归档），5份处理附件可读。当前成品为10份78页，精确哈希见最终PDF验收结果。

现场数值、具体菜单路径、批号、预期结果、恢复步骤和当前功能边界均在用户操作说明中。HBV录入5000、3.7/4.7/5.7及Ct31.6；HbA1c5.6/9.2；ALT40；TG1.8；RSVA B32/28.7。UU2→3、CT18→19→20、RSVB19→20和RSVA B19→20均保留现场路线。

## 产品及数据一致性

内置目录 `data/catalogs/bondson_2026_09_28.json` 含1054条正式产品、252条排除记录、0条待确认问题。原始用户源文件为`产品列表_20262328100913_1.xlsx`，SHA-256为`f9bfec9c342b6e3a274b0a7427b073c5986bfd49e0e12852e2bd43327ade0b44`。编号、名称、浓度、浓度编号四字段保留原文。

同目录编号对应同产品，不将不同单目标产品虚构为20项复合质控品。HbA1c `IQC-BI-05903`原目录含“水平1/水平2”；HBV、EBV、RSVA采用本地制备水平，未将其写成厂家新增规格或厂家赋值。

资料核对修正两处尺度不一致：HCV采用`IQC-CM-00505`（1e4—1e5 IU/mL）配合Log10均值4.5；HBV原材料假设5×10^5 IU/mL，1:100制备L1为5000 IU/mL、SD180，同L1 Log10约3.7，L2/L3为4.7/5.7。Ct31.6是独立设置的数值，不宣称由浓度自动换算。最终主库已完成这些修正。

逐产品来源为 `output/teaching-demo-2026-09-29/sources.json`，已根据最终manifest同步20项真实名称、产品代码、源表行号、实际试剂名称和官方依据。资料来源仅证明名称与技术存在；检测值、批号、资产号、参数、处理内容均为人工构造的流程资料。

## 试剂与设备来源（2026-09-28在线核对）

分子项目可参照圣湘生物真实产品，设备使用宏石SLAN-96P名称；来源证明名称和技术存在，不证明本次邦德盛材料已获该组合适配认证，不证明模拟稀释制备已经通过稳定性或互通性验证。

| 对象 | 官方来源 |
|---|---|
| 圣湘试剂总目录，含各单检项目 | https://www.sansure.com.cn/sj/index.aspx |
| HBV DNA 高敏定量试剂 | https://www.sansure.com.cn/sj/index.aspx?itemid=3&itemid1=1 |
| HCV RNA 高敏试剂 | https://www.sansure.com.cn/sj/index.aspx?itemid=8&itemid1=1 |
| HIV-1 核酸测定试剂 | https://www.sansure.com.cn/sj/index.aspx?itemid=151&itemid1=150 |
| EB病毒核酸试剂 | https://www.sansure.com.cn/sj/index.aspx?itemid=79&itemid1=78 |
| 人巨细胞病毒核酸试剂 | https://www.sansure.com.cn/sj/index.aspx?itemid=80&itemid1=78 |
| 肺炎支原体核酸试剂 | https://www.sansure.com.cn/sj/index.aspx?itemid=88&itemid1=85 |
| 肺炎衣原体核酸试剂 | https://www.sansure.com.cn/sj/index.aspx?itemid=144&itemid1=85 |
| 结核分枝杆菌核酸试剂 | https://www.sansure.com.cn/sj/index.aspx?itemid=87&itemid1=85 |
| 腺病毒核酸试剂 | https://www.sansure.com.cn/sj/index.aspx?itemid=95&itemid1=85 |
| 呼吸道合胞病毒核酸试剂 | https://www.sansure.com.cn/sj/index.aspx?itemid=145&itemid1=85 |
| HSV-1／HSV-2 核酸试剂 | https://www.sansure.com.cn/sj/index.aspx?itemid=30&itemid1=11 、https://www.sansure.com.cn/sj/index.aspx?itemid=29&itemid1=11 |
| NG／CT／UU 核酸试剂 | https://www.sansure.com.cn/sj/index.aspx?itemid=24&itemid1=11 、https://www.sansure.com.cn/sj/index.aspx?itemid=25&itemid1=11 、https://www.sansure.com.cn/sj/index.aspx?itemid=26&itemid1=11 |
| SLAN-96P官方设备名 | https://www.sansure.com.cn/yq/index.aspx?itemid=41&itemid1=39 、https://www.hongshitech.com/cpzx |
| 罗氏ALTL，IFCC法，cobas c 111，04718569190 | https://diagnostics.roche.com/global/en/products/lab/altl-ifcc-with-or-without-pyridoxal-phosphate-activation-cps-000021.html |
| 罗氏TRIGL／TG GPO-PAP，cobas c 111，04657594190 | https://diagnostics.roche.com/global/en/products/lab/trigl-cps-000263.html |
| Bio-Rad D-10 Dual Program Reorder Pack，220-0201 | https://www.bio-rad.com/webroot/web/pdf/cdg/solutions/Hemoglobin%20A1c/doc_A-210.pdf |
| Bio-Rad D-10阳离子交换HPLC方法 | https://www.bio-rad.com/sites/default/files/2024-04/DG23-0954-A-348_D-10%20BrochureUpdate_v3.pdf |

EV71单检名称在官方总目录可确认，其详情抓取失败，可只引用总目录。RSV通用试剂不能包装成已证实的分型试剂；演示A、B两种单目标质控品时应明确分型来自质控品。腺病毒3型同理。

## 已完成的核验与修复

正式库最终只读关联检查通过：全部1014次检测具有真实源记录、完整材料水平与已保存判读，项目名在总览和报告一致。证据：`formal-linkage-final/verification.json`。

原24个工作台逐页检查均有图、无报错；总览NG转入LJ准确定位批次16；巡查前后原检测值、材料映射和判读逐行相同。新增ALT/HIV1即时两个工作台有图、整组到保存前确认成功且未写入；最终4006条判读不变。证据：`formal-workbenches/verification.json`与`formal-instant-scales.json`。18类规则逐条命中证据为`formal-rules.json`。上述相对路径位于`output/teaching-demo-audit-2026-09-28/`。

服务与页面自动回归209个不同用例通过；195个既有基线/专项、4个异常搜索与跨方法筛选专项，以及10个本轮首次纳入统计的失控UI用例构成该数，重复回归不重复累计。独立失控报告链、月报恢复链、20项三方法102次整组操作链另有证据。详细范围见同目录`服务链验收说明.md`。随后新增月报摘要长字段/分页及带处理附页完整性2个专项通过，LJ/Z月报原检查重跑通过；应用入口2项通过。

真实浏览器完成HBV三方法保存、总览定位Z批次11和三水平图、名录产品IQC-CM-00107关联HBV项目及各实际水平批号、完成处理第4版/复测/附件/原报告下载入口。关键截图为`ui/formal-three-method-save.png`。先前17组UI检查输出作为阶段性回归证据保留，不作为最终入口安排的依据。

本轮实际修复包括：macOS打开文件夹、Excel日期单元格导入、数据库连接释放、正式旧值覆盖入口保护、LJ备注独立保存、即时转入LJ补存判读及总览关联、LJ/Z图初始化显示最近30天、正常入口与保存位置统一、帮助常驻操作说明，异常列表按缩写搜索及跨方法筛选项目关联，以及月报长名称越界和摘要盖住页脚。核心LJ、Z-score、即时法及CV计算算法哈希不变。

正式原值保护解决的是未完成全链更正前的覆盖问题，不能表述为历史更正/补录已实现。失控事件的新修订不会覆盖旧处理版本及旧PDF。

## 报告排版修复与最终视觉核验

初轮10份成品共70页已全部渲染并提取文本，未发现用户禁止的可见标签。HBV的LJ和3水平月报首页发现试剂/材料长名称越出表格，3水平摘要结论挤入页脚，故未将首轮认定为排版通过。

修复文件为`services/report_pdf_layout.py`：按所选字体的真实显示宽度换行，基本信息及统计表按内容高度分页，长结论分块续页，保留正常字号和原统计数据。`tests/monthly_summary_layout_smoke_test.py`同时检查LJ/Z的超长试剂、材料、70段来源依据和15段结论；逐单元格边界、正文/页脚间距以及跨页文字完整性均通过。测试样例的既有LJ月报固定5/4页改为6/5页，并明确断言第二页为“报告摘要（续）”；Z月报和三材料批号边界回归通过；额外重跑失控报告完整链、13项批量月报及报告历史全组，证明当前所有生产组页入口消费多页摘要。故障注入日志均对应预期测试，不是最终库导出失败。

最终8份月报在主库中另存为新历史版本，再输出当前PDF及ZIP。2份失控报告不改变。复核时另外发现带处理摘要的月报在`services/out_of_control_report_service.py`独立组页，仍只消费首页；已将LJ/Z两分支同步为多页摘要，并追加回归证明摘要统计、完整结论及处理附页同时保留。仅重出9月HBV两份及9月ZIP，旧历史保留。报告历史重生成、批量任务、单份页面均调用相同LJ/Z公共导出入口。最终10份成品78页全部渲染并逐页核对缩略图，复杂摘要、密集异常表及处理页按135dpi检查。中文字体、图、表、分页和页脚无裁切或遮盖；全文未出现用户禁止可见词，页边界外字符为0。8份月报均有摘要续页、统计摘要与月度结论；两份9月HBV月报均保留失控处理附页及独立报告引用，两份失控报告的复测和附件清单完整。2份ZIP各4份PDF，与当前成品SHA-256逐份一致。最终证据：`output/teaching-demo-audit-2026-09-28/final-pdf-review-complete/text-layout-audit.json`与`README.md`。

## 当前功能边界

当前不提供独立临时质控、质控品新旧批专用比对、患者样本试剂配对比对、账号登录/角色权限，以及管理员授权下的完整历史更正、补录和全链重算。已有常规材料验证、批号切换和姓名记录不等于上述能力。

数值工作台不接受纯阴阳性；Ct监测不自动作病原体定性判断。即时法需转入LJ才能生成相应正式期月报。自动质量评价限于已适用并采用的CV要求，未自动评价全部SD、偏倚、总误差或文字过程要求。

## 最终备份

最终完整备份已从真实UI创建成功：`data/backups/qc_lj_app_backup_20260928_200148.zip`。页面成功确认截图为`output/teaching-demo-audit-2026-09-28/ui/final-complete-backup.png`。备份包含最终1014次检测、处理附件和20份报告历史；ZIP内容的最终只读检查由数据验收记录。正常服务已在同一8501端口重启加载全部修复，数据保留。
