"""平台注册表 / 工厂。

通过平台名称动态创建对应的爬虫实例，便于扩展新平台。
新增平台只需：
1. 在 ``crawlerhub/platforms/`` 下新建子包，实现继承 BaseCrawler 的类；
2. 在 :data:`PLATFORM_REGISTRY` 中注册。
"""

from __future__ import annotations

from typing import Dict, Type

from crawlerhub.core.base import BaseCrawler
from crawlerhub.platforms.douyin.crawler import DouyinCrawler
from crawlerhub.platforms.weibo.crawler import WeiboCrawler

# 平台名称 -> 爬虫类 的注册表
PLATFORM_REGISTRY: Dict[str, Type[BaseCrawler]] = {
    "weibo": WeiboCrawler,
    "douyin": DouyinCrawler,
}


def get_platform_cls(name: str) -> Type[BaseCrawler]:
    """根据平台名称获取爬虫类。

    Args:
        name: 平台名称，如 ``weibo``、``douyin``。

    Returns:
        对应的爬虫类。

    Raises:
        ValueError: 平台未注册。
    """
    if name not in PLATFORM_REGISTRY:
        available = ", ".join(PLATFORM_REGISTRY.keys())
        raise ValueError(f"不支持的平台: {name}，可选: {available}")
    return PLATFORM_REGISTRY[name]


def register_platform(name: str, cls: Type[BaseCrawler]) -> None:
    """注册新平台（运行时扩展用）。

    Args:
        name: 平台名称。
        cls: 爬虫类。
    """
    PLATFORM_REGISTRY[name] = cls


def list_platforms() -> list[str]:
    """列出所有已注册的平台名称。"""
    return list(PLATFORM_REGISTRY.keys())
