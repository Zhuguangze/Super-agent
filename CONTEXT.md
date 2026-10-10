# Super Agent

仿照 pi 架构自建的通用任务 Agent：Python (FastAPI) 后端 + Vue3 (TypeScript) 前端，部署于内网服务器、多用户使用。编码只是其工具能力之一，不是产品边界。

## Language

### 定位

**Super Agent**:
本项目构建的通用任务 Agent——通过工具系统获得能力、能接任意任务的桌面级 Agent。
_Avoid_: 超级助手, AI 助手

**pi**:
架构参照物（earendil-works/pi，TypeScript 编码 agent）。本项目的分层与术语命名对齐它的设计，但不照搬其编码专属行为。
_Avoid_: pi-mono

### 运行时

**Agent Loop**:
核心循环：接收 prompt → 流式调用 LLM → 执行工具调用 → 结果回填 → 决定继续或结束。
_Avoid_: 主循环, while 循环

**Turn**:
一次完整的 prompt → LLM 响应 → 工具执行 周期；Agent Loop 由一或多个 Turn 组成。
_Avoid_: 轮次, round

**Steer**:
Agent 运行中插入的用户消息；进入 steering 队列，在当前 Turn 的工具执行完成后被消费。
_Avoid_: 打断（打断是 Abort）

**Follow-up**:
Agent 结束运行后排队、自动触发下一个 Turn 的用户消息。
_Avoid_: 追问

**Abort**:
用户主动终止当前运行。
_Avoid_: stop, cancel

### 工具

**Tool**:
供 LLM 调用的能力单元：schema 定义参数，异步执行，默认并行。
_Avoid_: function, skill

**Tool Approval**:
危险工具（如 bash）执行前推送到前端、需用户确认的内置机制；白名单可按工具/会话配置，关掉白名单即回到 pi 的无审批行为。
_Avoid_: 权限系统, permission system（本项目只有单层审批，无 RBAC）

**MCP Tool**:
由 MCP 服务器（stdio 子进程或 HTTP/SSE 远程端点）提供、注册进与内置 Tool 同一管道的外部工具。
_Avoid_: 插件工具, external tool

### 会话

**Session**:
一次完整对话的持久化记录：append-only JSONL 树，条目带 `id`/`parentId`，支持分支与 Compaction；重放分支即可重建完整上下文。
_Avoid_: 聊天记录, chat, conversation

**Branch**:
从 Session 树已有条目分叉出的新路径；活跃分支即到叶子的路径。
_Avoid_: fork

**Compaction**:
将 Session 较早内容替换为摘要以腾出上下文窗口的机制。
_Avoid_: 总结

### 记忆与多用户

**User**:
通过认证使用 Super Agent 的人；会话、记忆、工作区均按 User 隔离。
_Avoid_: 账号, member

**Workspace**:
分配给 User 的工作目录，是 bash 与文件工具的执行边界；v1 只有目录级隔离，无 OS 级沙箱。
_Avoid_: 沙箱

**Memory**:
按 User 隔离的文件式长期记忆：MEMORY.md 索引 + 单事实文件，由 agent 通过 memory 工具读写；分用户级与工作区级两层。
_Avoid_: 知识库, RAG

### 扩展与边界

**Extension**:
`extensions/` 目录下的 Python 插件模块，可注册 Tool 与命令、订阅关键事件钩子；命名对齐 pi 的 ExtensionAPI。
_Avoid_: 插件, plugin（hook 指单个事件回调，不指插件整体）

**Skill**:
声明式知识包（SKILL.md 加可选附属资源）：教 agent 怎么做好某类任务；索引常驻系统提示，全文由 agent 经 load_skill 工具按需加载；分全局级与工作区级两层，工作区同名覆盖全局。与 Extension（代码级行为）、MCP Tool（进程级工具）三分互补。
_Avoid_: 提示词模板, prompt template

**Provider**:
LLM API 供应方的统一抽象；v1 只接 Anthropic，但接口从第一天起按多 Provider 形态设计。
_Avoid_: 厂商, vendor

**AgentSession**:
前端边界的核心服务（pi 同名概念的后端等价物）：持有会话状态、向外发射统一事件流、接受命令。实时通道走 WebSocket，查询走 REST。
_Avoid_: controller, manager
