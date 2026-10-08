"""pytest 全局 fixtures。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest


@pytest.fixture
def tmp_cookie_file(tmp_path: Path) -> Path:
    """创建临时 Cookie 文件。"""
    data = {"cookie": "SUB=test123; SUBP=test456;", "uid": "10001", "updated_at": 0}
    path = tmp_path / "cookie.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def weibo_raw_data() -> Dict[str, Any]:
    """微博 /profile/info 接口的模拟返回数据。"""
    return {
        "data": {
            "user": {
                "screen_name": "测试用户",
                "followers_count": 1000,
                "follow_count": 200,
                "statuses_count": 500,
                "avatar_hd": "https://example.com/avatar.jpg",
                "verified": True,
                "verified_reason": "测试认证",
            }
        }
    }


@pytest.fixture
def weibo_detail_data() -> Dict[str, Any]:
    """微博 /profile/detail 接口的模拟返回数据。"""
    return {
        "data": {
            "description": "这是一个测试账号",
            "location": "北京",
            "gender": "m",
            "created_at": "2020-01-01",
        }
    }


@pytest.fixture
def douyin_raw_data() -> Dict[str, Any]:
    """抖音用户信息接口的模拟返回数据。"""
    return {
        "user": {
            "uid": "987654321",
            "nickname": "抖音测试用户",
            "signature": "测试签名",
            "follower_count": 5000,
            "following_count": 100,
            "total_favorited": 10000,
            "aweme_count": 50,
            "avatar_larger": {"url_list": ["https://example.com/avatar_l.jpg"]},
            "unique_id": "test_douyin",
            "region": "CN",
        }
    }


def make_xzqh_node(
    code: str,
    name: str | None,
    level: int,
    node_type: str,
    children: list | None = None,
) -> Dict[str, Any]:
    """构造一个区划节点，减少区划 fixture 中的样板代码。"""
    return {
        "code": code,
        "name": name,
        "level": level,
        "type": node_type,
        "children": children or [],
    }


@pytest.fixture
def xzqh_phase1_tree() -> Dict[str, Any]:
    """``code=&maxLevel=3`` 的模拟返回：省 / 地 / 县三级，不含乡级。

    结构刻意覆盖三种形态：

    - 湖北省 -> 武汉市(地级) -> 江岸区/武昌区(县级)
    - 湖北省 -> 仙桃市（省直辖县级单位，直接挂在省下）
    - 北京市(直辖市) -> 东城区（跳过地级层）
    """
    return make_xzqh_node(
        "00",
        None,
        0,
        "",
        [
            make_xzqh_node(
                "420000000000",
                "湖北省",
                1,
                "省",
                [
                    make_xzqh_node(
                        "420100000000",
                        "武汉市",
                        2,
                        "地级市",
                        [
                            make_xzqh_node("420102000000", "江岸区", 3, "市辖区"),
                            make_xzqh_node("420106000000", "武昌区", 3, "市辖区"),
                        ],
                    ),
                    make_xzqh_node("429004000000", "仙桃市", 3, "县级市"),
                ],
            ),
            make_xzqh_node(
                "110000000000",
                "北京市",
                1,
                "直辖市",
                [make_xzqh_node("110101000000", "东城区", 3, "市辖区")],
            ),
        ],
    )


@pytest.fixture
def xzqh_phase2_subtrees() -> Dict[str, Any]:
    """第二阶段各下钻代码的模拟返回（补全乡级）。"""
    return {
        "420100000000": make_xzqh_node(
            "420100000000",
            "武汉市",
            2,
            "地级市",
            [
                make_xzqh_node(
                    "420102000000",
                    "江岸区",
                    3,
                    "市辖区",
                    [make_xzqh_node("420102002000", "大智街道", 4, "街道")],
                ),
                make_xzqh_node(
                    "420106000000",
                    "武昌区",
                    3,
                    "市辖区",
                    [make_xzqh_node("420106003000", "中南路街道", 4, "街道")],
                ),
            ],
        ),
        "429004000000": make_xzqh_node(
            "429004000000",
            "仙桃市",
            3,
            "县级市",
            [make_xzqh_node("429004001000", "干河街道", 4, "街道")],
        ),
        "110101000000": make_xzqh_node(
            "110101000000",
            "东城区",
            3,
            "市辖区",
            [make_xzqh_node("110101001000", "东华门街道", 4, "街道")],
        ),
    }
