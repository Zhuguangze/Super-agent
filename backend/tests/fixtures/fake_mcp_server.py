"""假 MCP stdio 服务器（测试 fixture）——newline-delimited JSON-RPC 最小实现。

工具面：
- echo {text}：回显文本
- fail {}：返回 isError 结果
"""
import json
import sys


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = message.get("method")
        req_id = message.get("id")
        if req_id is None:
            continue  # 通知（如 notifications/initialized）
        if method == "initialize":
            send({
                "jsonrpc": "2.0", "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake", "version": "0.1"},
                },
            })
        elif method == "tools/list":
            send({
                "jsonrpc": "2.0", "id": req_id,
                "result": {
                    "tools": [
                        {
                            "name": "echo",
                            "description": "回显文本",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"text": {"type": "string"}},
                                "required": ["text"],
                            },
                        },
                        {"name": "fail", "description": "总是失败", "inputSchema": {"type": "object", "properties": {}}},
                    ]
                },
            })
        elif method == "tools/call":
            name = (message.get("params") or {}).get("name")
            args = (message.get("params") or {}).get("arguments") or {}
            if name == "echo":
                send({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {"content": [{"type": "text", "text": f"fake echo: {args.get('text', '')}"}]},
                })
            elif name == "fail":
                send({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {"content": [{"type": "text", "text": "故意失败"}], "isError": True},
                })
            else:
                send({
                    "jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32602, "message": f"未知工具: {name}"},
                })
        else:
            send({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "未知方法"}})


if __name__ == "__main__":
    main()
