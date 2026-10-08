"""中国行政区划爬虫（民政部·国家地名信息库）。

数据来源：https://dmfw.mca.gov.cn （中国·国家地名信息库）
接口文档：https://dmfw.mca.gov.cn/baseHtmls/index.htm

接口：``GET {api_base}/getList?code=&maxLevel=&year=``

数据口径（2026-10 实测复核）：

- 覆盖省、地、县、乡四级；每年 1 月发布截至上一年末的数据。
  以 ``XzqhVersionPublish.html`` 页面口径为准，当前数据截止 2025-12-31。
- 代码为 12 位，省/地/县三级的前 6 位即 GB/T 2260 代码。
- ``maxLevel`` 表示向根节点之下展开的层数，但**乡级只在根为地级或县级时
  才会返回**：以省为根请求最多只能拿到县级。因此全国四级需要两阶段抓取。
- 接口对 ``User-Agent`` / ``Referer`` 敏感，缺失会被 WAF 直接拒绝（403）。
- 接口不提供面积、人口、驻地等属性，只返回代码/名称/级别/类型。

合规提示：该接口是民政部面向开发者公开的接口服务，请遵守其使用说明，
合理控制请求频率，不要用于高频轮询。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from crawlerhub.core.exceptions import CrawlerHubError, ParseError
from crawlerhub.core.logger import logger
from crawlerhub.platforms.base import BaseCrawler
from crawlerhub.platforms.xzqh.parser import (
    count_nodes,
    flatten_node,
    flatten_tree,
    nodes_at_depth,
    parse_list_payload,
)

#: 全国省级单位数量（含港澳台），用于日志提示
EXPECTED_PROVINCES = 34


class XzqhCrawler(BaseCrawler):
    """中国行政区划（省/地/县/乡四级）爬虫实现。

    与微博、抖音等「一个 ID 对应一条记录」的平台不同，本平台的一个区划代码
    会展开成一棵子树。因此：

    - :meth:`fetch_user` 按单个代码返回一条记录；
    - :meth:`fetch_users` 会把每个代码的子树整棵摊平后逐条落盘（走统一存储与去重）；
    - :meth:`run_all` 走「全量快照」语义，直接覆盖写入配置文件。
    """

    #: 全国四级抓取时，第二阶段需要下钻的节点深度
    #: （虚拟根 -> 省级 -> 地级，即地级市 / 直辖市的市辖区 / 省直辖县级单位）
    EXPAND_DEPTH = 2

    @property
    def platform_name(self) -> str:
        return "xzqh"

    @property
    def api_base(self) -> str:
        return self.config.get("api_base", "https://dmfw.mca.gov.cn/9095/xzqh")

    @property
    def referer(self) -> str:
        return self.config.get(
            "referer", "https://dmfw.mca.gov.cn/XzqhVersionPublish.html"
        )

    @property
    def collection(self) -> str:
        """扁平记录写入存储时使用的集合名。"""
        return self.config.get("storage.collection", "xzqh_divisions")

    def _headers(self) -> Dict[str, str]:
        """构造请求头。

        该接口挂在 WAF 之后，缺失 ``Referer`` 会直接返回 403，
        因此这里显式带上来源页。
        """
        return {
            "Referer": self.referer,
            "Accept": "application/json, text/plain, */*",
        }

    async def _fetch_tree(
        self,
        code: str = "",
        max_level: int = 1,
        year: Optional[int] = None,
    ) -> Dict[str, Any]:
        """请求区划接口，返回未摊平的子树根节点。

        Args:
            code: 12 位行政区划代码，留空表示从全国根节点开始。
            max_level: 向根节点之下展开的层数。
            year: 数据年份，``None`` 表示使用接口最新版。

        Returns:
            子树根节点字典。

        Raises:
            ParseError: 响应解析失败。
            AntiCrawlError: 触发 WAF（如缺失请求头）。
            RequestError: 网络错误且重试耗尽。
        """
        params: Dict[str, Any] = {"code": code, "maxLevel": max_level}
        if year is not None:
            params["year"] = year

        url = f"{self.api_base}/getList"
        response = await self.client.get(url, params=params, headers=self._headers())

        try:
            payload = response.json()
        except ValueError as exc:
            raise ParseError(f"区划接口返回非 JSON: {exc}") from exc

        return parse_list_payload(payload, code=code)

    # ------------------------------------------------------------------
    # 与 BaseCrawler 契约对齐的接口
    # ------------------------------------------------------------------

    async def fetch_user(self, user_id: str) -> Dict[str, Any]:
        """按 12 位行政区划代码爬取单个区划节点。

        Args:
            user_id: 12 位行政区划代码，如 ``110000000000``（北京市）。

        Returns:
            单条扁平化区划记录。
        """
        node = await self._fetch_tree(code=user_id, max_level=1)
        return flatten_node(node)

    async def fetch_children(self, code: str) -> List[str]:
        """获取某个区划的直接下级代码列表。

        Args:
            code: 12 位行政区划代码。

        Returns:
            下级区划代码列表。
        """
        node = await self._fetch_tree(code=code, max_level=1)
        return [
            str(child["code"])
            for child in (node.get("children") or [])
            if child.get("code")
        ]

    async def fetch_related_users(self, user_id: str) -> List[str]:
        """把「下级区划」映射为关联实体，便于复用基类的扩展逻辑。"""
        return await self.fetch_children(user_id)

    async def fetch_flat(
        self,
        code: str = "",
        max_level: int = 3,
        year: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """抓取指定区划的子树并摊平成记录列表。

        Args:
            code: 12 位行政区划代码，留空表示全国。
            max_level: 展开层数，见模块文档中关于乡级限制的说明。
            year: 数据年份，``None`` 表示最新版。

        Returns:
            扁平记录列表。
        """
        root = await self._fetch_tree(code=code, max_level=max_level, year=year)
        return flatten_tree(root)

    async def fetch_users(self, user_ids: List[str]) -> List[Dict[str, Any]]:
        """批量抓取多个区划代码的子树，并逐条去重落盘。

        与用户型平台不同，这里一个代码会展开成多条记录，因此按「摊平后的
        每条记录」做去重与写入，去重键为 ``xzqh:<12位代码>``。

        Args:
            user_ids: 12 位行政区划代码列表。

        Returns:
            新写入的记录列表（已跳过的重复记录不计入）。
        """
        max_level = self.config.get("subtree_max_level", 3)
        year = self.config.get("year")
        collection = self.collection
        results: List[Dict[str, Any]] = []

        for code in user_ids:
            logger.info(f"[xzqh] 开始抓取 {code} 的子树（maxLevel={max_level}）")
            records = await self.fetch_flat(code=code, max_level=max_level, year=year)
            for record in records:
                key = self.dedup.make_key(self.platform_name, content_id=record["code"])
                if await self.dedup.check_and_mark(key):
                    continue
                await self.storage.save(record, collection)
                results.append(record)
            logger.info(f"[xzqh] {code} 子树完成，累计写入 {len(results)} 条")

        return results

    # ------------------------------------------------------------------
    # 全量快照
    # ------------------------------------------------------------------

    async def crawl_all(
        self,
        max_level: Optional[int] = None,
        year: Optional[int] = None,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> Dict[str, Any]:
        """全量抓取全国行政区划，返回扁平记录与嵌套树。

        两种口径：

        - ``max_level <= 3``：一次请求即可拿到省/地/县，直接完成。
        - ``max_level == 4``：先用一次请求取省/地/县，再对每个地级节点
          （含直辖市的市辖区、省直辖县级单位）单独下钻，就地补全乡级。

        Args:
            max_level: 展开层数，默认取配置 ``snapshot.max_level``（4）。
            year: 数据年份，``None`` 表示最新版。
            on_progress: 可选进度回调，签名为 ``(已完成, 总数)``。

        Returns:
            形如 ``{"records": [...], "tree": {...}, "requests": n}`` 的结果字典。
            其中 ``tree`` 在四级模式下已补全乡级分支。
        """
        level = (
            max_level
            if max_level is not None
            else self.config.get("snapshot.max_level", 4)
        )

        # 第一阶段：省 / 地 / 县 三级，一次请求完成
        root = await self._fetch_tree(code="", max_level=3, year=year)
        requests = 1
        province_count = len(root.get("children") or [])
        logger.info(
            f"[xzqh] 第一阶段完成：省级 {province_count} 个，"
            f"共 {count_nodes(root)} 个节点（省/地/县）"
        )
        if province_count and province_count != EXPECTED_PROVINCES:
            logger.warning(
                f"[xzqh] 省级单位数量为 {province_count}，与预期 {EXPECTED_PROVINCES} 不一致，"
                "请留意数据是否完整"
            )

        if level <= 3:
            return {
                "records": flatten_tree(root),
                "tree": root,
                "requests": requests,
            }

        # 第二阶段：逐地级节点下钻，把乡级挂回树上
        targets = nodes_at_depth(root, self.EXPAND_DEPTH)
        total = len(targets)
        logger.info(f"[xzqh] 第二阶段开始：需下钻 {total} 个地级节点以补全乡级")

        before = count_nodes(root)
        failed: List[str] = []

        for index, (node, _ancestors) in enumerate(targets, start=1):
            code = str(node.get("code") or "")
            if not code:
                continue
            try:
                sub_root = await self._fetch_tree(code=code, max_level=2, year=year)
                requests += 1
            except CrawlerHubError as exc:
                failed.append(code)
                logger.error(f"[xzqh] 下钻 {code} 失败，该分支乡级将缺失: {exc}")
                continue

            # ``node`` 是 root 树中的同一对象，就地替换 children 即可合并乡级
            children = sub_root.get("children")
            if children is not None:
                node["children"] = children

            if on_progress is not None:
                on_progress(index, total)
            elif index % 50 == 0 or index == total:
                logger.info(f"[xzqh] 第二阶段进度 {index}/{total}")

        added = count_nodes(root) - before
        logger.info(
            f"[xzqh] 第二阶段完成：新增 {added} 个乡级节点，"
            f"共 {requests} 次请求，失败 {len(failed)} 个"
        )
        if failed:
            logger.warning(f"[xzqh] 下钻失败的代码: {', '.join(failed[:20])}")

        return {"records": flatten_tree(root), "tree": root, "requests": requests}

    def save_snapshot(self, result: Dict[str, Any]) -> Dict[str, str]:
        """把全量结果写入快照文件。

        快照采用「每次覆盖」语义，避免重复运行时累积历史版本；如需留存历史，
        请自行对输出文件做版本管理。

        Args:
            result: :meth:`crawl_all` 的返回值。

        Returns:
            实际写入的文件路径字典，未配置的项会被省略。
        """
        written: Dict[str, str] = {}

        tree_path = self.config.get("snapshot.tree_output", "data/output/xzqh_tree.json")
        if tree_path:
            path = Path(tree_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(result["tree"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            written["tree"] = str(path)
            logger.info(f"[xzqh] 嵌套树已写入 {path}")

        flat_path = self.config.get(
            "snapshot.flat_output", "data/output/xzqh_divisions.jsonl"
        )
        if flat_path:
            path = Path(flat_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as handle:
                for record in result["records"]:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            written["flat"] = str(path)
            logger.info(f"[xzqh] {len(result['records'])} 条扁平记录已写入 {path}")

        return written

    async def run_all(
        self,
        max_level: Optional[int] = None,
        year: Optional[int] = None,
    ) -> Dict[str, Any]:
        """全量快照入口：``setup -> crawl_all -> 落盘 -> cleanup``。

        Args:
            max_level: 展开层数，默认取配置 ``snapshot.max_level``。
            year: 数据年份，``None`` 表示最新版。

        Returns:
            :meth:`crawl_all` 的结果，并额外附带 ``"written"`` 字段。
        """
        await self.setup()
        try:
            result = await self.crawl_all(max_level=max_level, year=year)
            result["written"] = self.save_snapshot(result)
            result["node_count"] = len(result["records"])
            return result
        finally:
            await self.cleanup()
