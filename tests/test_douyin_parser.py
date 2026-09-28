"""抖音平台解析器测试。"""

import pytest

from crawlerhub.core.exceptions import ParseError
from crawlerhub.platforms.douyin.parser import parse_user_info


class TestDouyinParser:
    def test_parse_valid_data(self, douyin_raw_data) -> None:
        result = parse_user_info(douyin_raw_data, "MS4wLjABAAAAxxx")
        assert result["platform"] == "douyin"
        assert result["sec_uid"] == "MS4wLjABAAAAxxx"
        assert result["user_id"] == "987654321"
        assert result["nickname"] == "抖音测试用户"
        assert result["signature"] == "测试签名"
        assert result["followers_count"] == 5000
        assert result["following_count"] == 100
        assert result["total_favorited"] == 10000
        assert result["aweme_count"] == 50
        assert result["avatar_larger"] == "https://example.com/avatar_l.jpg"
        assert result["unique_id"] == "test_douyin"
        assert result["region"] == "CN"

    def test_parse_missing_fields(self) -> None:
        raw = {"user": {"nickname": "test_user"}}
        result = parse_user_info(raw, "sec123")
        assert result["nickname"] == "test_user"
        assert result["followers_count"] == 0
        assert result["signature"] == ""
        assert result["avatar_larger"] == ""

    def test_parse_empty_data(self) -> None:
        result = parse_user_info({}, "sec123")
        assert result["platform"] == "douyin"
        assert result["sec_uid"] == "sec123"
        assert result["nickname"] == ""

    def test_parse_avatar_missing_url_list(self) -> None:
        raw = {"user": {"avatar_larger": {}}}
        result = parse_user_info(raw, "sec123")
        assert result["avatar_larger"] == ""

    def test_parse_invalid_input_raises(self) -> None:
        with pytest.raises(ParseError, match="解析抖音用户信息失败"):
            parse_user_info("not a dict", "sec123")  # type: ignore[arg-type]
