"""国家地名信息库行政区划解析器测试。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from crawlerhub.core.exceptions import ParseError
from crawlerhub.platforms.xzqh.parser import (
    LEVEL_NAMES,
    count_nodes,
    flatten_node,
    flatten_tree,
    iter_nodes,
    nodes_at_depth,
    numeric_code6,
    parse_list_payload,
)


def node(
    code: str,
    name: Optional[str],
    level: int,
    node_type: str = "",
    children: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """构造区划节点。"""
    return {
        "code": code,
        "name": name,
        "level": level,
        "type": node_type,
        "children": children or [],
    }


def envelope(data: Dict[str, Any], status: Any = 200) -> Dict[str, Any]:
    """包一层接口信封。"""
    return {"data": data, "message": None, "status": status, "total": 1, "tag": None}


class TestParseListPayload:
    def test_returns_data_node(self) -> None:
        payload = envelope(node("420000000000", "湖北省", 1, "省"))
        result = parse_list_payload(payload)
        assert result["code"] == "420000000000"
        assert result["name"] == "湖北省"

    def test_non_ok_status_raises(self) -> None:
        payload = envelope(node("420000000000", "湖北省", 1, "省"), status=500)
        with pytest.raises(ParseError, match="业务状态异常"):
            parse_list_payload(payload, code="420000000000")

    def test_missing_data_raises(self) -> None:
        with pytest.raises(ParseError, match="data 字段缺失"):
            parse_list_payload({"status": 200, "data": None})

    def test_non_dict_payload_raises(self) -> None:
        with pytest.raises(ParseError, match="返回非对象"):
            parse_list_payload(["not", "a", "dict"])  # type: ignore[arg-type]


class TestIterNodes:
    def test_depth_first_order_and_ancestors(self, xzqh_phase1_tree: Dict[str, Any]) -> None:
        visited = [(n["name"], tuple(a["name"] for a in anc)) for n, anc in iter_nodes(xzqh_phase1_tree)]

        names = [name for name, _ in visited]
        assert names == [
            None,
            "湖北省",
            "武汉市",
            "江岸区",
            "武昌区",
            "仙桃市",
            "北京市",
            "东城区",
        ]

        # 江岸区的祖先链应为 虚拟根 -> 湖北省 -> 武汉市
        jiang_an = next(anc for name, anc in visited if name == "江岸区")
        assert jiang_an == (None, "湖北省", "武汉市")

    def test_root_has_empty_ancestors(self, xzqh_phase1_tree: Dict[str, Any]) -> None:
        root, ancestors = next(iter(iter_nodes(xzqh_phase1_tree)))
        assert root is xzqh_phase1_tree
        assert ancestors == ()


class TestNodesAtDepth:
    def test_depth_two_returns_prefecture_tier(self, xzqh_phase1_tree: Dict[str, Any]) -> None:
        """深度 2 即「地级层」，含地级市、省直辖县级单位和直辖市的市辖区。"""
        found = [n["name"] for n, _ in nodes_at_depth(xzqh_phase1_tree, 2)]
        assert found == ["武汉市", "仙桃市", "东城区"]

    def test_depth_zero_returns_root(self, xzqh_phase1_tree: Dict[str, Any]) -> None:
        found = [n["code"] for n, _ in nodes_at_depth(xzqh_phase1_tree, 0)]
        assert found == ["00"]

    def test_returns_same_object_reference(self, xzqh_phase1_tree: Dict[str, Any]) -> None:
        """返回的是树内同一对象，便于就地合并下钻结果。"""
        target, _ = nodes_at_depth(xzqh_phase1_tree, 2)[0]
        target["children"] = [node("420102002000", "大智街道", 4, "街道")]
        wuhan = xzqh_phase1_tree["children"][0]["children"][0]
        assert wuhan["children"][0]["name"] == "大智街道"


class TestFlattenNode:
    def test_county_level_record(self) -> None:
        ancestors = (
            node("00", None, 0),
            node("420000000000", "湖北省", 1, "省"),
            node("420100000000", "武汉市", 2, "地级市"),
        )
        record = flatten_node(node("420102000000", "江岸区", 3, "市辖区"), ancestors)

        assert record["platform"] == "xzqh"
        assert record["code"] == "420102000000"
        assert record["code6"] == "420102"
        assert record["level"] == 3
        assert record["level_name"] == "县级"
        assert record["type"] == "市辖区"
        assert record["parent_code"] == "420100000000"
        assert record["parent_name"] == "武汉市"
        assert record["full_name"] == "湖北省武汉市江岸区"
        assert record["province_name"] == "湖北省"
        assert record["prefecture_name"] == "武汉市"
        assert record["county_name"] == "江岸区"

    def test_province_level_record_has_no_parent(self) -> None:
        """省级节点的父级是全国虚拟根，应被忽略。"""
        record = flatten_node(
            node("420000000000", "湖北省", 1, "省"),
            (node("00", None, 0),),
        )
        assert record["parent_code"] == ""
        assert record["parent_name"] == ""
        assert record["full_name"] == "湖北省"
        assert record["province_code"] == "420000000000"
        assert record["prefecture_code"] == ""

    def test_municipality_district_skips_prefecture(self) -> None:
        """直辖市没有地级层，prefecture_* 应为空。"""
        record = flatten_node(
            node("110101000000", "东城区", 3, "市辖区"),
            (node("00", None, 0), node("110000000000", "北京市", 1, "直辖市")),
        )
        assert record["province_name"] == "北京市"
        assert record["prefecture_code"] == ""
        assert record["prefecture_name"] == ""
        assert record["county_name"] == "东城区"

    def test_township_level_record(self) -> None:
        ancestors = (
            node("420000000000", "湖北省", 1, "省"),
            node("420100000000", "武汉市", 2, "地级市"),
            node("420102000000", "江岸区", 3, "市辖区"),
        )
        record = flatten_node(node("420102002000", "大智街道", 4, "街道"), ancestors)
        assert record["level_name"] == LEVEL_NAMES[4] == "乡级"
        assert record["full_name"] == "湖北省武汉市江岸区大智街道"
        assert record["code6"] == "420102"

    def test_unknown_level_falls_back_to_empty_name(self) -> None:
        record = flatten_node(node("999999999999", "未知", 9, ""))
        assert record["level_name"] == ""

    def test_non_numeric_code_leaves_code6_empty(self) -> None:
        """上游对台湾省返回的不是数字代码，code6 应取空串而非截断文本。"""
        record = flatten_node(node("资料暂缺", "台湾省", 1, "省"))
        assert record["code"] == "资料暂缺"
        assert record["code6"] == ""
        assert record["full_name"] == "台湾省"


class TestNumericCode6:
    def test_twelve_digit_code(self) -> None:
        assert numeric_code6("420102002000") == "420102"

    def test_six_digit_code(self) -> None:
        assert numeric_code6("420102") == "420102"

    def test_non_numeric_returns_empty(self) -> None:
        assert numeric_code6("资料暂缺") == ""

    def test_short_numeric_returns_empty(self) -> None:
        assert numeric_code6("4201") == ""

    def test_empty_returns_empty(self) -> None:
        assert numeric_code6("") == ""


class TestFlattenTree:
    def test_skips_virtual_root(self, xzqh_phase1_tree: Dict[str, Any]) -> None:
        records = flatten_tree(xzqh_phase1_tree)
        codes = [r["code"] for r in records]
        assert "00" not in codes
        assert len(records) == 7

    def test_records_are_depth_first(self, xzqh_phase1_tree: Dict[str, Any]) -> None:
        names = [r["name"] for r in flatten_tree(xzqh_phase1_tree)]
        assert names == ["湖北省", "武汉市", "江岸区", "武昌区", "仙桃市", "北京市", "东城区"]

    def test_dedup_by_code(self) -> None:
        shared = node("420102000000", "江岸区", 3, "市辖区")
        root = node(
            "00",
            None,
            0,
            "",
            [
                node("420100000000", "武汉市", 2, "地级市", [shared]),
                node("420199000000", "重复分支", 2, "地级市", [shared]),
            ],
        )
        deduped = flatten_tree(root)
        # 同一 code 只保留首次出现
        assert [r["name"] for r in deduped] == ["武汉市", "江岸区", "重复分支"]
        assert len(flatten_tree(root, dedup=False)) == 4

    def test_ancestors_prefix_extends_full_name(self) -> None:
        """第二阶段下钻时传入父级祖先链，full_name 应补全省名。"""
        ancestors = (
            node("00", None, 0),
            node("420000000000", "湖北省", 1, "省"),
        )
        subtree = node(
            "420100000000",
            "武汉市",
            2,
            "地级市",
            [
                node(
                    "420102000000",
                    "江岸区",
                    3,
                    "市辖区",
                    [node("420102002000", "大智街道", 4, "街道")],
                )
            ],
        )
        records = flatten_tree(subtree, ancestors)
        street = next(r for r in records if r["name"] == "大智街道")
        assert street["full_name"] == "湖北省武汉市江岸区大智街道"
        assert street["province_name"] == "湖北省"


class TestCountNodes:
    def test_counts_including_root(self, xzqh_phase1_tree: Dict[str, Any]) -> None:
        assert count_nodes(xzqh_phase1_tree) == 8
