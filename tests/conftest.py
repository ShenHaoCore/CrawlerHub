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
