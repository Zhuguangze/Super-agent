"""fetch 工具——抓取网页转纯文本。httpx 拉取，手写标签剥除，零外部依赖。"""

import html as html_mod
import re

import httpx

from super_agent.agent.types import AgentTool, AgentToolResult

TIMEOUT_SECONDS = 15
MAX_BYTES = 2_000_000
MAX_CHARS = 20_000

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SuperAgent/0.1; +https://example.local)",
    "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
}


def html_to_text(page: str) -> str:
    """最简 HTML → 纯文本：去 script/style、块级标签换行、剥标签、解实体。"""
    page = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", "", page)
    page = re.sub(r"(?i)<br\s*/?>", "\n", page)
    page = re.sub(r"(?i)</(p|div|li|tr|h[1-6]|section|article|blockquote|pre)>", "\n", page)
    page = re.sub(r"(?s)<[^>]+>", "", page)
    text = html_mod.unescape(page)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def extract_title(page: str) -> str:
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", page)
    return html_mod.unescape(match.group(1)).strip() if match else ""


def _describe(exc: Exception) -> str:
    """httpx 连接层错误的 str 常为空——展开异常链取底层原因。"""
    parts = [str(exc)]
    cause = exc.__cause__ or exc.__context__
    depth = 0
    while cause is not None and depth < 3:
        msg = str(cause)
        if msg:
            parts.append(msg)
        cause = cause.__cause__ or cause.__context__
        depth += 1
    text = " <- ".join(p for p in parts if p)
    return text or type(exc).__name__


class FetchTool(AgentTool):
    name = "fetch"
    description = (
        "抓取 URL 的网页内容并转为纯文本（自动跟随重定向）。"
        "适合读取文档、文章页面；只支持 http/https。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "完整 URL（http/https）"},
        },
        "required": ["url"],
    }
    execution_mode = "parallel"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        # transport 仅测试注入（httpx.MockTransport）；生产走真实网络
        self._transport = transport

    async def execute(self, tool_call_id, params, signal, on_update) -> AgentToolResult:
        url = str(params.get("url") or "")
        if not re.match(r"^https?://", url):
            return AgentToolResult(content="参数错误: url 需以 http:// 或 https:// 开头", is_error=True)
        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=TIMEOUT_SECONDS, headers=_HEADERS, transport=self._transport
            ) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            return AgentToolResult(content=f"抓取失败: {_describe(exc)}", is_error=True)
        if response.status_code >= 400:
            return AgentToolResult(content=f"HTTP {response.status_code}: {url}", is_error=True)
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type and not content_type.startswith("text/"):
            return AgentToolResult(content=f"不支持的内容类型: {content_type or '未知'}（仅支持文本/HTML）", is_error=True)

        body = response.content[:MAX_BYTES].decode(response.encoding or "utf-8", errors="replace")
        if "text/html" in content_type:
            title = extract_title(body)
            text = html_to_text(body)
            header = f"# {title}\n\n" if title else ""
        else:
            text = body
            header = ""
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS] + "\n…（内容已截断）"
        return AgentToolResult(content=f"{header}{text}".strip() or "（空内容）")
