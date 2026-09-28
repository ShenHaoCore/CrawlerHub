"""代理池工具：从文件加载代理列表并随机选取。"""

from __future__ import annotations

import random
from pathlib import Path
from typing import List

from crawlerhub.core.exceptions import ProxyError


class ProxyPool:
    """简单的代理池，从文本文件加载代理地址。

    文件格式：每行一个代理 URL，如 ``http://user:pass@host:port``。
    """

    def __init__(self, pool_file: str | Path) -> None:
        self.pool_file = Path(pool_file)
        self._proxies: List[str] = []
        self._load()

    def _load(self) -> None:
        """从文件加载代理列表。"""
        if not self.pool_file.exists():
            raise ProxyError(f"代理池文件不存在: {self.pool_file}")
        lines = self.pool_file.read_text(encoding="utf-8").splitlines()
        self._proxies = [line.strip() for line in lines if line.strip() and not line.startswith("#")]
        if not self._proxies:
            raise ProxyError(f"代理池为空: {self.pool_file}")

    def random(self) -> str:
        """随机返回一个代理地址。

        Returns:
            代理 URL 字符串。

        Raises:
            ProxyError: 代理池为空。
        """
        if not self._proxies:
            raise ProxyError("代理池为空，无法选取代理")
        return random.choice(self._proxies)

    def all(self) -> List[str]:
        """返回所有代理地址。"""
        return list(self._proxies)

    def __len__(self) -> int:
        return len(self._proxies)
