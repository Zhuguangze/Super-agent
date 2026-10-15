# 04: truncated guard + terminate 批次终止

**What to build:** 两件事——(1) 当 LLM 响应因 token 上限被截断(stopReason 为 length)时,该消息里的所有工具调用都标失败且**不执行**,让模型重发完整参数;(2) 审批钩子返回值增强,拒绝一个工具时可标记 terminate,当整批所有工具结果都设了 terminate 时才提前结束批次(全票,非一票)。

**Blocked by:** 02 (双层循环)

**Status:** resolved

- [x] stopReason 为 max_tokens 时,该消息的全部工具调用被标记失败、不执行,模型收到失败反馈后可重发
- [x] before_tool_call 钩子返回值支持 terminate 标记(与现有 str 拒绝原因并存)
- [x] 批次终止只在整批所有结果都设 terminate 时生效,单个拒绝不误杀整批
- [x] 审批拒绝危险工具时,可选 terminate 整批(与审批 ADR-0001 协同)
- [x] 测试覆盖:截断响应工具不执行、全票 terminate 终止批次、单票不终止

## Answer

完成。截断判断用 `stop_reason == "max_tokens"`(openai_compat 已把 length 映射为此,anthropic 透传同值),截断时整批工具标失败不执行。`before_tool_call` 返回类型扩展为 `str | ToolCallBlock | None`,`ToolCallBlock` 携带 `terminate` 标记;`_execute_tools` 返回 `(blocks, should_terminate)`,全票才终止。审批 `ApprovalService` 加 `terminate_on_reject` 开关(默认 False 保持现状),拒绝时可选 terminate 整批。新增 3 个测试,全量 277 passed。

