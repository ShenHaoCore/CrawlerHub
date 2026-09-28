"""抖音数据解析器。

将抖音接口返回的 JSON 解析为统一的用户信息结构。
"""

from __future__ import annotations

from typing import Any, Dict, List

from crawlerhub.core.exceptions import ParseError


def parse_user_info(raw: Dict[str, Any], sec_uid: str) -> Dict[str, Any]:
    """解析抖音用户信息接口返回。

    抖音 ``/aweme/v1/web/user/profile/other`` 接口返回结构中，
    用户信息位于 ``user`` 字段。

    Args:
        raw: 接口返回的原始 JSON 字典。
        sec_uid: 抖音 sec_uid。

    Returns:
        标准化的用户信息字典。

    Raises:
        ParseError: 数据结构不符合预期。
    """
    try:
        user = raw.get("user") or {}

        stats = user.get("follower_count") or {}
        return {
            "platform": "douyin",
            "sec_uid": sec_uid,
            "user_id": str(user.get("uid", "")),
            "nickname": user.get("nickname", ""),
            "signature": user.get("signature", ""),
            "followers_count": user.get("follower_count", 0),
            "following_count": user.get("following_count", 0),
            "total_favorited": user.get("total_favorited", 0),
            "aweme_count": user.get("aweme_count", 0),
            "avatar_larger": (user.get("avatar_larger") or {}).get("url_list", [""])[0],
            "unique_id": user.get("unique_id", ""),
            "region": user.get("region", ""),
        }
    except (AttributeError, TypeError, KeyError) as exc:
        raise ParseError(f"解析抖音用户信息失败: {exc}", raw=str(raw)) from exc


def parse_follow_list(raw: Dict[str, Any]) -> List[str]:
    """解析抖音粉丝/关注列表接口返回。

    抖音 ``/aweme/v1/web/user/follower/list/other`` 和
    ``/aweme/v1/web/user/following/list/other`` 返回结构中，
    用户列表位于 ``followings[]`` 或 ``followers[]``。

    Args:
        raw: 接口返回的原始 JSON 字典。

    Returns:
        sec_uid 字符串列表。

    Raises:
        ParseError: 数据结构不符合预期。
    """
    try:
        users = raw.get("followings") or raw.get("followers") or []
        return [u.get("sec_uid", "") for u in users if u.get("sec_uid")]
    except (AttributeError, TypeError) as exc:
        raise ParseError(f"解析抖音关注列表失败: {exc}", raw=str(raw)[:200]) from exc
