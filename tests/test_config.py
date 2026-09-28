"""crawlerhub.core.config 和 exceptions 模块测试。"""

from pathlib import Path

import pytest
import yaml

from crawlerhub.core.config import Config, load_config, load_yaml
from crawlerhub.core.exceptions import (
    AntiCrawlError,
    CrawlerHubError,
    ConfigError,
    LoginRequiredError,
    ParseError,
    ProxyError,
    RequestError,
    StorageError,
)


class TestExceptions:
    def test_all_inherit_from_base(self) -> None:
        for exc_cls in [
            RequestError,
            AntiCrawlError,
            LoginRequiredError,
            ParseError,
            ProxyError,
            StorageError,
            ConfigError,
        ]:
            assert issubclass(exc_cls, CrawlerHubError)

    def test_request_error_with_context(self) -> None:
        exc = RequestError("timeout", url="https://x.com", status_code=500)
        assert exc.url == "https://x.com"
        assert exc.status_code == 500

    def test_parse_error_with_raw(self) -> None:
        exc = ParseError("bad json", raw="<html>...")
        assert exc.raw == "<html>..."

    def test_exceptions_are_catchable_by_base(self) -> None:
        for exc_cls in [RequestError, AntiCrawlError, LoginRequiredError]:
            with pytest.raises(CrawlerHubError):
                raise exc_cls("test")


class TestLoadYaml:
    def test_load_valid(self, tmp_path: Path) -> None:
        path = tmp_path / "test.yaml"
        path.write_text("key: value\nnum: 42", encoding="utf-8")
        result = load_yaml(path)
        assert result == {"key": "value", "num": 42}

    def test_load_nonexistent(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="配置文件不存在"):
            load_yaml(tmp_path / "nonexistent.yaml")

    def test_load_invalid_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text(":\n  - [invalid", encoding="utf-8")
        with pytest.raises(ConfigError, match="解析 YAML 失败"):
            load_yaml(path)

    def test_load_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.yaml"
        path.write_text("", encoding="utf-8")
        result = load_yaml(path)
        assert result == {}


class TestConfig:
    def test_get_simple(self) -> None:
        cfg = Config({"a": 1, "b": 2}, {})
        assert cfg.get("a") == 1
        assert cfg.get("b") == 2

    def test_get_nested(self) -> None:
        cfg = Config({"request": {"timeout": 30, "retries": 3}}, {})
        assert cfg.get("request.timeout") == 30
        assert cfg.get("request.retries") == 3

    def test_get_default(self) -> None:
        cfg = Config({}, {})
        assert cfg.get("nonexistent", "default") == "default"
        assert cfg.get("a.b.c", None) is None

    def test_platform_overrides_base(self) -> None:
        """平台配置应覆盖全局配置。"""
        base = {"request": {"timeout": 30, "retries": 3}}
        platform = {"request": {"timeout": 60}}
        cfg = Config(base, platform)
        assert cfg.get("request.timeout") == 60  # 被覆盖
        assert cfg.get("request.retries") == 3   # 保留

    def test_deep_merge_preserves_base_keys(self) -> None:
        base = {"a": {"x": 1, "y": 2}}
        platform = {"a": {"z": 3}}
        cfg = Config(base, platform)
        assert cfg.get("a.x") == 1
        assert cfg.get("a.y") == 2
        assert cfg.get("a.z") == 3


class TestLoadConfig:
    def test_load_and_merge(self, tmp_path: Path) -> None:
        base_path = tmp_path / "base.yaml"
        platform_path = tmp_path / "platform.yaml"
        base_path.write_text("request:\n  timeout: 30\nstorage:\n  default: json", encoding="utf-8")
        platform_path.write_text("request:\n  timeout: 60\nplatform: weibo", encoding="utf-8")

        cfg = load_config(base_path, platform_path)
        assert cfg.get("request.timeout") == 60
        assert cfg.get("storage.default") == "json"
        assert cfg.get("platform") == "weibo"
