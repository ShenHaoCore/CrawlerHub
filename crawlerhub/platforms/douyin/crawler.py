"""抖音平台爬虫。

注意：抖音用户信息接口需要登录态 Cookie（含 ttwid、msToken 等）。请将合法获取的 Cookie 放入
``data/cookies/douyin.json``。

关联用户（粉丝/关注）接口需要 ``msToken``，若 Cookie 中缺失将自动生成一个兜底值，
但部分场景下仍需从浏览器获取真实 msToken。

合规提示：请仅爬取公开数据，并遵守抖音 robots.txt 与服务条款，合理控制请求频率。
"""

from __future__ import annotations

import asyncio
import base64
import os
from typing import Any, Dict, List, Optional

from crawlerhub.core.client import parse_json_response
from crawlerhub.core.exceptions import LoginRequiredError
from crawlerhub.core.logger import logger
from crawlerhub.platforms.base import BaseCrawler
from crawlerhub.platforms.douyin.parser import parse_follow_list, parse_user_info

#: 抖音接口的业务成功码。
API_STATUS_SUCCESS = 0

#: 抖音接口表示「未登录」的业务码（Cookie 或 msToken 失效）。
API_STATUS_NOT_LOGGED_IN = 8


class DouyinCrawler(BaseCrawler):
    """抖音爬虫实现。"""

    @property
    def platform_name(self) -> str:
        return "douyin"

    @property
    def api_base(self) -> str:
        return self.config.get("api_base", "https://www.douyin.com/aweme/v1/web")

    @staticmethod
    def _gen_ms_token(length: int = 128) -> str:
        """生成兜底的 msToken。

        抖音 web 端 msToken 由浏览器 JS 生成，与会话绑定。
        当 Cookie 中缺失时生成一个随机值作为兜底。
        """
        rand_bytes = os.urandom(length)
        return base64.b64encode(rand_bytes).decode("ascii").rstrip("=")[:length]

    def _cookies_with_mstoken(self) -> Optional[Dict[str, str]]:
        """获取 Cookie 字典，确保包含 msToken。"""
        cookies = self._cookies()
        if not cookies:
            return None
        if not cookies.get("msToken"):
            cookies["msToken"] = self._gen_ms_token()
            logger.debug("[douyin] Cookie 中缺少 msToken，已生成兜底值")
        return cookies

    async def fetch_user(self, user_id: str) -> Dict[str, Any]:
        """爬取抖音用户基础信息。

        Args:
            user_id: 抖音 sec_uid（抖音用户的加密标识）。

        Returns:
            标准化的用户信息字典。

        Raises:
            LoginRequiredError: Cookie 缺失或失效。
            ParseError: 返回数据解析失败。
        """
        cookies = self._cookies()
        if not cookies:
            raise LoginRequiredError(
                "抖音用户信息接口需要登录态 Cookie，请在 data/cookies/douyin.json 中配置"
            )

        url = f"{self.api_base}/user/profile/other"
        params = {"sec_user_id": user_id}

        logger.info(f"[douyin] 开始爬取用户 {user_id}")
        response = await self.client.get(url, params=params, cookies=cookies)
        raw = parse_json_response(response, context="抖音 user/profile/other")

        if raw.get("status_code") == API_STATUS_NOT_LOGGED_IN:
            raise LoginRequiredError(
                f"抖音接口需要登录态（status_code=8: {raw.get('status_msg', '用户未登录')}），"
                "请更新 data/cookies/douyin.json"
            )

        logger.debug(f"[douyin] 接口返回: {str(raw)[:500]}")
        return parse_user_info(raw, user_id)

    async def fetch_followers(self, sec_uid: str, max_pages: int = 5) -> List[str]:
        """获取抖音用户粉丝列表。

        Args:
            sec_uid: 抖音 sec_uid。
            max_pages: 最大分页数，每页 20 条。

        Returns:
            粉丝 sec_uid 列表。
        """
        return await self._fetch_follow_list(sec_uid, "follower", max_pages)

    async def fetch_following(self, sec_uid: str, max_pages: int = 5) -> List[str]:
        """获取抖音用户关注列表。

        Args:
            sec_uid: 抖音 sec_uid。
            max_pages: 最大分页数，每页 20 条。

        Returns:
            关注 sec_uid 列表。
        """
        return await self._fetch_follow_list(sec_uid, "following", max_pages)

    async def _fetch_follow_list(
        self, sec_uid: str, endpoint: str, max_pages: int
    ) -> List[str]:
        """通用粉丝/关注列表拉取。

        抖音 web 端使用 ``/aweme/v1/web/user/{follower|following}/list/`` 接口
        （注意路径末尾无 ``/other``）。需要 msToken 与登录态 Cookie。

        Args:
            sec_uid: 抖音 sec_uid。
            endpoint: ``follower`` 或 ``following``。
            max_pages: 最大分页数。

        Returns:
            sec_uid 字符串列表。
        """
        cookies = self._cookies_with_mstoken()
        if not cookies:
            raise LoginRequiredError("抖音接口需要登录态 Cookie")

        ms_token = cookies.get("msToken", "")
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": f"https://www.douyin.com/user/{sec_uid}",
            "Accept": "application/json, text/plain, */*",
        }
        url = f"{self.api_base}/user/{endpoint}/list/"
        sec_uids: List[str] = []
        cursor = 0

        for _page in range(max_pages):
            params = {
                "sec_user_id": sec_uid,
                "count": 20,
                "cursor": cursor,
                "msToken": ms_token,
                "aid": "6383",
            }
            resp = await self.client.get(url, params=params, headers=headers, cookies=cookies)
            if not resp.text.strip():
                # 空响应通常是接口限流或反爬，退避一段时间后停止翻页
                logger.warning(f"[douyin] {endpoint} 接口返回空响应，疑似限流，退避 30 秒")
                await asyncio.sleep(30)
                break
            raw = parse_json_response(resp, context=f"抖音 {endpoint} 列表")

            status_code = raw.get("status_code", API_STATUS_SUCCESS)
            if status_code == API_STATUS_NOT_LOGGED_IN:
                # 未登录，通常是 msToken 失效。关联用户属于「扩展」数据，
                # 用户本体已经落盘，因此这里降级处理：记警告后停止翻页，
                # 不中断整个任务（与基类对「关联用户扩展」失败的策略一致）。
                msg = raw.get("status_msg", "用户未登录")
                logger.warning(
                    f"[douyin] {endpoint} 接口返回未登录（status_code=8: {msg}），"
                    "请更新 data/cookies/douyin.json 中的 msToken；本次仅停止扩展"
                )
                break

            page_uids = parse_follow_list(raw)
            if not page_uids:
                break
            sec_uids.extend(page_uids)
            has_more = raw.get("has_more", False)
            cursor = raw.get("min_time", cursor)
            if not has_more:
                break
            logger.debug(f"[douyin] {endpoint} 已获取 {len(sec_uids)} 条")

        return list(dict.fromkeys(sec_uids))

    async def fetch_related_users(self, sec_uid: str) -> List[str]:
        """获取关联用户（粉丝 + 关注）sec_uid 列表。

        Args:
            sec_uid: 抖音 sec_uid。

        Returns:
            去重后的关联用户 sec_uid 列表。
        """
        max_pages = self.config.get("crawl_related.max_pages", 5)
        followers = await self.fetch_followers(sec_uid, max_pages)
        following = await self.fetch_following(sec_uid, max_pages)
        related = list(dict.fromkeys(followers + following))
        logger.info(
            f"[douyin] 用户 {sec_uid[:20]}... 关联用户: "
            f"{len(followers)} 粉丝 + {len(following)} 关注 = {len(related)} 人"
        )
        return related
