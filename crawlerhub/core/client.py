"""统一 HTTP 请求客户端。

内置功能：
- User-Agent 轮换（fake-useragent 或自定义列表）
- 代理 IP 池
- 基于 tenacity 的请求重试
- 全局限速（令牌桶）
- 统一的异常封装
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Dict, Optional

import httpx
from fake_useragent import UserAgent
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from crawlerhub.core.exceptions import AntiCrawlError, LoginRequiredError, RequestError
from crawlerhub.core.logger import logger
from crawlerhub.utils.proxy import ProxyPool


class RateLimiter:
    """带随机抖动的令牌桶限速器。

    在固定间隔基础上叠加随机抖动，避免请求间隔过于规律被风控识别。
    """

    def __init__(self, requests_per_second: float, jitter: float = 0.5) -> None:
        self._interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self._jitter = jitter
        self._last: float = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """获取令牌，若未到间隔则等待（含随机抖动）。"""
        if self._interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            wait = self._interval - (now - self._last)
            if wait > 0:
                # 叠加 ±jitter 比例的随机抖动
                jitter = wait * self._jitter * (random.random() * 2 - 1)
                await asyncio.sleep(max(0.1, wait + jitter))
            self._last = time.monotonic()


class HttpClient:
    """统一异步 HTTP 客户端。

    Args:
        timeout: 请求超时秒数。
        max_retries: 最大重试次数。
        retry_backoff: 重试退避基数秒。
        rate_limit: 全局限速（请求/秒），<=0 表示不限速。
        proxy_pool: 代理池实例，为 None 则不使用代理。
        custom_uas: 自定义 UA 列表，为空则使用 fake-useragent。
        ua_rotation: 是否启用 UA 轮换。
    """

    # 触发反爬/风控的常见 HTTP 状态码
    ANTI_CRAWL_STATUS = {403, 412, 429}
    LOGIN_REQUIRED_STATUS = {401}

    def __init__(
        self,
        timeout: float = 30.0,
        max_retries: int = 3,
        retry_backoff: float = 1.0,
        rate_limit: float = 0.0,
        proxy_pool: Optional[ProxyPool] = None,
        custom_uas: Optional[list[str]] = None,
        ua_rotation: bool = True,
        request_delay_min: float = 0.0,
        request_delay_max: float = 0.0,
        anti_crawl_cooldown: float = 60.0,
    ) -> None:
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.proxy_pool = proxy_pool
        self.ua_rotation = ua_rotation
        self._custom_uas = custom_uas or []
        self._ua = UserAgent() if not self._custom_uas else None
        self._limiter = RateLimiter(rate_limit) if rate_limit > 0 else None
        self._delay_min = request_delay_min
        self._delay_max = request_delay_max
        self._cooldown = anti_crawl_cooldown
        self._cooldown_until: float = 0.0
        self._client: Optional[httpx.AsyncClient] = None
        # 按代理 URL 缓存的客户端池（httpx 不支持 per-request proxy，需在客户端级指定）
        self._proxy_clients: Dict[str, httpx.AsyncClient] = {}

    async def __aenter__(self) -> "HttpClient":
        self._client = httpx.AsyncClient(timeout=self.timeout, follow_redirects=True)
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        for client in self._proxy_clients.values():
            await client.aclose()
        self._proxy_clients.clear()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("HttpClient 未初始化，请在 async with 块中使用")
        return self._client

    def _get_client(self, proxy: Optional[str]) -> httpx.AsyncClient:
        """根据代理获取对应的 AsyncClient（无代理返回默认客户端）。"""
        if not proxy:
            return self.client
        if proxy not in self._proxy_clients:
            self._proxy_clients[proxy] = httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                proxy=proxy,
            )
            logger.debug(f"已创建代理客户端: {proxy}")
        return self._proxy_clients[proxy]

    def _get_ua(self) -> str:
        """获取一个 User-Agent。"""
        if not self.ua_rotation:
            return (
                self._custom_uas[0]
                if self._custom_uas
                else "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
        if self._custom_uas:
            import random

            return random.choice(self._custom_uas)
        return self._ua.random  # type: ignore[union-attr]

    def _build_headers(self, headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """构建请求头，自动注入 UA。"""
        merged = {"User-Agent": self._get_ua()}
        if headers:
            merged.update(headers)
        return merged

    def _get_proxy(self) -> Optional[str]:
        """从代理池获取一个代理。"""
        if self.proxy_pool is None:
            return None
        try:
            return self.proxy_pool.random()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"获取代理失败，将不使用代理: {exc}")
            return None

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        json: Any = None,
        data: Any = None,
        cookies: Optional[Dict[str, str]] = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """发送 HTTP 请求，内置重试、限速、UA 轮换与代理。

        Args:
            method: HTTP 方法（GET/POST 等）。
            url: 请求 URL。
            headers: 额外请求头。
            params: URL 查询参数。
            json: JSON 请求体。
            data: 表单请求体。
            cookies: Cookie 字典。

        Returns:
            httpx.Response 对象。

        Raises:
            RequestError: 网络错误或重试耗尽。
            AntiCrawlError: 触发反爬状态码。
            LoginRequiredError: 需要登录。
        """
        if self._limiter:
            await self._limiter.acquire()

        # 熔断：若处于反爬冷却期，等待结束
        if self._cooldown_until > time.monotonic():
            wait = self._cooldown_until - time.monotonic()
            logger.warning(f"处于反爬冷却期，等待 {wait:.0f} 秒")
            await asyncio.sleep(wait)

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(multiplier=self.retry_backoff, min=2, max=60),
            retry=retry_if_exception_type((RequestError, httpx.TransportError)),
            reraise=True,
        ):
            with attempt:
                try:
                    proxy = self._get_proxy()
                    http_client = self._get_client(proxy)
                    if cookies:
                        http_client.cookies.update(cookies)
                    final_headers = self._build_headers(headers)
                    response = await http_client.request(
                        method,
                        url,
                        headers=final_headers,
                        params=params,
                        json=json,
                        data=data,
                        **kwargs,
                    )
                except httpx.TransportError as exc:
                    logger.warning(f"请求传输错误: {url} -> {exc}")
                    raise RequestError(str(exc), url=url) from exc

                self._check_status(response)

                # 请求后随机延迟，模拟人工操作
                if self._delay_max > 0:
                    delay = random.uniform(self._delay_min, self._delay_max)
                    await asyncio.sleep(delay)

                return response

        # 理论上不会到达，tenacity 会抛出最后一次异常
        raise RequestError("请求重试耗尽", url=url)  # pragma: no cover

    def _check_status(self, response: httpx.Response) -> None:
        """检查响应状态码，抛出对应业务异常。"""
        status = response.status_code
        if status in self.LOGIN_REQUIRED_STATUS:
            raise LoginRequiredError(f"需要登录态 (HTTP {status})")
        if status in self.ANTI_CRAWL_STATUS:
            # 触发反爬，进入熔断冷却
            self._cooldown_until = time.monotonic() + self._cooldown
            logger.warning(
                f"触发反爬 (HTTP {status})，进入 {self._cooldown} 秒冷却期"
            )
            raise AntiCrawlError(f"触发反爬机制 (HTTP {status})")
        if status >= 500:
            raise RequestError(f"服务器错误 (HTTP {status})", url=str(response.url), status_code=status)
        response.raise_for_status()

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        """发送 GET 请求。"""
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        """发送 POST 请求。"""
        return await self.request("POST", url, **kwargs)
