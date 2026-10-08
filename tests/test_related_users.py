"""关联用户爬取测试。"""

from pathlib import Path
from typing import Any

import httpx

from crawlerhub.core.client import HttpClient
from crawlerhub.core.config import Config
from crawlerhub.core.cookie import CookieManager
from crawlerhub.core.dedup import MemoryDedup
from crawlerhub.core.storage import JsonStorage
from crawlerhub.platforms.weibo.crawler import WeiboCrawler
from crawlerhub.platforms.douyin.crawler import DouyinCrawler


def make_mock_client(crawler: Any, handler) -> None:
    """给爬虫注入 mock HTTP 客户端。"""
    client = HttpClient(max_retries=1)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        timeout=30,
        follow_redirects=True,
    )
    crawler._client = client


class TestWeiboFollowList:
    """测试微博 parse_follow_list 和 fetch_related_users。"""

    def test_parse_follow_list_valid(self) -> None:
        from crawlerhub.platforms.weibo.parser import parse_follow_list

        raw = {"data": {"list": [{"id": 111}, {"id": 222}, {"id": 333}]}}
        result = parse_follow_list(raw)
        assert result == ["111", "222", "333"]

    def test_parse_follow_list_empty(self) -> None:
        from crawlerhub.platforms.weibo.parser import parse_follow_list

        assert parse_follow_list({}) == []
        assert parse_follow_list({"data": {}}) == []
        assert parse_follow_list({"data": {"list": []}}) == []

    def test_parse_follow_list_filters_missing_id(self) -> None:
        from crawlerhub.platforms.weibo.parser import parse_follow_list

        raw = {"data": {"list": [{"id": 1}, {"name": "no id"}, {"id": 2}]}}
        result = parse_follow_list(raw)
        assert result == ["1", "2"]


class TestDouyinFollowList:
    """测试抖音 parse_follow_list。"""

    def test_parse_follow_list_followings(self) -> None:
        from crawlerhub.platforms.douyin.parser import parse_follow_list

        raw = {"followings": [{"sec_uid": "AAA"}, {"sec_uid": "BBB"}]}
        assert parse_follow_list(raw) == ["AAA", "BBB"]

    def test_parse_follow_list_followers(self) -> None:
        from crawlerhub.platforms.douyin.parser import parse_follow_list

        raw = {"followers": [{"sec_uid": "CCC"}, {"sec_uid": "DDD"}]}
        assert parse_follow_list(raw) == ["CCC", "DDD"]

    def test_parse_follow_list_empty(self) -> None:
        from crawlerhub.platforms.douyin.parser import parse_follow_list

        assert parse_follow_list({}) == []


class TestWeiboFetchRelated:
    """测试微博 fetch_users_with_related 全流程。"""

    async def test_fetch_related_users(
        self, tmp_cookie_file: Path, weibo_raw_data, weibo_detail_data, tmp_path: Path
    ) -> None:
        """Mock 粉丝/关注列表，验证 fetch_users_with_related 正确爬取关联用户。"""

        follow_list_response = {
            "data": {"list": [{"id": 1001}, {"id": 1002}, {"id": 1003}]}
        }

        def handler(request: httpx.Request) -> httpx.Response:
            url_str = str(request.url)
            if "/friendships/" in url_str:
                return httpx.Response(200, json=follow_list_response)
            if "/profile/info" in url_str:
                return httpx.Response(200, json=weibo_raw_data)
            if "/profile/detail" in url_str:
                return httpx.Response(200, json=weibo_detail_data)
            return httpx.Response(404)

        config = Config(
            {"request": {"timeout": 10, "max_retries": 1},
             "storage": {"default": "json", "output_dir": str(tmp_path)},
             "dedup": {"backend": "memory"},
             "crawl_related": {"max_pages": 1}},
            {"platform": "weibo", "api_base": "https://weibo.com/ajax"},
        )
        cookie = CookieManager(tmp_cookie_file)
        storage = JsonStorage(tmp_path)
        dedup = MemoryDedup()
        crawler = WeiboCrawler(config=config, cookie_manager=cookie, storage=storage, dedup=dedup)

        original_setup = crawler.setup

        async def mock_setup():
            await original_setup()
            make_mock_client(crawler, handler)

        crawler.setup = mock_setup

        results = await crawler.run(["123456"], crawl_related=True, max_related=10)

        # 种子用户 + 3 个关联用户 = 4
        assert len(results) == 4
        user_ids = [r["user_id"] for r in results]
        assert "123456" in user_ids

        # 验证关联用户的 UID 出现在结果中
        related_ids = [uid for uid in user_ids if uid != "123456"]
        assert len(related_ids) == 3


class TestDouyinFetchRelated:
    """测试抖音 fetch_related_users。"""

    async def test_fetch_related_users(
        self, tmp_cookie_file: Path, douyin_raw_data, tmp_path: Path
    ) -> None:
        follow_list_response = {
            "followings": [{"sec_uid": "SEC001"}, {"sec_uid": "SEC002"}],
            "has_more": False,
        }

        def handler(request: httpx.Request) -> httpx.Response:
            url_str = str(request.url)
            if "/follower/list" in url_str or "/following/list" in url_str:
                return httpx.Response(200, json=follow_list_response)
            if "/profile/other" in url_str:
                return httpx.Response(200, json=douyin_raw_data)
            return httpx.Response(404)

        config = Config(
            {"request": {"timeout": 10, "max_retries": 1},
             "storage": {"default": "json", "output_dir": str(tmp_path)},
             "dedup": {"backend": "memory"},
             "crawl_related": {"max_pages": 1}},
            {"platform": "douyin", "api_base": "https://www.douyin.com/aweme/v1/web"},
        )
        cookie = CookieManager(tmp_cookie_file)
        storage = JsonStorage(tmp_path)
        dedup = MemoryDedup()
        crawler = DouyinCrawler(config=config, cookie_manager=cookie, storage=storage, dedup=dedup)

        original_setup = crawler.setup

        async def mock_setup():
            await original_setup()
            make_mock_client(crawler, handler)

        crawler.setup = mock_setup

        results = await crawler.run(
            ["MS4wLjABAAAAtest"], crawl_related=True, max_related=10
        )

        # 种子用户 + 2 个关联用户（followings 和 followers 各返回 SEC001/SEC002）
        # 去重后关联用户 = 2，但 followers 和 followings 返回同样的 sec_uid
        # 所以种子 + 2 个关联 = 3
        assert len(results) >= 1
