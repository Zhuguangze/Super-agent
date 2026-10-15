# 02: 双层循环 + finishTurn 干预

**What to build:** Agent Loop 从单层循环重构为 pi 式的双层循环——外层循环在 follow-up 消息到达时续跑,内层循环处理 tool 调用与 steering 插话。follow-up 的续跑逻辑从 app 层收进 loop 内部。同时 `finish_turn` 钩子从纯通知升级为能返回 end/continue 干预循环走向(默认不干预)。

**Blocked by:** 01 (拆包)

**Status:** resolved

- [x] Agent Loop 变为外层 follow-up / 内层 tool+steer 的双层结构
- [x] follow-up 由 loop 自己续跑,app 层不再 drain follow-up 队列
- [x] `finish_turn` 钩子能返回 end(强制结束)/ continue(主动续跑),缺省时行为与现状一致
- [x] `max_turns=32` 护栏、审批、Steer/Abort 语义全部保持
- [x] 测试覆盖:多个 follow-up 自动续跑、finish_turn 返回 end 强制停止、返回 continue 主动续跑

## Answer

完成。`agent_loop` 重构为双层循环(外层 follow-up / 内层 tool+steer),follow-up 从 app 层 `run_prompt` 收进 loop 自己续跑;`finish_turn` 钩子返回值从 `None` 改为 `end`/`continue`/`None`。新增 3 个测试(finish_turn end/continue、follow-up 续跑)+ 更新 1 个旧 follow-up 测试。全量 272 passed。

