"""微博平台爬虫。

注意：微博用户信息接口需要登录态 Cookie。请将合法获取的 Cookie 放入
``data/cookies/weibo.json``，格式见 :class:`crawlerhub.core.cookie.CookieManager`。

合规提示：请仅爬取公开数据，并遵守微博 robots.txt 与服务条款，合理控制请求频率。
"""

from __future__ import annotations

from typing import Any, Dict, List

from crawlerhub.core.client import parse_json_response
from crawlerhub.core.exceptions import LoginRequiredError
from crawlerhub.core.logger import logger
from crawlerhub.platforms.base import BaseCrawler
from crawlerhub.platforms.weibo.parser import (
    parse_follow_list,
    parse_user_info,
    raise_if_login_required,
)


class WeiboCrawler(BaseCrawler):
    """微博爬虫实现。"""

    @property
    def platform_name(self) -> str:
        return "weibo"

    @property
    def api_base(self) -> str:
        return self.config.get("api_base", "https://weibo.com/ajax")

    async def fetch_user(self, user_id: str) -> Dict[str, Any]:
        """爬取微博用户基础信息。

        合并 ``/profile/info``（昵称、粉丝数等）和 ``/profile/detail``（简介、性别等）
        两个接口的数据，返回完整用户信息。

        Args:
            user_id: 微博用户 ID（数字 UID）。

        Returns:
            标准化的用户信息字典。

        Raises:
            LoginRequiredError: Cookie 缺失或失效。
            ParseError: 返回数据解析失败。
        """
        cookies = self._cookies()
        if not cookies:
            raise LoginRequiredError(
                "微博用户信息接口需要登录态 Cookie，请在 data/cookies/weibo.json 中配置"
            )

        headers = {
            "Referer": f"https://weibo.com/u/{user_id}",
            "X-XSRF-TOKEN": cookies.get("XSRF-TOKEN", ""),
        }

        logger.info(f"[weibo] 开始爬取用户 {user_id}")

        # 1. /profile/info — 昵称、粉丝数、微博数等
        info_url = f"{self.api_base}/profile/info"
        params = {"uid": user_id}
        info_resp = await self.client.get(
            info_url, params=params, headers=headers, cookies=cookies
        )
        info_raw = parse_json_response(info_resp, context="微博 profile/info")
        raise_if_login_required(info_raw, api="profile/info")

        # 2. /profile/detail — 简介、性别、注册时间等
        detail_url = f"{self.api_base}/profile/detail"
        detail_resp = await self.client.get(
            detail_url, params=params, headers=headers, cookies=cookies
        )
        detail_raw = parse_json_response(detail_resp, context="微博 profile/detail")
        raise_if_login_required(detail_raw, api="profile/detail")

        logger.debug(f"[weibo] info 返回: {str(info_raw)[:300]}")
        logger.debug(f"[weibo] detail 返回: {str(detail_raw)[:300]}")

        return parse_user_info(info_raw, detail_raw, user_id)

    async def fetch_followers(self, user_id: str, max_pages: int = 5) -> List[str]:
        """获取微博用户粉丝列表。

        Args:
            user_id: 微博用户 ID。
            max_pages: 最大分页数，每页 50 条。

        Returns:
            粉丝 UID 列表。
        """
        return await self._fetch_friendship_list(user_id, "fans", max_pages)

    async def fetch_following(self, user_id: str, max_pages: int = 5) -> List[str]:
        """获取微博用户关注列表。

        Args:
            user_id: 微博用户 ID。
            max_pages: 最大分页数，每页 50 条。

        Returns:
            关注 UID 列表。
        """
        return await self._fetch_friendship_list(user_id, "follows", max_pages)

    async def _fetch_friendship_list(
        self, user_id: str, follow_type: str, max_pages: int
    ) -> List[str]:
        """通用粉丝/关注列表拉取。

        微博当前使用 ``/ajax/friendships/friends`` 接口，通过 ``type`` 参数区分：
        - ``fans``: 粉丝列表
        - ``follows``: 关注列表

        Args:
            user_id: 微博用户 ID。
            follow_type: ``fans`` 或 ``follows``。
            max_pages: 最大分页数。
        """
        cookies = self._cookies()
        if not cookies:
            raise LoginRequiredError("微博接口需要登录态 Cookie")

        headers = {
            "Referer": f"https://weibo.com/u/{user_id}",
            "X-XSRF-TOKEN": cookies.get("XSRF-TOKEN", ""),
            "Accept": "application/json, text/plain, */*",
        }
        url = f"{self.api_base}/friendships/friends"
        uids: List[str] = []

        for page in range(1, max_pages + 1):
            params = {"uid": user_id, "page": page, "count": 50, "type": follow_type}
            resp = await self.client.get(
                url, params=params, headers=headers, cookies=cookies
            )
            raw = parse_json_response(resp, context=f"微博 {follow_type} 列表")
            raise_if_login_required(raw, api=f"friendships/friends({follow_type})")

            page_uids = parse_follow_list(raw)
            if not page_uids:
                break
            uids.extend(page_uids)
            logger.debug(f"[weibo] {follow_type} 第{page}页: {len(page_uids)} 条")

        return list(dict.fromkeys(uids))  # 去重保序

    async def fetch_related_users(self, user_id: str) -> List[str]:
        """获取关联用户（粉丝 + 关注）UID 列表。

        Args:
            user_id: 微博用户 ID。

        Returns:
            去重后的关联用户 UID 列表。
        """
        max_pages = self.config.get("crawl_related.max_pages", 5)
        followers = await self.fetch_followers(user_id, max_pages)
        following = await self.fetch_following(user_id, max_pages)
        related = list(dict.fromkeys(followers + following))
        logger.info(f"[weibo] 用户 {user_id} 关联用户: {len(followers)} 粉丝 + {len(following)} 关注 = {len(related)} 人")
        return related
