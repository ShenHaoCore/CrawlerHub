"""配置加载模块。

统一加载全局配置与平台配置，支持 YAML 格式。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

from crawlerhub.core.exceptions import ConfigError


def load_yaml(path: str | Path) -> Dict[str, Any]:
    """加载 YAML 配置文件。

    Args:
        path: YAML 文件路径。

    Returns:
        配置字典。

    Raises:
        ConfigError: 文件不存在或解析失败。
    """
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"配置文件不存在: {path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"解析 YAML 失败: {path} -> {exc}") from exc


class Config:
    """配置容器，支持点号访问嵌套字段。

    Args:
        base_config: 全局配置字典。
        platform_config: 平台配置字典。
    """

    def __init__(self, base_config: Dict[str, Any], platform_config: Dict[str, Any]) -> None:
        self.base = base_config
        self.platform = platform_config
        # 合并：平台配置优先
        self._merged: Dict[str, Any] = self._deep_merge(base_config, platform_config)

    @staticmethod
    def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(base)
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = Config._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置值，支持点号分隔的嵌套路径。

        Args:
            key: 配置键，如 ``request.timeout``。
            default: 默认值。

        Returns:
            配置值。
        """
        keys = key.split(".")
        value: Any = self._merged
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value

    def __getitem__(self, key: str) -> Any:
        return self.get(key)


def load_config(base_path: str | Path, platform_path: str | Path) -> Config:
    """加载全局配置与平台配置并合并。

    Args:
        base_path: 全局配置文件路径。
        platform_path: 平台配置文件路径。

    Returns:
        合并后的 Config 对象。
    """
    base = load_yaml(base_path)
    platform = load_yaml(platform_path)
    return Config(base, platform)
