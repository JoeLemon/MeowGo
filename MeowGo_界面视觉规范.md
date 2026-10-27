# MeowGo 软件界面视觉规范（Design Tokens）

> 版本：v1.3.2 ｜ 适用：MeowGo 系列桌面端 / 移动端界面 / HTML 运行报告页 ｜ 依据：MeowGo 品牌 Logo（黑色圆润字标 + 黑猫剪影 + 暖橙点缀）
>
> v1.1 起，桌面端的实际色值、字号、间距、圆角、控件高度与图标尺寸统一收敛到 `rpa/theme.py`；本文档描述设计意图与 token 语义，代码不得再各自维护一套色值。
>
> v1.2：新增语义色深变体令牌（`*-text`，用于浅色底上的小字），并把 HTML 运行报告页纳入规范（§12），报告页色值同样只从 `rpa/theme.py` 取。
>
> v1.3：按参考图重做滚动条、下拉选择与表格三类核心组件，并统一全部按钮（§8.1 / §8.8 / §8.9 / §8.10）。新增中性控件面令牌（§2.7）；橙色继续只作品牌点缀，不再作为表格 Hover 或输入焦点的默认色。自定义滚动条、下拉字段与表格容器都必须使用这些令牌，且同时支持浅色 / 暗色主题。
>
> v1.3.1：文本输入全部改用 `uikit.TextField`，内部 Entry 使用无边框 `Field.TEntry`，每个字段只保留外层一圈圆角描边，修复下拉 / 输入框的双边框；运行设置表单改为标签左对齐、控件右对齐；步骤表「#」编号列由 44px 提升到 66px（1.5 倍）。
>
> v1.3.2：步骤表首列图标槽由 30px 加宽到 40px，`#` 编号列由 66px 收窄到 56px；所有列宽均可在表头拖动调整，并写入 `rpa_settings.json` 的 `columns`，重启后恢复上次列宽。同时修复浅色主题下日志头「运行日志 / 复制 / 清空」ghost 按钮 hover / pressed 变成浅灰的问题。

---

## 1. 品牌与设计语言

### 1.1 Logo 风格拆解

| Logo 特征 | 视觉语言提取 | 界面落地方式 |
|---|---|---|
| 黑色圆润字标 | 圆润、敦实、无尖锐折角 | 全局大圆角、粗重标题字、无衬线 |
| 黑猫剪影（猫耳 / 猫尾 / 胡须） | 亲和、俏皮、有生命力 | 空状态插画、待机动效、图标细节 |
| 暖橙点缀（字母内圆点） | 单一亮色点睛、克制 | 仅用于主操作、选中态、徽章，占比 ≤ 10% |
| 纯净白背景 | 极简、通透、留白 | 大面积留白、卡片化布局 |
| 扁平风格 | 无立体、无渐变滥用 | 扁平组件 + 低饱和柔和阴影 |

### 1.2 设计关键词

**圆润 · 亲和 · 极简 · 黑与橙的克制碰撞**

Logo 素材：`./meowgo_logo_transparent.png`（透明底，可直接用于界面顶部品牌位）

### 1.3 桌面端落地方式（v1.1）

| 层 | 文件 | 职责 |
|---|---|---|
| 设计令牌 | `rpa/theme.py` | 浅/暗色板、字体阶梯、间距、圆角、控件高度、图标尺寸、按钮配色 |
| 基础控件 | `rpa/uikit.py` | `RoundButton` / `RoundedCard` / `TextField` / `Switch` / `SearchableCombo` / `ReadonlySelect` / `SlimScrollbar`，只读令牌 |
| 主界面 | `rpa/gui.py` | 顶栏、执行组面板、步骤表、日志、状态栏与全部对话框 |
| 报告页（HTML） | `rpa/engine.py` | 运行报告索引页，色值同样注入自 `rpa/theme.py` |
| 校验 | `tests/test_theme.py` | 色板键名一致、HEX 合法、文本对比度、尺寸阶梯、按钮配色、语义文字对比度 |

> 桌面端用逻辑像素布局，再乘 `App.scale`（当前 150% DPI ≈ 1.4985）。不要把物理像素写死到控件里。

