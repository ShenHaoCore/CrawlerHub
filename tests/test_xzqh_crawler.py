"""中国行政区划爬虫测试。

使用 ``httpx.MockTransport`` mock 接口，覆盖：

- 请求头（WAF 要求 Referer）
- 单节点 / 下级 / 子树抓取
- 全国三级（一次请求）与全国四级（两阶段合并乡级）
- 下钻失败时的降级行为
- 全量快照落盘
- 走统一存储与去重的按代码批量抓取
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

import httpx
import pytest

from crawlerhub.core.client import HttpClient
from crawlerhub.core.config import Config
from crawlerhub.core.dedup import MemoryDedup
from crawlerhub.core.storage import JsonStorage
from crawlerhub.platforms.xzqh.crawler import XzqhCrawler

API_BASE = "https://dmfw.mca.gov.cn/9095/xzqh"
REFERER = "https://dmfw.mca.gov.cn/XzqhVersionPublish.html"


def inject_client(crawler: XzqhCrawler, handler: Callable[[httpx.Request], httpx.Response]) -> None:
    """给爬虫注入 mock HTTP 客户端。"""
    client = HttpClient(max_retries=1)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        timeout=10,
        follow_redirects=True,
    )
    crawler._client = client


def patch_setup(crawler: XzqhCrawler, handler: Callable[[httpx.Request], httpx.Response]) -> None:
    """包装 setup，让 run_all 使用 mock 客户端。"""
    original_setup = crawler.setup

    async def mock_setup() -> None:
        await original_setup()
        inject_client(crawler, handler)

    crawler.setup = mock_setup  # type: ignore[method-assign]


def make_handler(
    responses: Dict[str, Dict[str, Any]],
    calls: List[Tuple[str, str, str]],
) -> Callable[[httpx.Request], httpx.Response]:
    """构造按 ``code`` 分发的 mock handler。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith(API_BASE)
        code = request.url.params.get("code", "")
        calls.append(
            (
                code,
                request.url.params.get("maxLevel", ""),
                request.headers.get("referer", ""),
            )
        )
        if code not in responses:
            return httpx.Response(404, json={"status": 404, "message": "not mocked"})
        payload = {
            "data": responses[code],
            "message": None,
            "status": 200,
            "total": 1,
            "tag": None,
        }
        return httpx.Response(200, json=payload)

    return handler


def build_crawler(
    tmp_path: Path,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    snapshot_max_level: int = 4,
    subtree_max_level: int = 3,
) -> XzqhCrawler:
    """构造注入了 mock 客户端的爬虫。"""
    config = Config(
        base_config={
            "request": {"timeout": 10, "max_retries": 1},
            "storage": {"default": "json", "output_dir": str(tmp_path)},
            "dedup": {"backend": "memory"},
        },
        platform_config={
            "platform": "xzqh",
            "api_base": API_BASE,
            "referer": REFERER,
            "subtree_max_level": subtree_max_level,
            "snapshot": {
                "max_level": snapshot_max_level,
                "tree_output": str(tmp_path / "xzqh_tree.json"),
                "flat_output": str(tmp_path / "xzqh_divisions.jsonl"),
            },
        },
    )
    crawler = XzqhCrawler(
        config=config,
        storage=JsonStorage(tmp_path),
        dedup=MemoryDedup(),
    )
    inject_client(crawler, handler)
    return crawler


@pytest.fixture
def xzqh_responses(
    xzqh_phase1_tree: Dict[str, Any],
    xzqh_phase2_subtrees: Dict[str, Any],
) -> Dict[str, Any]:
    """按 ``code`` 索引的 mock 响应集合。"""
    responses: Dict[str, Any] = {
        "": xzqh_phase1_tree,
        "420000000000": copy.deepcopy(xzqh_phase1_tree["children"][0]),
        "110000000000": copy.deepcopy(xzqh_phase1_tree["children"][1]),
    }
    responses.update(xzqh_phase2_subtrees)
    return responses


@pytest.fixture
def xzqh_calls() -> List[Tuple[str, str, str]]:
    return []


