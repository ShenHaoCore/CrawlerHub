"""Cookie 管理模块。

支持：
- 从 JSON 文件加载 Cookie
- Cookie 字符串解析为字典
- 定时刷新（由外部调度器触发 refresh）
- 自动保存更新后的 Cookie

合规说明：Cookie 必须由用户本人合法提供，本模块不实现任何 Cookie 窃取或绕过登录的逻辑。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Dict, Optional

from crawlerhub.core.logger import logger


class CookieManager:
    """Cookie 管理器。

    Cookie 文件为 JSON 格式，示例::

        {
            "cookie": "SUB=xxx; SUBP=yyy;",
            "uid": "123456",
            "updated_at": 1700000000
        }

    Args:
        cookie_file: Cookie JSON 文件路径。
        auto_refresh: 是否启用自动刷新。
        refresh_interval: 刷新间隔（秒）。
    """

    def __init__(
        self,
        cookie_file: str | Path,
        auto_refresh: bool = False,
        refresh_interval: int = 1800,
    ) -> None:
        self.cookie_file = Path(cookie_file)
        self.auto_refresh = auto_refresh
        self.refresh_interval = refresh_interval

        self._cookie_str: str = ""
        self._uid: Optional[str] = None
        self._updated_at: float = 0.0
        self._cookie_dict: Dict[str, str] = {}
        self._refresh_task: Optional[asyncio.Task] = None

        self.load()

    def load(self) -> None:
        """从文件加载 Cookie。"""
        if not self.cookie_file.exists():
            logger.warning(f"Cookie 文件不存在: {self.cookie_file}")
            return
        try:
            data = json.loads(self.cookie_file.read_text(encoding="utf-8"))
            self._cookie_str = data.get("cookie", "")
            self._uid = data.get("uid")
            self._updated_at = data.get("updated_at", 0.0)
            self._cookie_dict = self._parse_cookie_string(self._cookie_str)
            logger.info(
                f"已加载 Cookie: {self.cookie_file.name} "
                f"(uid={self._uid}, updated={self._updated_at})"
            )
        except (json.JSONDecodeError, OSError) as exc:
            logger.error(f"加载 Cookie 失败: {exc}")

    @staticmethod
    def _parse_cookie_string(cookie_str: str) -> Dict[str, str]:
        """将 Cookie 字符串解析为字典。

        Args:
            cookie_str: 形如 ``"a=1; b=2"`` 的 Cookie 字符串。

        Returns:
            Cookie 键值对字典。
        """
        result: Dict[str, str] = {}
        for part in cookie_str.split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            key, _, value = part.partition("=")
            result[key.strip()] = value.strip()
        return result

    @property
    def cookie_str(self) -> str:
        """原始 Cookie 字符串。"""
        return self._cookie_str

    @property
    def cookie_dict(self) -> Dict[str, str]:
        """Cookie 字典，可直接传给 httpx。"""
        return dict(self._cookie_dict)

    @property
    def uid(self) -> Optional[str]:
        """当前登录用户 ID。"""
        return self._uid

    @property
    def is_valid(self) -> bool:
        """Cookie 是否非空。"""
        return bool(self._cookie_str)

    def update(self, cookie_str: str, uid: Optional[str] = None) -> None:
        """更新 Cookie 并持久化到文件。

        Args:
            cookie_str: 新的 Cookie 字符串。
            uid: 可选的用户 ID。
        """
        self._cookie_str = cookie_str
        self._cookie_dict = self._parse_cookie_string(cookie_str)
        if uid is not None:
            self._uid = uid
        self._updated_at = time.time()
        self._save()
        logger.info(f"Cookie 已更新: {self.cookie_file.name}")

    def _save(self) -> None:
        """将当前 Cookie 持久化到文件。"""
        self.cookie_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "cookie": self._cookie_str,
            "uid": self._uid,
            "updated_at": self._updated_at,
        }
        self.cookie_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def start_auto_refresh(self, refresh_cb=None) -> None:
        """启动后台自动刷新任务。

        Args:
            refresh_cb: 刷新回调函数，接收 CookieManager 实例，负责获取新 Cookie 并调用 update。
        """
        if not self.auto_refresh or refresh_cb is None:
            return

        async def _loop() -> None:
            while True:
                try:
                    await asyncio.sleep(self.refresh_interval)
                    logger.info("开始自动刷新 Cookie")
                    await refresh_cb(self)
                except asyncio.CancelledError:
                    break
                except Exception as exc:  # noqa: BLE001
                    logger.error(f"Cookie 自动刷新失败: {exc}")

        self._refresh_task = asyncio.create_task(_loop())
        logger.info(f"Cookie 自动刷新已启动，间隔 {self.refresh_interval}s")

    def stop_auto_refresh(self) -> None:
        """停止后台自动刷新任务。"""
        if self._refresh_task:
            self._refresh_task.cancel()
            self._refresh_task = None
            logger.info("Cookie 自动刷新已停止")
