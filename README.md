# Super Agent

内网多用户的通用任务 Agent：Python FastAPI 后端 + Vue3 TypeScript 前端，仿 earendil-works/pi 的架构手写 Agent Loop。

## 功能一览

- **流式对话**：adaptive thinking、逐字输出、Steer（运行中插话）/ Follow-up（结束后续跑）
- **会话**：JSONL append-only 会话树（多分支、从任意历史条目分叉）、列表/切换/删除、上下文自动压缩（compaction）
- **内置工具 10 个**：read / write / edit / grep / find / ls / fetch / bash / web_search / memory，文件类工具约束在每用户 Workspace 内（路径归一化 + 越界拒绝，含 symlink 逃逸）
- **Tool Approval**（ADR-0001）：bash 等非白名单工具执行前向前端推送审批弹窗；白名单存 settings.json
- **长期记忆**（ADR-0003）：文件式 MEMORY.md 索引 + 单事实文件，索引常驻系统提示，agent 经 memory 工具自主维护；工作区级 AGENTS.md 注入
- **扩展系统**：`extensions/` 放 Python 插件即可注册工具/命令、订阅 7 个事件钩子；坏插件隔离（示例见 `backend/examples/extensions/`）
- **MCP**：`mcp.json` 配 stdio 服务器，工具以 `mcp__<server>__<tool>` 注册，走同一审批管道
- **模型选择**：会话级模型与 thinking level 切换（对下一 run 生效）；per-user token 用量统计
- **生产打磨**：全局并发上限（排队）、429/5xx 指数退避重试、结构化日志、错误以 error 事件对前端可见

## 目录

```
backend/    Python 3.12 + FastAPI（src/super_agent/{ai,agent,app,server}，分层 import 方向 server→app→agent→ai）
frontend/   Vite + Vue3 + TS + Pinia
deploy/     Dockerfile / docker-compose.yml / systemd 单元 / .env.example
docs/adr/   架构决策记录（审批、会话格式、记忆）
```

## 开发

```bash
# 后端（Python 3.12 + uv）
cd backend && uv sync && uv run pytest -q        # 测试
uv run uvicorn super_agent.server.main:app --reload --port 8000

# 前端
cd frontend && npm install && npm run dev        # http://localhost:5173，/ws 代理到 8000
npm run type-check && npx vitest run             # 检查与测试
```

数据目录默认 `~/.super-agent/`（可用 `SUPER_AGENT_DATA` 改）：`super_agent.db`（用户与 token）、`sessions/`、`workspaces/`、`memory/`、`extensions/`、`settings.json`、`mcp.json`、`.env`。

## 内网部署

docker-compose（推荐，多阶段构建自动打包前端，**单容器即完整 UI**）：

```bash
cd Super Agent/deploy
cp .env.example .env && $EDITOR .env        # 填 ANTHROPIC_API_KEY
docker compose up -d --build                # 数据持久化在 ../data
```

或 systemd：见 `deploy/super-agent.service`。

部署后浏览器直接访问 `http://<server>:8000`——后端托管前端构建产物（`frontend/dist`，构建时 `SUPER_AGENT_FRONTEND_DIST` 指向拷贝位置）：`/assets` 走静态目录，前端路由（如 /login）刷新回落 index.html，`/api/*` 与 `/ws` 优先。

本机开发也可直接验证生产形态：`cd frontend && npm run build && cd ../backend && uv run uvicorn super_agent.server.main:app --port 8000`。

## 架构要点

- **不用 agent 框架**：Agent Loop 手写（spec §4.4）——LLM 流式响应 → 并行执行工具 → 结果回填 → 继续或结束
- **四层分工**：ai（Provider 抽象）← agent（loop/工具协议/hooks）← app（编排/持久化/审批/记忆/扩展/MCP）← server（WS/REST）；import 方向强制
- **审批与记忆是相对 pi 的两处有意增强**（多用户内网场景的爆炸半径控制与跨会话延续），见 docs/adr/
