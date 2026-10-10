"""示例扩展（ticket 12）：注册 word_count 工具、ping 命令，并订阅事件。

部署：把本文件拷到全局扩展目录（默认 ~/.super-agent/extensions/）或某用户的
工作区 <workspace>/extensions/ 下即可，无需重启全局部分之外的任何东西。
工作区扩展在该用户下一次对话时自动加载。
"""

from super_agent.agent.types import AgentToolResult


def register(api) -> None:
    def word_count(tool_call_id, params, signal, on_update):
        """同步 execute 也可以——扩展壳两种形态都支持。"""
        text = str(params.get("text") or "")
        return AgentToolResult(content=f"字符数：{len(text)}")

    api.register_tool(
        name="word_count",
        description="统计给定文本的字符数（示例扩展工具）",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        execute=word_count,
    )

    async def ping(payload):
        return {"pong": str(payload.get("text") or "")}

    api.register_command("ping", ping)

    async def on_session_start(payload):
        # 事件钩子示例：v1 只记录到标准日志
        import logging

        logging.info("示例扩展：session_start %s", payload)

    api.on("session_start", on_session_start)
