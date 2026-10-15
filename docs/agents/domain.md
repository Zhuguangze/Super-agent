# Domain Docs

工程类 skill 在探索本仓库代码时,应如何消费本仓库的领域文档。

## Before exploring, read these

- **`CONTEXT.md`**(仓库根),或
- **`CONTEXT-MAP.md`**(仓库根,若存在):它指向每个 context 各自的 `CONTEXT.md`。读取与当前主题相关的那些。
- **`docs/adr/`**:读取与你即将改动的区域相关的 ADR。多上下文仓库还要看 `src/<context>/docs/adr/` 下的 context 级决策。

如果这些文件不存在,**静默继续**。不要指出缺失,也不要主动提议创建它们。`/domain-modeling` skill(经 `/grill-with-docs` 和 `/improve-codebase-architecture` 到达)会在术语或决策真正被敲定时惰性创建。

## File structure

本仓库是单上下文仓库:

```
/
├── CONTEXT.md                ← 领域术语表
├── docs/adr/                 ← 架构决策记录
│   ├── 0001-tool-approval.md
│   ├── 0002-pi-jsonl-session-tree.md
│   └── 0003-file-based-memory.md
├── backend/                  ← FastAPI 后端(src/super_agent/{ai,agent,app,server})
└── frontend/                 ← Vue3 + TS 前端(src/{api,stores,views,components})
```

## Use the glossary's vocabulary

当你的产出命名某个领域概念时(issue 标题、重构提案、假设、测试名),使用 `CONTEXT.md` 中定义的术语。不要漂移到术语表明确规避的同义词。

本仓库的术语表(`CONTEXT.md`)已经定义了严格词汇,包括:**Agent Loop**、**Turn**、**Steer**、**Follow-up**、**Abort**、**Tool**、**Tool Approval**、**MCP Tool**、**Session**、**Branch**、**Compaction**、**User**、**Workspace**、**Memory**、**Extension**、**Skill**、**Provider**、**AgentSession**。这些词都带 `_Avoid_` 行,列出了应避免的说法——注意区分。

如果需要的概念还没进术语表,这是个信号:要么你在发明项目不用的语言(重新考虑),要么确实存在缺口(记下来交给 `/domain-modeling`)。

## Flag ADR conflicts

如果你的产出与既有 ADR 冲突,显式指出,而不是静默覆盖:

> _与 ADR-0007(event-sourced orders)冲突,但值得重开,因为……_

本仓库有三份 ADR,其中 [ADR-0001](../../docs/adr/0001-tool-approval.md) 明确声明**"未来任何『对齐 pi』的重构建议都不应移除它"**——涉及工具审批的设计改动必须显式处理这条约束。
