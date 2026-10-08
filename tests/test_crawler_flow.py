"""爬虫全流程集成测试。

使用 MockTransport mock HTTP 请求，验证：
- 微博/抖音爬虫的完整 setup -> fetch_user -> cleanup 流程
- 去重：重复爬取同一用户会跳过
- 存储：数据正确写入 JSON/CSV/SQLite
- 无 Cookie 时抛出 LoginRequiredError
"""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from crawlerhub.core.client import HttpClient
from crawlerhub.core.config import Config
from crawlerhub.core.cookie import CookieManager
from crawlerhub.core.dedup import MemoryDedup
from crawlerhub.core.exceptions import LoginRequiredError
from crawlerhub.core.storage import JsonStorage, SqliteStorage
from crawlerhub.platforms.douyin.crawler import DouyinCrawler
from crawlerhub.platforms.weibo.crawler import WeiboCrawler


def make_mock_client(crawler: Any, handler) -> None:
    """给爬虫注入 mock HTTP 客户端。"""
    client = HttpClient(max_retries=1)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        timeout=30,
        follow_redirects=True,
    )
    crawler._client = client


def get_weibo_config() -> Config:
    return Config(
        base_config={
            "request": {"timeout": 10, "max_retries": 1},
            "storage": {"default": "json", "output_dir": "data/output"},
            "dedup": {"backend": "memory"},
        },
        platform_config={
            "platform": "weibo",
            "api_base": "https://weibo.com/ajax",
        },
    )


def get_douyin_config() -> Config:
    return Config(
        base_config={
            "request": {"timeout": 10, "max_retries": 1},
            "storage": {"default": "json", "output_dir": "data/output"},
            "dedup": {"backend": "memory"},
        },
        platform_config={
            "platform": "douyin",
            "api_base": "https://www.douyin.com/aweme/v1/web",
        },
    )


