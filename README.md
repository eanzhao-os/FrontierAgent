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

## License

与上游一致,Apache 2.0,见 [LICENSE](LICENSE)。
