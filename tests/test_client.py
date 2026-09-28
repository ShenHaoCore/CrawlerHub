"""crawlerhub.core.client 模块测试。

使用 httpx 的 MockTransport 进行 HTTP mock，不发起真实网络请求。
"""

import pytest
import httpx

from crawlerhub.core.client import HttpClient, RateLimiter
from crawlerhub.core.exceptions import AntiCrawlError, LoginRequiredError, RequestError


def make_mock_client(handler, **kwargs) -> HttpClient:
    """创建使用 MockTransport 的 HttpClient。"""
    client = HttpClient(**kwargs)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        timeout=kwargs.get("timeout", 30),
        follow_redirects=True,
    )
    return client


class TestRateLimiter:
    async def test_acquire_does_not_block_when_zero(self) -> None:
        limiter = RateLimiter(0)
        await limiter.acquire()  # 应立即返回

    async def test_acquire_enforces_interval(self) -> None:
        import time

        limiter = RateLimiter(requests_per_second=100)  # 0.01s 间隔
        t0 = time.monotonic()
        await limiter.acquire()
        await limiter.acquire()
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.005  # 至少经过了一个间隔


class TestHttpClient:
    async def test_get_success(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        client = make_mock_client(handler, max_retries=1)
        resp = await client.get("https://test.com/api")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    async def test_post_success(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"received": True})

        client = make_mock_client(handler, max_retries=1)
        resp = await client.post("https://test.com/api", json={"data": "hello"})
        assert resp.status_code == 200

    async def test_403_raises_anti_crawl(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="Forbidden")

        client = make_mock_client(handler, max_retries=1)
        with pytest.raises(AntiCrawlError, match="触发反爬"):
            await client.get("https://test.com/api")

    async def test_401_raises_login_required(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text="Unauthorized")

        client = make_mock_client(handler, max_retries=1)
        with pytest.raises(LoginRequiredError, match="需要登录"):
            await client.get("https://test.com/api")

    async def test_500_raises_request_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Server Error")

        client = make_mock_client(handler, max_retries=1)
        with pytest.raises(RequestError, match="服务器错误"):
            await client.get("https://test.com/api")

    async def test_429_raises_anti_crawl(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, text="Too Many Requests")

        client = make_mock_client(handler, max_retries=1)
        with pytest.raises(AntiCrawlError):
            await client.get("https://test.com/api")

    async def test_retry_on_transport_error(self) -> None:
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise httpx.ConnectError("connection failed")
            return httpx.Response(200, json={"ok": True})

        client = make_mock_client(handler, max_retries=3, retry_backoff=0.01)
        resp = await client.get("https://test.com/api")
        assert resp.status_code == 200
        assert call_count == 2  # 第一次失败，第二次成功

    async def test_retry_exhausted(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("always fails")

        client = make_mock_client(handler, max_retries=2, retry_backoff=0.01)
        with pytest.raises(RequestError, match="always fails"):
            await client.get("https://test.com/api")

    async def test_user_agent_rotation(self) -> None:
        """每次请求应注入不同的 UA。"""
        seen_uas = set()

        def handler(request: httpx.Request) -> httpx.Response:
            ua = request.headers.get("user-agent", "")
            seen_uas.add(ua)
            return httpx.Response(200, json={"ua": ua})

        client = make_mock_client(handler, max_retries=1, ua_rotation=True)
        for _ in range(5):
            await client.get("https://test.com/api")
        # 启用轮换后，5次请求应产生多个不同 UA
        assert len(seen_uas) > 1

    async def test_custom_headers_preserved(self) -> None:
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["headers"] = request.headers
            return httpx.Response(200)

        client = make_mock_client(handler, max_retries=1)
        await client.get("https://test.com/api", headers={"X-Custom": "test123"})
        assert captured["headers"]["x-custom"] == "test123"

    async def test_cookies_sent(self) -> None:
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["cookie"] = request.headers.get("cookie", "")
            return httpx.Response(200)

        client = make_mock_client(handler, max_retries=1)
        await client.get("https://test.com/api", cookies={"session": "abc123"})
        assert "session=abc123" in captured["cookie"]

    async def test_params_sent(self) -> None:
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200)

        client = make_mock_client(handler, max_retries=1)
        await client.get("https://test.com/api", params={"page": "1", "size": "20"})
        assert "page=1" in captured["url"]
        assert "size=20" in captured["url"]
