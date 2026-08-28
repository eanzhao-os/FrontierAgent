# FrontierAgent Web UI

个人使用 [FrontierAgent](https://github.com/ApodexAI/FrontierAgent) 的方式 —— 在上游仓库基础上加了一个 Web UI,用浏览器代替 TUI 来跑 ReAct / Agent Team 工作流。

> 上游项目的完整介绍、安装与配置说明见 [README.upstream.md](README.upstream.md)。

## 快速开始

```bash
./boot.sh            # 默认 ReAct 模式,端口 3030
./boot.sh --team     # 初始进入 Agent Team 模式
./boot.sh --port 8080
```

启动后自动打开 <http://localhost:3030>,工作流模式可以在 Web 界面顶栏随时切换。按 `Ctrl+C` 停止服务。

## 这个 fork 改了什么

新增一套 FastAPI + SSE 的 Web 服务,把终端里的 thoughts、tool calls、task board 和 human-in-the-loop 审批流搬到浏览器:

- `server.py` — 根目录入口
- `apodex/web_server.py` — REST / SSE 接口
- `apodex/web_observer.py` — 把 agent 事件广播到前端
- `apodex/web_static/index.html` — 内置前端页面
- `boot.sh` / `scripts/boot.sh` — 一键启动脚本(自动清理端口占用)

除上述新增外,其余代码与上游 [ApodexAI/FrontierAgent](https://github.com/ApodexAI/FrontierAgent) 保持一致。

## Web 与 TUI 的行为对等

浏览器端与终端共用同一套命令注册表(`apodex/commands.py`)与会话动作层(`apodex/session_actions.py`),TUI 能做的在 Web 里都有对应入口:斜杠命令走输入框,其余能力走右侧栏、F2 设置与快捷键。

### 命令词汇表

输入框支持全部 TUI 斜杠命令(`/help` 查看完整列表,`Ctrl-P` / `⌘K` 打开命令面板):

`/help` `/mode` `/workflow` `/model` `/settings` `/cwd` `/clear` `/new` `/fork` `/sessions` `/rename` `/plan` `/revert` `/compact` `/cost` `/context` `/config` `/init` `/resume` `/log` `/auto` `/bypass` `/autome` `/verbose` `/filter` `/find` `/report` `/copy` `/attach` `/attachments` `/detach` `/paste` `/theme` `/exit`(别名:`/quit` `/menu` `/auto-for-me`)。

任务运行中只允许 steer、中断与审批,其余命令返回 409 busy 并提示先中断。

### 浏览器快捷键

| 快捷键 | 动作 |
| --- | --- |
| `F1` / `F2` | 帮助 / 设置 |
| `Ctrl-P`、`⌘K` | 命令面板 |
| `Ctrl-B` / `Ctrl-O` | 左侧栏 / 右侧工作面板 |
| `Alt-J` / `Alt-K` / `Alt-Enter` | 审查阅读:下一条 / 上一条 / 展开折叠 |
| `Ctrl-G` / `Ctrl-Y` | 跳到报告 / 复制报告 |
| `Ctrl-.` | 中断当前任务(也可点顶栏 Stop 按钮) |
| `Ctrl-C` | 有选中文字时复制(不会中断任务);中断一律用 `Ctrl-.` 或 Stop |

### 审批决定

审批卡提供与 TUI 等价的六种决定:拒绝(默认焦点)、重定向(需填反馈)、批准、对我自动批准、本会话允许、始终允许。高危操作必须在服务端输入 `yes` 才能批准,持久允许不能越过硬拒绝。

### 附件与上传

浏览器上传限制单文件 100 MiB、单次请求 500 MiB(环境变量 `APODEX_WEB_UPLOAD_MAX_FILE_MIB` / `APODEX_WEB_UPLOAD_MAX_REQUEST_MIB` 可覆盖);更大文件用 `/attach <主机路径>` 挂载。支持拖放、粘贴与 `@` 工作区路径补全。

### 断线重连

页面先拉 `/api/state` 快照再订阅 SSE;每个事件带递增 id,断线重连按 id 补发,缺口过大时服务端发 `snapshot_required`,前端自动重新拉快照,不丢状态。

### 主题

TUI 全部配色(catppuccin、tokyo-night、dracula 等 + `mono`)在 F2 → 外观中选择,经 `UserSettings.theme` 持久化,与终端共享同一份偏好。

### `/exit` 不会关服务

Web 里的 `/exit` 只离开当前会话视图并提示;本地服务继续运行。停止服务请回到终端 `Ctrl+C`。

## License

与上游一致,Apache 2.0,见 [LICENSE](LICENSE)。
