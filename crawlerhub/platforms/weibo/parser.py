"""微博数据解析器。

将微博接口返回的 JSON 解析为统一的用户信息结构。
解析规则集中在此模块，便于页面/接口改版时集中维护。
"""

from __future__ import annotations

from typing import Any, Dict, List

from crawlerhub.core.exceptions import LoginRequiredError, ParseError

#: 微博接口的业务成功码。
API_OK_SUCCESS = 1

#: 微博接口表示「登录态失效」的业务码。
#: 实测 ``/ajax/friendships/friends`` 在 Cookie 过期时**不返回 302**，
#: 而是返回 HTTP 200 + ``{"ok": -100, "url": "https://weibo.com/login.php?..."}``。
#: 这种「200 里藏着一个错误」的响应，只看状态码和 JSON 解析都发现不了。
API_OK_LOGIN_REQUIRED = -100


def raise_if_login_required(raw: Dict[str, Any], *, api: str) -> None:
    """检查微博接口返回的业务码，登录态失效时抛出异常。

    Args:
        raw: 接口返回的原始 JSON 字典。
        api: 接口名称，用于错误信息，如 ``"friendships/friends"``。

    Raises:
        LoginRequiredError: 业务码表明登录态失效。
        ParseError: 业务码表明其他业务错误。
    """
    if not isinstance(raw, dict):
        return
    ok = raw.get("ok")
    if ok is None or ok == API_OK_SUCCESS:
        return

    redirect_url = str(raw.get("url") or "")
    if ok == API_OK_LOGIN_REQUIRED or "login" in redirect_url.lower():
        raise LoginRequiredError(
            f"微博 {api} 需要登录态（ok={ok}），Cookie 已失效，"
            "请更新 data/cookies/weibo.json"
        )
    raise ParseError(f"微博 {api} 返回业务错误 ok={ok}", raw=str(raw)[:200])


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
    粉丝（``fans``）和关注（``follows``）。用户列表的位置随接口版本而变，
    这里同时兼容两种已知结构：

    - 顶层 ``users[]``（现行 AJAX 接口）
    - ``data.list[]``（部分新版页面使用）

    每个用户的 UID 取 ``idstr``，回退到 ``id`` / ``uid``。

    Args:
        raw: 接口返回的原始 JSON 字典。

    Returns:
        用户 UID 字符串列表；结构为空时返回空列表。

    Raises:
        ParseError: 数据结构不符合预期。
    """
    try:
        user_list = raw.get("users")
        if not user_list:
            data = raw.get("data")
            user_list = (data or {}).get("list") if isinstance(data, dict) else None
        if not user_list:
            return []

        uids: List[str] = []
        for item in user_list:
            if not isinstance(item, dict):
                continue
            uid = item.get("idstr") or item.get("id") or item.get("uid")
            if uid:
                uids.append(str(uid))
        return uids
    except (AttributeError, TypeError) as exc:
        raise ParseError(f"解析微博关注列表失败: {exc}", raw=str(raw)[:200]) from exc
