"""微博数据解析器。

将微博接口返回的 JSON 解析为统一的用户信息结构。
解析规则集中在此模块，便于页面/接口改版时集中维护。
"""

from __future__ import annotations

from typing import Any, Dict, List

from crawlerhub.core.exceptions import ParseError


def parse_user_info(
    info_raw: Dict[str, Any],
    detail_raw: Dict[str, Any],
    user_id: str,
) -> Dict[str, Any]:
    """解析微博用户信息接口返回。

    合并两个接口的数据：
    - ``/ajax/profile/info``: 昵称、粉丝数、微博数、头像、认证等
    - ``/ajax/profile/detail``: 简介、性别、注册时间、IP 归属地等

    Args:
        info_raw: ``/profile/info`` 接口返回的原始 JSON。
        detail_raw: ``/profile/detail`` 接口返回的原始 JSON。
        user_id: 目标用户 ID。

    Returns:
        标准化的用户信息字典。

    Raises:
        ParseError: 数据结构不符合预期。
    """
    try:
        info_data = info_raw.get("data") or {}
        user = info_data.get("user") or info_data
        detail = detail_raw.get("data") or {}

        return {
            "platform": "weibo",
            "user_id": str(user_id),
            "screen_name": user.get("screen_name", ""),
            "description": detail.get("description", ""),
            "followers_count": user.get("followers_count", 0),
            "friends_count": user.get("follow_count", user.get("friends_count", 0)),
            "statuses_count": user.get("statuses_count", 0),
            "location": detail.get("location", "") or detail.get("ip_location", ""),
            "profile_url": f"https://weibo.com/u/{user_id}",
            "avatar_hd": user.get("avatar_hd", ""),
            "verified": bool(user.get("verified", False)),
            "verified_reason": user.get("verified_reason", ""),
            "gender": detail.get("gender", ""),
            "created_at": detail.get("created_at", ""),
        }
    except (AttributeError, TypeError, KeyError) as exc:
        raise ParseError(
            f"解析微博用户信息失败: {exc}",
            raw=f"info={str(info_raw)[:200]} detail={str(detail_raw)[:200]}",
        ) from exc


def parse_follow_list(raw: Dict[str, Any]) -> List[str]:
    """解析微博粉丝/关注列表接口返回。

    微博 ``/ajax/friendships/friends`` 接口通过 ``type`` 参数区分
    粉丝（``fans``）和关注（``follows``），返回结构中用户列表位于
    顶层 ``users[]``，每个用户的 UID 字符串在 ``idstr`` 字段。

    Args:
        raw: 接口返回的原始 JSON 字典。

    Returns:
        用户 UID 字符串列表。

    Raises:
        ParseError: 数据结构不符合预期。
    """
    try:
        user_list = raw.get("users") or []
        return [
            str(item.get("idstr") or item.get("id", ""))
            for item in user_list
            if item.get("idstr") or item.get("id")
        ]
    except (AttributeError, TypeError) as exc:
        raise ParseError(f"解析微博关注列表失败: {exc}", raw=str(raw)[:200]) from exc
