"""app/tools/search.py——SearchProvider 抽象、Tavily 实现、web_search 工具。"""

import httpx
import pytest

from super_agent.app.tools.search import (
    SearchProvider,
    SearchResult,
    TavilySearchProvider,
    WebSearchTool,
)


async def _run(tool: WebSearchTool, **params):
    return await tool.execute("t1", params, None, None)


def _tavily_transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _ok_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "results": [
                {"title": "第一条", "url": "https://a.example/1", "content": "摘要一", "score": 0.9},
                {"title": "Second", "url": "https://b.example/2", "content": "snippet two"},
            ]
        },
        headers={"content-type": "application/json"},
    )


# ---- SearchProvider 抽象 ----


async def test_search_provider_is_replaceable() -> None:
    """任何实现 search() 的对象都是 SearchProvider——替换后端不改工具协议。"""

    class IntranetSearch(SearchProvider):
        async def search(self, query: str, max_results: int) -> list[SearchResult]:
            return [SearchResult(title="内网结果", url="http://wiki.local/x", snippet="内部 wiki")]

    result = await _run(WebSearchTool(IntranetSearch()), query="公司制度")
    assert result.is_error is False
    assert "内网结果" in result.content and "wiki.local" in result.content


# ---- Tavily 实现 ----


async def test_tavily_parses_results_with_bearer_auth() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = request.read()
        return _ok_response()

    provider = TavilySearchProvider(api_key="tvly-test", transport=_tavily_transport(handler))
    results = await provider.search("最新天气", 2)

    assert captured["auth"] == "Bearer tvly-test"
    assert len(results) == 2
    assert results[0].title == "第一条" and results[0].url == "https://a.example/1"


async def test_tavily_auth_failure_is_clear() -> None:
    provider = TavilySearchProvider(
        api_key="bad",
        transport=_tavily_transport(lambda request: httpx.Response(401)),
    )
    with pytest.raises(LookupError, match="认证失败"):
        await provider.search("q", 5)


async def test_tavily_http_error_is_clear() -> None:
    provider = TavilySearchProvider(
        api_key="k",
        transport=_tavily_transport(lambda request: httpx.Response(500)),
    )
    with pytest.raises(LookupError, match="500"):
        await provider.search("q", 5)


async def test_tavily_missing_key_raises_clearly(monkeypatch) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    provider = TavilySearchProvider()
    with pytest.raises(LookupError, match="TAVILY_API_KEY"):
        await provider.search("q", 5)


# ---- web_search 工具 ----


async def test_tool_without_provider_errors_clearly() -> None:
    result = await _run(WebSearchTool(), query="q")
    assert result.is_error is True
    assert "TAVILY_API_KEY" in result.content  # 错误清晰，不是静默失败


async def test_tool_formats_numbered_results() -> None:
    class FakeProvider(SearchProvider):
        async def search(self, query: str, max_results: int) -> list[SearchResult]:
            return [SearchResult("标题A", "https://a/", "摘要A"), SearchResult("标题B", "https://b/", "")]

    result = await _run(WebSearchTool(FakeProvider()), query="x")
    assert result.is_error is False
    assert "[1] 标题A" in result.content and "https://a/" in result.content
    assert "[2] 标题B" in result.content


async def test_tool_passes_max_results_and_caps() -> None:
    captured: dict = {}

    class Spy(SearchProvider):
        async def search(self, query: str, max_results: int) -> list[SearchResult]:
            captured["max"] = max_results
            captured["query"] = query
            return []

    tool = WebSearchTool(Spy())
    await _run(tool, query="  q  ", max_results=99)
    assert captured == {"max": 10, "query": "q"}  # 上限 10，query 去空白


async def test_tool_empty_query_is_error() -> None:
    result = await _run(WebSearchTool(object()), query="   ")  # type: ignore[arg-type]
    assert result.is_error and "query" in result.content


async def test_tool_no_results_message() -> None:
    class Empty(SearchProvider):
        async def search(self, query: str, max_results: int) -> list[SearchResult]:
            return []

    result = await _run(WebSearchTool(Empty()), query="冷门词")
    assert result.is_error is False and "无搜索结果" in result.content