class TestWeiboCrawlerFlow:
    async def test_fetch_user_success(
        self, tmp_cookie_file: Path, weibo_raw_data, weibo_detail_data, tmp_path: Path
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert "uid" in request.url.params
            assert request.url.params["uid"] == "123456"
            if "/profile/info" in str(request.url):
                return httpx.Response(200, json=weibo_raw_data)
            return httpx.Response(200, json=weibo_detail_data)

        config = Config(
            {"request": {"timeout": 10, "max_retries": 1},
             "storage": {"default": "json", "output_dir": str(tmp_path)},
             "dedup": {"backend": "memory"}},
            {"platform": "weibo", "api_base": "https://weibo.com/ajax"},
        )
        cookie = CookieManager(tmp_cookie_file)
        storage = JsonStorage(tmp_path)
        dedup = MemoryDedup()
        crawler = WeiboCrawler(config=config, cookie_manager=cookie, storage=storage, dedup=dedup)

        make_mock_client(crawler, handler)
        result = await crawler.fetch_user("123456")

        assert result["platform"] == "weibo"
        assert result["user_id"] == "123456"
        assert result["screen_name"] == "测试用户"
        assert result["followers_count"] == 1000

    async def test_fetch_user_no_cookie_raises(self, tmp_path: Path) -> None:
        config = get_weibo_config()
        cookie = CookieManager(tmp_path / "nonexistent.json")  # 空 Cookie
        crawler = WeiboCrawler(config=config, cookie_manager=cookie)

        with pytest.raises(LoginRequiredError, match="需要登录态 Cookie"):
            await crawler.fetch_user("123456")

    async def test_run_full_flow_with_dedup(
        self, tmp_cookie_file: Path, weibo_raw_data, weibo_detail_data, tmp_path: Path
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "/profile/info" in str(request.url):
                return httpx.Response(200, json=weibo_raw_data)
            return httpx.Response(200, json=weibo_detail_data)

        config = Config(
            {"request": {"timeout": 10, "max_retries": 1},
             "storage": {"default": "json", "output_dir": str(tmp_path)},
             "dedup": {"backend": "memory"}},
            {"platform": "weibo", "api_base": "https://weibo.com/ajax"},
        )
        cookie = CookieManager(tmp_cookie_file)
        storage = JsonStorage(tmp_path)
        dedup = MemoryDedup()
        crawler = WeiboCrawler(config=config, cookie_manager=cookie, storage=storage, dedup=dedup)

        # Patch setup/cleanup to use mock client
        original_setup = crawler.setup
        original_cleanup = crawler.cleanup

        async def mock_setup():
            await original_setup()
            make_mock_client(crawler, handler)

        crawler.setup = mock_setup
        crawler.cleanup = original_cleanup

        # 首次爬取两个用户
        results = await crawler.run(["123456", "789012"])
        assert len(results) == 2

        # 验证 JSON 输出
        output = (tmp_path / "weibo_users.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(output) == 2
        for line in output:
            record = json.loads(line)
            assert record["platform"] == "weibo"

    async def test_dedup_skips_duplicate(
        self, tmp_cookie_file: Path, weibo_raw_data, weibo_detail_data, tmp_path: Path
    ) -> None:
        user_call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal user_call_count
            user_call_count += 1
            if "/profile/info" in str(request.url):
                return httpx.Response(200, json=weibo_raw_data)
            return httpx.Response(200, json=weibo_detail_data)

        config = Config(
            {"request": {"timeout": 10, "max_retries": 1},
             "storage": {"default": "json", "output_dir": str(tmp_path)},
             "dedup": {"backend": "memory"}},
            {"platform": "weibo", "api_base": "https://weibo.com/ajax"},
        )
        cookie = CookieManager(tmp_cookie_file)
        dedup = MemoryDedup()
        storage = JsonStorage(tmp_path)
        crawler = WeiboCrawler(config=config, cookie_manager=cookie, storage=storage, dedup=dedup)

        # 预标记用户 123456 为已爬取
        key = dedup.make_key("weibo", content_id="123456")
        await dedup.mark(key)

        original_setup = crawler.setup

        async def mock_setup():
            await original_setup()
            make_mock_client(crawler, handler)

        crawler.setup = mock_setup

        results = await crawler.run(["123456", "789012"])
        # 123456 被去重跳过，只有 789012
        assert len(results) == 1
        assert results[0]["user_id"] == "789012"
        assert user_call_count == 2  # 789012 发了 2 次请求（info + detail）


class TestDouyinCrawlerFlow:
    async def test_fetch_user_success(self, tmp_cookie_file: Path, douyin_raw_data, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert "sec_user_id" in request.url.params
            return httpx.Response(200, json=douyin_raw_data)

        config = Config(
            {"request": {"timeout": 10, "max_retries": 1},
             "storage": {"default": "json", "output_dir": str(tmp_path)},
             "dedup": {"backend": "memory"}},
            {"platform": "douyin", "api_base": "https://www.douyin.com/aweme/v1/web"},
        )
        cookie = CookieManager(tmp_cookie_file)
        storage = JsonStorage(tmp_path)
        dedup = MemoryDedup()
        crawler = DouyinCrawler(config=config, cookie_manager=cookie, storage=storage, dedup=dedup)

        make_mock_client(crawler, handler)
        result = await crawler.fetch_user("MS4wLjABAAAAxxx")

        assert result["platform"] == "douyin"
        assert result["sec_uid"] == "MS4wLjABAAAAxxx"
        assert result["nickname"] == "抖音测试用户"
        assert result["followers_count"] == 5000

    async def test_fetch_user_no_cookie_raises(self, tmp_path: Path) -> None:
        config = get_douyin_config()
        cookie = CookieManager(tmp_path / "nonexistent.json")
        crawler = DouyinCrawler(config=config, cookie_manager=cookie)

        with pytest.raises(LoginRequiredError, match="需要登录态 Cookie"):
            await crawler.fetch_user("MS4wLjABAAAAxxx")


class TestLoginFailureEscalation:
    """登录态失效是**会话级**致命错误，必须向上传播。

    否则任务会以「抓到 0 条 + status: ok」收尾——摘要看起来一切正常，
    实际上整批数据一条都没拿到。这是本次修复的核心动机。
    """

    @staticmethod
    def _inject(crawler: Any, handler) -> None:
        """把 mock 客户端注入爬虫（沿用本文件既有的 setup 替换手法）。"""
        original_setup = crawler.setup

        async def mock_setup():
            await original_setup()
            make_mock_client(crawler, handler)

        crawler.setup = mock_setup

    @staticmethod
    def _weibo_crawler(tmp_cookie_file: Path, tmp_path: Path) -> WeiboCrawler:
        return WeiboCrawler(
            config=Config(
                {"request": {"timeout": 10, "max_retries": 1},
                 "storage": {"default": "json", "output_dir": str(tmp_path)},
                 "dedup": {"backend": "memory"}},
                {"platform": "weibo", "api_base": "https://weibo.com/ajax"},
            ),
            cookie_manager=CookieManager(tmp_cookie_file),
            storage=JsonStorage(tmp_path),
            dedup=MemoryDedup(),
        )

    async def test_weibo_login_redirect_propagates(
        self, tmp_cookie_file: Path, tmp_path: Path
    ) -> None:
        """Cookie 失效 -> 302 到登录页 -> 任务失败，而不是报 ok / 0 条。"""

        def handler(request: httpx.Request) -> httpx.Response:
            if "login" in str(request.url):
                return httpx.Response(
                    200,
                    content="<html>请登录</html>".encode("gbk"),
                    headers={"content-type": "text/html; charset=utf-8"},
                )
            return httpx.Response(
                302,
                headers={"location": "https://login.sina.com.cn/sso/login.php?url=x"},
            )

        crawler = self._weibo_crawler(tmp_cookie_file, tmp_path)
        self._inject(crawler, handler)

        with pytest.raises(LoginRequiredError, match="重定向到登录页"):
            await crawler.run(["123456"], crawl_related=True, max_depth=1)

    async def test_weibo_business_code_propagates(
        self, tmp_cookie_file: Path, tmp_path: Path
    ) -> None:
        """Cookie 失效 -> 200 + ``ok=-100`` -> 同样必须失败。"""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"ok": -100, "url": "https://weibo.com/login.php?url=x"},
            )

        crawler = self._weibo_crawler(tmp_cookie_file, tmp_path)
        self._inject(crawler, handler)

        with pytest.raises(LoginRequiredError):
            await crawler.run(["123456"])

    async def test_douyin_not_logged_in_propagates(
        self, tmp_cookie_file: Path, tmp_path: Path
    ) -> None:
        """抖音 ``status_code=8``（未登录）同样必须失败。"""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"status_code": 8, "status_msg": "用户未登录"}
            )

        crawler = DouyinCrawler(
            config=Config(
                {"request": {"timeout": 10, "max_retries": 1},
                 "storage": {"default": "json", "output_dir": str(tmp_path)},
                 "dedup": {"backend": "memory"}},
                {"platform": "douyin", "api_base": "https://www.douyin.com/aweme/v1/web"},
            ),
            cookie_manager=CookieManager(tmp_cookie_file),
            storage=JsonStorage(tmp_path),
            dedup=MemoryDedup(),
        )
        self._inject(crawler, handler)

        with pytest.raises(LoginRequiredError, match="status_code=8"):
            await crawler.run(["MS4wLjABAAAAxxx"])

    async def test_related_expansion_failure_degrades_gracefully(
        self, tmp_cookie_file: Path, weibo_raw_data, weibo_detail_data, tmp_path: Path
    ) -> None:
        """关联用户拉取失败只降级：已抓到的用户必须保留，任务不算失败。"""

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "/profile/info" in url:
                return httpx.Response(200, json=weibo_raw_data)
            if "/profile/detail" in url:
                return httpx.Response(200, json=weibo_detail_data)
            return httpx.Response(500, text="boom")

        crawler = self._weibo_crawler(tmp_cookie_file, tmp_path)
        self._inject(crawler, handler)

        results = await crawler.run(["123456"], crawl_related=True, max_depth=1)

        assert [r["user_id"] for r in results] == ["123456"]


