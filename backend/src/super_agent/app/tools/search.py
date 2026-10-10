"""web_search 工具——SearchProvider 抽象 + 默认 Tavily 实现（spec §4.4）。

替换搜索后端只需实现 SearchProvider 并注入 WebSearchTool，工具协议不动。
API key 从环境 TAVILY_API_KEY 读取（create_app 会先把数据目录 .env 载入环境）；
未配置时工具创建成功但执行报错——错误清晰而非静默失败。
"""

import os
from dataclasses import dataclass

import httpx

from super_agent.agent.types import AgentTool, AgentToolResult

TAVILY_ENDPOINT = "https://api.tavily.com/search"
TAVILY_TIMEOUT = 20.0


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchProvider:
    """搜索后端抽象：实现 search() 即可接入（Tavily / 内网搜索服务 / 测试替身）。"""

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        raise NotImplementedError


class TavilySearchProvider(SearchProvider):
    """Tavily REST API（免费档即可，https://tavily.com）。"""

    def __init__(self, api_key: str | None = None, transport: httpx.AsyncBaseTransport | None = None) -> None:
        # key 未显式给出时从环境读（create_app 已把数据目录 .env 载入环境）
        self._api_key = api_key or os.environ.get("TAVILY_API_KEY")
        self._transport = transport  # 仅测试注入

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        if not self._api_key:
            raise LookupError("TAVILY_API_KEY 未配置")
        async with httpx.AsyncClient(timeout=TAVILY_TIMEOUT, transport=self._transport) as client:
            response = await client.post(
                TAVILY_ENDPOINT,
                json={"query": query, "max_results": max_results},
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        if response.status_code == 401:
            raise LookupError("Tavily 认证失败：API key 无效")
        if response.status_code >= 400:
            raise LookupError(f"Tavily 请求失败：HTTP {response.status_code}")
        body = response.json()
        results: list[SearchResult] = []
        for item in body.get("results", []):
            results.append(
                SearchResult(
                    title=str(item.get("title") or ""),
                    url=str(item.get("url") or ""),
                    snippet=str(item.get("content") or ""),
                )
            )
        return results


class WebSearchTool(AgentTool):
    name = "web_search"
    description = (
        "联网搜索：返回与 query 相关的网页结果（标题 + 链接 + 摘要）。"
        "适合时事、事实核查、需要最新信息的场景；要读具体页面用 fetch。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索词（与目标语言一致效果好）"},
            "max_results": {"type": "integer", "description": "结果条数上限，默认 5"},
        },
        "required": ["query"],
    }
    execution_mode = "parallel"

    def __init__(self, provider: SearchProvider | None = None) -> None:
        # provider None = 未配置搜索后端；执行时给清晰错误而不是静默失败
        self._provider = provider

    async def execute(self, tool_call_id, params, signal, on_update) -> AgentToolResult:
        if self._provider is None:
            return AgentToolResult(
                content="web_search 未配置：请在部署目录 .env 或环境变量中设置 TAVILY_API_KEY（免费注册 https://tavily.com）",
                is_error=True,
            )
        query = params.get("query")
        if not isinstance(query, str) or not query.strip():
            return AgentToolResult(content="参数错误: query 不能为空", is_error=True)
        try:
            max_results = max(1, min(10, int(params.get("max_results") or 5)))
        except (TypeError, ValueError):
            max_results = 5

        try:
            results = await self._provider.search(query.strip(), max_results)
        except LookupError as exc:
            return AgentToolResult(content=f"搜索失败: {exc}", is_error=True)
        except httpx.HTTPError as exc:
            return AgentToolResult(content=f"搜索失败: {exc}", is_error=True)

        if not results:
            return AgentToolResult(content=f"无搜索结果: {query.strip()}")
        lines = []
        for i, result in enumerate(results, start=1):
            lines.append(f"[{i}] {result.title}\n{result.url}\n{result.snippet.strip()}")
        return AgentToolResult(content="\n\n".join(lines))