---

## 2. 色彩体系（Color）

### 2.1 品牌色（取自 Logo 实色）

| Token | 名称 | HEX | RGB | 用途 |
|---|---|---|---|---|
| `--mg-ink` | 墨黑（品牌主色） | `#080806` | (8, 8, 6) | 主文字、品牌字标、深色面 |
| `--mg-ink-soft` | 墨黑-柔和 | `#3D3A36` | (61, 58, 54) | 次级标题、深灰面 |
| `--mg-orange` | 暖橙（品牌点缀） | `#FDAD63` | (253, 174, 99) | 主操作按钮、选中态、徽章、猫元素 |
| `--mg-white` | 纯净白 | `#FFFFFF` | (255, 255, 255) | 页面背景、卡片 |

### 2.2 暖橙扩展（同色系）

| Token | 名称 | HEX | 用途 |
|---|---|---|---|
| `--mg-orange-strong` | 暖橙-深 | `#E8861F` | 按钮 Hover、橙色文字（保证对比度） |
| `--mg-orange-tint` | 暖橙-浅 | `#FFE9D2` | 选中底色、提示底色、Tag 底 |
| `--mg-orange-mist` | 暖橙-雾 | `#FFF6EC` | 大面积橙色容器底、图标底 |

> 注意：橙色为点缀色而非大面积底色。除主操作外，禁止将橙色铺满页面，避免与 Logo 的克制美学冲突。

### 2.3 暖调中性灰阶（统一偏暖，避免冷灰）

| Token | 名称 | HEX | 用途 |
|---|---|---|---|
| `--mg-bg` | 页面背景 | `#FAF9F7` | 全局页面底色 |
| `--mg-surface` | 卡片面 | `#FFFFFF` | 卡片 / 输入框 / 弹窗 |
| `--mg-surface-2` | 二级面 | `#F3F1ED` | 次级容器、导航底 |
| `--mg-border` | 边框 | `#E6E2DB` | 描边、分隔线 |
| `--mg-border-strong` | 边框-强 | `#D4CFC6` | Hover 边框、表单焦点框 |
| `--mg-text-3` | 文本-弱 | `#A8A29A` | 占位符、禁用、辅助说明 |
| `--mg-text-2` | 文本-次 | `#6B655E` | 次要信息、说明文字 |
| `--mg-text-1` | 文本-主 | `#080806` | 正文、标题（同品牌墨黑） |

### 2.4 语义色（低饱和、与暖色协调）

| Token | 语义 | HEX | 场景 |
|---|---|---|---|
| `--mg-success` | 成功 | `#3E9B6F` | 成功提示、完成状态 |
| `--mg-warning` | 警告 | `#E0A33C` | 警告提示、待处理 |
| `--mg-danger` | 错误 | `#D9534F` | 错误提示、删除操作 |
| `--mg-info` | 信息 | `#5B8DEF` | 信息提示、链接 |
| `--mg-success-text` | 成功-文字 | `#2C7755` | 浅色底（Tag / pill）上的成功文字，对比度 ≥ 4.5:1 |
| `--mg-warning-text` | 警告-文字 | `#8F5600` | 浅色底上的警告文字 |
| `--mg-danger-text` | 错误-文字 | `#B03B37` | 浅色底上的错误文字 |
| 各语义色浅底 | 提示底色 | 同色 12% 透明度 | Tag 底 / Toast 底 |

> 语义色直接作文字对比度不足（如 `#3E9B6F` 仅 3.9:1）；小字（≤12px）一律使用上面的 `*-text` 深变体。暗色模式下 `*-text` 改用提亮后的语义色本身（深色浅底上对比度已 ≥ 6:1）。

### 2.5 暗色模式