class TestSqliteIntegration:
    async def test_crawler_with_sqlite_storage(
        self, tmp_cookie_file: Path, weibo_raw_data, weibo_detail_data, tmp_path: Path
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "/profile/info" in str(request.url):
                return httpx.Response(200, json=weibo_raw_data)
            return httpx.Response(200, json=weibo_detail_data)

        db_url = f"sqlite:///{tmp_path}/test.db"
        config = Config(
            {"request": {"timeout": 10, "max_retries": 1},
             "storage": {"default": "sqlite", "sqlite": {"url": db_url}},
             "dedup": {"backend": "memory"}},
            {"platform": "weibo", "api_base": "https://weibo.com/ajax"},
        )
        cookie = CookieManager(tmp_cookie_file)
        storage = SqliteStorage(db_url)
        dedup = MemoryDedup()
        crawler = WeiboCrawler(config=config, cookie_manager=cookie, storage=storage, dedup=dedup)

        original_setup = crawler.setup

        async def mock_setup():
            await original_setup()
            make_mock_client(crawler, handler)

        crawler.setup = mock_setup

        await crawler.run(["123456"])

        # 验证 SQLite 写入
        from sqlalchemy import create_engine, text

        engine = create_engine(db_url)
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT * FROM weibo_users")).fetchall()
            assert len(rows) == 1
