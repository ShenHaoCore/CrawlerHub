"""自定义异常体系。

所有爬虫相关异常均继承自 :class:`CrawlerHubError`，便于统一捕获和处理。
"""

from __future__ import annotations


class CrawlerHubError(Exception):
    """CrawlerHub 所有异常的基类。"""


class RequestError(CrawlerHubError):
    """HTTP 请求相关异常（网络错误、超时等）。"""

    def __init__(self, message: str, *, url: str | None = None, status_code: int | None = None) -> None:
        super().__init__(message)
        self.url = url
        self.status_code = status_code


class AntiCrawlError(CrawlerHubError):
    """触发目标平台风控/反爬机制时抛出。

    收到该异常时通常需要停止请求、更换 IP 或更新 Cookie，避免加剧封禁风险。
    """


class LoginRequiredError(CrawlerHubError):
    """需要登录态但 Cookie 缺失或失效时抛出。"""


class ParseError(CrawlerHubError):
    """数据解析失败时抛出。"""

    def __init__(self, message: str, *, raw: str | None = None) -> None:
        super().__init__(message)
        self.raw = raw


class ProxyError(CrawlerHubError):
    """代理池不可用或代理请求失败时抛出。"""


class StorageError(CrawlerHubError):
    """数据存储失败时抛出。"""


class ConfigError(CrawlerHubError):
    """配置加载或校验失败时抛出。"""