| Token | 名称 | HEX | 用途 |
|---|---|---|---|
| `--mg-dark-bg` | 暗色背景 | `#12110F` | 页面底色（暖黑） |
| `--mg-dark-surface` | 暗色卡片 | `#1D1B18` | 卡片 / 输入框 |
| `--mg-dark-surface-2` | 暗色二级面 | `#26231F` | 导航、悬浮层 |
| `--mg-dark-border` | 暗色边框 | `#35312B` | 描边 |
| `--mg-dark-text-1` | 暗色文本主 | `#F5F2ED` | 正文 |
| `--mg-dark-text-2` | 暗色文本次 | `#A8A29A` | 次要信息 |
| `--mg-dark-text-3` | 暗色文本弱 | `#6B655E` | 占位符 |
| 暗色品牌色 | 墨黑→暖白 | 文字反白；橙 `#FDAD63` 不变 | 品牌色仅在暗底提亮文字 |

**无障碍对比度要求：** 正文文本与背景对比度 ≥ 4.5:1（橙色文字请使用 `#E8861F` 而非 `#FDAD63`）；大字（≥24px）≥ 3:1。

### 2.6 配色比例

| 颜色 | 占比 | 说明 |
|---|---|---|
| 墨黑 / 深灰 | ~60% | 内容、文字、结构性元素 |
| 白 / 暖白 | ~30% | 背景、留白 |
| 暖橙 | ~10% | 仅主操作、选中、徽章、猫元素点缀 |

### 2.7 中性控件面（v1.3）

滚动条、下拉字段和表格以中性灰阶层级表达「轨道 → 表面 → 选中」；浅色下用灰白，暗色下用暖黑，不把橙色铺进控件内部。所有值来自 `rpa/theme.py` 的浅 / 暗色板。

| Token | 浅色 | 暗色 | 用途 |
|---|---|---|---|
| `SELECT_BG` | `#F0F0F0` | `#302C27` | 下拉当前项、表格选中行、菜单高亮 |
| `TABLE_HEAD_BG` | `#F7F7F7` | `#24211D` | 表头底 |
| `TABLE_HEAD_FG` | `#6B655E` | `#B8B1A8` | 表头文字 |
| `TABLE_BORDER` | `#EAEAEA` | `#35312B` | 表格容器 / 单元格细分隔线 |
| `TABLE_ROW_HOVER` | `#FAFAFA` | `#25221E` | 行 Hover（仅表格自身使用） |
| `TABLE_ROW_SELECTED` | `#F0F0F0` | `#302C27` | 表格选中行 |
| `MENU_BG` | `#F7F7F7` | `#24211D` | 下拉面板 / 菜单浮层 |
| `MENU_BORDER` | `#E6E2DB` | `#35312B` | 下拉面板描边 |
| `MENU_ROW_HOVER` | `#EFEFEF` | `#302C27` | 下拉项 Hover |
| `MENU_ROW_SELECTED` | `#E9E9E9` | `#35312B` | 下拉当前项药丸底 |
| `FIELD_FOCUS` | `#080806` | `#F5F2ED` | 输入 / 下拉字段聚焦描边 |
| `SCROLL_TRACK` | `#F2F2F2` | `#1A1815` | 滚动条圆角轨道 |
| `SCROLL_THUMB` | `#2D2D2D` | `#B8B1A8` | 滚动条滑块 |
| `SCROLL_THUMB_FG` | `#FFFFFF` | `#12110F` | 滑块上的猫爪前景 |

> 控件面与品牌橙分离：橙色只保留在主操作、类型图标和运行状态；表格 Hover、焦点描边、下拉当前项均使用上表的中性色。

---

## 3. 字体体系（Typography）

### 3.1 字体选择

| 场景 | 字体 | 说明 |
|---|---|---|
| 品牌字标 | Nunito ExtraBold / Baloo 2 / Quicksand Bold | 圆润无衬线，呼应 Logo 字形；优先使用官方字标图片 |
| 界面英文/数字 | Nunito、Inter | 圆润优先选 Nunito |
| 界面中文 | 思源黑体（Noto Sans SC）/ 小米兰亭 / 微软雅黑 | 标题用 Bold，正文用 Regular |
| 等宽（代码/日志） | JetBrains Mono / Cascadia Code | 若产品含开发或数据场景 |

> 规则：**不使用尖锐衬线字体**（如宋体、Times），标题优先粗重圆润风格。

### 3.2 字号阶梯（Type Scale）

