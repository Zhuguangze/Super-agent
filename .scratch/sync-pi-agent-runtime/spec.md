Status: ready-for-agent

# Spec: 同步 pi 的 agent runtime 到 Super-agent

## Problem Statement

Super-agent 的 Agent Loop([loop.py](backend/src/super_agent/agent/loop.py))相比架构参照物 pi 缺少多项健全机制,导致运行时能力有真实缺口:

- **单层循环 + follow-up 泄漏在 app 层**——loop 只处理 tool 调用和 Steer,Follow-up 的续跑逻辑散落在 app 层([chat.py](backend/src/super_agent/app/chat.py) 的 `run_prompt`),loop 不知道 follow-up 的存在
- **无 truncated-response guard**——LLM 输出被 token 上限截断时(`stopReason="length"`),截断的工具参数仍会被执行,可能产生错误副作用
- **无批次终止**——审批(ADR-0001)拒绝一个危险工具时,同批其余工具仍继续执行
- **无两级队列**——Steer 与 Follow-up 混在一个队列里,排空模式不可控
- **`finish_turn` 是纯通知**——不能干预循环走向(end/continue)
- **模块解耦不足**——agent 层是平的 4 个文件,难以针对性修改单一职责

## Solution

把 pi 的 agent runtime 核心能力完整同步到 Super-agent,同时把 agent 层按职责拆成子包。分两期实施,一期解决结构缺陷(双层循环、truncated guard、terminate、两级队列、finishTurn 干预),二期做增强(嵌套调用、prepare 扩展点、细粒度信号)。**保持 Super-agent 自身的护栏不变**——`max_turns=32`、审批(ADR-0001)、Steer/Abort 语义全部保留。

## User Stories

1. 作为开发者,我想要 agent 层按职责拆成 `loop/`、`queue/`、`execution/` 子包,以便改队列只碰 `queue/`、改执行只碰 `execution/`、改循环只碰 `loop/`
2. 作为开发者,我想要 `_drain` 只在 `queue/` 定义一次(现在 [loop.py](backend/src/super_agent/agent/loop.py) 和 [chat.py](backend/src/super_agent/app/chat.py) 各有一份重复),以便消除重复实现
3. 作为 agent 运行时,我想要外层 follow-up / 内层 tool+steer 的双层循环,以便 follow-up 收进 loop、app 层变薄
4. 作为 agent 运行时,我想要 `finish_turn` 钩子能返回 end/continue,以便干预循环走向
5. 作为 agent 运行时,我想要在 `stopReason="length"` 时把整批工具标失败不执行,以便模型重发完整参数而非执行截断参数
6. 作为 agent 运行时,我想要 `terminate` 批次终止(全票才生效),以便审批拒绝危险工具时整批停止
7. 作为 agent 运行时,我想要 steering 与 follow-up 分离成两级队列,以便运行中插话和结束后续跑语义清晰
8. 作为 agent 运行时,我想要 `all`/`one-at-a-time` 两种排空模式,以便控制每轮注入的消息条数
9. 作为用户,我想要看到 turn 计数/状态,以便知道 agent 跑到第几轮
10. 作为用户,我想要在 truncated guard 触发时看到可见提示,以便知道模型正在重发完整参数
11. 作为用户,我想要看到 steering 队列的待处理状态,以便知道还有多少插话待消费
12. 作为用户,我想要看到 finishTurn continue 触发的主动续跑,以便知道 agent 在继续而非卡住
13. 作为多用户内网部署者,我想要 `max_turns=32` 护栏保留,以便防止失控会话拖垮全局并发
14. 作为维护者,我想要审批(ADR-0001)逻辑一行不动,以便守住"对齐 pi 的重构不应移除审批"的 ADR 承诺

## Implementation Decisions

- **模块划分(按职责拆子包)**:`agent/` 拆成 `loop/`(循环 + turn)、`queue/`(两级队列)、`execution/`(工具执行 + truncated guard + terminate),`hooks.py`、`types.py`、`tools.py` 保持
- **循环结构**:完整双层循环(外层 follow-up / 内层 tool+steer),follow-up 从 app 层收进 loop
- **迁移策略**:拆包先行——先做纯结构重构(行为零变化、测试全绿),再逐块搬能力
- **`finish_turn` 返回值语义**:从 `-> None` 改为返回 end/continue,默认"不干预"
- **terminate 语义**:整批所有结果都设 terminate 才终止(全票,`every` 不是 `some`),避免单个拒绝误杀整批
- **truncated guard**:仅当 `stopReason="length"` 时触发,把该 message 的所有 tool call 标失败不执行
- **`max_turns=32` 保留**:pi 的无限循环不适合多用户内网,保留作为安全网
- **审批(ADR-0001)不动**:审批钩子仍走 before_tool_call,拒绝语义不变,terminate 是新增的可选能力
- **前端事件消费**:`turn_start`/`turn_end`/`agent_start`/`compaction_start` 从 declared-but-ignored 变为被消费
- **新领域术语**:引入 `terminate`(批次终止)进 [CONTEXT.md](CONTEXT.md) 术语表

## Testing Decisions

- **测外部行为,不测实现细节**:断言事件序列和结果,不断言内部函数调用
- **主 seam = `agent_loop` 函数边界**:五块能力(双层循环、finishTurn、truncated guard、terminate、两级队列)全部在这个纯函数 seam 上测,用 fake provider + fake tool + 收集事件,不碰 WS/app/前端
- **配套 seam**:
  - `ChatService.run_prompt`——follow-up 收进 loop 后 app 层行为变化
  - WS 事件流——turn 事件新增、finishTurn continue 下发
  - 前端 `chat.ts` handleEvent——消费 turn/steering/compaction 事件
- **prior art**:[test_agent_loop.py](backend/tests/test_agent_loop.py) 的 `ScriptedLoopProvider` + `EchoTool` + `_collect` 模式是主 seam 的既有样板;前端 [chat-store.test.ts](frontend/src/__tests__/chat-store.test.ts) 是前端 seam 的样板

## Out of Scope

- **嵌套工具调用**(二期)——工具调工具、递归走钩子
- **prepareNextTurn / prepareRequest 扩展点**(二期)——每次请求前/turn 后换 model、thinking level
- **细粒度 AbortSignal**(二期)——贯穿 hook/tool/stream 的可取消信号
- **pi 的 durable 崩溃恢复 / 存储后端 / SSH 远程执行环境**——Super-agent 已有 JSONL session_store + 单机 workspace 等价物,不照搬
- **codemode 沙箱**——独立的另一项能力,不属本次 runtime 同步

## Further Notes

- 二期能力在拆包时预留接口,但不实现
- 分两期是为了控制 diff 规模和风险;一期是结构缺陷修复,二期是增强
- `terminate` 与审批(ADR-0001)是天然协同,一期一并落地
