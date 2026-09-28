"""微博平台解析器测试。"""

import pytest

from crawlerhub.core.exceptions import ParseError
from crawlerhub.platforms.weibo.parser import parse_user_info


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