| Token | 用途 | 字号 | 字重 | 行高 |
|---|---|---|---|---|
| `--mg-fs-caption` | 辅助说明 / 徽章 | 11px | Regular | 14px |
| `--mg-fs-note` | 次要文字 / 占位符 | 12px | Regular | 16px |
| `--mg-fs-body-s` | 正文-小 | 13px | Regular | 18px |
| `--mg-fs-body` | 正文 | 14px | Regular | 20px |
| `--mg-fs-body-l` | 正文-大 | 16px | Regular | 24px |
| `--mg-fs-subtitle` | 小标题 | 18px | SemiBold | 26px |
| `--mg-fs-title` | 区块标题 | 20px | Bold | 28px |
| `--mg-fs-h3` | 页面主标题 | 24px | Bold | 32px |
| `--mg-fs-h2` | 页面大标题 | 28px | Bold | 38px |
| `--mg-fs-h1` | 首屏 / 空状态标题 | 32px | ExtraBold | 44px |
| `--mg-fs-display` | 品牌展示 | 40–48px | ExtraBold | 1.15em |

> 桌面端正文建议 14px；移动端正文 15–16px。中英文混排行高取 1.4–1.6。

---

## 4. 形状与圆角（Radius）

圆角体系呼应 Logo 字形的"圆润"，**全局禁用直角**。

| Token | 半径 | 应用 |
|---|---|---|
| `--mg-radius-s` | 8px | 输入框、小按钮、下拉项、Tag |
| `--mg-radius-m` | 12px | 卡片、弹窗、表格容器 |
| `--mg-radius-l` | 16px | 大卡片、抽屉、页面级容器 |
| `--mg-radius-xl` | 24px | 全屏引导页、移动端大容器 |
| `--mg-radius-pill` | 999px | 胶囊按钮、头像、Tab 指示器 |

---

## 5. 间距与布局网格（Spacing & Grid）

- 基础网格：**8pt 网格**（桌面）/ 4pt 网格（移动端密集场景）。
- 间距尺度：`4 / 8 / 12 / 16 / 24 / 32 / 48 / 64px`。
- 页面内容区安全边距：桌面 24–32px，移动端 16px。
- 栅格：桌面 12 列 / 内容最大宽 1200px；移动 4 列。
- 卡片内部间距统一 16–20px，卡片间距 16px。

---

## 6. 图标与插画（Icon & Illustration）

| 类别 | 规范 |
|---|---|
| 图标风格 | 线性为主，描边 2px，**圆头端点、圆角连接**（呼应字形圆润） |
| 图标尺寸 | 16 / 20 / 24 / 32 / 48px 五档 |
| 图标颜色 | 默认 `--mg-text-2`；Hover/选中 `--mg-ink`；强调态 `--mg-orange` |
| 图标容器 | 背景容器用 `--mg-radius-m`（12px），图标底色 `--mg-orange-mist` |
| 空状态插画 | 使用像素猫图片（191×160 网格 / 130 色，源图 `images/像素猫-canvas绘制.html`，由 `rpa/pixel_cat.py` 内联解码）：坐姿黑猫、桃色内耳、暖橙项圈与鼻子；中性色随主题在 paper↔ink 间重映射，暖色移入 `--mg-orange` 色系；外圈背景透明（透出卡片底色，眼白 / 高光保留不透明），可平移到任意底色；带轻微呼吸浮动与定时眨眼 |
| 空状态动效 | 呼吸浮动约 1.5s 一次、约 2 个设计像素（随 DPI 缩放）；每 4–6s 眨一次眼，闭眼约 130ms，不抢内容焦点 |
| 徽章 / 角标 | 暖橙底白字 `#E8861F` 底 + 白字，胶囊圆角 |

---

## 7. 阴影与层级（Elevation）

| Token | 说明 | 值（浅色模式） |
|---|---|---|
| `--mg-shadow-1` | 基础浮起（卡片） | `0 1px 2px rgba(8,8,6,0.04)` |
| `--mg-shadow-2` | 悬浮（Hover / 下拉） | `0 4px 12px rgba(8,8,6,0.08)` |
| `--mg-shadow-3` | 浮层（弹窗 / Drawer） | `0 8px 24px rgba(8,8,6,0.12)` |
| `--mg-shadow-4` | 最高层（Toast / 引导） | `0 12px 32px rgba(8,8,6,0.16)` |

