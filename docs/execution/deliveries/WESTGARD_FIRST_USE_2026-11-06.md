# 首次质控方法说明修订

2026-11-06。用户指出原文“单水平：每次录入一个水平；多水平：同一次录入全部水平”只在重复录入方式，要求依据 Westgard 相关说明撰写。

## 实际修改

- 首次选择改为两个独立说明段：单水平使用一个浓度水平的均值、SD建立 L-J 图并观察连续结果；多水平按各自均值和SD评价不同浓度，结合本次和连续检测结果按启用规则综合判读。保留无默认选择和首项方法可改的原流程。
- 首次选择和新手教程共用 `METHOD_DESCRIPTIONS`。教程补各水平参数独立、Z标准化、1₂s警告和保留原始结果/处理记录的含义；方法说明前置，操作步骤保留。帮助页列出 Westgard 来源，两份用户说明同步。
- 文案依据：[Westgard L-J 图](https://www.westgard.com/lessons/basic-qc-practices/lesson12.html)、[统计计算与 Z 值](https://www.westgard.com/lessons/basic-qc-practices/lesson14.html)、[多规则判读](https://www.westgard.com/lessons/basic-qc-practices/lesson18.html)。水平数与多规则分别说明，不以“单水平”等同于“单规则”。
- `release.json` 构建号更新为 **2026110602**，版本仍1.0.0、资料格式仍1。没有判读算法或数据库变更；没有操作在用资料、提交推送或部署。

## 验证

相关4套去重33项通过：首次引导UI19、教程8、使用说明3、入口3；无失败/跳过或数据库守卫命中。初次使用说明测试发现目录引入重复首次章节锚点（19链接对18章节），已去掉目录正文的重复链接，最终3项通过；原失败保留，没有删改测试绕过。

实际 Chrome 独立8521、全新资料根检查：无默认选择/继续禁用、两段方法文字正常、帮助两种教程、各水平mean/SD与Z公式、Westgard来源链接均可见；10张业务表全0。页面截图和回执在 `output/westgard-copy-2026-11-06/browser-qa/`。返回按钮已点击但关页前未等待切换确认，该项仍以已有AppTest结果为据，不冒称本轮浏览器已确认返回。预览和标签页已关闭。

首次Markdown渲染发现粗体结束标记与冒号/正文邻接导致字面量显示，实际浏览器确认后改为标签单独加粗；因此第一份02中间构建已标记拒绝交付。最终产物收录修正后的字节。

## 替代旧交付的最终产物

Mac：

`output/installers/1.0.0-2026110602-macos-arm64-20261106T054636Z/LJQCApp-1.0.0-2026110602-macos-arm64-pending-signing.dmg`

333,549,762字节；SHA-256 `d5f8cbea3e2c701fc5ba0e4c6d076d621f98bdf36b9a226e2f522e04ea8da431`。

Windows构建资料：

`output/installers/1.0.0-2026110602-windows-source-kit-20261106T054637Z-e9a6dcea/LJQCApp-1.0.0-2026110602-windows-source-kit.zip`

3,659,028字节；SHA-256 `7bd0ec0d3b834f63fd4fb094e186c5eb3895e105cd18828028d16327eaa55b60`。

两端固定快照摘要相同：`2f0b67b38d359805ab5a7cc4bd023fd2b53208133b6d1f466e0aa46ce7e6ab7e`，完整Git基线1b1a717，`source_dirty=true`。最终资源与快照逐字节一致、两文件SHA复验通过。Windows传入本机构建应使用此02 ZIP，替代原01构建资料。

Mac通过DMG校验/只读挂载/独立安装、严格递归签名校验、主程序和服务arm64、内外metadata一致、最终onboarding/tutorial资源一致、版本命令及实际Qt正常开关（6.89秒）。10张业务表全0，退出后端口关闭、资料锁释放且关闭请求清理。仅本地ad-hoc签名，Apple Developer ID与公证仍未配置；Windows尚无原生Setup/安装验收。

汇总 `output/desktop-westgard-2026-11-06/installer-refresh.json`，Mac详细结果在最终stage的 `frozen-install-verification.json`。

## 规则专项待处理

对照还发现既有单水平R₄s跨次使用、多水平2₂s覆盖子集及阈值等号差异，已用内存输入核对并记录于[规则范围审查](../notes/WESTGARD_RULE_SCOPE_AUDIT_2026-11-06.md)。本轮不改判读/历史重算，不能将这些待验收包称为完整Westgard符合性验证通过。下载清单仍未发布，在线更新器仍未实现。
