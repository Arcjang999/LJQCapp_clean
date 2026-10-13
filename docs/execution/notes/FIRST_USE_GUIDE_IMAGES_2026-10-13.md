# 操作说明配图标注

三张图以实际app.py页面截图为输入，使用内置image_gen编辑工具添加编号和箭头。原始截图留在本地output/user-onboarding-2026-10-13。核对标注位置、关键字段、数值、按钮名称后选用以下资源。

## 01-navigation

保存：docs/images/first-use/01-navigation.png

Use case: precise-object-edit. Edit target: this exact screenshot of Chinese laboratory QC software. Create a user manual screenshot by ONLY adding three small red numbered circles and thin red arrows. Preserve the screenshot original content, all Chinese text, tables, buttons, colors, watermark, and layout exactly. Do not redraw or redesign the interface. Keep full screenshot framing and high resolution so all original text remains readable. Annotation 1: circle 1 adjacent to the 系统设置 button on top right, arrow points precisely at that button. Annotation 2: circle 2 beside the open 资料与批次 menu, arrow points at the dropdown header 资料与批次, and a fine red rectangular outline groups its 厂家管理/仪器管理/试剂管理/基础资料 first four entries. Annotation 3: circle 3 immediately below the top 质控品管理 button, arrow points at that button. Circles and arrows must occupy blank space, never cover Chinese characters. Add only numbers 1,2,3 as new text; instructions will be provided separately. Invariants: every underlying screenshot pixel, original UI text and button labels remain unchanged; do not add decorative elements.

## 02-new-project

保存：docs/images/first-use/02-new-project.png

Use case: precise-object-edit. Edit target: this exact screenshot of Chinese laboratory QC software 新建项目 dialog. Produce a manual screenshot by ONLY adding three small red numbered circles and thin red arrows. Keep original full screenshot, dialog fields and text perfectly unchanged, every Chinese label and value readable, no interface redraw, no redesign. Annotation 1: a red number 1 in a small circle just left of 检验项目 field, arrow toward 检验项目 selector; optionally thin red rectangle surrounds the upper fields 检验项目/仪器/试剂/方法学/单位. Annotation 2: small red circle 2 in blank space alongside 试剂与质控品 section in lower part of dialog, arrow points at section heading and blue hint 先选择仪器和试剂，再核对质控品. Annotation 3: red circle 3 directly beside bottom blue button 下一步：核对质量要求, arrow points at button. Place annotations in margins without covering words. Add only numbers 1,2,3; no new Chinese text or banners. Preserve all original screenshot text and buttons exactly and full original framing.

## 03-quality-standard

保存：docs/images/first-use/03-quality-standard.png

Use case: precise-object-edit. Edit target: exact screenshot of Chinese laboratory QC software 质量目标设置 dialog. Make user manual screenshot by ONLY adding three small red circles numbered 1,2,3 with thin red arrows. Preserve entire original screenshot, every Chinese character, numeric value, button label and layout unchanged. No redraw or redesign. Annotation 1 circle in blank margin points to 核对检测条件 collapsed row near top of modal. Annotation 2 circle in blank margin points to 引用现行数值标准 area with WS/T 403—2024 / 2024 · HbA1c (NGSP单位) and 允许不精密度：2.0%; a thin red rectangle can surround this area without obscuring words. Annotation 3 circle in blank margin near lower right points to 保存质量目标 button; fine red outline groups 确认人 field, 核对 checkbox and the bottom save controls, leaving text readable. Only new text is numbers 1,2,3. Maintain full original screenshot framing and resolution. All UI text/buttons/values remain identical, annotations must never obscure Chinese labels.


## 最终配图更新

全系统文案修改后，02/03从实际页面重新截图，只拍弹窗及标注所需边距。最终采用以下内置编辑提示，按钮与蓝色提示已按当前页面核对。01沿用原入口截图。

### 02-new-project

输入：new-project-crop-v2.jpg；保存：docs/images/first-use/02-new-project.png

Use case: precise-object-edit. Edit target: this EXACT cropped screenshot of the real Chinese QC software 新建项目 dialog. Add only three small red numbered circles and arrows as manual annotations. Preserve the full original screenshot framing, every Chinese label, number, button, spacing and original pixel content with high fidelity. Circle 1 placed in left margin, arrow to 检验项目 selector. Circle 2 in left margin, arrow to 试剂与质控品 heading and blue hint area. Circle 3 beside bottom blue 下一步：设置质量要求 button, arrow points at the button. All circles outside text. New text only digits 1, 2, 3. Critical exact original hint must stay verbatim: 选择仪器和试剂后，在这里选择本次使用的质控品。 Critical exact button: 下一步：设置质量要求. Do not change interface, truncate text, add fields, fill any fields, or alter labels. Retain all empty controls, checked 单水平（LJ） and effective points 20. Keep screenshot sharp, annotations thin and easy to read.

### 03-quality-standard

输入：quality-standard-crop-v2.jpg；保存：docs/images/first-use/03-quality-standard.png

Use case: precise-object-edit. Edit target: this EXACT cropped screenshot of real Chinese QC software 质量目标设置 dialog. Add only three red numbered small circles and thin arrows for user manual. Preserve original screenshot framing, Chinese text, numeric values, buttons and layout with high fidelity; original pixel content untouched except overlay annotations. Circle 1 in left margin points at 检测条件 row. Circle 2 in left margin points at 引用现行数值标准; thin red rectangle groups title, WS/T 403—2024 / 2024 · HbA1c（NGSP单位）and 允许不精密度：2.0%. Circle 3 in lower right blank margin points at 保存质量目标 button. Keep the 确认人 field and checkbox readable. New text only digits 1,2,3. Critical checkbox exact text: 已确认检测方法、单位、各水平和以上质量要求填写正确. Critical buttons: 保存，稍后继续 and 保存质量目标. Every original label and value must remain identical; do not redraw, invent fields, fill fields, check checkbox, or add decoration. Keep screenshot sharp and full dialog visible.


### 01-navigation 最终裁图

输入：navigation-crop-v2.jpg；保存：docs/images/first-use/01-navigation.png。仅保留导航入口和菜单，图片按1400宽度显示并由小窗口自动收窄，可点击放大。

Use case: precise-object-edit. Edit target: this exact cropped screenshot of real Chinese QC software navigation. Create a manual illustration by adding ONLY three small red numbered circles and thin red arrows. All original buttons, menu text, colors, layout and watermark must remain perfectly preserved and readable. Keep full original framing. Circle 1 in blank top margin points precisely to 系统设置 (top right). Circle 2 in blank margin points to 资料与批次 (open dropdown header near top middle); thin red rectangular outline groups its first four entries 厂家管理、仪器管理、试剂管理、基础资料. Circle 3 in blank top margin points to 质控品管理 (top left). New text only numbers 1,2,3. Never obscure words or redraw the underlying interface. Keep all original screenshot text exact, including 项目/批次管理、质量目标、异常处理 and existing buttons. Do not fill, move, remove or invent UI. If needed add a narrow blank top margin for number circles, but preserve full UI content. Keep result sharp at high resolution.
