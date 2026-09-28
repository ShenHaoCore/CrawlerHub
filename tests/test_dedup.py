"""crawlerhub.core.dedup 模块测试。"""

import pytest

from crawlerhub.core.dedup import BaseDedup, MemoryDedup, create_dedup


class TestMakeKey:
    def test_content_id_priority(self) -> None:
        """content_id 优先级最高。"""
        key = BaseDedup.make_key("weibo", content_id="123", url="https://x.com")
        assert key == "weibo:123"

    def test_url_fallback(self) -> None:
        """无 content_id 时回退到 url 哈希。"""
        key = BaseDedup.make_key("weibo", url="https://weibo.com/u/123")
        assert key.startswith("weibo:")
        assert len(key) == len("weibo:") + 64

    def test_content_fallback(self) -> None:
        """无 content_id 和 url 时回退到内容哈希。"""
        key = BaseDedup.make_key("douyin", content={"a": 1})
        assert key.startswith("douyin:")
        assert len(key) == len("douyin:") + 64

    def test_different_platform_different_key(self) -> None:
        """相同 content_id 不同平台应生成不同 key。"""
        k1 = BaseDedup.make_key("weibo", content_id="123")
        k2 = BaseDedup.make_key("douyin", content_id="123")
        assert k1 != k2

    def test_no_args_raises(self) -> None:
        with pytest.raises(ValueError, match="至少提供一个"):
            BaseDedup.make_key("weibo")

    def test_url_normalized(self) -> None:
        """相同 URL 不同空格应生成相同 key。"""
        k1 = BaseDedup.make_key("weibo", url="  https://x.com  ")
        k2 = BaseDedup.make_key("weibo", url="https://x.com")
        assert k1 == k2


class TestMemoryDedup:
    async def test_first_seen_is_not_duplicate(self) -> None:
        d = MemoryDedup()
        assert await d.check_and_mark("key1") is False

    async def test_second_seen_is_duplicate(self) -> None:
        d = MemoryDedup()
        await d.mark("key1")
        assert await d.is_duplicate("key1") is True

    async def test_check_and_mark_atomic(self) -> None:
        d = MemoryDedup()
        assert await d.check_and_mark("key1") is False  # 首次
        assert await d.check_and_mark("key1") is True   # 重复

    async def test_different_keys_independent(self) -> None:
        d = MemoryDedup()
        await d.mark("key1")
        assert await d.is_duplicate("key2") is False


class TestCreateDedup:
    def test_create_memory(self) -> None:
        d = create_dedup("memory")
        assert isinstance(d, MemoryDedup)

    def test_create_redis(self) -> None:
        d = create_dedup("redis", host="127.0.0.1", port=6379)
        assert isinstance(d, BaseDedup)

    def test_unknown_backend_raises(self) -> None:
        with pytest.raises(ValueError, match="不支持的去重后端"):
            create_dedup("invalid")