class TestSingleNode:
    async def test_fetch_user_returns_single_record(
        self, xzqh_responses: Dict[str, Any], xzqh_calls: List[Tuple[str, str, str]], tmp_path: Path
    ) -> None:
        handler = make_handler(xzqh_responses, xzqh_calls)
        crawler = build_crawler(tmp_path, handler)

        record = await crawler.fetch_user("420000000000")

        assert record["platform"] == "xzqh"
        assert record["code"] == "420000000000"
        assert record["name"] == "湖北省"
        assert record["level"] == 1
        assert record["level_name"] == "省级"
        assert record["type"] == "省"
        assert record["code6"] == "420000"
        assert "children" not in record

    async def test_request_carries_referer_header(
        self, xzqh_responses: Dict[str, Any], xzqh_calls: List[Tuple[str, str, str]], tmp_path: Path
    ) -> None:
        """接口挂在 WAF 之后，缺失 Referer 会 403，必须带上。"""
        handler = make_handler(xzqh_responses, xzqh_calls)
        crawler = build_crawler(tmp_path, handler)

        await crawler.fetch_user("420000000000")

        assert xzqh_calls == [("420000000000", "1", REFERER)]

    async def test_fetch_children_returns_codes(
        self, xzqh_responses: Dict[str, Any], xzqh_calls: List[Tuple[str, str, str]], tmp_path: Path
    ) -> None:
        handler = make_handler(xzqh_responses, xzqh_calls)
        crawler = build_crawler(tmp_path, handler)

        children = await crawler.fetch_children("420000000000")

        assert children == ["420100000000", "429004000000"]

    async def test_fetch_related_users_maps_to_children(
        self, xzqh_responses: Dict[str, Any], xzqh_calls: List[Tuple[str, str, str]], tmp_path: Path
    ) -> None:
        handler = make_handler(xzqh_responses, xzqh_calls)
        crawler = build_crawler(tmp_path, handler)

        assert await crawler.fetch_related_users("110000000000") == ["110101000000"]

    async def test_non_json_response_raises_parse_error(
        self, tmp_path: Path
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>WAF</html>")

        crawler = build_crawler(tmp_path, handler)
        from crawlerhub.core.exceptions import ParseError

        with pytest.raises(ParseError, match="返回非 JSON"):
            await crawler.fetch_user("420000000000")


class TestFetchFlat:
    async def test_three_levels_returns_seven_records(
        self, xzqh_responses: Dict[str, Any], xzqh_calls: List[Tuple[str, str, str]], tmp_path: Path
    ) -> None:
        handler = make_handler(xzqh_responses, xzqh_calls)
        crawler = build_crawler(tmp_path, handler)

        records = await crawler.fetch_flat(code="", max_level=3)

        assert len(records) == 7
        assert [r["name"] for r in records] == [
            "湖北省",
            "武汉市",
            "江岸区",
            "武昌区",
            "仙桃市",
            "北京市",
            "东城区",
        ]
        assert xzqh_calls == [("", "3", REFERER)]


class TestCrawlAll:
    async def test_three_levels_uses_single_request(
        self, xzqh_responses: Dict[str, Any], xzqh_calls: List[Tuple[str, str, str]], tmp_path: Path
    ) -> None:
        handler = make_handler(xzqh_responses, xzqh_calls)
        crawler = build_crawler(tmp_path, handler, snapshot_max_level=3)

        result = await crawler.crawl_all()

        assert result["requests"] == 1
        assert len(result["records"]) == 7
        assert xzqh_calls == [("", "3", REFERER)]

    async def test_four_levels_expands_prefecture_tier(
        self, xzqh_responses: Dict[str, Any], xzqh_calls: List[Tuple[str, str, str]], tmp_path: Path
    ) -> None:
        handler = make_handler(xzqh_responses, xzqh_calls)
        crawler = build_crawler(tmp_path, handler, snapshot_max_level=4)

        result = await crawler.crawl_all()

        # 1 次拿省/地/县 + 3 次下钻地级层（武汉市 / 仙桃市 / 东城区）
        assert result["requests"] == 4
        assert [code for code, _, _ in xzqh_calls] == [
            "",
            "420100000000",
            "429004000000",
            "110101000000",
        ]
        assert all(level == "2" for _, level, _ in xzqh_calls[1:])

        records = result["records"]
        assert len(records) == 11
        names = [r["name"] for r in records]
        assert "大智街道" in names
        assert "干河街道" in names
        assert "东华门街道" in names

        street = next(r for r in records if r["name"] == "大智街道")
        assert street["level"] == 4
        assert street["level_name"] == "乡级"
        assert street["full_name"] == "湖北省武汉市江岸区大智街道"
        assert street["parent_name"] == "江岸区"

    async def test_four_levels_merges_into_tree(
        self, xzqh_responses: Dict[str, Any], xzqh_calls: List[Tuple[str, str, str]], tmp_path: Path
    ) -> None:
        """乡级应被合并进树（而不是只出现在扁平记录里）。"""
        handler = make_handler(xzqh_responses, xzqh_calls)
        crawler = build_crawler(tmp_path, handler, snapshot_max_level=4)

        tree = (await crawler.crawl_all())["tree"]

        hubei = tree["children"][0]
        wuhan = hubei["children"][0]
        jiang_an = wuhan["children"][0]
        assert jiang_an["name"] == "江岸区"
        assert [c["name"] for c in jiang_an["children"]] == ["大智街道"]

    async def test_drilldown_failure_keeps_partial_tree(
        self, xzqh_responses: Dict[str, Any], tmp_path: Path
    ) -> None:
        """单个地级下钻失败时只降级该分支，其余数据照常返回。"""

        def handler(request: httpx.Request) -> httpx.Response:
            code = request.url.params.get("code", "")
            if code == "420100000000":
                return httpx.Response(500, text="boom")
            payload = {
                "data": xzqh_responses[code],
                "message": None,
                "status": 200,
                "total": 1,
                "tag": None,
            }
            return httpx.Response(200, json=payload)

        crawler = build_crawler(tmp_path, handler, snapshot_max_level=4)

        result = await crawler.crawl_all()

        names = [r["name"] for r in result["records"]]
        # 武汉市分支下钻失败，江岸区/武昌区保留但无乡级
        assert "江岸区" in names
        assert "大智街道" not in names
        # 其他分支正常
        assert "干河街道" in names
        assert "东华门街道" in names
        assert result["requests"] == 3


class TestRunAll:
    async def test_writes_snapshot_files(
        self, xzqh_responses: Dict[str, Any], xzqh_calls: List[Tuple[str, str, str]], tmp_path: Path
    ) -> None:
        handler = make_handler(xzqh_responses, xzqh_calls)
        crawler = build_crawler(tmp_path, handler, snapshot_max_level=4)
        patch_setup(crawler, handler)

        result = await crawler.run_all()

        tree_path = tmp_path / "xzqh_tree.json"
        flat_path = tmp_path / "xzqh_divisions.jsonl"
        assert tree_path.exists()
        assert flat_path.exists()

        tree = json.loads(tree_path.read_text(encoding="utf-8"))
        assert tree["children"][0]["name"] == "湖北省"

        lines = flat_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 11
        assert json.loads(lines[0])["name"] == "湖北省"

        assert result["node_count"] == 11
        assert set(result["written"]) == {"tree", "flat"}

    async def test_snapshot_overwrites_previous_run(
        self, xzqh_responses: Dict[str, Any], xzqh_calls: List[Tuple[str, str, str]], tmp_path: Path
    ) -> None:
        """快照语义：重复运行不累积历史记录。"""
        for _ in range(2):
            handler = make_handler(xzqh_responses, xzqh_calls)
            crawler = build_crawler(tmp_path, handler, snapshot_max_level=4)
            patch_setup(crawler, handler)
            await crawler.run_all()

        flat_path = tmp_path / "xzqh_divisions.jsonl"
        assert len(flat_path.read_text(encoding="utf-8").strip().splitlines()) == 11


class TestRunSubtree:
    async def test_run_writes_subtree_through_storage(
        self,
        xzqh_phase1_tree: Dict[str, Any],
        xzqh_phase2_subtrees: Dict[str, Any],
        xzqh_calls: List[Tuple[str, str, str]],
        tmp_path: Path,
    ) -> None:
        hubei = copy.deepcopy(xzqh_phase1_tree["children"][0])
        responses: Dict[str, Any] = {"420000000000": hubei}
        handler = make_handler(responses, xzqh_calls)
        crawler = build_crawler(tmp_path, handler)
        patch_setup(crawler, handler)

        results = await crawler.run(["420000000000"])

        assert [r["name"] for r in results] == ["湖北省", "武汉市", "江岸区", "武昌区", "仙桃市"]
        assert xzqh_calls == [("420000000000", "3", REFERER)]

        output = (tmp_path / "xzqh_divisions.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(output) == 5
        assert json.loads(output[0])["name"] == "湖北省"

    async def test_run_dedups_on_second_pass(
        self,
        xzqh_phase1_tree: Dict[str, Any],
        xzqh_calls: List[Tuple[str, str, str]],
        tmp_path: Path,
    ) -> None:
        hubei = copy.deepcopy(xzqh_phase1_tree["children"][0])
        handler = make_handler({"420000000000": hubei}, xzqh_calls)
        crawler = build_crawler(tmp_path, handler)
        patch_setup(crawler, handler)

        first = await crawler.run(["420000000000"])
        second = await crawler.run(["420000000000"])

        assert len(first) == 5
        assert second == []

        output = (tmp_path / "xzqh_divisions.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(output) == 5