暗色模式阴影改为 `rgba(0,0,0,0.5)` 以上的黑色系。

---

## 8. 核心组件规范（Components）

### 8.1 按钮 Button（v1.3 统一）

桌面端所有按钮统一由 `uikit.RoundButton` 绘制，不再使用原生 `tk.Button` / `ttk.Button`。按钮由「圆角实心底 + 1px 描边 + 左侧 16px 线性图标 + 12/13px 文字」组成；尺寸取 `CONTROL_XS / SM / MD / LG`，默认圆角 `RADIUS_M`。同组按钮等高、紧凑排列，主操作只保留一个。

| 类型（kind） | 默认背景 | 文字 / 图标 | 边框 | 用途 |
|---|---|---|---|---|
| `accent`（主操作） | `ACCENT` | `INK`（浅色墨黑 / 暗色暖白） | `ACCENT_STRONG` | 运行组、确认、保存 |
| `solid`（默认次级） | `HEADING_BG` | `TEXT` | `BORDER` | 常规操作、工具按钮 |
| `outline` | `CARD` | `TEXT` | `BORDER` | 需要从卡片中区分的操作 |
| `ghost` | 透明 | `TEXT` / 次级图标 | 无 | 顶栏图标、卡片内低层级操作 |
| `danger` | `DANGER` | 浅色白 / 暗色 `INK` | `DANGER_TEXT` / `DANGER` | 删除、清空等不可逆操作 |

- Hover：`solid` / `outline` 进入 `HEADING_BG` 或 `SELECT_BG`，`ghost` 默认进入 `HEADING_BG`；位于深色日志头 / 控制台上的 ghost 按钮（「运行日志」「复制」「清空」）改用 `LOG_HEAD_HOVER`，Pressed 用 `LOG_HEAD_ACTIVE`，浅色主题下也保持深色，不能翻成浅灰。禁止用橙色整块填充表示 Hover。
- Pressed：再深一级（`SELECT_BG` / 预设 active 色），边框提升到 `BORDER_STRONG`。
- Disabled：统一 `BORDER` 底（`accent` / `danger` 暗色用 `#2A2622`）+ `TEXT_WEAK` 文字，保留 1px 边框，不响应 Hover / Pressed。
- 圆角按钮内部若只有图标，保持方形点击区与居中图标；图标颜色随按钮前景色，不单独使用橙色。
- 点击反馈仍为 0.12s 缩放至 0.97；开关 `uikit.Switch` 属于状态控件，不套用按钮配色，其橙色猫爪造型保持不变。

### 8.2 卡片 Card

- 背景 `--mg-surface`，圆角 12–16px，阴影 `--mg-shadow-1`，Hover 提升到 `--mg-shadow-2`。
- 头部：标题 16px SemiBold + 右侧操作区；内容区留白 16–20px。
- 选中态：2px 边框 `--mg-orange` + 底色 `--mg-orange-mist`。

### 8.3 输入框 Input（v1.3.1 统一）

- 桌面端普通文本输入统一使用 `uikit.TextField`：外层 `RoundedCard` 圆角外壳负责唯一一圈 1px `BORDER` 描边，内部 `ttk.Entry` 使用 `Field.TEntry`（`borderwidth=0`、无内层焦点框）；不得再在 Entry 外套第二个描边容器。
- 高 36–40px，圆角 `RADIUS_S`（8px），背景 `ENTRY_BG`；搜索框、可输入下拉、只读下拉与普通文本输入共用同一圆角外壳和视觉高度。
- 焦点：只切换外壳描边到 `FIELD_FOCUS`（浅色墨黑 / 暗色暖白），不使用橙色发光；输入内容保持 `TEXT`。
- 占位符 `--mg-text-3`；错误态边框 `--mg-danger`。
- 步骤内容、备注、分组、区域、图片目录、Webhook 等全部文本字段使用 `TextField`；搜索框把清除按钮放在同一字段外壳的右侧，不额外生成边框。
- 步骤对话框中的「指令类型」字段左侧为可搜索输入区，右侧固定猫爪按钮；聚焦 / 已有选中值时猫爪加深为 `TEXT`，空值与失焦时回到 `MUTED`。下拉直接使用 `SearchableCombo(shell=True)`，不再嵌套外层 `RoundedCard`。
- 运行设置表单：标签列左对齐，控件列统一 `sticky="e"` 右对齐；语言、倒计时、相似度、开关都贴着右侧边线，图像目录等需要伸展的行占满整个右列，形成左右两端对齐。

