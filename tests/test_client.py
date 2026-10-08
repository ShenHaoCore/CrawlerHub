"""crawlerhub.core.client 模块测试。

使用 httpx 的 MockTransport 进行 HTTP mock，不发起真实网络请求。
"""

import pytest
import httpx

from crawlerhub.core.client import (
    LOGIN_REDIRECT_RE,
    HttpClient,
    RateLimiter,
    parse_json_response,
)
from crawlerhub.core.exceptions import (
    AntiCrawlError,
    LoginRequiredError,
    ParseError,
    RequestError,
)


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


class TestLoginRedirectDetection:
    """Cookie 失效时常表现为「被重定向到登录页」，且最终可能拿到 HTTP 200。

    仅看状态码完全无法识别——这正是它最容易被误判成解析 bug 的原因。
    """

    @staticmethod
    def _redirect_to_login(request: httpx.Request) -> httpx.Response:
        """模拟微博的行为：API 地址 302 到登录页，登录页返回 200 HTML。"""
        if "login" in str(request.url):
            return httpx.Response(
                200,
                text="<html>请登录</html>",
                headers={"content-type": "text/html; charset=utf-8"},
            )
        return httpx.Response(
            302,
            headers={"location": "https://login.sina.com.cn/sso/login.php?url=x"},
        )

    async def test_redirect_to_login_raises(self) -> None:
        client = make_mock_client(self._redirect_to_login, max_retries=1)

        with pytest.raises(LoginRequiredError, match="重定向到登录页"):
            await client.get("https://weibo.com/ajax/profile/info")

    async def test_direct_200_html_is_not_flagged(self) -> None:
        """没有重定向时不应误报——只有重定向链才会被判定为登录页。"""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text="<html>某个正常返回的 HTML 页面</html>",
                headers={"content-type": "text/html; charset=utf-8"},
            )

        client = make_mock_client(handler, max_retries=1)
        resp = await client.get("https://test.com/api")

        assert resp.status_code == 200

    async def test_ordinary_redirect_not_flagged(self) -> None:
        """普通的非登录类重定向（如接口版本跳转）不应被误判。"""

        def handler(request: httpx.Request) -> httpx.Response:
            if "/v2/" in str(request.url):
                return httpx.Response(200, json={"ok": True})
            return httpx.Response(302, headers={"location": "https://test.com/v2/api"})

        client = make_mock_client(handler, max_retries=1)
        resp = await client.get("https://test.com/api")

        assert resp.json()["ok"] is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://login.sina.com.cn/sso/login.php?url=x",
            "https://passport.example.com/signin",
            "https://example.com/sso/authorize",
            "https://example.com/SIGN_IN",
        ],
    )
    def test_pattern_matches_login_entrypoints(self, url: str) -> None:
        assert LOGIN_REDIRECT_RE.search(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://weibo.com/ajax/profile/info",
            "https://dmfw.mca.gov.cn/9095/xzqh/getList",
            "https://test.com/v2/api",
        ],
    )
    def test_pattern_ignores_normal_api_urls(self, url: str) -> None:
        assert not LOGIN_REDIRECT_RE.search(url)


class TestParseJsonResponse:
    """非 JSON 响应应给出可读、有指向性的错误，而不是编码解码异常。"""

    def test_valid_json_returned(self) -> None:
        resp = httpx.Response(200, json={"ok": True})
        assert parse_json_response(resp, context="测试接口") == {"ok": True}

    def test_gbk_html_raises_readable_parse_error(self) -> None:
        """复现真实场景：服务端返回 GBK 编码的 HTML 登录页。

        原实现抛的是 ``'utf-8' codec can't decode byte 0xd0``，
        完全看不出问题是登录态失效。
        """
        resp = httpx.Response(
            200,
            content="<html>登录</html>".encode("gbk"),
            headers={"content-type": "text/html; charset=utf-8"},
        )

        with pytest.raises(ParseError, match="HTML 页面") as exc_info:
            parse_json_response(resp, context="微博 profile/info")

        assert "微博 profile/info" in str(exc_info.value)
        assert "html" in (exc_info.value.raw or "").lower()

    def test_empty_body_raises(self) -> None:
        resp = httpx.Response(200, content=b"")

        with pytest.raises(ParseError, match="空响应体"):
            parse_json_response(resp, context="抖音 列表")

    def test_plain_text_raises_without_html_hint(self) -> None:
        resp = httpx.Response(
            200, text="not json", headers={"content-type": "text/plain"}
        )

        with pytest.raises(ParseError) as exc_info:
            parse_json_response(resp, context="测试接口")

        assert "HTML 页面" not in str(exc_info.value)
