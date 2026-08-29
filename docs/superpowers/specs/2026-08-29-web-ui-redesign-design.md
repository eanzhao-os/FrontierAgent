# Web UI 彻底重构设计 — 2026-08-29

目标：让 `apodex/web_static/index.html`（单文件 React-via-CDN，无构建）视觉与功能全面对齐
`~/Code/native-ai-ui` 设计体系，并补会话管理能力。

## 约束

- 单文件、无构建：不引入 Vite/Tailwind 构建/npm 依赖；native-ai-ui 的 shadcn 控件不可直接
  安装，按 `references/tokens.md` 与 `references/design-principles.md` 手工移植模式。
- 每次编辑后用 `@babel/parser`（jsx 插件）解析整个 `<script type="text/babel">` 区域。
- 界面文案保持极简：删说明性长文本，只留标签与状态。

## 1. 布局与视觉基线

对齐 `assets/preview/native-chat.html` mockup：

- 侧栏 232px、`--canvas` 底、`--line` 右边线；顶栏 48px；对话列 max-width 680px 居中。
- 卡片一律 hairline-first 阴影（`--shadow-card` / `--shadow-raised`），弹层 `--shadow-overlay`。
- 动效 100–300ms `cubic-bezier(0.23,1,0.32,1)`；`prefers-reduced-motion` 下去掉循环动画。
- 字号密度档 10.5–13.5px，等宽字体用于路径/命令/计时。
- 状态色一律 tint 对（accent/green/orange/red + tint），不用裸色块。

## 2. 会话卡片（左侧栏）

- 分区：置顶 / 活跃 / 归档（归档默认折叠，显示计数）。
- 卡片内容：标题、模式点（ReAct=accent / Team=green）、相对时间、消息数/文件数；
  运行中绿色脉冲点（Session List 模式）、未读计数徽标。
- hover 浮出操作：重命名（内联输入框，Enter 保存 / Esc 取消）、置顶/取消置顶、
  归档/恢复、删除（二次确认，命名目标）。
- 顶部搜索框即时过滤标题/工作区。
- 当前会话卡片用 `accent-tint` 底 + `accent-ink` 字。

## 3. 对话流卡片

- ToolChipRow 对齐 Tool Chips 模式：一行一个 chip、状态一眼可辨（running/done/failed）、
  时间序排列。
- ThinkingTrace 对齐 Thinking 模式：运行中 header=spinner+活跃标签，完成后 settle 为
  安静摘要（"Thought for N seconds"），可展开。
- ApprovalCard 顶部加进度 pill；危险操作保留显式确认。
- 用户消息改 `accent-tint` 气泡（右对齐，max-width 75%）。

## 4. 弹窗与设置

- 统一 modal 壳：居中卡 + `--shadow-overlay` + 统一 tab 条样式。
- SettingsModal 各 tab 重排到 token 体系；WorkspaceManager / Inspect / Help / Palette 统一。
- 删除各处的说明性段落，控件自解释。

## 5. 后端改动（最小）

- `session_state.py`：`pinned` 布尔字段（存 session.json），set/get 函数。
- `web_server.py`：`POST /api/sessions/{id}/pin`（body `{pinned}`）；`list_all_sessions`
  透出 `pinned`；`/api/actions` 加 `pin_session`/`unpin_session`。
- 重命名复用已有 `rename_session` action；归档/删除复用现有端点。
- 前端在 `/api/sessions` 刷新间保持乐观更新。

## 6. native-ai-ui issue

catalog 只有 Session List（行式 + 徽标），缺「置顶/内联重命名/归档分区/搜索」的会话
管理卡片组件。自研后将设计（分区模型、卡片解剖、交互稿）整理成 issue 提交到
`eanzhao-os/native-ai-ui`。

## 验证

1. `@babel/parser` 全脚本 JSX 解析零错误。
2. jsdom harness 挂载真实 App：会话卡片渲染、归档区折叠、重命名内联、删除确认、
   主题切换不空白。
3. `pytest apodex/tests/test_web_api.py apodex/tests/test_session_actions.py`（含新增
   pin 测试）。