### 8.4 导航 Navigation

- 侧边栏：宽 240px，背景 `--mg-surface-2`；选中项：暖橙左指示条 3px + 浅橙底 `--mg-orange-tint` + 墨黑文字。
- 顶栏：高 56px，白底，下方 1px `--mg-border`；品牌位放置透明 Logo + "MeowGo" 字标。

### 8.5 标签 / 徽章 Tag

- 胶囊圆角，底色用语义色 12% 透明，文字用语义色深变体（成功 `#2C7755` / 警告 `#8F5600` / 错误 `#B03B37`，即 `--mg-*-text`）。
- 选中 / 活跃 Tag：`--mg-orange-tint` 底 + `#E8861F` 文字。

### 8.6 提示 Toast / 弹窗 Modal

- Toast：墨黑底 `rgba(8,8,6,0.92)` 白字，圆角 12px，阴影 `--mg-shadow-4`，顶部可加猫爪图标。
- Modal：圆角 16px，标题 18px SemiBold，遮罩 `rgba(8,8,6,0.4)`。

### 8.7 空状态 Empty

- 居中像素猫插画（191×160 网格，显示约 145–200px）+ 标题 `--mg-fs-h2` + 辅助文字 `--mg-text-2` + 主按钮。
- 插画表情与文案呼应（如"这里空空如也，喵～"）；待机时轻微呼吸并定时眨眼，动作幅度保持克制。

### 8.8 表格 Table（v1.3）

- 外层：`RoundedCard` 圆角容器（`RADIUS_M`），底 `CARD`、1px `TABLE_BORDER`、无外阴影；内部表格与容器内缘留 2px 呼吸位。
- 表头：`TABLE_HEAD_BG` 底、`TABLE_HEAD_FG` 13px SemiBold、左对齐、与单元格内容同起点；表头下缘由 `TABLE_BORDER` 细分隔线收口。
- 行：白 / `CARD` 底；行高 `TABLE_ROW_HEIGHT`（38px 逻辑值，按 DPI 缩放）；斑马纹仍用 `ZEBRA`（浅色 `#F7F5F1` / 暗色 `#25221E`），保持现有奇偶行约定。
- Hover：`TABLE_ROW_HOVER`；选中行：`TABLE_ROW_SELECTED`（浅色 `#F0F0F0` / 暗色 `#302C27`），文字保持 `TEXT`，不使用橙色整行。
- 首列：`#0` 为 40px 类型图标槽（v1.3.2 由 30px 加宽），图标与表头均左对齐（图标配色仍沿用 `TYPE_COLORS`，作为唯一允许的橙色点缀）；紧随其后的「#」编号列宽 56px（v1.3.2 由 66px 收窄），表头与行号同样左对齐；「类型」列保持 140px。
- 列宽：表头分隔线可拖动调整；每次松开后把 `#0 / # / 类型 / 内容 / 重复 / 备注` 六列宽度以逻辑像素写入 `rpa_settings.json` 的 `columns`（`#0` 键名为 `icon`），下次启动自动恢复。窗口变窄时仍用横向滚动条查看被挡住的列。
- 内容：主文本 13–14px `TEXT`，次要说明 `MUTED`；超长内容在列宽内截断，必要时在文本末尾显示省略号（HTML 报告页用 `text-overflow: ellipsis`，桌面表格由列宽裁切）。
- 状态：若表格含状态列，使用胶囊 pill；成功 / 失败 / 运行中分别取 `MARK_DONE` / `MARK_FAILED` / `MARK_RUNNING` 底与 `SUCCESS_TEXT` / `DANGER_TEXT` / `WARNING_TEXT` 字，圆角 pill，不改变行底色。
- 分组树与步骤表共用 `Treeview` 中性样式；表格容器不嵌套橙边，避免和卡片选中态混淆。

