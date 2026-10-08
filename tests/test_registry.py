"""平台注册表测试。"""

import pytest

from crawlerhub.core.base import BaseCrawler
from crawlerhub.platforms.douyin.crawler import DouyinCrawler
from crawlerhub.platforms.registry import (
    PLATFORM_REGISTRY,
    get_platform_cls,
    list_platforms,
    register_platform,
)
from crawlerhub.platforms.weibo.crawler import WeiboCrawler
from crawlerhub.platforms.xzqh.crawler import XzqhCrawler


class TestRegistry:
    def test_list_platforms(self) -> None:
        platforms = list_platforms()
        assert "weibo" in platforms
        assert "douyin" in platforms
        assert "xzqh" in platforms

    def test_get_weibo_cls(self) -> None:
        cls = get_platform_cls("weibo")
        assert cls is WeiboCrawler
        assert issubclass(cls, BaseCrawler)

    def test_get_douyin_cls(self) -> None:
        cls = get_platform_cls("douyin")
        assert cls is DouyinCrawler
        assert issubclass(cls, BaseCrawler)

    def test_get_xzqh_cls(self) -> None:
        cls = get_platform_cls("xzqh")
        assert cls is XzqhCrawler
        assert issubclass(cls, BaseCrawler)

    def test_unknown_platform_raises(self) -> None:
        with pytest.raises(ValueError, match="不支持的平台"):
            get_platform_cls("nonexistent")

    def test_register_new_platform(self) -> None:
        """动态注册新平台。"""
        class FakeCrawler(BaseCrawler):
            @property
            def platform_name(self) -> str:
                return "fake"

            async def fetch_user(self, user_id: str):
                return {}

        register_platform("fake", FakeCrawler)
        assert "fake" in list_platforms()
        assert get_platform_cls("fake") is FakeCrawler

        # 清理
        PLATFORM_REGISTRY.pop("fake", None)

    def test_registry_has_three_platforms(self) -> None:
        assert len(PLATFORM_REGISTRY) == 3
