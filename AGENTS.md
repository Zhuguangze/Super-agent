# Super-agent

内网多用户的通用任务 Agent:Python FastAPI 后端 + Vue3 TypeScript 前端,仿 earendil-works/pi 的架构手写 Agent Loop。

## Agent skills

### Issue tracker

Issues 与 spec 存放在 GitHub(`Zhuguangze/Super-agent`),通过 `gh` CLI 读写。参见 `docs/agents/issue-tracker.md`。

### Triage labels

使用默认的五个规范角色标签:`needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix`。参见 `docs/agents/triage-labels.md`。

### Domain docs

单上下文仓库:根目录 `CONTEXT.md` 术语表 + `docs/adr/` 架构决策记录。参见 `docs/agents/domain.md`。
