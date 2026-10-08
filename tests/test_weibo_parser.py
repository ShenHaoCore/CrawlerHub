"""微博平台解析器测试。"""

import pytest

from crawlerhub.core.exceptions import LoginRequiredError, ParseError
from crawlerhub.platforms.weibo.parser import (
    parse_follow_list,
    parse_user_info,
    raise_if_login_required,
)


class TestWeiboParser:
    def test_parse_valid_data(self, weibo_raw_data, weibo_detail_data) -> None:
        result = parse_user_info(weibo_raw_data, weibo_detail_data, "123456")
        assert result["platform"] == "weibo"
        assert result["user_id"] == "123456"
        assert result["screen_name"] == "测试用户"
        assert result["followers_count"] == 1000
        assert result["friends_count"] == 200
        assert result["statuses_count"] == 500
        assert result["location"] == "北京"
        assert result["verified"] is True
        assert result["verified_reason"] == "测试认证"
        assert result["gender"] == "m"
        assert result["description"] == "这是一个测试账号"
        assert result["created_at"] == "2020-01-01"

    def test_parse_missing_fields(self) -> None:
        """缺失字段应使用默认值。"""
        info = {"data": {"screen_name": "test"}}
        detail = {"data": {}}
        result = parse_user_info(info, detail, "999")
        assert result["screen_name"] == "test"
        assert result["followers_count"] == 0
        assert result["description"] == ""
        assert result["verified"] is False

    def test_parse_empty_data(self) -> None:
        result = parse_user_info({}, {}, "123")
        assert result["platform"] == "weibo"
        assert result["user_id"] == "123"
        assert result["screen_name"] == ""
        assert result["followers_count"] == 0

    def test_parse_nested_data_none(self) -> None:
        """data 为 None 时不应报错。"""
        result = parse_user_info({"data": None}, {"data": None}, "123")
        assert result["screen_name"] == ""

    def test_parse_invalid_input_raises(self) -> None:
        with pytest.raises(ParseError, match="解析微博用户信息失败"):
            parse_user_info("not a dict", {}, "123")  # type: ignore[arg-type]


class TestWeiboLoginRequired:
    """微博对失效 Cookie 并不总是返回 302，也可能返回 200 + 业务码 ``ok=-100``。

    这种「200 里藏着错误」的响应，既不触发状态码检查，也不会让 JSON 解析失败，
    必须在业务层显式判定，否则会被当成「抓到 0 条」而静默吞掉。
    """

    def test_ok_minus_100_raises_login_required(self) -> None:
        raw = {"ok": -100, "url": "https://weibo.com/login.php?url=x"}

        with pytest.raises(LoginRequiredError, match="Cookie 已失效"):
            raise_if_login_required(raw, api="friendships/friends(fans)")

    def test_login_url_with_other_code_raises_login_required(self) -> None:
        raw = {"ok": -1, "url": "https://weibo.com/login.php"}

        with pytest.raises(LoginRequiredError):
            raise_if_login_required(raw, api="profile/info")

    def test_success_code_passes(self) -> None:
        raise_if_login_required({"ok": 1, "users": []}, api="profile/info")

    def test_missing_ok_passes(self) -> None:
        """没有业务码的响应（如新版 data 结构）不应被误判。"""
        raise_if_login_required({"data": {"user": {}}}, api="profile/info")

    def test_non_dict_payload_passes(self) -> None:
        raise_if_login_required([1, 2, 3], api="profile/info")  # type: ignore[arg-type]

    def test_other_business_error_raises_parse_error(self) -> None:
        with pytest.raises(ParseError, match="业务错误 ok=-5"):
            raise_if_login_required({"ok": -5}, api="profile/info")


class TestWeiboFollowListShapes:
    """``parse_follow_list`` 需同时兼容新旧两种列表位置。"""

    def test_top_level_users(self) -> None:
        raw = {"users": [{"idstr": "111"}, {"id": 222}]}
        assert parse_follow_list(raw) == ["111", "222"]

    def test_nested_data_list(self) -> None:
        raw = {"data": {"list": [{"id": 333}, {"uid": 444}]}}
        assert parse_follow_list(raw) == ["333", "444"]

    def test_non_dict_items_are_skipped(self) -> None:
        raw = {"users": [{"id": 1}, "bad", None, 42]}
        assert parse_follow_list(raw) == ["1"]

    def test_login_required_payload_yields_empty(self) -> None:
        """真实失效响应的形状：既没有 users 也没有 data。"""
        raw = {"ok": -100, "url": "https://weibo.com/login.php?url=x"}
        assert parse_follow_list(raw) == []

    def test_empty_structures(self) -> None:
        assert parse_follow_list({}) == []
        assert parse_follow_list({"users": []}) == []
        assert parse_follow_list({"data": {"list": []}}) == []
        assert parse_follow_list({"data": None}) == []