### 8.9 下拉选择 Select（v1.3）

**只读字段（`ReadonlySelect`）**

- 结构与输入框一致：白色 / 暗色 `ENTRY_BG` 圆角外壳、`BORDER` 1px 描边，右侧猫爪按钮；字段高度 `CONTROL_SM`。
- 焦点：外壳描边切到 `FIELD_FOCUS`；猫爪同步变为 `TEXT`；失焦后回到 `BORDER` / 当前值对应的 `TEXT` 或 `MUTED`。
- 点击字段或猫爪展开面板；语言、定时执行组、表格内联类型编辑均使用该组件，不再出现原生 `ttk.Combobox` 的白底方角下拉。

**下拉面板（`_DropdownPanel`）**

- 圆角 `RADIUS_M` 浅灰面板，底 `MENU_BG`、1px `MENU_BORDER`，可选轻微下沉阴影；内边距 6–8px。
- 行高 30px 逻辑值（按字体行高自适应）；分组标题用 `TEXT_WEAK`，不可点；选项文字用 `TEXT`，保证可读性。
- 当前项：整行 `MENU_ROW_SELECTED` 药丸底（圆角），左侧深色 `check` 图标；悬停 / 键盘当前行用 `MENU_ROW_HOVER`。
- 最多显示 6 行，超出时面板内部滚动；滚动条使用 §8.10 的 `SlimScrollbar`，面板本身不产生系统滚动条。
- 点击面板外自动收起，且这次点击仍要落到目标控件；方向键、回车、Esc 与输入过滤保持可用。

### 8.10 滚动条 Scrollbar（v1.3）

- 统一由 `uikit.SlimScrollbar` 绘制，替换 `ttk.Scrollbar`；轨道为圆角浅色胶囊，滑块为深色圆角胶囊，滑块中心有白色猫爪。
- 轨道 `SCROLL_TRACK`、滑块 `SCROLL_THUMB`、猫爪 `SCROLL_THUMB_FG`；暗色模式轨道沉入背景、滑块提亮为暖灰。
- 默认竖向宽约 10–12px（DPI 缩放）；滑块最小高度约 20px；内容不足一屏时只保留极浅轨道，不画滑块。
- Hover 滑块提亮 10%；拖动按 `moveto`、滚轮按 `scroll units` 协议驱动目标控件；日志 / 控制台可换用 `variant="console"` 的深色轨道。
- 滚动条不抢焦点（`takefocus=0`），不覆盖内容；下拉面板、表格、日志和列表统一使用该组件。

---

## 9. 动效与微交互（Motion）

| 场景 | 规格 |
|---|---|
| 标准过渡 | 240ms `cubic-bezier(0.25, 0.1, 0.25, 1)` |
| 出现 / 消失 | 200ms 淡入 + 8px 上移 |
| 按钮反馈 | 120ms 缩放 0.97 |
| 空状态待机 | 上半身小猫呼吸 1.5s 循环 + 每 4–6s 眨眼 |
| 下拉展开 | 200ms 高度展开 + 淡入 |
| 成功反馈 | 显示猫爪图标 0.6s 后收起（"喵～"） |

> 动效克制：单屏同帧动画 ≤ 2 处；尊重系统"减少动态效果"设置。

---

## 10. Logo 使用规则（Brand Usage）

- 使用透明底素材：`meowgo_logo_transparent.png`（禁止带白色底板使用）。
- 安全距离：Logo 四周留白 ≥ 字标高 50%。
- 最小尺寸：横向字标 ≥ 96px 宽；图标位 ≥ 32px。
- 禁止：改色、拉伸变形、加描边 / 投影、旋转、替换字体、叠加复杂背景。
- 深色背景上可整体反白（墨黑→暖白 `#F5F2ED`），橙色点缀保持 `#FDAD63`。

