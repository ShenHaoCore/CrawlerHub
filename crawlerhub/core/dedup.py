"""数据去重模块。

提供基于 URL 或内容哈希的去重能力，支持内存和 Redis 两种后端。
去重键优先使用 ``platform:content_id``，无 content_id 时回退到内容哈希。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from crawlerhub.utils.hash import content_hash, url_hash


class BaseDedup(ABC):
    """去重器抽象基类。"""

    @abstractmethod
    async def is_duplicate(self, key: str) -> bool:
        """判断 key 是否已存在。

        Args:
            key: 去重键。

        Returns:
            True 表示已存在（重复），False 表示新数据。
        """

    @abstractmethod
    async def mark(self, key: str) -> None:
        """将 key 标记为已处理。

        Args:
            key: 去重键。
        """

    async def check_and_mark(self, key: str) -> bool:
        """检查并标记，原子操作。

        Args:
            key: 去重键。

        Returns:
            True 表示是重复数据，False 表示首次出现并已标记。
        """
        if await self.is_duplicate(key):
            return True
        await self.mark(key)
        return False

    @staticmethod
    def make_key(
        platform: str,
        content_id: Optional[str] = None,
        url: Optional[str] = None,
        content: Optional[object] = None,
    ) -> str:
        """生成稳定的去重键。

        优先级：content_id > url > content 哈希。

        Args:
            platform: 平台名称，作为命名空间。
            content_id: 平台内容 ID。
            url: 内容 URL。
            content: 内容对象（用于哈希）。

        Returns:
            形如 ``weibo:12345`` 的去重键。
        """
        if content_id:
            suffix = content_id
        elif url:
            suffix = url_hash(url)
        elif content is not None:
            suffix = content_hash(content)
        else:
            raise ValueError("content_id / url / content 至少提供一个")
        return f"{platform}:{suffix}"


class MemoryDedup(BaseDedup):
    """基于内存集合的去重器，适用于单进程短任务。"""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    async def is_duplicate(self, key: str) -> bool:
        return key in self._seen

    async def mark(self, key: str) -> None:
        self._seen.add(key)


class RedisDedup(BaseDedup):
    """基于 Redis 的去重器，支持过期时间，适用于长任务/多进程。

    Args:
        host: Redis 主机。
        port: Redis 端口。
        db: Redis 数据库编号。
        ttl: 去重键过期时间（秒），<=0 表示永不过期。
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6379,
        db: int = 0,
        ttl: int = 86400,
    ) -> None:
        self.ttl = ttl
        self._client = None  # 延迟导入，避免无 redis 时无法导入模块
        self._host = host
        self._port = port
        self._db = db

    def _get_client(self):
        if self._client is None:
            import redis.asyncio as redis

            self._client = redis.Redis(host=self._host, port=self._port, db=self._db)
        return self._client

    async def is_duplicate(self, key: str) -> bool:
        client = self._get_client()
        return bool(await client.exists(key))

    async def mark(self, key: str) -> None:
        client = self._get_client()
        if self.ttl > 0:
            await client.setex(key, self.ttl, "1")
        else:
            await client.set(key, "1")

    async def check_and_mark(self, key: str) -> bool:
        """使用 SET NX 实现原子检查+标记。"""
        client = self._get_client()
        if self.ttl > 0:
            result = await client.set(key, "1", ex=self.ttl, nx=True)
        else:
            result = await client.set(key, "1", nx=True)
        # result 为 None 表示已存在（重复）
        return result is None


def create_dedup(backend: str = "memory", **kwargs) -> BaseDedup:
    """工厂方法：根据配置创建去重器。

    Args:
        backend: 后端类型 ``memory`` 或 ``redis``。
        **kwargs: 传递给去重器构造函数的参数。

    Returns:
        去重器实例。
    """
    if backend == "memory":
        return MemoryDedup()
    if backend == "redis":
        return RedisDedup(**kwargs)
    raise ValueError(f"不支持的去重后端: {backend}")
