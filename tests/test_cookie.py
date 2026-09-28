"""crawlerhub.core.cookie 模块测试。"""

import json
from pathlib import Path

import pytest

from crawlerhub.core.cookie import CookieManager


class TestCookieManager:
    def test_load_from_file(self, tmp_cookie_file: Path) -> None:
        cm = CookieManager(tmp_cookie_file)
        assert cm.is_valid is True
        assert cm.uid == "10001"
        assert "SUB" in cm.cookie_dict
        assert cm.cookie_dict["SUB"] == "test123"
        assert cm.cookie_dict["SUBP"] == "test456"

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        cm = CookieManager(tmp_path / "nonexistent.json")
        assert cm.is_valid is False
        assert cm.cookie_str == ""
        assert cm.cookie_dict == {}

    def test_parse_cookie_string(self) -> None:
        """Cookie 字符串解析。"""
        result = CookieManager._parse_cookie_string("a=1; b=2; c=3;")
        assert result == {"a": "1", "b": "2", "c": "3"}

    def test_parse_cookie_with_spaces(self) -> None:
        result = CookieManager._parse_cookie_string("  a = 1 ;  b = 2  ")
        assert result == {"a": "1", "b": "2"}

    def test_parse_empty_string(self) -> None:
        result = CookieManager._parse_cookie_string("")
        assert result == {}

    def test_parse_no_equals(self) -> None:
        """无等号的片段应被忽略。"""
        result = CookieManager._parse_cookie_string("malformed; a=1")
        assert result == {"a": "1"}

    def test_update_and_persist(self, tmp_cookie_file: Path) -> None:
        cm = CookieManager(tmp_cookie_file)
        cm.update("NEW_COOKIE=abc; OTHER=xyz", uid="99999")
        # 重新加载验证持久化
        cm2 = CookieManager(tmp_cookie_file)
        assert cm2.cookie_dict["NEW_COOKIE"] == "abc"
        assert cm2.uid == "99999"
        assert cm2.is_valid is True

    def test_invalid_json_file(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("not a json", encoding="utf-8")
        cm = CookieManager(path)
        assert cm.is_valid is False

    def test_stop_auto_refresh_no_task(self, tmp_cookie_file: Path) -> None:
        """无后台任务时停止不应报错。"""
        cm = CookieManager(tmp_cookie_file)
        cm.stop_auto_refresh()  # 不应抛异常
