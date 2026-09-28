"""命令行入口。

无参数运行，自动读取 ``configs/tasks.yaml`` 并发启动所有配置的平台爬虫。

支持单次运行和定时持续运行两种模式：
- ``schedule.enabled: false``（默认）：单次运行后退出
- ``schedule.enabled: true``：按 ``schedule.interval``（秒）定时循环运行

使用示例::

    python -m crawlerhub
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from crawlerhub.core.config import Config, load_config, load_yaml
from crawlerhub.core.cookie import CookieManager
from crawlerhub.core.logger import logger, setup_logger
from crawlerhub.platforms.registry import get_platform_cls

CONFIG_DIR = Path(__file__).resolve().parent.parent / "configs"
COOKIE_DIR = Path(__file__).resolve().parent.parent / "data" / "cookies"
TASK_FILE = CONFIG_DIR / "tasks.yaml"
BASE_CONFIG = CONFIG_DIR / "base.yaml"


def _init_logging(base_config: Config) -> None:
    """初始化日志。"""
    setup_logger(
        level=base_config.get("logging.level", "INFO"),
        log_file=base_config.get("logging.file"),
        rotation=base_config.get("logging.rotation", "10 MB"),
        retention=base_config.get("logging.retention", "7 days"),
    )


async def _run_single_platform(
    platform: str,
    uids: List[str],
    base_config_path: str,
    storage_override: Optional[str] = None,
    cookie_file_override: Optional[str] = None,
    crawl_related: bool = False,
    max_related: int = 50,
    max_depth: int = 1,
) -> Dict[str, Any]:
    """运行单个平台爬虫并返回摘要。"""
    try:
        platform_cfg_path = str(CONFIG_DIR / "platforms" / f"{platform}.yaml")
        config = load_config(base_config_path, platform_cfg_path)

        if storage_override:
            config._merged.setdefault("storage", {})["default"] = storage_override  # noqa: SLF001

        cookie_file = cookie_file_override or str(COOKIE_DIR / f"{platform}.json")
        cookie_mgr = CookieManager(
            cookie_file=cookie_file,
            auto_refresh=config.get("cookie.auto_refresh", False),
            refresh_interval=config.get("cookie.refresh_interval", 1800),
        )

        crawler_cls = get_platform_cls(platform)
        crawler = crawler_cls(config=config, cookie_manager=cookie_mgr)
        results = await crawler.run(
            uids,
            crawl_related=crawl_related,
            max_related=max_related,
            max_depth=max_depth,
        )
        return {"platform": platform, "fetched": len(results), "errors": 0}
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[{platform}] 平台运行失败: {exc}")
        return {"platform": platform, "fetched": 0, "errors": 1, "error": str(exc)}


async def _run_multi_platform(task_file: str, base_config_path: str) -> List[Dict[str, Any]]:
    """从任务文件加载多平台配置并并发执行。"""
    tasks = load_yaml(task_file)
    platforms = tasks.get("platforms") or {}
    if not platforms:
        raise ValueError(f"任务文件中未找到平台配置: {task_file}")

    coros = []
    for platform, settings in platforms.items():
        uids = settings.get("uids") or []
        if not uids:
            logger.warning(f"[{platform}] 未配置 uids，跳过")
            continue
        storage = settings.get("storage")
        cookie_file = settings.get("cookie_file")
        crawl_related = settings.get("crawl_related", False)
        max_related = settings.get("max_related_per_user", 50)
        max_depth = settings.get("max_depth", 1)
        coros.append(
            _run_single_platform(
                platform, uids, base_config_path, storage, cookie_file,
                crawl_related, max_related, max_depth,
            )
        )

    if not coros:
        raise ValueError("任务文件中没有任何有效的平台任务")

    logger.info(f"已加载 {len(coros)} 个平台任务，开始并发执行")
    results = await asyncio.gather(*coros, return_exceptions=False)
    return results


async def main() -> int:
    """主入口：读取 configs/tasks.yaml 并发启动所有平台爬虫。

    若 ``schedule.enabled`` 为 true，则按 ``schedule.interval`` 秒循环运行。
    """
    base_config = load_yaml(BASE_CONFIG)
    _init_logging(Config(base_config, {}))
    logger.info(f"多平台并发模式，任务文件: {TASK_FILE}")

    tasks = load_yaml(str(TASK_FILE))
    schedule = tasks.get("schedule") or {}
    enabled = schedule.get("enabled", False)
    interval = schedule.get("interval", 3600)

    if enabled:
        logger.info(f"定时持续运行模式已启用，间隔 {interval} 秒（{interval / 3600:.1f} 小时）")
        cycle = 0
        while True:
            cycle += 1
            logger.info(f"========== 第 {cycle} 轮爬取开始 ==========")
            try:
                results = await _run_multi_platform(str(TASK_FILE), str(BASE_CONFIG))
                total = sum(r.get("fetched", 0) for r in results)
                logger.info(f"第 {cycle} 轮完成，共抓取 {total} 个用户")
            except Exception as exc:  # noqa: BLE001
                logger.error(f"第 {cycle} 轮运行异常: {exc}")
            logger.info(f"等待 {interval} 秒后进入下一轮（Ctrl+C 停止）")
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                logger.info("收到停止信号，退出")
                break
        return 0

    results = await _run_multi_platform(str(TASK_FILE), str(BASE_CONFIG))

    summary = {
        "mode": "multi-platform",
        "platforms": results,
        "total_fetched": sum(r.get("fetched", 0) for r in results),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def run() -> None:
    """同步入口。"""
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n用户中断")
        sys.exit(130)


if __name__ == "__main__":
    run()