---

## 11. 落地示例配色（页面级）

| 界面区域 | 建议 |
|---|---|
| 首屏 / 登录页 | 暖白底 + 中央大 Logo（可配橙雾底图形） + 主按钮暖橙 |
| 仪表盘 | 白卡片 × 暖灰背景，关键指标卡可加橙色左条 |
| 空列表 | 黑猫插画 + 主按钮引导 |
| 品牌位 | 墨黑字标 + 暖橙圆点，如 "**MeowGo**·" 标识 |

---

## 12. HTML 运行报告页（ reports/index.html ）

运行报告索引是产品界面在浏览器里的延伸，**同样只允许使用本规范的令牌**（色值由 `rpa/theme.py` 注入，代码不得另写一套）：

| 元素 | 规范 |
|---|---|
| 整体基调 | 恒为浅色暖白板（`--mg-bg` / `--mg-surface`），不跟随桌面端暗色主题 |
| 标题区 | 「MeowGo」字标 + 暖橙圆点（`--mg-orange`），与桌面端品牌位一致；favicon 用墨黑圆角方 + 暖橙圆点 |
| 表格容器 | 白底卡片、1px `--mg-border`、圆角 12px（`--mg-radius-m`）、阴影 `--mg-shadow-1` |
| 表头 | `--mg-surface-2` 底 + `--mg-text-2` 字，12px SemiBold |
| 行 | 隔行 `--mg-zebra`；Hover `--mg-orange-mist`；数字列等宽字形（tabular-nums） |
| 链接 | `--mg-orange-strong`（保证对比度），Hover 下划线 |
| 结果 pill | 成功 `--mg-mark-done` 底 + `--mg-success-text` 字；失败 `--mg-mark-failed` + `--mg-danger-text`；已停止 `--mg-mark-running` + `--mg-warning-text`；胶囊圆角 |
| 空状态 | `--mg-text-3` 居中文案 |

> 注：§8.8 的桌面端中性表格（`TABLE_HEAD_BG` / `TABLE_ROW_SELECTED` / `TABLE_BORDER`）与报告页表格是两套落地：报告页保持浅色暖白品牌基调，仍按本表使用 `HEADING_BG` / `ZEBRA` / `ACCENT_MIST`。若未来把报告页切到中性表格，需同步修改 `rpa/engine.py` 与 §12。

---

## 附：Token 速查表

| 分类 | 关键 Token |
|---|---|
| 品牌 | `--mg-ink #080806`、`--mg-orange #FDAD63`、`--mg-white #FFFFFF` |
| 背景 | `--mg-bg #FAF9F7`、`--mg-surface #FFFFFF`、`--mg-surface-2 #F3F1ED` |
| 文本 | `--mg-text-1 #080806`、`-2 #6B655E`、`-3 #A8A29A` |
| 语义文字 | 成功 `--mg-success-text #2C7755`、警告 `--mg-warning-text #8F5600`、错误 `--mg-danger-text #B03B37` |
| 边框 | `--mg-border #E6E2DB`、`-strong #D4CFC6` |
| 控件中性面 | `SELECT_BG #F0F0F0`、`TABLE_HEAD_BG #F7F7F7`、`MENU_BG #F7F7F7`、`TABLE_BORDER #EAEAEA` |
| 控件强调 | `FIELD_FOCUS #080806`、`SCROLL_TRACK #F2F2F2`、`SCROLL_THUMB #2D2D2D`、`SCROLL_THUMB_FG #FFFFFF` |
| 深色日志头 | `LOG_HEAD_BG #1D1B18`、`LOG_HEAD_HOVER #2A2622`、`LOG_HEAD_ACTIVE #3A342C` |
| 圆角 | 8 / 12 / 16 / 24 / 999 |
| 间距 | 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64 |
| 阴影 | 4 档（见 §7） |
| 字体 | Nunito（圆润无衬线）+ 思源黑体 |

> 本文档所有色值均取自 MeowGo Logo 实色（`cutout_logo.py` 提取），可直接作为 CSS 变量 / Design Token 落地。
