"""爬虫基类。

所有平台爬虫继承 :class:`BaseCrawler`，统一生命周期：
``setup -> run -> cleanup``，并内置请求客户端、Cookie 管理、去重、存储等能力。
"""

from __future__ import annotations

import asyncio
import random
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from crawlerhub.core.client import HttpClient
from crawlerhub.core.config import Config
from crawlerhub.core.cookie import CookieManager
from crawlerhub.core.dedup import BaseDedup, create_dedup
from crawlerhub.core.logger import logger
from crawlerhub.core.storage import BaseStorage, create_storage
from crawlerhub.utils.proxy import ProxyPool


class BaseCrawler(ABC):
    """平台爬虫基类。

    子类需实现 :meth:`platform_name`、:meth:`fetch_user` 等抽象方法。

    Args:
        config: 合并后的配置对象。
        cookie_manager: Cookie 管理器实例。
        storage: 存储后端实例，为 None 时根据配置自动创建。
        dedup: 去重器实例，为 None 时根据配置自动创建。
    """

    def __init__(
        self,
        config: Config,
        cookie_manager: Optional[CookieManager] = None,
        storage: Optional[BaseStorage] = None,
        dedup: Optional[BaseDedup] = None,
    ) -> None:
        self.config = config
        self.cookie = cookie_manager
        self.storage = storage or self._build_storage()
        self.dedup = dedup or self._build_dedup()
        self._client: Optional[HttpClient] = None

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """平台名称，如 ``weibo``、``douyin``。"""

    def _build_storage(self) -> BaseStorage:
        backend = self.config.get("storage.default", "json")
        if backend == "sqlite":
            return create_storage(backend, db_url=self.config.get("storage.sqlite.url"))
        return create_storage(backend, output_dir=self.config.get("storage.output_dir"))

    def _build_dedup(self) -> BaseDedup:
        backend = self.config.get("dedup.backend", "memory")
        if backend == "redis":
            return create_dedup(
                backend,
                host=self.config.get("dedup.redis.host"),
                port=self.config.get("dedup.redis.port"),
                db=self.config.get("dedup.redis.db"),
                ttl=self.config.get("dedup.redis.ttl"),
            )
        return create_dedup(backend)

    def _build_client(self) -> HttpClient:
        proxy_pool = None
        if self.config.get("proxy.enabled", False):
            proxy_pool = ProxyPool(self.config.get("proxy.pool_file"))

        rl_cfg = self.config.get("request.rate_limit", {}) or {}
        rate_limit = rl_cfg.get("requests_per_second", 0.0) if rl_cfg else 0.0

        delay_cfg = self.config.get("request.request_delay", {}) or {}
        delay_min = delay_cfg.get("min", 0.0) if delay_cfg else 0.0
        delay_max = delay_cfg.get("max", 0.0) if delay_cfg else 0.0
        cooldown = self.config.get("request.anti_crawl_cooldown", 60.0)

        return HttpClient(
            timeout=self.config.get("request.timeout", 30),
            max_retries=self.config.get("request.max_retries", 3),
            retry_backoff=self.config.get("request.retry_backoff", 2.0),
            rate_limit=rate_limit,
            proxy_pool=proxy_pool,
            custom_uas=self.config.get("user_agent.custom_uas") or None,
            ua_rotation=self.config.get("user_agent.rotation", True),
            request_delay_min=delay_min,
            request_delay_max=delay_max,
            anti_crawl_cooldown=cooldown,
        )

    async def setup(self) -> None:
        """初始化资源：创建 HTTP 客户端等。"""
        self._client = self._build_client()
        await self._client.__aenter__()
        logger.info(f"[{self.platform_name}] 爬虫已初始化")

    async def cleanup(self) -> None:
        """释放资源。"""
        if self._client:
            await self._client.__aexit__(None, None, None)
            self._client = None
        logger.info(f"[{self.platform_name}] 爬虫已清理")

    @property
    def client(self) -> HttpClient:
        """获取 HTTP 客户端。"""
        if self._client is None:
            raise RuntimeError("爬虫未初始化，请先调用 setup()")
        return self._client

    def _cookies(self) -> Optional[Dict[str, str]]:
        """获取 Cookie 字典，用于请求。"""
        if self.cookie and self.cookie.is_valid:
            return self.cookie.cookie_dict
        return None

    @abstractmethod
    async def fetch_user(self, user_id: str) -> Dict[str, Any]:
        """爬取单个用户的基础信息。

        Args:
            user_id: 平台用户 ID。

        Returns:
            用户信息字典。
        """

    async def fetch_related_users(self, user_id: str) -> List[str]:
        """获取关联用户 ID 列表（粉丝 + 关注）。

        子类应实现此方法以支持关联用户爬取。

        Args:
            user_id: 平台用户 ID。

        Returns:
            去重后的关联用户 ID 列表。
        """
        return []

    async def fetch_users(self, user_ids: List[str]) -> List[Dict[str, Any]]:
        """批量爬取用户信息，自动去重。

        Args:
            user_ids: 用户 ID 列表。

        Returns:
            用户信息字典列表。
        """
        results: List[Dict[str, Any]] = []
        for uid in user_ids:
            key = self.dedup.make_key(self.platform_name, content_id=uid)
            if await self.dedup.check_and_mark(key):
                logger.info(f"[{self.platform_name}] 用户 {uid} 已爬取，跳过")
                continue
            try:
                user = await self.fetch_user(uid)
                results.append(user)
                await self.storage.save(user, f"{self.platform_name}_users")
                logger.info(f"[{self.platform_name}] 已保存用户 {uid}")
            except Exception as exc:  # noqa: BLE001
                logger.error(f"[{self.platform_name}] 爬取用户 {uid} 失败: {exc}")
        return results

    async def fetch_users_with_related(
        self,
        user_ids: List[str],
        max_related: int = 50,
        max_depth: int = 1,
    ) -> List[Dict[str, Any]]:
        """爬取种子用户及其关联用户，支持多层深度。

        采用广度优先（BFS）逐层扩展：
        - 第 0 层：种子用户
        - 第 1 层：种子用户的关联用户
        - 第 2 层：第 1 层用户的关联用户
        - ... 直到达到 ``max_depth`` 或无新用户

        每个用户的关联用户数受 ``max_related`` 限制，全局去重避免重复爬取。

        Args:
            user_ids: 种子用户 ID 列表。
            max_related: 每个用户最多爬取多少个关联用户。
            max_depth: 最大爬取深度，``1`` 表示只爬种子用户的直接关联用户。

        Returns:
            所有爬取到的用户信息字典列表。
        """
        results: List[Dict[str, Any]] = []

        # 当前层待爬取的用户 ID
        current_level: List[str] = list(user_ids)
        visited: set = set()

        for depth in range(max_depth + 1):
            if not current_level:
                logger.info(f"[{self.platform_name}] 第 {depth} 层无新用户，爬取结束")
                break

            logger.info(
                f"[{self.platform_name}] 开始爬取第 {depth} 层，"
                f"共 {len(current_level)} 个用户"
            )

            next_level: List[str] = []

            for uid in current_level:
                # 1. 爬取用户自身信息
                key = self.dedup.make_key(self.platform_name, content_id=uid)
                if not await self.dedup.check_and_mark(key):
                    try:
                        user = await self.fetch_user(uid)
                        results.append(user)
                        await self.storage.save(user, f"{self.platform_name}_users")
                        logger.info(f"[{self.platform_name}] 已保存用户 {uid}")
                    except Exception as exc:  # noqa: BLE001
                        logger.error(f"[{self.platform_name}] 爬取用户 {uid} 失败: {exc}")

                # 2. 拉取关联用户（最后一层不再扩展）
                if depth < max_depth:
                    try:
                        related_ids = await self.fetch_related_users(uid)
                    except Exception as exc:  # noqa: BLE001
                        logger.error(f"[{self.platform_name}] 获取 {uid} 关联用户失败: {exc}")
                        continue

                    if related_ids:
                        related_ids = related_ids[:max_related]
                        # 过滤已访问和本层已处理的用户
                        new_ids = [r for r in related_ids if r not in visited]
                        next_level.extend(new_ids)
                        visited.update(new_ids)

                # 用户间随机延迟，降低请求密度避免触发风控
                await asyncio.sleep(random.uniform(0.5, 2.0))

            # 去重保序，进入下一层
            current_level = list(dict.fromkeys(next_level))

        return results

    async def run(
        self,
        user_ids: List[str],
        crawl_related: bool = False,
        max_related: int = 50,
        max_depth: int = 1,
    ) -> List[Dict[str, Any]]:
        """完整运行流程：setup -> fetch -> cleanup。

        Args:
            user_ids: 用户 ID 列表。
            crawl_related: 是否爬取关联用户。
            max_related: 每个用户最多爬取多少个关联用户。
            max_depth: 关联用户最大爬取深度。

        Returns:
            爬取结果列表。
        """
        await self.setup()
        try:
            if crawl_related:
                return await self.fetch_users_with_related(
                    user_ids, max_related, max_depth
                )
            return await self.fetch_users(user_ids)
        finally:
            await self.cleanup()
